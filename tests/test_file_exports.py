import zipfile

import pytest

from core.errors import ToolError
from core.file_exports import verify_exported_file, write_standard_3mf


def padded(lines):
    return ("\n".join(lines) + "\n" + ("; padding\n" * 300)).encode("utf-8")


def test_classic_and_5d_gcode_are_validated_by_mode(tmp_path):
    classic = tmp_path / "classic.gcode"
    classic.write_bytes(padded(["M104 S200", "M140 S60", "G1 X1 E0.4"]))
    result = verify_exported_file(str(classic), "gcode", "classic", 60)
    assert result["commands"]["extrusion"]

    five_d = tmp_path / "five-d.gcode"
    five_d.write_bytes(padded([";PRINTING_MODE:[cylindrical_full]", "M109 S205",
                               "START", "G56", "G1 X1 A2 C3 E0.5"]))
    result = verify_exported_file(str(five_d), "gcode", "cylindrical_full", 0)
    assert result["commands"]["rotary_motion"]
    assert not result["commands"]["M140/M190"]


def test_5d_gcode_requires_rotary_motion(tmp_path):
    path = tmp_path / "bad.gcode"
    path.write_bytes(padded(["M109 S205", "START", "G56", "G1 X1 E0.5"]))
    with pytest.raises(ToolError):
        verify_exported_file(str(path), "gcode", "cylindrical_full", 0)


def test_3mf_writer_and_structural_verifier_reject_corruption(tmp_path):
    vertices = [[float(index), 0.0, 0.0] for index in range(300)]
    triangles = [[index, index + 1, index + 2] for index in range(0, 297, 3)]
    path = tmp_path / "project.3mf"
    result = write_standard_3mf(str(path), [{"name": "part", "vertices": vertices,
                                             "triangles": triangles}],
                                {"schema": "slicer_mcp_project_v1"})
    assert result["verified"] is True
    assert verify_exported_file(str(path), "3mf")["triangles"] == len(triangles)
    broken = tmp_path / "broken.3mf"
    with zipfile.ZipFile(str(broken), "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("padding", "x" * 2000)
    with pytest.raises(ToolError):
        verify_exported_file(str(broken), "3mf")
