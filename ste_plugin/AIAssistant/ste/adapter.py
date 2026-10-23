import math
import os

from PyQt5.QtCore import QUrl

from UM.Application import Application
from UM.Backend.Backend import BackendState
from UM.Logger import Logger
from UM.Math.Quaternion import Quaternion
from UM.Math.Vector import Vector
from UM.Operations.GroupedOperation import GroupedOperation
from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
from UM.Operations.RotateOperation import RotateOperation
from UM.Operations.ScaleOperation import ScaleOperation
from UM.Operations.TranslateOperation import TranslateOperation
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.SceneNode import SceneNode
from UM.Settings.ContainerRegistry import ContainerRegistry

from steslicer.Settings.ExtruderManager import ExtruderManager

from ..core.errors import ToolError


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


class SteSlicerAdapter(object):
    def __init__(self, application, executor):
        self._app = application
        self._executor = executor
        self._nodes = {}

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
        return {
            "key": key,
            "scope": scope,
            "value": stack.getProperty(key, "value"),
            "enabled": bool(stack.getProperty(key, "enabled")),
        }

    def set_setting(self, args):
        return self._on_qt(
            self._set_setting, args["key"], args["value"], args.get("scope", "global")
        )

    def _set_setting(self, key, value, scope):
        self._require_setting(key)
        stack = self._setting_stack(scope)
        container = stack.userChanges
        container.setProperty(key, "value", value)
        actual = stack.getProperty(key, "value")
        if str(actual) != str(value) and not (isinstance(value, bool) and bool(actual) == value):
            Logger.log(
                "w", "AI Assistant requested %s=%s; effective value is %s", key, value, actual
            )
        return {"key": key, "scope": scope, "requested": value, "effective": actual}

    def set_print_mode(self, args):
        mode = args["mode"]
        if mode not in ("classic", "cylindrical", "cylindrical_full"):
            raise ToolError("mode must be classic, cylindrical or cylindrical_full")
        return self._on_qt(self._set_setting, "printing_mode", mode, "global")

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
        backend.enableTimer()
        backend.tickle()
        return {"started": True, "status": self._slice_status()}

    def slicing_status(self, unused=None):
        return self._on_qt(self._slice_status)

    def _slice_status(self):
        backend = self._app.getBackend()
        state = backend.getState()
        names = {
            BackendState.NotStarted: "not_started",
            BackendState.Processing: "processing",
            BackendState.Done: "done",
            BackendState.Error: "error",
            BackendState.Disabled: "disabled",
        }
        status = {"state": names.get(state, str(state))}
        if state == BackendState.Done:
            information = self._app.getPrintInformation()
            status.update(
                {
                    "print_time": str(information.currentPrintTime),
                    "material_lengths_m": list(information.materialLengths),
                    "material_weights_g": list(information.materialWeights),
                    "material_names": list(information.materialNames),
                }
            )
        return status

    def export_gcode(self, args):
        return self._on_qt(self._export, args["path"], "gcode")

    def save_project(self, args):
        return self._on_qt(self._export, args["path"], "3mf")

    def _export(self, path, kind):
        path = os.path.abspath(os.path.expanduser(path))
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise ToolError("Output directory does not exist: %s" % parent)
        root = self._app.getController().getScene().getRoot()
        if kind == "gcode":
            if self._slice_status()["state"] != "done":
                raise ToolError("Slice must finish before exporting G-code")
            writer = self._app.getMeshFileHandler().getWriter("GCodeWriter")
            mode = writer.OutputMode.TextMode
            stream_mode = "w"
        else:
            writer = self._app.getWorkspaceFileHandler().getWriter("3MFWriter")
            mode = writer.OutputMode.BinaryMode
            stream_mode = "wb"
        if writer is None:
            raise ToolError("%s writer is unavailable" % kind.upper())
        with open(path, stream_mode) as stream:
            success = writer.write(stream, [root], mode)
        if not success or not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise ToolError("Failed to export %s" % kind)
        return {"path": path, "bytes": os.path.getsize(path), "format": kind}

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
        }
