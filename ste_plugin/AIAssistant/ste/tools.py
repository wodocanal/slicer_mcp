"""Tool schemas shared by Chat Completions and the MCP JSON-RPC endpoint."""

STRING = {"type": "string"}
NUMBER = {"type": "number"}


def build_registry(registry, adapter):
    registry.add(
        "get_slicer_state",
        "Read active machine, print mode, profiles, objects and slice state.",
        {},
        [],
        adapter.get_state,
    )
    registry.add(
        "get_setting",
        "Read one supported setting from the global or extruder stack.",
        {"key": STRING, "scope": {"type": "string", "default": "global"}},
        ["key"],
        adapter.get_setting,
    )
    registry.add(
        "set_setting",
        "Change one allowlisted setting and return its effective value.",
        {"key": STRING, "value": {}, "scope": {"type": "string", "default": "global"}},
        ["key", "value"],
        adapter.set_setting,
        False,
    )
    registry.add(
        "set_print_mode",
        "Switch between 3D classic, 5D Spiral and 5D Spiral Full.",
        {"mode": {"type": "string", "enum": ["classic", "cylindrical", "cylindrical_full"]}},
        ["mode"],
        adapter.set_print_mode,
        False,
    )
    registry.add(
        "list_profiles",
        "List quality profiles or materials available in STE Slicer.",
        {"kind": {"type": "string", "enum": ["quality", "material"]}},
        ["kind"],
        adapter.list_profiles,
    )
    registry.add(
        "select_profile",
        "Select a quality profile or material by an ID returned by list_profiles.",
        {
            "kind": {"type": "string", "enum": ["quality", "material"]},
            "id": STRING,
            "extruder": {"type": "integer", "minimum": 0, "default": 0},
        },
        ["kind", "id"],
        adapter.select_profile,
        False,
    )
    registry.add(
        "load_model",
        "Load an STL, OBJ or 3MF file into the live scene.",
        {"path": STRING},
        ["path"],
        adapter.load_model,
        False,
    )
    registry.add(
        "list_objects",
        "List scene objects with IDs, dimensions, positions and scales.",
        {},
        [],
        adapter.list_objects,
    )
    registry.add(
        "transform_object",
        "Set object position, add XYZ rotations in degrees, or set scale.",
        {
            "object_id": STRING,
            "position": {"type": "array", "items": NUMBER, "minItems": 3, "maxItems": 3},
            "rotation_deg": {"type": "array", "items": NUMBER, "minItems": 3, "maxItems": 3},
            "scale": {"type": "array", "items": NUMBER, "minItems": 3, "maxItems": 3},
        },
        ["object_id"],
        adapter.transform_object,
        False,
    )
    registry.add(
        "arrange_models",
        "Start automatic placement of all models on the active build plate.",
        {},
        [],
        adapter.arrange_models,
        False,
    )
    registry.add(
        "remove_object",
        "Remove one scene object; the action is undoable in STE Slicer.",
        {"object_id": STRING},
        ["object_id"],
        adapter.remove_object,
        False,
    )
    registry.add(
        "start_slicing",
        "Start slicing the current scene using effective live settings.",
        {},
        [],
        adapter.start_slicing,
        False,
    )
    registry.add(
        "get_slicing_status",
        "Read slicing state and final time/material estimates.",
        {},
        [],
        adapter.slicing_status,
    )
    registry.add(
        "export_gcode",
        "Export finished G-code to an explicit local path.",
        {"path": STRING},
        ["path"],
        adapter.export_gcode,
        False,
    )
    registry.add(
        "save_project_3mf",
        "Save the configured scene and profiles as a STE 3MF project.",
        {"path": STRING},
        ["path"],
        adapter.save_project,
        False,
    )
    return registry
