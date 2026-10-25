"""Build a deterministic STE plugin ZIP after Python 3.5 syntax validation."""

import ast
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "AIAssistantTeam"
OUTPUT = ROOT / "dist" / "AIAssistantTeam-ste-plugin.zip"


def main():
    files = sorted(path for path in SOURCE.rglob("*") if path.is_file()
                   and "__pycache__" not in path.parts
                   and path.suffix not in (".pyc", ".pyo", ".qmlc"))
    for path in files:
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path),
                      feature_version=(3, 5))
    OUTPUT.parent.mkdir(exist_ok=True)
    timestamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(str(OUTPUT), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = Path("AIAssistantTeam") / path.relative_to(SOURCE)
            info = zipfile.ZipInfo(relative.as_posix(), timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print("Built %s with %d files" % (OUTPUT, len(files)))
    print("SHA256 %s" % digest)


if __name__ == "__main__":
    main()
