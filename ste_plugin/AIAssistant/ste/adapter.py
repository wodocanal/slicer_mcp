import copy
import math
import os
import tempfile
import time

from PyQt5.QtCore import QUrl

from UM.Application import Application
from UM.Logger import Logger
from UM.Math.Quaternion import Quaternion
from UM.Math.Vector import Vector
from UM.Operations.GroupedOperation import GroupedOperation
from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
from UM.Operations.RotateOperation import RotateOperation
from UM.Operations.ScaleOperation import ScaleOperation
from UM.Operations.TranslateOperation import TranslateOperation
from UM.Qt.Duration import DurationFormat
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.SceneNode import SceneNode
from UM.Settings.ContainerRegistry import ContainerRegistry

from steslicer.Settings.ExtruderManager import ExtruderManager

from ..core.errors import ToolError
from ..core.economics import estimate_print_cost, compare_cost_variants
from ..core.risk import assess_print_risk


ALLOWED_SETTINGS = set(
    [
        "printing_mode",
        "layer_height",
        "wall_line_count",
        "wall_thickness",
        "top_layers",
        "bottom_layers",
        "infill_sparse_density",
        "infill_pattern",
        "support_enable",
        "support_type",
        "support_angle",
        "adhesion_type",
        "material_print_temperature",
        "material_bed_temperature",
        "print_speed",
        "travel_speed",
        "retraction_enable",
        "retraction_amount",
        "cool_fan_enabled",
        "cool_fan_speed",
        "cylindrical_mode_base_diameter",
        "cylindrical_mode_overlap",
    ]
)

SAFE_NUMERIC_RANGES = {
    "layer_height": (0.05, 0.4),
    "wall_line_count": (1, 10),
    "infill_sparse_density": (0, 100),
    "support_angle": (0, 90),
    "cool_fan_speed": (0, 100),
    "material_print_temperature": (150, 320),
    "material_bed_temperature": (0, 120),
    "print_speed": (5, 200),
    "travel_speed": (5, 350),
}


class SteSlicerAdapter(object):
    def __init__(self, application, executor):
        self._app = application
        self._executor = executor
        self._nodes = {}
        self._cost_variants = {}

    def _on_qt(self, function, *args):
        return self._executor.call(function, *args)

    def get_state(self, unused=None):
        return self._on_qt(self._get_state)

    def _get_state(self):
        stack = self._stack()
        manager = self._app.getMachineManager()
        return {
            "machine_id": stack.getId(),
            "machine_name": stack.getName(),
            "printing_mode": stack.getProperty("printing_mode", "value"),
            "quality_type": manager.activeQualityType,
            "material_id": manager.activeMaterialId,
            "objects": self._list_objects(),
            "slicing": self._slice_status(),
        }

    def get_setting(self, args):
        return self._on_qt(self._get_setting, args["key"], args.get("scope", "global"))

    def _get_setting(self, key, scope):
        self._require_setting(key)
        stack = self._setting_stack(scope)
        value = stack.getProperty(key, "value")
        if value is None:
            raise ToolError("Setting is unavailable in the active profile: %s" % key)
        return {
            "key": key,
            "scope": scope,
            "value": value,
            "enabled": bool(stack.getProperty(key, "enabled")),
        }

    def set_setting(self, args):
        return self._on_qt(
            self._set_setting, args["key"], args["value"], args.get("scope", "global")
        )

    def _set_setting(self, key, value, scope):
        self._require_setting(key)
        stack = self._setting_stack(scope)
        previous = stack.getProperty(key, "value")
        if previous is None:
            raise ToolError("Setting is unavailable in the active profile: %s" % key)
        if not stack.getProperty(key, "enabled"):
            raise ToolError("Setting is disabled in the active profile: %s" % key)
        value = self._validate_setting_value(stack, key, value, previous)
        container = stack.userChanges
        container.setProperty(key, "value", value)
        actual = stack.getProperty(key, "value")
        if str(actual) != str(value) and not (isinstance(value, bool) and bool(actual) == value):
            Logger.log(
                "w", "AI Assistant requested %s=%s; effective value is %s", key, value, actual
            )
        return {"key": key, "scope": scope, "before": previous,
                "requested": value, "effective": actual}

    def set_print_mode(self, args):
        mode = args["mode"]
        if mode not in ("classic", "cylindrical", "cylindrical_full"):
            raise ToolError("mode must be classic, cylindrical or cylindrical_full")
        return self._on_qt(self._set_setting, "printing_mode", mode, "global")

    def _validate_setting_value(self, stack, key, value, previous):
        if key == "printing_mode" and value != "classic":
            if not stack.getProperty("machine_five_axis", "value"):
                raise ToolError("The active printer does not support 5D printing")
        if isinstance(previous, bool):
            if isinstance(value, str):
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
            if not isinstance(value, bool):
                raise ToolError("Boolean setting requires true or false")
            return value
        if isinstance(previous, (int, float)):
            if isinstance(value, bool):
                raise ToolError("Numeric setting requires a number")
            try:
                number = float(value.replace(",", ".") if isinstance(value, str) else value)
            except (TypeError, ValueError):
                raise ToolError("Numeric setting requires a number")
            if not math.isfinite(number):
                raise ToolError("Setting value must be finite")
            if isinstance(previous, int):
                if not number.is_integer():
                    raise ToolError("Setting requires an integer")
                value = int(number)
            else:
                value = number
            limits = SAFE_NUMERIC_RANGES.get(key)
            if limits and not limits[0] <= value <= limits[1]:
                raise ToolError("Value outside safe range %s-%s" % limits)
            return value
        if isinstance(previous, str):
            if not isinstance(value, str):
                raise ToolError("Setting requires a text option")
            options = stack.getProperty(key, "options") or {}
            if options and value not in options:
                raise ToolError("Option is unavailable in the active profile")
            return value
        raise ToolError("Unsupported setting type")

    def list_profiles(self, args):
        return self._on_qt(self._list_profiles, args.get("kind", "quality"))

    def _list_profiles(self, kind):
        if kind == "quality":
            manager = self._app.getMachineManager()
            groups = manager._quality_manager.getQualityGroups(manager.activeMachine)
            return {
                "kind": kind,
                "active": manager.activeQualityType,
                "profiles": [
                    {"id": key, "name": str(value), "available": bool(value.is_available)}
                    for key, value in sorted(groups.items())
                ],
            }
        if kind == "material":
            registry = ContainerRegistry.getInstance()
            materials = registry.findInstanceContainers(type="material")
            seen = set()
            result = []
            for material in materials:
                root_id = material.getMetaDataEntry("base_file", material.getId())
                if root_id in seen:
                    continue
                seen.add(root_id)
                result.append(
                    {
                        "id": root_id,
                        "name": material.getName(),
                        "brand": material.getMetaDataEntry("brand", ""),
                        "material": material.getMetaDataEntry("material", ""),
                    }
                )
            result.sort(key=lambda item: (item["brand"], item["name"]))
            return {
                "kind": kind,
                "active": self._app.getMachineManager().activeMaterialId,
                "profiles": result,
            }
        raise ToolError("kind must be quality or material")

    def select_profile(self, args):
        return self._on_qt(
            self._select_profile, args["kind"], args["id"], str(args.get("extruder", 0))
        )

    def _select_profile(self, kind, profile_id, extruder):
        manager = self._app.getMachineManager()
        if kind == "quality":
            manager.setQualityGroupByQualityType(profile_id)
            return {"kind": kind, "selected": manager.activeQualityType}
        if kind == "material":
            manager.setMaterialById(extruder, profile_id)
            return {"kind": kind, "extruder": extruder, "selected": manager.activeMaterialId}
        raise ToolError("kind must be quality or material")

    def load_model(self, args):
        path = os.path.abspath(os.path.expanduser(args["path"]))
        if not os.path.isfile(path):
            raise ToolError("Model file not found: %s" % path)
        if os.path.splitext(path)[1].lower() not in (".stl", ".obj", ".3mf"):
            raise ToolError("Supported model formats: STL, OBJ, 3MF")
        self._on_qt(self._app.readLocalFile, QUrl.fromLocalFile(path), True)
        return {
            "accepted": True,
            "path": path,
            "note": "Loading is asynchronous; call list_objects to observe completion.",
        }

    def list_objects(self, unused=None):
        return self._on_qt(self._list_objects)

    def _list_objects(self):
        self._nodes = {}
        result = []
        for node in self._scene_nodes():
            node_id = str(id(node))
            self._nodes[node_id] = node
            bounds = node.getBoundingBox()
            position = node.getWorldPosition()
            scale = node.getScale()
            result.append(
                {
                    "id": node_id,
                    "name": node.getName(),
                    "dimensions_mm": [bounds.width, bounds.height, bounds.depth],
                    "footprint_bbox_mm2": bounds.width * bounds.depth,
                    "position": [position.x, position.y, position.z],
                    "scale": [scale.x, scale.y, scale.z],
                    "build_plate": node.callDecoration("getBuildPlateNumber"),
                }
            )
        return result

    def transform_object(self, args):
        return self._on_qt(self._transform_object, args)

    def _transform_object(self, args):
        node = self._node(args["object_id"])
        operation = GroupedOperation()
        if "position" in args:
            xyz = self._vector3(args["position"], "position")
            operation.addOperation(TranslateOperation(node, Vector(*xyz), set_position=True))
        if "rotation_deg" in args:
            angles = self._vector3(args["rotation_deg"], "rotation_deg")
            axes = (Vector.Unit_X, Vector.Unit_Y, Vector.Unit_Z)
            for angle, axis in zip(angles, axes):
                quaternion = Quaternion()
                quaternion.setByAngleAxis(math.radians(angle), axis)
                operation.addOperation(RotateOperation(node, quaternion))
        if "scale" in args:
            xyz = self._vector3(args["scale"], "scale")
            if min(xyz) <= 0:
                raise ToolError("Scale values must be positive")
            operation.addOperation(ScaleOperation(node, Vector(*xyz), set_scale=True))
        operation.push()
        return self._object_result(node)

    def remove_object(self, args):
        return self._on_qt(self._remove_object, args["object_id"])

    def _remove_object(self, object_id):
        node = self._node(object_id)
        name = node.getName()
        RemoveSceneNodeOperation(node).push()
        return {"removed": object_id, "name": name}

    def arrange_models(self, unused=None):
        self._on_qt(self._app.arrangeAll)
        return {"started": True, "note": "Arrangement runs asynchronously."}

    def start_slicing(self, unused=None):
        return self._on_qt(self._start_slicing)

    def _start_slicing(self):
        backend = self._app.getBackend()
        if getattr(backend, "_slicing", False):
            return {"started": False, "status": self._slice_status(),
                    "note": "Slicing is already running"}
        backend.forceSlice()
        return {"started": True, "status": self._slice_status()}

    def slicing_status(self, unused=None):
        return self._on_qt(self._slice_status)

    def assess_risk(self, args):
        return self._on_qt(self._assess_risk, args)

    def _assess_risk(self, args):
        stack = self._stack()
        slicing = self._slice_status()
        settings = {}
        for key in (
            "layer_height", "line_width", "machine_nozzle_size", "nozzle_size",
            "wall_line_count", "infill_sparse_density", "support_enable", "support_angle",
            "adhesion_type", "material_print_temperature", "material_bed_temperature",
            "print_speed", "speed_print_layer_0", "travel_speed", "cool_fan_enabled",
            "cool_fan_speed", "machine_buildplate_type", "build_plate_surface",
        ):
            value = stack.getProperty(key, "value")
            if value is not None:
                settings[key] = value
        material_names = slicing.get("material_names") or []
        manager = self._app.getMachineManager()
        material_name = ", ".join(str(item) for item in material_names)
        if not material_name:
            active_material = manager.activeMaterialId
            for material in ContainerRegistry.getInstance().findInstanceContainers(type="material"):
                root_id = material.getMetaDataEntry("base_file", material.getId())
                if root_id == active_material or material.getId() == active_material:
                    material_name = "%s %s" % (
                        material.getMetaDataEntry("material", ""), material.getName()
                    )
                    break
        snapshot = {
            "printing_mode": stack.getProperty("printing_mode", "value"),
            "material_id": manager.activeMaterialId,
            "material_name": material_name or str(manager.activeMaterialId or ""),
            "objects": self._list_objects(),
            "settings": settings,
            "slicing": slicing,
        }
        return assess_print_risk(snapshot, args.get("observations") or {})

    def estimate_print_cost(self, args):
        return self._on_qt(self._estimate_print_cost, args)

    def _estimate_print_cost(self, args):
        return estimate_print_cost(self._slice_status(), args)

    def capture_cost_variant(self, args):
        return self._on_qt(self._capture_cost_variant, args)

    def _capture_cost_variant(self, args):
        slot = args.get("slot")
        if slot not in ("baseline", "candidate"):
            raise ToolError("slot must be baseline or candidate")
        quote = self._estimate_print_cost(args)
        state = self._get_state()
        if state["slicing"]["state"] != "done":
            raise ToolError("Slice changed while capturing the cost variant")
        tariffs = {
            "machine_hour_rate_rub": quote["machine_hour_rate_rub"],
            "material_prices_rub_per_kg": [
                item["price_rub_per_kg"] for item in quote["materials"]
            ],
            "outside_work_items": quote["outside_work_items"],
            "markup_rub": quote["markup_rub"],
            "tax_rub": quote["tax_rub"],
        }
        snapshot = {
            "slot": slot,
            "label": str(args.get("label") or slot),
            "quote": quote,
            "tariffs": tariffs,
            "machine_name": state["machine_name"],
            "printing_mode": state["printing_mode"],
            "material_id": state["material_id"],
            "quality_type": state["quality_type"],
            "scene_objects": state["objects"],
            "settings": {
                key: self._stack().getProperty(key, "value")
                for key in ("layer_height", "wall_line_count", "wall_thickness",
                            "top_layers", "bottom_layers", "infill_sparse_density",
                            "support_enable", "adhesion_type", "print_speed")
            },
        }
        self._cost_variants[slot] = copy.deepcopy(snapshot)
        return snapshot

    def compare_cost_variants(self, unused=None):
        return self._on_qt(self._compare_cost_variants)

    def _compare_cost_variants(self):
        return compare_cost_variants(self._cost_variants.get("baseline"),
                                     self._cost_variants.get("candidate"))

    def _slice_status(self):
        backend = self._app.getBackend()
        scene = self._app.getController().getScene()
        plates = self._app.getMultiBuildPlateModel()
        plate = plates.activeBuildPlate if plates else 0
        gcode = getattr(scene, "gcode_dict", {}).get(plate, [])
        if getattr(backend, "_is_disabled", False):
            state = "disabled"
        elif getattr(backend, "_slicing", False):
            state = "processing"
        elif gcode:
            state = "done"
        else:
            state = "not_started"
        status = {"state": state}
        if state == "processing":
            started_at = getattr(backend, "_slice_start_time", None)
            if started_at:
                elapsed = max(0, int(time.time() - started_at))
                status["elapsed_seconds"] = elapsed
                if elapsed >= 180:
                    status["warning"] = (
                        "Slicing is still processing after 3 minutes; "
                        "no completed G-code or time/material estimate is available"
                    )
        if state == "done":
            information = self._app.getPrintInformation()
            seconds = int(
                information.currentPrintTime.getDisplayString(DurationFormat.Format.Seconds)
            )
            status.update(
                {
                    "print_time_seconds": seconds,
                    "print_time": "%02d:%02d:%02d" %
                                  (seconds // 3600, (seconds % 3600) // 60, seconds % 60),
                    "material_lengths_m": list(information.materialLengths),
                    "material_weights_g": list(information.materialWeights),
                    "material_names": list(information.materialNames),
                }
            )
        return status

    def export_gcode(self, args):
        return self._export(args["path"], "gcode")

    def save_project(self, args):
        return self._export(args["path"], "3mf")

    def _export(self, path, kind):
        path = os.path.abspath(os.path.expanduser(path))
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise ToolError("Output directory does not exist: %s" % parent)
        if os.path.exists(path):
            raise ToolError("Output file already exists: %s" % path)
        writer, root, mode = self._on_qt(self._prepare_export, kind)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".ste-assistant-", suffix=".part", dir=parent
        )
        os.close(descriptor)
        try:
            if kind == "gcode":
                with open(temporary_path, "w", encoding="utf-8") as stream:
                    success = writer.write(stream, [root], mode)
            else:
                with open(temporary_path, "wb") as stream:
                    success = writer.write(stream, [root], mode)
            if not success or os.path.getsize(temporary_path) == 0:
                raise ToolError("Failed to export %s" % kind)
            if os.path.exists(path):
                raise ToolError("Output file already exists: %s" % path)
            os.rename(temporary_path, path)
            return {"path": path, "bytes": os.path.getsize(path), "format": kind}
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

    def _prepare_export(self, kind):
        root = self._app.getController().getScene().getRoot()
        if kind == "gcode":
            if self._slice_status()["state"] != "done":
                raise ToolError("Slice must finish before exporting G-code")
            writer = self._app.getMeshFileHandler().getWriter("GCodeWriter")
            if writer is None:
                raise ToolError("G-code writer is unavailable")
            mode = writer.OutputMode.TextMode
        elif kind == "3mf":
            writer = self._app.getWorkspaceFileHandler().getWriter("3MFWriter")
            if writer is None:
                raise ToolError("3MF writer is unavailable")
            mode = writer.OutputMode.BinaryMode
        else:
            raise ToolError("Unsupported export format: %s" % kind)
        return writer, root, mode

    def _stack(self):
        stack = self._app.getGlobalContainerStack()
        if stack is None:
            raise ToolError("No active printer profile")
        return stack

    def _setting_stack(self, scope):
        if scope == "global":
            return self._stack()
        if scope.startswith("extruder:"):
            position = scope.split(":", 1)[1]
            stack = self._stack()
            if position not in stack.extruders:
                raise ToolError("Unknown extruder position: %s" % position)
            return stack.extruders[position]
        raise ToolError("scope must be global or extruder:<position>")

    @staticmethod
    def _require_setting(key):
        if key not in ALLOWED_SETTINGS:
            raise ToolError("Setting is not in the safe AI allowlist: %s" % key)

    def _scene_nodes(self):
        root = self._app.getController().getScene().getRoot()
        return [
            node
            for node in DepthFirstIterator(root)
            if isinstance(node, SceneNode)
            and node.getMeshData() is not None
            and node.isSelectable()
        ]

    def _node(self, object_id):
        self._list_objects()
        if object_id not in self._nodes:
            raise ToolError("Unknown object_id; call list_objects again")
        return self._nodes[object_id]

    @staticmethod
    def _vector3(value, label):
        if not isinstance(value, list) or len(value) != 3:
            raise ToolError("%s must contain three numbers" % label)
        return [float(item) for item in value]

    @staticmethod
    def _object_result(node):
        position = node.getWorldPosition()
        bounds = node.getBoundingBox()
        return {
            "id": str(id(node)),
            "name": node.getName(),
            "position": [position.x, position.y, position.z],
            "dimensions_mm": [bounds.width, bounds.height, bounds.depth],
            "footprint_bbox_mm2": bounds.width * bounds.depth,
        }
