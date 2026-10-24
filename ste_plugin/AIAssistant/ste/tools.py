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
        "Switch between classic = 3D, cylindrical = 5D Spiral and cylindrical_full = 5D.",
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
        "assess_print_risk",
        "Calculate a transparent 0-100 print-risk rating from live STE settings, scene, "
        "material and completed slice facts. Optional geometry observations must be measured "
        "or visually supported; omitted facts are reported as unknown.",
        {
            "observations": {
                "type": "object",
                "properties": {
                    "first_layer_contact_area_mm2": NUMBER,
                    "longest_bridge_mm": NUMBER,
                    "max_unsupported_overhang_deg": {
                        "type": "number", "minimum": 0, "maximum": 90,
                    },
                    "enclosure": {"type": "boolean"},
                    "material_dry": {"type": "boolean"},
                    "bed_clean": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        [],
        adapter.assess_risk,
    )
    priority_properties = {
        "scenario": {"type": "string", "enum": ["prototype", "production"]},
        "quantity": {"type": "integer", "minimum": 1},
        "strength_priority": {"type": "number", "minimum": 0, "maximum": 100},
        "appearance_priority": {"type": "number", "minimum": 0, "maximum": 100},
        "speed_priority": {"type": "number", "minimum": 0, "maximum": 100},
        "cost_priority": {"type": "number", "minimum": 0, "maximum": 100},
    }
    registry.add(
        "plan_manufacturing_settings",
        "Recommend transparent starting settings for a fast low-cost prototype or final "
        "production. Does not change the slicer and does not certify strength.",
        priority_properties,
        ["scenario"],
        adapter.plan_manufacturing,
    )
    cost_properties = {
        "machine_hour_rate_rub": NUMBER,
        "material_prices_rub_per_kg": {
            "type": "array", "items": NUMBER,
            "description": "RUB per kg, one entry per extruder slot; 0 for unused slots",
        },
        "outside_work_items": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "label": STRING, "amount_rub": NUMBER,
            }, "required": ["label", "amount_rub"]},
        },
        "markup_rub": NUMBER,
        "tax_rub": NUMBER,
        "target_budget_rub": NUMBER,
    }
    cost_required = ["machine_hour_rate_rub", "material_prices_rub_per_kg"]
    registry.add(
        "estimate_print_cost",
        "Estimate print cost from a completed live slice and explicit user tariffs. "
        "Returns print cost, separately entered outside work, and customer price; "
        "does not infer rates or strength.",
        cost_properties, cost_required, adapter.estimate_print_cost,
    )
    variant_properties = dict(cost_properties)
    variant_properties.update({
        "slot": {"type": "string", "enum": ["baseline", "candidate"]},
        "label": STRING,
    })
    registry.add(
        "capture_cost_variant",
        "Capture a completed live slice, its settings and quote as baseline or candidate. "
        "Does not change or restore the project.",
        variant_properties, cost_required + ["slot"], adapter.capture_cost_variant,
        False,
    )
    registry.add(
        "compare_cost_variants",
        "Compare previously captured baseline and candidate costs and measured settings. "
        "Warn if tariffs or scene objects differ; does not apply settings.",
        {}, [], adapter.compare_cost_variants,
    )
    manufacturing_properties = dict(cost_properties)
    manufacturing_properties.update({
        "scenario": {"type": "string", "enum": ["prototype", "production"]},
        "quantity": {"type": "integer", "minimum": 1},
        "parts_per_job": {"type": "integer", "minimum": 1},
        "machine_count": {"type": "integer", "minimum": 1},
        "batch_setup_cost_rub": NUMBER,
        "per_part_extra_cost_rub": NUMBER,
        "reserve_percent": {"type": "number", "minimum": 0, "maximum": 100},
        "observations": {
            "type": "object",
            "properties": {
                "first_layer_contact_area_mm2": NUMBER,
                "longest_bridge_mm": NUMBER,
                "max_unsupported_overhang_deg": {
                    "type": "number", "minimum": 0, "maximum": 90,
                },
                "enclosure": {"type": "boolean"},
                "material_dry": {"type": "boolean"},
                "bed_clean": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    })
    registry.add(
        "capture_manufacturing_scenario",
        "Capture the completed live slice as prototype or production, including actual "
        "settings, cost, risk, batch quantity, reserve and multi-printer schedule.",
        manufacturing_properties,
        cost_required + ["scenario", "quantity", "parts_per_job"],
        adapter.capture_manufacturing,
        False,
    )
    registry.add(
        "compare_manufacturing_scenarios",
        "Compare captured prototype and production scenarios by settings, actual slice facts, "
        "cost, schedule and screening risk.",
        {}, [], adapter.compare_manufacturing,
    )
    registry.add(
        "get_optimization_capabilities",
        "Check the optional topology-optimization bridge, Mechanical installation, license "
        "launch and prepared templates. A deep check can start Mechanical and consume a license.",
        {"deep": {"type": "boolean", "default": True}},
        [], adapter.optimization_capabilities,
    )
    registry.add(
        "list_optimization_templates",
        "List prepared, allowlisted Ansys topology-optimization workflows and their required inputs.",
        {}, [], adapter.optimization_templates,
    )
    registry.add(
        "start_topology_optimization",
        "Start a prepared Ansys workflow with an explicit source model, material, loads, fixtures, "
        "preserved regions, objective and FDM manufacturing constraints.",
        {"specification": {"type": "object"}},
        ["specification"], adapter.start_optimization, False,
    )
    registry.add(
        "get_topology_optimization_status",
        "Read status, errors, engineering results and artifacts for one optimization job.",
        {"job_id": STRING}, ["job_id"], adapter.optimization_status,
    )
    registry.add(
        "cancel_topology_optimization",
        "Cancel a running optimization job and request that its Mechanical process stop.",
        {"job_id": STRING}, ["job_id"], adapter.cancel_optimization, False,
    )
    registry.add(
        "import_optimized_variant",
        "Import the optimized STL from a completed bridge job into the live STE scene as a new variant.",
        {"job_id": STRING}, ["job_id"], adapter.import_optimized_variant, False,
    )
    optimization_variant = dict(cost_properties)
    optimization_variant.update({
        "slot": {"type": "string", "enum": ["baseline", "optimized"]},
        "label": STRING,
        "observations": {
            "type": "object",
            "properties": {
                "first_layer_contact_area_mm2": NUMBER,
                "longest_bridge_mm": NUMBER,
                "max_unsupported_overhang_deg": {
                    "type": "number", "minimum": 0, "maximum": 90,
                },
                "enclosure": {"type": "boolean"},
                "material_dry": {"type": "boolean"},
                "bed_clean": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    })
    registry.add(
        "capture_optimization_variant",
        "Capture a completed baseline or optimized STE slice with settings, cost and print risk.",
        optimization_variant, cost_required + ["slot"],
        adapter.capture_optimization_variant, False,
    )
    registry.add(
        "compare_optimization_variants",
        "Compare captured original and optimized slices by time, material, cost, settings and risk. "
        "This does not certify structural strength.",
        {}, [], adapter.compare_optimization,
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
