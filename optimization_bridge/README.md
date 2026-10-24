# Ansys optimization bridge (beta)

This separate Windows process connects the STE plugin to a licensed Ansys
Mechanical installation. It requires Python 3.12+ and PyMechanical. It never
ships or imitates a topology optimizer.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install ansys-mechanical-core
python server.py --data-dir C:\slicer-optimization-data
```

The server writes its loopback URL and random token to the current user's
`%LOCALAPPDATA%\SlicerMCP\optimization-bridge.json`. The STE plugin discovers
this file automatically, so normally you only need to start the bridge and
click **Check Ansys**. The file is removed on a normal shutdown and is protected
by the Windows user-profile ACL. `--connection-file` can override its location;
that nonstandard path requires corresponding manual plugin configuration.

The token is still printed for diagnostics and can be entered manually in the
plugin's **Optimization beta** tab as a fallback.

Each calculation is an explicitly prepared template:

```text
C:\slicer-optimization-data\
├── templates\
│   └── bracket_v1\
│       ├── manifest.json
│       └── mechanical_script.py
└── jobs\
```

`manifest.json` declares safe inputs and allowed source formats:

```json
{
  "id": "bracket_v1",
  "name": "Cantilever bracket v1",
  "description": "Prepared static structural and topology workflow",
  "required_inputs": ["material_id", "load_n", "mass_retained_percent"],
  "source_extensions": [".step", ".stp"]
}
```

`mechanical_script.py` executes inside Mechanical. The bridge supplies two
global variables: `REQUEST_PATH` and `OUTPUT_DIR`. The script must read the
request, run the baseline analysis, topology optimization and validation,
export the optimized STL inside `OUTPUT_DIR`, and create `result.json` there.
Artifact paths in `result.json` must remain inside the job directory.

The result must contain `baseline_analysis`, `optimization`, `validation` with
status `passed` or `failed`, and `artifacts.optimized_stl`. The bridge refuses to
mark a job completed when these engineering stages or the actual STL are absent.

Until a real template and a compatible license exist, `/health?deep=1` reports
the backend as unavailable and the STE plugin must not claim that optimization
ran successfully.
