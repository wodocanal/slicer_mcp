"""Validation helpers for verified project and G-code file writes."""

import json
import os
import re
import time
import zipfile
import math
import xml.etree.ElementTree as ET

try:
    from xml.sax.saxutils import escape
except ImportError:  # pragma: no cover
    escape = None

from .errors import ToolError


def resolve_output_path(raw_path, extension, default_directory, default_stem,
                        replace_existing=False):
    """Return a new absolute path, creating only the default export directory."""
    raw_path = str(raw_path or "").strip()
    extension = extension.lower()
    if not extension.startswith("."):
        extension = "." + extension
    if raw_path:
        path = os.path.abspath(os.path.expanduser(raw_path))
        if os.path.splitext(path)[1].lower() != extension:
            path += extension
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise ToolError("Output directory does not exist: %s" % parent)
    else:
        parent = os.path.abspath(os.path.expanduser(default_directory))
        if not os.path.isdir(parent):
            try:
                os.makedirs(parent)
            except OSError as error:
                if not os.path.isdir(parent):
                    raise ToolError("Could not create output directory: %s" % error)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(parent, "%s-%s%s" % (default_stem, stamp, extension))
        index = 2
        while os.path.exists(path):
            path = os.path.join(
                parent, "%s-%s-%d%s" % (default_stem, stamp, index, extension)
            )
            index += 1
    if os.path.exists(path) and not replace_existing:
        raise ToolError("Output file already exists: %s" % path)
    return path


def gcode_text_from_buffer(value):
    """Flatten Cura's per-plate G-code buffer without inventing commands."""
    parts = []

    def append(item):
        if item is None:
            return
        if isinstance(item, bytes):
            parts.append(item.decode("utf-8", "replace"))
            return
        if isinstance(item, str):
            parts.append(item)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                append(child)
            return
        raise ToolError("Unsupported value in the Cura G-code buffer")

    append(value)
    text = "".join(parts)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def write_standard_3mf(path, objects, project_metadata):
    """Write a non-interactive standards-based 3MF archive with project metadata."""
    path = os.path.abspath(path)
    if not objects:
        raise ToolError("Cannot save a 3MF project without printable scene objects")
    if os.path.exists(path):
        if not os.path.isfile(path):
            raise ToolError("3MF output target is not a file: %s" % path)
        os.remove(path)
    try:
        model_xml = _build_3mf_model_xml(objects)
        metadata_json = json.dumps(
            project_metadata or {}, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr("[Content_Types].xml", _content_types_xml())
            archive.writestr("_rels/.rels", _root_relationships_xml())
            archive.writestr("3D/3dmodel.model", model_xml)
            archive.writestr("Metadata/slicer_mcp_project.json", metadata_json)
        verification = verify_exported_file(path, "3mf")
        return {
            "success": True,
            "status": "saved",
            "verified": True,
            "path": path,
            "bytes": verification["bytes"],
            "format": "3mf",
            "writer": "stdlib_zipfile_3mf",
            "objects": len(objects),
        }
    except Exception:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        raise


def _build_3mf_model_xml(objects):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
        '<metadata name="Application">Slicer MCP STE project exporter</metadata>',
        '<resources>',
    ]
    build_items = []
    for index, item in enumerate(objects, 1):
        vertices = item.get("vertices") or []
        triangles = item.get("triangles") or []
        if not vertices or not triangles:
            raise ToolError("3MF object %d has no usable triangle mesh" % index)
        name = _xml_escape(item.get("name") or "object-%d" % index)
        lines.append('<object id="%d" type="model" name="%s"><mesh>' % (index, name))
        lines.append("<vertices>")
        for vertex in vertices:
            lines.append(
                '<vertex x="%s" y="%s" z="%s" />'
                % (_number(vertex[0]), _number(vertex[1]), _number(vertex[2]))
            )
        lines.append("</vertices><triangles>")
        vertex_count = len(vertices)
        for triangle in triangles:
            values = [int(triangle[0]), int(triangle[1]), int(triangle[2])]
            if min(values) < 0 or max(values) >= vertex_count:
                raise ToolError("3MF object %d contains an invalid triangle index" % index)
            lines.append(
                '<triangle v1="%d" v2="%d" v3="%d" />'
                % (values[0], values[1], values[2])
            )
        lines.append("</triangles></mesh></object>")
        build_items.append('<item objectid="%d" />' % index)
    lines.append("</resources><build>")
    lines.extend(build_items)
    lines.append("</build></model>")
    return "\n".join(lines).encode("utf-8")


def _content_types_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />'
        '<Default Extension="json" ContentType="application/json" />'
        '</Types>'
    ).encode("utf-8")


def _root_relationships_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" />'
        '</Relationships>'
    ).encode("utf-8")


def _xml_escape(value):
    value = str(value)
    if escape is None:
        return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return escape(value, {'"': "&quot;"})


def _number(value):
    return "%.9g" % float(value)


def verify_exported_file(path, kind, expected_mode=None, bed_temperature=None):
    """Verify that a writer really created a useful file on disk."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise ToolError("Writer reported completion, but the file was not created: %s" % path)
    if not os.path.isfile(path):
        raise ToolError("Export target is not a file: %s" % path)
    size = os.path.getsize(path)
    if kind == "3mf":
        if size <= 1024:
            raise ToolError("The saved 3MF project is too small (%d bytes): %s"
                            % (size, path))
        required = set([
            "[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model",
            "Metadata/slicer_mcp_project.json",
        ])
        try:
            with zipfile.ZipFile(path, "r") as archive:
                names = set(archive.namelist())
                missing = sorted(required - names)
                if missing:
                    raise ToolError("3MF entries missing: %s" % ", ".join(missing))
                bad = archive.testzip()
                if bad:
                    raise ToolError("Corrupt 3MF member: %s" % bad)
                root = ET.fromstring(archive.read("3D/3dmodel.model"))
                metadata = json.loads(
                    archive.read("Metadata/slicer_mcp_project.json").decode("utf-8")
                )
        except (zipfile.BadZipFile, ET.ParseError, ValueError) as error:
            raise ToolError("Invalid 3MF package: %s" % error)
        namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        objects = root.findall(".//m:resources/m:object", namespace)
        object_ids = [item.get("id") for item in objects]
        if not objects or len(object_ids) != len(set(object_ids)):
            raise ToolError("3MF must contain uniquely identified model objects")
        object_id_set = set(object_ids)
        triangle_count = 0
        for item in objects:
            vertices = item.findall(".//m:vertices/m:vertex", namespace)
            for vertex in vertices:
                for axis in ("x", "y", "z"):
                    try:
                        value = float(vertex.get(axis))
                    except (TypeError, ValueError):
                        raise ToolError("3MF contains an invalid vertex coordinate")
                    if not math.isfinite(value):
                        raise ToolError("3MF contains a non-finite vertex coordinate")
            for triangle in item.findall(".//m:triangles/m:triangle", namespace):
                try:
                    indices = [int(triangle.get(key)) for key in ("v1", "v2", "v3")]
                except (TypeError, ValueError):
                    raise ToolError("3MF contains an invalid triangle index")
                if min(indices) < 0 or max(indices) >= len(vertices):
                    raise ToolError("3MF triangle references a missing vertex")
                triangle_count += 1
        build_items = root.findall(".//m:build/m:item", namespace)
        if not build_items or any(item.get("objectid") not in object_id_set
                                  for item in build_items):
            raise ToolError("3MF build section references missing objects")
        if not isinstance(metadata, dict) or not metadata.get("schema"):
            raise ToolError("3MF project metadata is missing its schema")
        return {"bytes": size, "objects": len(objects),
                "triangles": triangle_count, "metadata_schema": metadata.get("schema")}
    if kind != "gcode":
        raise ToolError("Unsupported verification format: %s" % kind)
    if size <= 1024:
        raise ToolError("The exported G-code is too small (%d bytes): %s" % (size, path))
    with open(path, "rb") as stream:
        sample = stream.read(min(size, 4 * 1024 * 1024)).decode("utf-8", "replace")
    patterns = {
        "G28": r"(?mi)^\s*G28(?:\s|;|$)",
        "motion": r"(?mi)^\s*G[01](?:\s|;|$)",
        "extrusion": r"(?mi)^\s*G[01][^\r\n;]*\bE-?(?:\d|\.)",
        "M104/M109": r"(?mi)^\s*M10[49](?:\s|;|$)",
        "M140/M190": r"(?mi)^\s*M(?:140|190)(?:\s|;|$)",
        "START": r"(?mi)^\s*START(?:\s|;|$)",
        "G56": r"(?mi)^\s*G56(?:\s|;|$)",
        "rotary_motion": r"(?mi)^\s*G[01][^\r\n;]*\b[AC]-?(?:\d|\.)",
    }
    commands = {name: bool(re.search(pattern, sample)) for name, pattern in patterns.items()}
    # G28 is optional for Stereotech profiles: homing may be performed by the
    # printer/controller rather than emitted in the exported payload.
    detected_mode = expected_mode
    if not detected_mode:
        match = re.search(r"(?mi)^;\s*PRINTING_MODE:\[([^\]]+)\]", sample)
        detected_mode = match.group(1).strip() if match else "classic"
    is_five_d = detected_mode in ("cylindrical", "cylindrical_full")
    required = ["motion", "extrusion", "M104/M109"]
    if is_five_d:
        required.extend(["START", "G56", "rotary_motion"])
    elif bed_temperature is not None and float(bed_temperature) > 0:
        required.append("M140/M190")
    missing = [name for name in required if not commands[name]]
    if missing:
        raise ToolError(
            "G-code validation failed; missing required commands: %s" % ", ".join(missing)
        )
    warnings = [] if commands["G28"] else [
        "G28 is absent; accepted for a Stereotech profile"
    ]
    if not is_five_d and bed_temperature is None and not commands["M140/M190"]:
        warnings.append("Bed heating is absent; accepted because the active bed temperature is unknown")
    return {"bytes": size, "commands": commands, "warnings": warnings,
            "printing_mode": detected_mode}


def verified_write_result(tool_name, result):
    """Accept success only after a write tool reports and proves its disk artifact."""
    if tool_name not in ("save_project_3mf", "export_gcode"):
        return False
    if not isinstance(result, dict):
        return False
    path = result.get("path")
    if (result.get("success") is not True or result.get("verified") is not True
            or not isinstance(path, str) or not os.path.isabs(path)):
        return False
    try:
        actual_size = os.path.getsize(path)
    except OSError:
        return False
    minimum = 1024
    return actual_size > minimum and actual_size == result.get("bytes")
