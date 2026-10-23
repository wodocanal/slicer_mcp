"""Build a deterministic source ZIP accepted by the STE user plugins folder."""

import ast
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ste_plugin" / "AIAssistant"
OUTPUT = ROOT / "dist" / "AIAssistant-ste-plugin.zip"


def check_python35(path):
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 5))


def main():
    files = sorted(
        path
        for path in SOURCE.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in (".pyc", ".pyo")
    )
    for path in files:
        if path.suffix == ".py":
            check_python35(path)
    OUTPUT.parent.mkdir(exist_ok=True)
    timestamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(str(OUTPUT), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = Path("AIAssistant") / path.relative_to(SOURCE)
            info = zipfile.ZipInfo(relative.as_posix(), timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    print(f"Built {OUTPUT} with {len(files)} files")


if __name__ == "__main__":
    main()
