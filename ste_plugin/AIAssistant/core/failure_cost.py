"""Economic what-if scenarios for failed FDM print jobs."""

import math

from .economics import estimate_print_cost
from .errors import ToolError


def _number(value, label, minimum=0, maximum=None):
    if isinstance(value, bool):
        raise ToolError("%s must be a finite number" % label)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ToolError("%s must be a finite number" % label)
    if not math.isfinite(result) or result < minimum or (
        maximum is not None and result > maximum
    ):
        raise ToolError("%s must be between %s and %s" % (label, minimum, maximum))
    return result


def _integer(value, label, minimum=1, maximum=None):
    result = _number(value, label, minimum, maximum)
    if not result.is_integer():
        raise ToolError("%s must be a whole number" % label)
    return int(result)


def analyze_failure_cost(slicing, arguments):
    """Calculate deterministic failure scenarios and optional user-calibrated expected loss."""
    quote = estimate_print_cost(
        slicing,
        {
            "machine_hour_rate_rub": arguments.get("machine_hour_rate_rub"),
            "material_prices_rub_per_kg": arguments.get("material_prices_rub_per_kg"),
            "outside_work_items": [],
            "markup_rub": 0,
            "tax_rub": 0,
        },
    )
    quantity = _integer(arguments.get("quantity", 1), "quantity", 1, 1000000)
    parts_per_job = _integer(
        arguments.get("parts_per_job", 1), "parts_per_job", 1, 1000000
    )
    machine_count = _integer(arguments.get("machine_count", 1), "machine_count", 1, 10000)
    delay_rate = _number(arguments.get("delay_cost_rub_per_hour", 0),
                         "delay_cost_rub_per_hour")
    stages = arguments.get("failure_stages_percent", [25, 50, 100])
    if not isinstance(stages, list) or not stages:
        raise ToolError("failure_stages_percent must be a non-empty array")
    probabilities = arguments.get("failure_probabilities_percent")
    if probabilities is not None and (
        not isinstance(probabilities, list) or len(probabilities) != len(stages)
    ):
        raise ToolError("Provide one explicit probability for each failure stage")

    base_jobs = int(math.ceil(float(quantity) / parts_per_job))
    reference_seconds = quote["print_time_seconds"]
    reference_cost = quote["print_cost_rub"]
    baseline_machine_seconds = base_jobs * reference_seconds
    baseline_elapsed_seconds = int(math.ceil(float(base_jobs) / machine_count)) * reference_seconds
    baseline_cost = round(base_jobs * reference_cost, 2)
    scenarios = []
    expected_extra = 0.0
    for index, stage_value in enumerate(stages):
        stage = _number(stage_value, "failure_stages_percent[%d]" % index, 0, 100)
        fraction = stage / 100.0
        lost_materials = [round(value * fraction, 3) for value in quote["material_weights_g"]]
        failed_attempt_cost = round(reference_cost * fraction, 2)
        extra_machine_seconds = int(round(reference_seconds * fraction + reference_seconds))
        delay_seconds = int(math.ceil(float(extra_machine_seconds) / machine_count))
        delay_cost = round(delay_seconds / 3600.0 * delay_rate, 2)
        extra_cost = round(failed_attempt_cost + reference_cost + delay_cost, 2)
        scenario = {
            "failure_stage_percent": stage,
            "lost_material_weights_g": lost_materials,
            "failed_attempt_cost_rub": failed_attempt_cost,
            "replacement_job_cost_rub": reference_cost,
            "extra_machine_time_seconds": extra_machine_seconds,
            "estimated_schedule_delay_seconds": delay_seconds,
            "delay_cost_rub": delay_cost,
            "total_extra_cost_rub": extra_cost,
            "batch_cost_after_failure_rub": round(baseline_cost + extra_cost, 2),
        }
        if probabilities is not None:
            probability = _number(
                probabilities[index], "failure_probabilities_percent[%d]" % index, 0, 100
            )
            scenario["user_supplied_probability_percent"] = probability
            scenario["expected_extra_cost_rub"] = round(extra_cost * probability / 100.0, 2)
            expected_extra += extra_cost * probability / 100.0
        scenarios.append(scenario)

    result = {
        "source": "completed_live_slice",
        "reference_job": quote,
        "production": {
            "quantity": quantity,
            "parts_per_job": parts_per_job,
            "base_jobs": base_jobs,
            "machine_count": machine_count,
            "baseline_machine_time_seconds": baseline_machine_seconds,
            "baseline_elapsed_seconds": baseline_elapsed_seconds,
            "baseline_print_cost_rub": baseline_cost,
        },
        "scenarios": scenarios,
        "notes": [
            "Each scenario models one failed reference job plus one complete replacement job.",
            "Schedule delay assumes the extra work is distributed evenly across identical printers.",
            "The print-risk score is not used as a probability.",
        ],
    }
    if probabilities is not None:
        total_probability = sum(item["user_supplied_probability_percent"] for item in scenarios)
        if total_probability > 100:
            raise ToolError("Failure scenario probabilities must sum to at most 100%")
        result["user_supplied_total_failure_probability_percent"] = total_probability
        result["expected_extra_cost_rub"] = round(expected_extra, 2)
        result["expected_batch_cost_rub"] = round(baseline_cost + expected_extra, 2)
    return result
