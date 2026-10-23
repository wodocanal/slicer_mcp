"""Write only this checkout's Codex MCP configuration; run after uv sync."""

import json
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".codex/config.toml"
python = root / ".venv/bin/python"
if not python.exists():
    sys.exit("Run uv sync first")
config = f"""# Local paths: regenerate with uv run python scripts/configure_codex.py.
[mcp_servers.slicer]
command = {json.dumps(str(python))}
args = ["-m", "slicer_mcp.server"]
cwd = {json.dumps(str(root))}
startup_timeout_sec = 30
tool_timeout_sec = 240
enabled = true

[mcp_servers.slicer.env]
SLICER_MCP_WORKSPACE = {json.dumps(str(root / ".slicer-mcp"))}
"""
if target.exists():
    existing = tomllib.loads(target.read_text())
    if "slicer" in existing.get("mcp_servers", {}):
        if target.read_text() == config:
            print(f"Already configured: {target}")
            sys.exit(0)
        sys.exit(
            "A slicer entry already exists; edit it explicitly to preserve local customization"
        )
    config = target.read_text().rstrip() + "\n\n" + config
target.parent.mkdir(exist_ok=True)
target.write_text(config)
print(f"Configured project MCP: {target}")
