"""Checks that do not require a running STE Slicer instance."""

import ast
import json
import math
import os
import pathlib
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PLUGIN = pathlib.Path(__file__).resolve().parents[1] / "ste_plugin" / "AIAssistant"
sys.path.insert(0, str(PLUGIN))

from core.economics import compare_cost_variants, estimate_print_cost
from core.errors import ToolError
from core.jsonrpc import JsonRpcDispatcher
from core.llm import Agent, requested_file_writes
from core.mcp_server import McpHttpServer
from core.tool_registry import ToolRegistry


class IntegrationTest(unittest.TestCase):
    def test_economics_separates_print_work_and_customer_price(self):
        status = {
            "state": "done",
            "print_time_seconds": 5400,
            "material_weights_g": [40, 10],
            "material_names": ["PC", "PVA"],
        }
        quote = estimate_print_cost(
            status,
            {
                "machine_hour_rate_rub": 200,
                "material_prices_rub_per_kg": [2000, 4000],
                "outside_work_items": [{"label": "Подготовка CAD", "amount_rub": 50}],
                "markup_rub": 25,
                "tax_rub": 10,
                "target_budget_rub": 500,
            },
        )
        self.assertEqual([item["cost_rub"] for item in quote["materials"]], [80, 40])
        self.assertEqual(quote["machine_cost_rub"], 300)
        self.assertEqual(quote["print_cost_rub"], 420)
        self.assertEqual(quote["outside_work_cost_rub"], 50)
        self.assertEqual(quote["customer_price_rub"], 505)
        self.assertFalse(quote["budget_met"])
        self.assertEqual(quote["budget_difference_rub"], 5)

    def test_economics_requires_completed_slice_and_explicit_prices(self):
        status = {"state": "processing", "print_time_seconds": 30, "material_weights_g": [2]}
        inputs = {"machine_hour_rate_rub": 100, "material_prices_rub_per_kg": [1000]}
        with self.assertRaises(ToolError):
            estimate_print_cost(status, inputs)
        status["state"] = "done"
        for prices in ([], [None], [-1], [float("nan")]):
            with self.assertRaises(ToolError):
                estimate_print_cost(status, dict(inputs, material_prices_rub_per_kg=prices))
        unused = {"state": "done", "print_time_seconds": 30, "material_weights_g": [0, 2]}
        self.assertEqual(
            estimate_print_cost(unused, dict(inputs, material_prices_rub_per_kg=[None, 1000]))[
                "materials"
            ][0]["cost_rub"],
            0,
        )

    def test_cost_comparison_exposes_changed_tariffs_and_objects(self):
        status = {"state": "done", "print_time_seconds": 3600, "material_weights_g": [100]}
        first = estimate_print_cost(
            status, {"machine_hour_rate_rub": 100, "material_prices_rub_per_kg": [1000]}
        )
        status["print_time_seconds"] = 1800
        second = estimate_print_cost(
            status, {"machine_hour_rate_rub": 100, "material_prices_rub_per_kg": [1000]}
        )
        baseline = {
            "quote": first,
            "tariffs": {"machine": 100},
            "scene_objects": [{"name": "part"}],
        }
        candidate = {
            "quote": second,
            "tariffs": {"machine": 100},
            "scene_objects": [{"name": "part"}],
        }
        result = compare_cost_variants(baseline, candidate)
        self.assertEqual(result["changes"]["print_cost_rub"], -50)
        self.assertTrue(result["same_tariffs"])
        self.assertTrue(result["same_scene_objects"])
        candidate["tariffs"]["machine"] = 200
        candidate["scene_objects"] = []
        result = compare_cost_variants(baseline, candidate)
        self.assertFalse(result["same_tariffs"])
        self.assertFalse(result["same_scene_objects"])

    def test_agent_requires_verified_save_tool_result(self):
        self.assertEqual(
            requested_file_writes("Сохрани проект в project.3mf"), {"save_project_3mf"}
        )
        self.assertEqual(
            requested_file_writes("Сохрани готовый G-code текущего проекта в result.gcode"),
            {"export_gcode"},
        )
        self.assertEqual(
            requested_file_writes("Сохрани проект и G-code в файлы project.3mf и result.gcode"),
            {"save_project_3mf", "export_gcode"},
        )
        self.assertEqual(
            requested_file_writes(
                "Сохрани только 3MF текущего проекта в project.3mf. "
                "Новый слайсинг не запускай и G-code не экспортируй"
            ),
            {"save_project_3mf"},
        )
        self.assertEqual(requested_file_writes("Ничего не сохраняй"), set())

        class Client:
            def __init__(self, responses):
                self.responses = iter(responses)

            def complete(self, messages, tools):
                return next(self.responses)

        request = "Сохрани проект в project.3mf"
        registry = ToolRegistry()
        registry.add(
            "save_project_3mf",
            "Save project",
            {"path": {"type": "string"}},
            ["path"],
            lambda args: {"path": args["path"], "bytes": 100},
            False,
        )
        hallucination = Agent(Client([{"content": "Проект сохранён, 100 байт"}]), registry)
        self.assertIn("save_project_3mf", hallucination.run([], request, []))

        call = {
            "id": "call-1",
            "function": {"name": "save_project_3mf", "arguments": '{"path":"project.3mf"}'},
        }
        verified = Agent(Client([{"tool_calls": [call]}, {"content": "Проект сохранён"}]), registry)
        self.assertEqual(verified.run([], request, []), "Проект сохранён")

        failing_registry = ToolRegistry()

        def fail(args):
            raise RuntimeError("Disk full")

        failing_registry.add(
            "save_project_3mf", "Save project", {"path": {"type": "string"}}, ["path"], fail, False
        )
        failed = Agent(
            Client([{"tool_calls": [call]}, {"content": "Проект сохранён"}]), failing_registry
        )
        self.assertIn("save_project_3mf", failed.run([], request, []))

    def test_python_35_syntax(self):
        for path in PLUGIN.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 5))

    def test_mcp_requires_token_for_write_tools(self):
        registry = ToolRegistry()
        writes = []
        registry.add(
            "change",
            "Change",
            {"value": {"type": "number"}},
            ["value"],
            lambda args: writes.append(args["value"]) or {"ok": True},
            False,
        )
        server = McpHttpServer(JsonRpcDispatcher(registry))
        port = server.start()
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "change", "arguments": {"value": 3}},
            }
        ).encode()
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(url, data=body, headers={"Content-Type": "application/json"}))
            self.assertEqual(denied.exception.code, 403)
            self.assertEqual(writes, [])
            accepted = urlopen(
                Request(
                    url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Session-Token": server.token,
                    },
                )
            )
            self.assertEqual(json.load(accepted)["result"]["isError"], False)
            self.assertEqual(writes, [3])
        finally:
            server.stop()

    def test_setting_validation_rejects_bad_values_and_unsupported_5d(self):
        source = PLUGIN / "ste" / "adapter.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        limits = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SAFE_NUMERIC_RANGES"
                for target in node.targets
            )
        )
        adapter = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SteSlicerAdapter"
        )
        validator = next(
            node
            for node in adapter.body
            if isinstance(node, ast.FunctionDef) and node.name == "_validate_setting_value"
        )

        class ToolError(Exception):
            pass

        namespace = {"math": math, "ToolError": ToolError}
        module = ast.Module(body=[limits, validator], type_ignores=[])
        exec(compile(module, str(source), "exec"), namespace)

        class Stack:
            def getProperty(self, key, field):
                if key == "machine_five_axis":
                    return False
                if field == "options":
                    return {"classic": "3D", "cylindrical_full": "5D"}
                return None

        validate = namespace["_validate_setting_value"]
        self.assertEqual(validate(None, Stack(), "layer_height", "0,16", 0.1), 0.16)
        with self.assertRaises(ToolError):
            validate(None, Stack(), "layer_height", 5, 0.1)
        with self.assertRaises(ToolError):
            validate(None, Stack(), "printing_mode", "cylindrical_full", "classic")

    def test_slice_status_uses_installed_ste_backend_api(self):
        source = PLUGIN / "ste" / "adapter.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        adapter = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SteSlicerAdapter"
        )
        method = next(
            node
            for node in adapter.body
            if isinstance(node, ast.FunctionDef) and node.name == "_slice_status"
        )

        class DurationFormat:
            class Format:
                Seconds = "seconds"

        namespace = {"DurationFormat": DurationFormat}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)

        class Stub:
            pass

        backend = Stub()
        backend._is_disabled = False
        backend._slicing = False
        scene = Stub()
        scene.gcode_dict = {}
        controller = Stub()
        controller.getScene = lambda: scene
        plates = Stub()
        plates.activeBuildPlate = 0
        information = Stub()
        duration = Stub()
        duration.getDisplayString = lambda duration_format: "60"
        information.currentPrintTime = duration
        information.materialLengths = [1.0]
        information.materialWeights = [3.0]
        information.materialNames = ["PLA"]
        application = Stub()
        application.getBackend = lambda: backend
        application.getController = lambda: controller
        application.getMultiBuildPlateModel = lambda: plates
        application.getPrintInformation = lambda: information
        instance = Stub()
        instance._app = application
        status = namespace["_slice_status"]

        self.assertEqual(status(instance)["state"], "not_started")
        backend._slicing = True
        self.assertEqual(status(instance)["state"], "processing")

        class Clock:
            @staticmethod
            def time():
                return 300

        namespace["time"] = Clock()
        backend._slice_start_time = 100
        self.assertEqual(status(instance)["elapsed_seconds"], 200)
        self.assertIn("warning", status(instance))
        backend._slicing = False
        scene.gcode_dict[0] = ["G1 X0"]
        self.assertEqual(status(instance)["state"], "done")
        self.assertEqual(status(instance)["print_time_seconds"], 60)
        self.assertEqual(status(instance)["print_time"], "00:01:00")
        backend._is_disabled = True
        self.assertEqual(status(instance)["state"], "disabled")

    def test_start_slicing_uses_same_entry_point_as_prepare_button(self):
        source = PLUGIN / "ste" / "adapter.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        adapter = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SteSlicerAdapter"
        )
        method = next(
            node
            for node in adapter.body
            if isinstance(node, ast.FunctionDef) and node.name == "_start_slicing"
        )
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)

        class Backend:
            def __init__(self):
                self._slicing = False
                self.calls = 0

            def forceSlice(self):
                self.calls += 1
                self._slicing = True

        backend = Backend()

        class App:
            def getBackend(self):
                return backend

        class Instance:
            _app = App()

            def _slice_status(self):
                return {"state": "processing"}

        start = namespace["_start_slicing"]
        self.assertTrue(start(Instance())["started"])
        self.assertFalse(start(Instance())["started"])
        self.assertEqual(backend.calls, 1)

    def test_export_writes_outside_ui_thread_and_cleans_failed_files(self):
        source = PLUGIN / "ste" / "adapter.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        adapter = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SteSlicerAdapter"
        )
        methods = [
            node
            for node in adapter.body
            if isinstance(node, ast.FunctionDef)
            and node.name in ("export_gcode", "save_project", "_export")
        ]

        class ToolError(Exception):
            pass

        namespace = {"os": os, "tempfile": tempfile, "ToolError": ToolError}
        export_class = ast.ClassDef(
            name="ExportAdapter", bases=[], keywords=[], body=methods, decorator_list=[]
        )
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[export_class], type_ignores=[])),
                str(source),
                "exec",
            ),
            namespace,
        )
        instance = namespace["ExportAdapter"]()
        instance.in_ui = False

        class Writer:
            success = True

            def write(self, stream, nodes, mode):
                self_test.assertFalse(instance.in_ui)
                if mode == "gcode":
                    stream.write(";тип=3D\nG1 X0\n")
                else:
                    stream.write(b"valid test content")
                return self.success

        self_test = self
        writer = Writer()
        instance._prepare_export = lambda kind: (writer, object(), kind)

        def on_qt(function, *args):
            instance.in_ui = True
            try:
                return function(*args)
            finally:
                instance.in_ui = False

        instance._on_qt = on_qt
        with tempfile.TemporaryDirectory(dir=str(PLUGIN.parent)) as directory:
            target = os.path.join(directory, "project.3mf")
            result = instance.save_project({"path": target})
            self.assertEqual(result["bytes"], len(b"valid test content"))
            self.assertEqual(pathlib.Path(target).read_bytes(), b"valid test content")
            with self.assertRaises(ToolError):
                instance.save_project({"path": target})

            gcode = os.path.join(directory, "result.gcode")
            result = instance.export_gcode({"path": gcode})
            self.assertEqual(result["format"], "gcode")
            self.assertEqual(pathlib.Path(gcode).read_text(encoding="utf-8"), ";тип=3D\nG1 X0\n")

            writer.success = False
            failed = os.path.join(directory, "failed.3mf")
            with self.assertRaises(ToolError):
                instance.save_project({"path": failed})
            self.assertFalse(os.path.exists(failed))
            self.assertEqual(sorted(os.listdir(directory)), ["project.3mf", "result.gcode"])


if __name__ == "__main__":
    unittest.main()
