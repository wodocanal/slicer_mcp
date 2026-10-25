import pytest

from core.errors import ToolError
from core.printer_fleet import PrinterFleet


def configs():
    return [
        {"id": "p1", "name": "Printer 1", "backend": "octoprint",
         "url": "http://127.0.0.1:5001", "token": "secret"},
        {"id": "p2", "name": "Printer 2", "backend": "moonraker",
         "url": "http://127.0.0.1:7125"},
    ]


def test_fleet_lists_without_exposing_tokens_and_balances_jobs():
    fleet = PrinterFleet(configs())
    listing = fleet.list()
    assert listing["configured"] == 2
    assert "token" not in listing["printers"][0]
    plan = fleet.plan([{"job_id": "long", "duration_seconds": 100},
                       {"job_id": "short", "duration_seconds": 40},
                       {"job_id": "mid", "duration_seconds": 70}])
    assert len(plan["assignments"]) == 3
    assert plan["makespan_seconds"] == 110


def test_fleet_rejects_unknown_backend_and_printer():
    with pytest.raises(ToolError):
        PrinterFleet([{"id": "x", "url": "http://x", "backend": "unknown"}])
    with pytest.raises(ToolError):
        PrinterFleet(configs()).get("missing")
