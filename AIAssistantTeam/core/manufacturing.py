"""Planning and comparison for prototype and production printing scenarios."""

import math

from .economics import estimate_print_cost
from .errors import ToolError
from .risk import assess_print_risk


def _number(value, label, minimum=0, maximum=None, integer=False):
    if isinstance(value, bool):
        raise ToolError("%s must be a number" % label)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ToolError("%s must be a number" % label)
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise ToolError("%s must be between %s and %s" % (label, minimum, maximum))
    if integer and not result.is_integer():
        raise ToolError("%s must be a whole number" % label)
    return int(result) if integer else result


def _setting(snapshot, key, default=None):
    return (snapshot.get("settings") or {}).get(key, default)


def plan_manufacturing_settings(snapshot, arguments):
    """Suggest conservative starting settings without modifying the slicer."""
    scenario = arguments.get("scenario")
    if scenario not in ("prototype", "production"):
        raise ToolError("scenario must be prototype or production")
    quantity = _number(arguments.get("quantity", 1), "quantity", 1, 1000000, True)
    strength = _number(arguments.get("strength_priority", 50), "strength_priority", 0, 100)
    appearance = _number(arguments.get("appearance_priority", 50), "appearance_priority", 0, 100)
    speed = _number(arguments.get("speed_priority", 50), "speed_priority", 0, 100)
    cost = _number(arguments.get("cost_priority", 50), "cost_priority", 0, 100)
    nozzle = _number(_setting(snapshot, "machine_nozzle_size",
                             _setting(snapshot, "nozzle_size", 0.4)),
                     "nozzle diameter", 0.1, 2.0)
    current_speed = _number(_setting(snapshot, "print_speed", 50),
                            "current print speed", 1, 500)

    if scenario == "prototype":
        layer_ratio = 0.70 if speed >= appearance else 0.55
        walls = 2
        infill = 8 if strength < 60 else 15
        speed_target = min(100, max(45, current_speed * 1.15))
        purpose = "Fast, low-cost physical check of shape, scale, fit and basic assembly."
    else:
        layer_ratio = 0.40 if appearance >= 70 else 0.50
        walls = 4 if strength >= 70 else 3
        infill = 30 if strength >= 80 else (20 if strength >= 50 else 15)
        speed_target = min(current_speed, 60 if appearance >= 70 else 75)
        if cost >= 70 and strength < 50 and appearance < 70:
            layer_ratio = 0.60
            walls = 2
            infill = 12
            speed_target = min(85, max(current_speed, 60))
        purpose = "Repeatable final-part starting point; validate on representative samples."

    layer_height = round(max(0.05, min(0.4, nozzle * layer_ratio)), 2)
    targets = {
        "layer_height": layer_height,
        "wall_line_count": walls,
        "infill_sparse_density": infill,
        "print_speed": round(speed_target, 1),
    }
    if scenario == "prototype" and appearance >= 80:
        targets["layer_height"] = round(max(0.05, nozzle * 0.45), 2)
    if scenario == "production" and speed >= 80 and appearance < 70:
        targets["layer_height"] = round(max(0.05, min(0.4, nozzle * 0.60)), 2)

    reasons = [
        "Layer height is derived from the active %.2f mm nozzle." % nozzle,
        "Walls and infill are starting points, not validated strength values.",
    ]
    if quantity > 1:
        reasons.append(
            "For %d parts, validate one representative article before committing the batch." % quantity
        )
    if strength >= 70:
        reasons.append(
            "Strength priority is high; orientation, layer bonding and material choice can matter more than infill."
        )
    if appearance >= 70:
        reasons.append("Appearance priority is high; a smaller layer height and moderate speed are preferred.")

    return {
        "scenario": scenario,
        "quantity": quantity,
        "purpose": purpose,
        "priorities": {
            "strength": strength,
            "appearance": appearance,
            "speed": speed,
            "cost": cost,
        },
        "current_settings": {
            key: _setting(snapshot, key)
            for key in ("layer_height", "wall_line_count", "infill_sparse_density", "print_speed")
        },
        "recommended_settings": targets,
        "reasons": reasons,
        "validation": [
            "Apply only settings available in the active STE profile and slice the scenario.",
            "Run assess_print_risk after applying settings and again after slicing.",
            "For production, print and inspect a representative sample before the full run.",
        ],
        "disclaimer": (
            "Recommendations optimize relative priorities; they do not certify mechanical strength, "
            "dimensional accuracy or production yield."
        ),
    }


def capture_manufacturing_scenario(snapshot, arguments):
    """Capture actual slice economics and risk, scaled to an explicit production run."""
    scenario = arguments.get("scenario")
    if scenario not in ("prototype", "production"):
        raise ToolError("scenario must be prototype or production")
    quantity = _number(arguments.get("quantity", 1), "quantity", 1, 1000000, True)
    parts_per_job = _number(arguments.get("parts_per_job", 1), "parts_per_job", 1, 1000000, True)
    machine_count = _number(arguments.get("machine_count", 1), "machine_count", 1, 10000, True)
    setup_cost = _number(arguments.get("batch_setup_cost_rub", 0), "batch_setup_cost_rub")
    unit_extra = _number(arguments.get("per_part_extra_cost_rub", 0),
                         "per_part_extra_cost_rub")
    reserve_percent = _number(arguments.get("reserve_percent", 0), "reserve_percent", 0, 100)

    quote_args = {
        "machine_hour_rate_rub": arguments.get("machine_hour_rate_rub"),
        "material_prices_rub_per_kg": arguments.get("material_prices_rub_per_kg"),
        "outside_work_items": [],
        "markup_rub": 0,
        "tax_rub": 0,
    }
    quote = estimate_print_cost(snapshot.get("slicing") or {}, quote_args)
    risk = assess_print_risk(snapshot, arguments.get("observations") or {})
    jobs = int(math.ceil(float(quantity) / parts_per_job))
    reserve_parts = int(math.ceil(quantity * reserve_percent / 100.0))
    planned_parts = quantity + reserve_parts
    planned_jobs = int(math.ceil(float(planned_parts) / parts_per_job))
    seconds_per_job = quote["print_time_seconds"]
    total_machine_seconds = planned_jobs * seconds_per_job
    waves = int(math.ceil(float(planned_jobs) / machine_count))
    elapsed_seconds = waves * seconds_per_job
    print_cost = round(planned_jobs * quote["print_cost_rub"], 2)
    extra_cost = round(quantity * unit_extra, 2)
    batch_cost = round(print_cost + setup_cost + extra_cost, 2)
    total_material_weights = [
        round(item["weight_g"] * planned_jobs, 3) for item in quote["materials"]
    ]

    return {
        "scenario": scenario,
        "quantity_requested": quantity,
        "parts_per_job": parts_per_job,
        "base_jobs": jobs,
        "reserve_percent": reserve_percent,
        "reserve_parts": reserve_parts,
        "planned_jobs": planned_jobs,
        "planned_capacity_parts": planned_jobs * parts_per_job,
        "unused_capacity_parts": planned_jobs * parts_per_job - planned_parts,
        "machine_count": machine_count,
        "unit_slice": quote,
        "risk": risk,
        "settings": dict(snapshot.get("settings") or {}),
        "production": {
            "total_machine_time_seconds": total_machine_seconds,
            "estimated_elapsed_seconds": elapsed_seconds,
            "total_material_weights_g": total_material_weights,
            "scheduling_assumption": (
                "Identical jobs, identical machines, no changeover time, and each job is charged "
                "as the completed reference slice including the final partial job."
            ),
        },
        "cost": {
            "reference_job_cost_rub": quote["print_cost_rub"],
            "all_print_jobs_cost_rub": print_cost,
            "batch_setup_cost_rub": round(setup_cost, 2),
            "per_part_extra_cost_rub": round(unit_extra, 2),
            "all_parts_extra_cost_rub": extra_cost,
            "batch_cost_rub": batch_cost,
            "cost_per_requested_part_rub": round(batch_cost / quantity, 2),
        },
        "notes": [
            "Risk is the risk of the reference print job; it is not multiplied into a failure probability.",
            "Reserve is user-selected planning capacity, not a predicted scrap rate.",
            "The last partial job is conservatively costed like the completed reference slice.",
        ],
    }


def compare_manufacturing_scenarios(prototype, production):
    if not prototype or not production:
        raise ToolError("Capture both prototype and production scenarios first")
    proto_cost = prototype["cost"]["cost_per_requested_part_rub"]
    prod_cost = production["cost"]["cost_per_requested_part_rub"]
    proto_time = prototype["unit_slice"]["print_time_seconds"]
    prod_time = production["unit_slice"]["print_time_seconds"]
    return {
        "prototype": prototype,
        "production": production,
        "comparison": {
            "reference_job_time_change_seconds": prod_time - proto_time,
            "reference_job_material_change_g": round(
                sum(production["unit_slice"]["material_weights_g"])
                - sum(prototype["unit_slice"]["material_weights_g"]), 3
            ),
            "cost_per_requested_part_change_rub": round(prod_cost - proto_cost, 2),
            "risk_score_change": (
                production["risk"]["overall_score"] - prototype["risk"]["overall_score"]
            ),
        },
        "warnings": [
            "The comparison is meaningful only if both captures represent the intended geometry and tariffs.",
            "Lower screening risk does not prove higher yield or mechanical strength.",
        ],
    }
