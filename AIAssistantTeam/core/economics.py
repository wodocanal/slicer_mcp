"""Transparent estimate from a completed STE Slicer result and user tariffs.

All monetary inputs are RUB amounts supplied by the user. No material price,
machine rate, tax rule or strength figure is inferred from a slicer profile.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .errors import ToolError


KOPECK = Decimal("0.01")


def _decimal(value, label, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ToolError("%s must be a non-negative number" % label)
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise ToolError("%s must be a non-negative number" % label)
    if not number.is_finite() or number < 0:
        raise ToolError("%s must be a finite non-negative number" % label)
    return number


def _rub(number):
    return float(number.quantize(KOPECK, rounding=ROUND_HALF_UP))


def estimate_print_cost(slicing, arguments):
    """Return a quote only when live slicing has actually completed.

    Prices are indexed by extruder slot, matching material_weights_g from STE.
    A null price is allowed only for a slot with zero consumed material.
    """
    if not isinstance(slicing, dict) or slicing.get("state") != "done":
        raise ToolError("A completed slice is required before estimating cost")
    seconds = _decimal(slicing.get("print_time_seconds"), "print_time_seconds")
    if seconds != seconds.to_integral_value():
        raise ToolError("print_time_seconds must be a whole number")
    weights = slicing.get("material_weights_g")
    prices = arguments.get("material_prices_rub_per_kg")
    if not isinstance(weights, list) or not isinstance(prices, list):
        raise ToolError("Material weights and prices must be arrays by extruder slot")
    if len(weights) != len(prices):
        raise ToolError("One price is required for each extruder slot")
    names = slicing.get("material_names") or []
    if not isinstance(names, list):
        names = []

    rate = _decimal(arguments.get("machine_hour_rate_rub"), "machine_hour_rate_rub")
    materials = []
    material_total = Decimal(0)
    for index, raw_weight in enumerate(weights):
        weight = _decimal(raw_weight, "material_weights_g[%d]" % index)
        price = _decimal(prices[index], "material_prices_rub_per_kg[%d]" % index,
                         optional=weight == 0)
        if price is None:
            price = Decimal(0)
        cost = (weight / Decimal(1000) * price).quantize(KOPECK, rounding=ROUND_HALF_UP)
        material_total += cost
        materials.append({
            "extruder": index,
            "name": str(names[index]) if index < len(names) else "Extruder %d" % (index + 1),
            "weight_g": float(weight),
            "price_rub_per_kg": float(price),
            "cost_rub": float(cost),
        })

    machine_cost = (seconds / Decimal(3600) * rate).quantize(
        KOPECK, rounding=ROUND_HALF_UP)
    print_cost = material_total + machine_cost

    items = arguments.get("outside_work_items", [])
    if not isinstance(items, list):
        raise ToolError("outside_work_items must be an array")
    outside = []
    outside_total = Decimal(0)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ToolError("outside_work_items[%d] must be an object" % index)
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ToolError("Each outside work item needs a label")
        amount = _decimal(item.get("amount_rub"), "outside_work_items[%d].amount_rub" % index)
        amount = amount.quantize(KOPECK, rounding=ROUND_HALF_UP)
        outside_total += amount
        outside.append({"label": label.strip(), "amount_rub": float(amount)})

    markup = _decimal(arguments.get("markup_rub", 0), "markup_rub")
    tax = _decimal(arguments.get("tax_rub", 0), "tax_rub")
    markup = markup.quantize(KOPECK, rounding=ROUND_HALF_UP)
    tax = tax.quantize(KOPECK, rounding=ROUND_HALF_UP)
    customer_price = print_cost + outside_total + markup + tax
    budget = _decimal(arguments.get("target_budget_rub"), "target_budget_rub", optional=True)

    result = {
        "source": "completed_live_slice",
        "print_time_seconds": int(seconds),
        "material_weights_g": [item["weight_g"] for item in materials],
        "machine_hour_rate_rub": float(rate),
        "materials": materials,
        "material_cost_rub": float(material_total),
        "machine_cost_rub": float(machine_cost),
        "print_cost_rub": float(print_cost),
        "outside_work_items": outside,
        "outside_work_cost_rub": float(outside_total),
        "markup_rub": float(markup),
        "tax_rub": float(tax),
        "customer_price_rub": float(customer_price),
        "note": "Estimate from slicer time and mass; not a strength or printability guarantee",
    }
    if budget is not None:
        result["target_budget_rub"] = _rub(budget)
        result["budget_met"] = customer_price <= budget
        result["budget_difference_rub"] = _rub(customer_price - budget)
    return result


def compare_cost_variants(baseline, candidate):
    """Compare two captured quotes without changing the live project."""
    if not baseline or not candidate:
        raise ToolError("Capture both baseline and candidate after completed slices")
    before = baseline["quote"]
    after = candidate["quote"]
    keys = ("print_time_seconds", "material_cost_rub", "machine_cost_rub",
            "print_cost_rub", "outside_work_cost_rub", "customer_price_rub")
    changes = {}
    for key in keys:
        changes[key] = round(after[key] - before[key], 2 if key != "print_time_seconds" else 0)
    return {
        "baseline": baseline,
        "candidate": candidate,
        "changes": changes,
        "same_tariffs": baseline["tariffs"] == candidate["tariffs"],
        "same_scene_objects": baseline["scene_objects"] == candidate["scene_objects"],
        "note": "Sequential snapshots; the plugin has not restored or applied print settings",
    }
