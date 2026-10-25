import copy
import math
import os
import sys
import threading
import time

from PyQt5.QtCore import QUrl

from UM.Application import Application
from UM.Logger import Logger
from UM.Math.Quaternion import Quaternion
from UM.Math.Vector import Vector
from UM.Operations.GroupedOperation import GroupedOperation
from UM.Operations.GravityOperation import GravityOperation
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
from ..core.file_exports import (
    gcode_text_from_buffer,
    resolve_output_path,
    verify_exported_file,
    write_standard_3mf,
)
from ..core.manufacturing import (
    capture_manufacturing_scenario,
    compare_manufacturing_scenarios,
    plan_manufacturing_settings,
)
from ..core.optimization import (
    compare_optimization_variants,
    normalize_optimization_specification,
)
from ..core.placement import centered_scene_build_area, choose_adjacent_offset
from ..core.risk import assess_print_risk
from ..core.orientation import choose_orientation


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
    def __init__(self, application, executor, optimization=None):
        self._app = application
        self._executor = executor
        self._nodes = {}
        self._cost_variants = {}
        self._manufacturing_scenarios = {}
        self._optimization = optimization
        self._optimization_variants = {}
        self._snapshot_lock = threading.RLock()
        self._objects_snapshot = None
        self._slicing_snapshot = None
        self._project_objects_snapshot = None
        self._project_metadata_snapshot = None
        self._snapshot_refresh_pending = False
        self._objects_read_refresh_pending = False
        self._slicing_read_refresh_pending = False
        # Prime read-only snapshots once the application event loop is ready.
        self._executor.submit(self._initialize_read_snapshots)

    def _on_qt(self, function, *args):
        return self._executor.call(function, *args)

    def get_state(self, unused=None):
        return self._on_qt(self._get_state)

    def _get_state(self):
        stack = self._stack()
        manager = self._app.getMachineManager()
        area = self._machine_build_area()
        return {
            "machine_id": stack.getId(),
            "machine_name": stack.getName(),
            "printing_mode": stack.getProperty("printing_mode", "value"),
            "quality_type": manager.activeQualityType,
            "material_id": manager.activeMaterialId,
            "build_area": area,
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
        self._refresh_project_metadata_snapshot()
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
            self._refresh_project_metadata_snapshot()
            return {"kind": kind, "selected": manager.activeQualityType}
        if kind == "material":
            manager.setMaterialById(extruder, profile_id)
            self._refresh_project_metadata_snapshot()
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
        snapshot = self._read_snapshot("objects")
        if snapshot is not None:
            self._queue_read_snapshot_refresh("objects")
            return snapshot
        return self._on_qt(self._refresh_objects_snapshot)

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
        self._write_snapshot("objects", result)
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

    def auto_orient_object(self, args):
        return self._on_qt(self._auto_orient_object, args["object_id"])

    def _auto_orient_object(self, object_id):
        mode = self._stack().getProperty("printing_mode", "value")
        if mode != "classic":
            raise ToolError("Automatic flat-face orientation is for classic 3D mode; "
                            "5D needs an axis and geometry-specific placement.")
        node = self._node(object_id)
        mesh = node.getMeshDataTransformed()
        if mesh is None:
            raise ToolError("The object has no usable transformed mesh")
        indices = mesh.getIndices() if mesh.hasIndices() else None
        choice = choose_orientation(mesh.getVertices(), indices)
        if choice["changed"]:
            normal = choice["normal"]
            quaternion = Quaternion.rotationTo(Vector(*normal), Vector(0.0, -1.0, 0.0))
            operation = GroupedOperation()
            operation.addOperation(RotateOperation(node, quaternion))
            operation.addOperation(GravityOperation(node))
            operation.push()
        return {"object": self._object_result(node), "orientation": choice,
                "printing_mode": mode}

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
        snapshot = self._read_snapshot("slicing")
        if snapshot is not None:
            self._queue_read_snapshot_refresh("slicing")
            return snapshot
        return self._on_qt(self._refresh_slicing_snapshot)

    def _refresh_read_snapshots(self):
        self._refresh_objects_snapshot()
        self._refresh_slicing_snapshot()

    def _initialize_read_snapshots(self):
        # STE can load extensions before an active machine stack exists.  A
        # missing printer must leave snapshots empty instead of aborting the
        # whole extension registration (and hiding the team UI/menu entry).
        for name, refresher in (
                ("read", self._refresh_read_snapshots),
                ("project_objects", self._refresh_project_objects_snapshot),
                ("project_metadata", self._refresh_project_metadata_snapshot)):
            try:
                refresher()
            except Exception:
                Logger.logException(
                    "w", "Could not initialize MCP %s snapshot; will retry later", name)
        try:
            scene = self._app.getController().getScene()
            scene.sceneChanged.connect(self._scene_changed)
        except Exception:
            Logger.logException("w", "Could not subscribe MCP snapshots to scene changes")

    def _scene_changed(self, *unused):
        """Coalesce Cura scene signals into one safe UI-thread snapshot refresh."""
        with self._snapshot_lock:
            if self._snapshot_refresh_pending:
                return
            self._snapshot_refresh_pending = True
        self._app.callLater(self._refresh_after_scene_change)

    def _refresh_after_scene_change(self):
        try:
            self._refresh_objects_snapshot()
            self._refresh_project_objects_snapshot()
            self._refresh_project_metadata_snapshot()
        finally:
            with self._snapshot_lock:
                self._snapshot_refresh_pending = False

    def _queue_read_snapshot_refresh(self, kind):
        attribute = ("_objects_read_refresh_pending" if kind == "objects"
                     else "_slicing_read_refresh_pending")
        with self._snapshot_lock:
            if getattr(self, attribute):
                return
            setattr(self, attribute, True)
        self._executor.submit(self._finish_read_snapshot_refresh, kind, attribute)

    def _finish_read_snapshot_refresh(self, kind, attribute):
        try:
            if kind == "objects":
                self._refresh_objects_snapshot()
            else:
                self._refresh_slicing_snapshot()
        finally:
            with self._snapshot_lock:
                setattr(self, attribute, False)

    def _refresh_objects_snapshot(self):
        return self._list_objects()

    def _refresh_slicing_snapshot(self):
        return self._slice_status()

    def _read_snapshot(self, kind):
        with self._snapshot_lock:
            value = (self._objects_snapshot if kind == "objects"
                     else self._slicing_snapshot)
            return copy.deepcopy(value)

    def _write_snapshot(self, kind, value):
        snapshot = copy.deepcopy(value)
        with self._snapshot_lock:
            if kind == "objects":
                self._objects_snapshot = snapshot
            else:
                self._slicing_snapshot = snapshot

    def assess_risk(self, args):
        return self._on_qt(self._assess_risk, args)

    def _assess_risk(self, args):
        return assess_print_risk(self._manufacturing_snapshot(), args.get("observations") or {})

    def _manufacturing_snapshot(self):
        stack = self._stack()
        slicing = self._slice_status()
        settings = {}
        for key in (
            "layer_height", "line_width", "machine_nozzle_size", "nozzle_size",
            "wall_line_count", "wall_thickness", "top_layers", "bottom_layers",
            "infill_sparse_density", "infill_pattern", "support_enable", "support_type",
            "support_angle", "adhesion_type", "material_print_temperature",
            "material_bed_temperature", "print_speed", "speed_print_layer_0",
            "travel_speed", "cool_fan_enabled", "cool_fan_speed",
            "machine_buildplate_type", "build_plate_surface",
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
        return snapshot

    def plan_manufacturing(self, args):
        return self._on_qt(self._plan_manufacturing, args)

    def _plan_manufacturing(self, args):
        return plan_manufacturing_settings(self._manufacturing_snapshot(), args)

    def capture_manufacturing(self, args):
        return self._on_qt(self._capture_manufacturing, args)

    def _capture_manufacturing(self, args):
        result = capture_manufacturing_scenario(self._manufacturing_snapshot(), args)
        self._manufacturing_scenarios[args["scenario"]] = copy.deepcopy(result)
        return result

    def compare_manufacturing(self, unused=None):
        return self._on_qt(self._compare_manufacturing)

    def _compare_manufacturing(self):
        return compare_manufacturing_scenarios(
            self._manufacturing_scenarios.get("prototype"),
            self._manufacturing_scenarios.get("production"),
        )

    def optimization_capabilities(self, args):
        self._require_optimization()
        return self._optimization.capabilities(args.get("deep", True))

    def optimization_templates(self, unused=None):
        self._require_optimization()
        return self._optimization.templates()

    def start_optimization(self, args):
        self._require_optimization()
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_source = None
        if not self._on_qt(self._scene_nodes):
            default_source = os.path.join(
                plugin_root, "optimization_bridge", "data", "test-fixtures",
                "bracket.stl"
            )
        specification = normalize_optimization_specification(
            args["specification"], default_source
        )
        return self._optimization.start(specification)

    def optimization_status(self, args):
        self._require_optimization()
        return self._optimization.status(args["job_id"])

    def cancel_optimization(self, args):
        self._require_optimization()
        return self._optimization.cancel(args["job_id"])

    def import_optimized_variant(self, args):
        self._require_optimization()
        result = self._optimization.status(args["job_id"])
        if result.get("status") != "completed":
            raise ToolError("Optimization job is not completed")
        path = (result.get("artifacts") or {}).get("optimized_stl")
        if not path:
            raise ToolError("Optimization result has no optimized_stl artifact")
        path = os.path.abspath(os.path.expanduser(path))
        before_ids = self._on_qt(lambda: [id(node) for node in self._scene_nodes()])
        self._on_qt(self._app.readLocalFile, QUrl.fromLocalFile(path), True)
        deadline = time.time() + 12.0
        imported_node = None
        while time.time() < deadline:
            imported_node = self._on_qt(self._find_imported_node, before_ids)
            if imported_node is not None:
                break
            time.sleep(0.1)
        if imported_node is None:
            raise ToolError(
                "The optimized STL was accepted by STE Slicer but its scene object "
                "did not appear within 12 seconds"
            )
        placement = self._on_qt(
            self._place_optimized_node, imported_node, before_ids, 10.0
        )
        return {
            "accepted": True,
            "path": path,
            "job_id": args["job_id"],
            "object": placement["object"],
            "placement": placement,
            "engineering_result": result.get("result"),
        }

    def _find_imported_node(self, before_ids):
        for node in self._scene_nodes():
            if id(node) not in before_ids:
                return node
        return None

    def _place_optimized_node(self, node, before_ids, gap_mm):
        bounds = node.getBoundingBox()
        current_plate = node.callDecoration("getBuildPlateNumber")
        occupied = []
        for other in self._scene_nodes():
            if id(other) in before_ids:
                other_plate = other.callDecoration("getBuildPlateNumber")
                if other_plate == current_plate or other_plate is None or current_plate is None:
                    other_bounds = other.getBoundingBox()
                    occupied.append((other_bounds.minimum.x, other_bounds.maximum.x,
                                     other_bounds.minimum.z, other_bounds.maximum.z))
        rectangle = (bounds.minimum.x, bounds.maximum.x,
                     bounds.minimum.z, bounds.maximum.z)
        build_area = self._machine_build_area()
        adjacent = choose_adjacent_offset(
            rectangle, occupied, build_area, gap_mm
        )
        if adjacent is None:
            RemoveSceneNodeOperation(node).push()
            area_str = (
                "width=%.1f, depth=%.1f, shape=%s" % (
                    build_area["max_x"] - build_area["min_x"],
                    build_area["max_z"] - build_area["min_z"],
                    build_area.get("shape", "rectangular"),
                ) if build_area else "unknown"
            )
            raise ToolError(
                "No collision-free position for the optimized result was found on "
                "the active build plate (build plate: %s, candidate: %.1f×%.1f×%.1f mm); "
                "the imported copy was removed. Clear space or use another build plate and import again." % (
                    area_str, bounds.width, bounds.height, bounds.depth
                )
            )
        vertical_offset = -bounds.minimum.y
        TranslateOperation(
            node,
            Vector(adjacent["dx"], vertical_offset, adjacent["dz"]),
        ).push()
        mode = self._stack().getProperty("printing_mode", "value")
        effective_gap = adjacent.get("gap_mm", gap_mm)
        strategy = (
            "adjacent_without_moving_source"
            if adjacent["side"] != "free_space_grid"
            else "free_space_on_plate"
        )
        return {
            "object": self._object_result(node),
            "strategy": strategy,
            "side": adjacent["side"],
            "gap_mm": effective_gap,
            "surface_axis": "Y",
            "placed_on_surface": True,
            "orientation_changed": False,
            "has_intersection": False,
            "printing_mode": mode,
            "five_axis_orientation_requires_confirmation": mode != "classic",
            "note": (
                "STE scene coordinates use Y as the vertical axis; X and Z lie "
                "on the build surface. In 5D mode this placement prevents overlap "
                "but does not validate the rotary-axis orientation."
            ),
        }

    def _machine_build_area(self):
        stack = self._stack()
        try:
            width = float(stack.getProperty("machine_width", "value"))
            depth = float(stack.getProperty("machine_depth", "value"))
        except (TypeError, ValueError):
            return None
        if width <= 0 or depth <= 0:
            return None
        # machine_center_is_zero controls exported machine coordinates. STE's
        # visual scene and its object bounding boxes remain centered on X/Z=0.
        return centered_scene_build_area(
            width,
            depth,
            stack.getProperty("machine_shape", "value") or "rectangular",
        )

    def capture_optimization_variant(self, args):
        return self._on_qt(self._capture_optimization_variant, args)

    def _capture_optimization_variant(self, args):
        slot = args.get("slot")
        if slot not in ("baseline", "optimized"):
            raise ToolError("slot must be baseline or optimized")
        snapshot = self._manufacturing_snapshot()
        quote = estimate_print_cost(snapshot["slicing"], args)
        risk = assess_print_risk(snapshot, args.get("observations") or {})
        tariffs = {
            "machine_hour_rate_rub": quote["machine_hour_rate_rub"],
            "material_prices_rub_per_kg": [
                item["price_rub_per_kg"] for item in quote["materials"]
            ],
        }
        captured = {
            "slot": slot,
            "label": str(args.get("label") or slot),
            "quote": quote,
            "risk": risk,
            "tariffs": tariffs,
            "settings": snapshot["settings"],
            "objects": snapshot["objects"],
            "printing_mode": snapshot["printing_mode"],
        }
        self._optimization_variants[slot] = copy.deepcopy(captured)
        return captured

    def compare_optimization(self, unused=None):
        return self._on_qt(self._compare_optimization)

    def _compare_optimization(self):
        return compare_optimization_variants(
            self._optimization_variants.get("baseline"),
            self._optimization_variants.get("optimized"),
        )

    def _require_optimization(self):
        if self._optimization is None:
            raise ToolError("Optimization bridge is not configured")

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
        self._write_snapshot("slicing", status)
        return status

    def export_gcode(self, args):
        path = self._output_path(args.get("path"), ".gcode", "result")
        try:
            self._export_trace("START_GCODE_EXPORT", path)
            status = self._on_qt(self._slice_status)
            if status.get("state") != "done":
                if (args.get("start_slicing_if_needed", False)
                        and status.get("state") not in ("processing", "disabled")):
                    started = self._on_qt(self._start_slicing)
                    raise ToolError(
                        "G-code export is waiting for slicing. Slicing was started: %s. "
                        "Retry export_gcode only after get_slicing_status returns done."
                        % bool(started.get("started"))
                    )
                raise ToolError(
                    "G-code is not ready (slicing state: %s). Run start_slicing, wait "
                    "until get_slicing_status returns done, then call export_gcode again."
                    % status.get("state", "unknown")
                )
            buffer_value = self._on_qt(self._active_gcode_buffer)
            self._export_trace("GCODE_BUFFER_READY", path)
            text = gcode_text_from_buffer(buffer_value)
            if not text:
                raise ToolError("The completed slice has an empty G-code buffer")
            result = self._write_gcode_file(path, text)
            self._export_trace("GCODE_DONE", path)
            Logger.log("i", "Verified G-code export: %s (%d bytes)", path, result["bytes"])
            return result
        except ToolError as error:
            Logger.log("e", "G-code export failed for %s: %s", path, str(error))
            raise
        except Exception as error:
            Logger.logException("e", "Unexpected G-code export failure for %s", path)
            raise ToolError("G-code export failed for %s: %s" % (path, error))

    def save_project(self, args):
        path = self._output_path(
            args.get("path"), ".3mf", "project", replace_existing=True)
        try:
            self._export_trace("START_3MF_EXPORT", path)
            payload = self._read_project_3mf_payload()
            self._export_trace("WRITER_CALLED", path, "stdlib_zipfile_3mf")
            result = write_standard_3mf(
                path, payload["objects"], payload["project"])
            self._export_trace("3MF_DONE", path)
            Logger.log("i", "Verified 3MF project save: %s (%d bytes)", path, result["bytes"])
            return result
        except ToolError as error:
            Logger.log("e", "3MF project save failed for %s: %s", path, str(error))
            raise
        except Exception as error:
            Logger.logException("e", "Unexpected 3MF project save failure for %s", path)
            raise ToolError("3MF project save failed for %s: %s" % (path, error))

    def _output_path(self, path, extension, stem, replace_existing=False):
        documents = os.path.join(os.path.expanduser("~"), "Documents")
        default_directory = os.path.join(documents, "STE-Slicer-Exports")
        return resolve_output_path(path, extension, default_directory, stem,
                                   replace_existing=replace_existing)

    def _refresh_project_objects_snapshot(self):
        objects = []
        for node in self._scene_nodes():
            mesh = node.getMeshDataTransformed()
            if mesh is None:
                continue
            vertices = self._mesh_rows(mesh.getVertices(), 3, float)
            if mesh.hasIndices():
                triangles = self._mesh_rows(mesh.getIndices(), 3, int)
            else:
                triangles = [
                    [index, index + 1, index + 2]
                    for index in range(0, len(vertices) - 2, 3)
                ]
            # STE/Cura uses Y-up; 3MF uses Z-up. This right-handed mapping
            # preserves the world-space orientation stored in the scene.
            converted = [[value[0], -value[2], value[1]] for value in vertices]
            objects.append({
                "name": node.getName(),
                "vertices": converted,
                "triangles": triangles,
            })
        with self._snapshot_lock:
            self._project_objects_snapshot = copy.deepcopy(objects)
        return objects

    def _refresh_project_metadata_snapshot(self):
        stack = self._stack()
        settings = {}
        for key in sorted(ALLOWED_SETTINGS):
            value = stack.getProperty(key, "value")
            if value is not None:
                settings[key] = self._json_safe(value)
        manager = self._app.getMachineManager()
        project = {
            "schema": "slicer_mcp_project_v1",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "machine_id": stack.getId(),
            "machine_name": stack.getName(),
            "printing_mode": stack.getProperty("printing_mode", "value"),
            "quality_type": self._json_safe(manager.activeQualityType),
            "material_id": self._json_safe(manager.activeMaterialId),
            "settings": settings,
        }
        with self._snapshot_lock:
            self._project_metadata_snapshot = copy.deepcopy(project)
        return project

    def _read_project_3mf_payload(self):
        with self._snapshot_lock:
            objects = copy.deepcopy(self._project_objects_snapshot)
            project = copy.deepcopy(self._project_metadata_snapshot)
        if objects is None or project is None:
            raise ToolError(
                "3MF scene snapshot is not ready; wait for model loading to finish"
            )
        if not objects:
            raise ToolError("Cannot save a 3MF project without printable scene objects")
        return {"objects": objects, "project": project}

    @staticmethod
    def _mesh_rows(value, width, converter):
        if hasattr(value, "tolist"):
            value = value.tolist()
        rows = list(value or [])
        if rows and not isinstance(rows[0], (list, tuple)):
            rows = [rows[index:index + width]
                    for index in range(0, len(rows), width)]
        result = []
        for row in rows:
            values = list(row)
            if len(values) < width:
                raise ToolError("Mesh data contains an incomplete row")
            result.append([converter(values[index]) for index in range(width)])
        return result

    @staticmethod
    def _json_safe(value):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [SteSlicerAdapter._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): SteSlicerAdapter._json_safe(item)
                    for key, item in value.items()}
        return str(value)

    @staticmethod
    def _export_trace(marker, path, detail=None):
        message = "%s path=%s" % (marker, os.path.abspath(path))
        if detail:
            message += " detail=%s" % detail
        print(message)
        sys.stdout.flush()

    def _active_gcode_buffer(self):
        scene = self._app.getController().getScene()
        plates = self._app.getMultiBuildPlateModel()
        plate = plates.activeBuildPlate if plates else 0
        value = getattr(scene, "gcode_dict", {}).get(plate)
        return copy.deepcopy(value)

    def _write_gcode_file(self, path, text):
        path = os.path.abspath(path)
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        verification = verify_exported_file(path, "gcode")
        return {
            "success": True,
            "status": "exported",
            "verified": True,
            "path": path,
            "bytes": verification["bytes"],
            "format": "gcode",
            "source": "cura_engine_buffer",
            "commands": verification["commands"],
        }

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
