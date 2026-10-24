"""Deterministic print-risk assessment from live slicer facts and explicit observations."""

import math

from .errors import ToolError


MATERIALS = {
    "PLA": {"base": 12, "nozzle": (185, 230), "bed": (0, 70), "speed": 90},
    "PETG": {"base": 26, "nozzle": (220, 260), "bed": (60, 90), "speed": 70},
    "ABS": {"base": 58, "nozzle": (230, 270), "bed": (85, 115), "speed": 65,
            "enclosure": True},
    "ASA": {"base": 62, "nozzle": (235, 275), "bed": (90, 120), "speed": 65,
            "enclosure": True},
    "PC": {"base": 72, "nozzle": (250, 310), "bed": (90, 120), "speed": 55,
           "enclosure": True, "dry": True},
    "PA": {"base": 68, "nozzle": (240, 300), "bed": (70, 110), "speed": 55,
           "enclosure": True, "dry": True},
    "NYLON": {"base": 68, "nozzle": (240, 300), "bed": (70, 110), "speed": 55,
              "enclosure": True, "dry": True},
    "TPU": {"base": 48, "nozzle": (205, 245), "bed": (20, 60), "speed": 35,
            "dry": True},
    "PVA": {"base": 65, "nozzle": (185, 225), "bed": (45, 65), "speed": 35,
            "dry": True},
}


def _number(value, label, optional=True):
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise ToolError("%s must be a finite number" % label)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ToolError("%s must be a finite number" % label)
    if not math.isfinite(result) or result < 0:
        raise ToolError("%s must be a non-negative finite number" % label)
    return result


def _clamp(value):
    return max(0, min(100, int(round(value))))


def _level(score):
    if score < 25:
        return "low"
    if score < 50:
        return "moderate"
    if score < 75:
        return "high"
    return "critical"


def _material_family(name):
    upper = (name or "").upper()
    for family in ("PETG", "PLA", "ASA", "ABS", "NYLON", "PA", "TPU", "PVA", "PC"):
        if family in upper:
            return "PA" if family == "NYLON" else family
    return "UNKNOWN"


def _category(name, title, score, reasons, recommendations):
    return {
        "id": name,
        "title": title,
        "score": _clamp(score),
        "level": _level(_clamp(score)),
        "reasons": reasons,
        "recommendations": recommendations,
    }


def assess_print_risk(snapshot, observations=None):
    """Return a 0..100 rating. Unknown geometry is reported, never guessed."""
    observations = observations or {}
    settings = snapshot.get("settings") or {}
    objects = snapshot.get("objects") or []
    slicing = snapshot.get("slicing") or {}
    material_name = snapshot.get("material_name") or snapshot.get("material_id") or ""
    family = _material_family(material_name)
    material = MATERIALS.get(family, {"base": 38, "speed": 60})

    contact = _number(observations.get("first_layer_contact_area_mm2"),
                      "first_layer_contact_area_mm2")
    bridge = _number(observations.get("longest_bridge_mm"), "longest_bridge_mm")
    overhang = _number(observations.get("max_unsupported_overhang_deg"),
                       "max_unsupported_overhang_deg")
    if overhang is not None and overhang > 90:
        raise ToolError("max_unsupported_overhang_deg must be between 0 and 90")
    enclosure = observations.get("enclosure")
    if enclosure not in (None, True, False):
        raise ToolError("enclosure must be true, false or omitted")
    material_dry = observations.get("material_dry")
    if material_dry not in (None, True, False):
        raise ToolError("material_dry must be true, false or omitted")
    bed_clean = observations.get("bed_clean")
    if bed_clean not in (None, True, False):
        raise ToolError("bed_clean must be true, false or omitted")

    heights = [float(item.get("dimensions_mm", [0, 0, 0])[2] or 0) for item in objects]
    footprints = [float(item.get("footprint_bbox_mm2", 0) or 0) for item in objects]
    bbox_volumes = []
    for item in objects:
        dimensions = item.get("dimensions_mm", [0, 0, 0])
        bbox_volumes.append(float(dimensions[0] or 0) * float(dimensions[1] or 0) *
                            float(dimensions[2] or 0))
    max_height = max(heights or [0])
    bbox_footprint = sum(footprints)
    bbox_volume = sum(bbox_volumes)
    effective_contact = contact if contact is not None else None
    duration = _number(slicing.get("print_time_seconds"), "print_time_seconds") or 0
    material_g = sum(_number(value, "material weight", False)
                     for value in slicing.get("material_weights_g", []) or [])
    print_speed = _number(settings.get("print_speed"), "print_speed") or 0
    first_layer_speed = _number(settings.get("speed_print_layer_0"),
                                "speed_print_layer_0") or 0
    layer_height = _number(settings.get("layer_height"), "layer_height") or 0
    nozzle = (_number(settings.get("machine_nozzle_size"), "machine_nozzle_size") or
              _number(settings.get("nozzle_size"), "nozzle_size") or 0.4)
    nozzle_temp = _number(settings.get("material_print_temperature"),
                          "material_print_temperature")
    bed_temp = _number(settings.get("material_bed_temperature"),
                       "material_bed_temperature")
    supports = bool(settings.get("support_enable"))
    adhesion = str(settings.get("adhesion_type") or "unknown").lower()
    bed_surface = str(settings.get("machine_buildplate_type") or
                      settings.get("build_plate_surface") or "unknown")

    unknowns = []
    if contact is None:
        unknowns.append("Actual first-layer contact area was not supplied; bounding-box footprint is not treated as contact area.")
    if bridge is None:
        unknowns.append("Longest unsupported bridge was not supplied.")
    if overhang is None:
        unknowns.append("Maximum unsupported overhang was not supplied.")
    if enclosure is None and material.get("enclosure"):
        unknowns.append("Enclosure state is unknown for a warp-sensitive material.")
    if material_dry is None and material.get("dry"):
        unknowns.append("Material drying state is unknown for a moisture-sensitive material.")
    if bed_surface == "unknown":
        unknowns.append("Build-plate surface type is unavailable.")
    if slicing.get("state") != "done":
        unknowns.append("Slice is not complete; duration and material-use risks are provisional.")

    adhesion_score = 12 + material.get("base", 38) * 0.38
    adhesion_reasons = []
    adhesion_advice = []
    if effective_contact is not None:
        if effective_contact < 100:
            adhesion_score += 35
            adhesion_reasons.append("Very small measured first-layer contact area: %.0f mm²." % effective_contact)
        elif effective_contact < 400:
            adhesion_score += 20
            adhesion_reasons.append("Small measured first-layer contact area: %.0f mm²." % effective_contact)
    elif bbox_footprint and max_height / max(math.sqrt(bbox_footprint), 1) > 2:
        adhesion_score += 14
        adhesion_reasons.append("The scene is tall relative to its bounding-box footprint.")
    if max_height > 180:
        adhesion_score += 12
        adhesion_reasons.append("Tall model increases leverage on the first layer: %.0f mm." % max_height)
    if adhesion in ("none", "skirt", "unknown"):
        adhesion_score += 10
        adhesion_reasons.append("No brim or raft is confirmed in the active adhesion setting.")
        adhesion_advice.append("Consider a brim after checking dimensional requirements.")
    if bed_clean is False:
        adhesion_score += 25
        adhesion_reasons.append("The user reported that the build plate is not clean.")
        adhesion_advice.append("Clean and prepare the build surface before printing.")
    if duration > 8 * 3600:
        adhesion_score += 10
        adhesion_reasons.append("Long thermal exposure increases cumulative warping risk.")
    if first_layer_speed > 35:
        adhesion_score += min(18, (first_layer_speed - 35) * 0.7)
        adhesion_reasons.append("First-layer speed is relatively high: %.0f mm/s." % first_layer_speed)
        adhesion_advice.append("Reduce first-layer speed if adhesion is not already validated.")
    if bbox_volume > 8000000:
        adhesion_score += 10
        adhesion_reasons.append(
            "Large scene envelope increases shrinkage exposure: %.1f L bounding-box volume." %
            (bbox_volume / 1000000.0)
        )
    if family in ("ABS", "ASA", "PC", "PA"):
        adhesion_advice.append("Verify bed preparation and stable ambient temperature for %s." % family)

    geometry_score = 8
    geometry_reasons = []
    geometry_advice = []
    if bridge is not None:
        geometry_score += min(38, bridge * 1.25)
        if bridge >= 10:
            geometry_reasons.append("Unsupported bridge span is %.1f mm." % bridge)
            geometry_advice.append("Reduce the span, reorient the part or enable suitable supports.")
    if overhang is not None and overhang > 45:
        geometry_score += min(38, (overhang - 45) * 1.5)
        geometry_reasons.append("Unsupported overhang reaches %.1f°." % overhang)
    if (bridge is not None and bridge >= 10 or overhang is not None and overhang > 50) and not supports:
        geometry_score += 18
        geometry_reasons.append("Risky unsupported geometry is present while supports are disabled.")
        geometry_advice.append("Enable supports or confirm that the selected 5D orientation makes them unnecessary.")
    if len(objects) > 1:
        geometry_score += min(15, (len(objects) - 1) * 4)
        geometry_reasons.append("Multiple objects increase collision and partial-failure exposure: %d." % len(objects))

    thermal_score = material.get("base", 38)
    thermal_reasons = ["Material family %s has baseline process sensitivity %d/100." %
                       (family, material.get("base", 38))]
    thermal_advice = []
    for key, actual, label in (("nozzle", nozzle_temp, "nozzle"), ("bed", bed_temp, "bed")):
        expected = material.get(key)
        if expected and actual is not None and not expected[0] <= actual <= expected[1]:
            thermal_score += 28
            thermal_reasons.append("%s temperature %.0f°C is outside the generic %s range %d–%d°C." %
                                   (label.capitalize(), actual, family, expected[0], expected[1]))
            thermal_advice.append("Use the material manufacturer's temperature range; generic ranges are only a warning threshold.")
    if material.get("enclosure"):
        if enclosure is False:
            thermal_score += 25
            thermal_reasons.append("The material is warp-sensitive and the user reported no enclosure.")
        elif enclosure is None:
            thermal_score += 10
        thermal_advice.append("Confirm enclosure and chamber conditions for %s." % family)
    if material.get("dry"):
        if material_dry is False:
            thermal_score += 28
            thermal_reasons.append("The user reported that the moisture-sensitive material is not dry.")
        elif material_dry is None:
            thermal_score += 10
        thermal_advice.append("Dry the material according to its datasheet and keep it dry during printing.")
    if "CF" in material_name.upper() or "GF" in material_name.upper() or "CARBON" in material_name.upper():
        thermal_score += 10
        thermal_reasons.append("Filled material can increase nozzle wear and clog sensitivity.")
        thermal_advice.append("Confirm a wear-resistant nozzle and the material's minimum nozzle diameter.")
        if nozzle < 0.5:
            thermal_score += 20
            thermal_reasons.append("Filled material is paired with a nozzle smaller than 0.5 mm.")
    if family == "PETG" and ("smooth" in bed_surface.lower() or "glass" in bed_surface.lower()):
        thermal_score += 6
        thermal_reasons.append("PETG can bond aggressively to some smooth or glass surfaces.")
        thermal_advice.append(
            "Confirm the surface manufacturer's PETG procedure and use a release layer if required."
        )

    quality_score = 10
    quality_reasons = []
    quality_advice = []
    speed_limit = material.get("speed", 60)
    if print_speed > speed_limit:
        quality_score += min(35, (print_speed - speed_limit) * 0.65)
        quality_reasons.append("Print speed %.0f mm/s exceeds the conservative %s threshold %.0f mm/s." %
                               (print_speed, family, speed_limit))
        quality_advice.append("Reduce speed or verify a high-flow profile validated for this material.")
    if nozzle and layer_height / nozzle > 0.7:
        quality_score += 28
        quality_reasons.append("Layer height is %.0f%% of nozzle diameter." % (100 * layer_height / nozzle))
        quality_advice.append("Use a layer height no greater than about 70% of nozzle diameter unless validated.")
    if bridge is not None and bridge >= 10:
        quality_score += min(20, bridge * 0.5)
        quality_reasons.append("Bridges can sag and leave visible underside defects.")
    if overhang is not None and overhang > 45:
        quality_score += min(20, (overhang - 45) * 0.8)
        quality_reasons.append("Steep unsupported surfaces can show roughness or curling.")

    duration_score = 5
    duration_reasons = []
    duration_advice = []
    if duration:
        hours = duration / 3600.0
        duration_score += min(70, hours * 4.5)
        duration_reasons.append("Completed slice duration is %.1f hours." % hours)
    if material_g > 250:
        duration_score += min(20, (material_g - 250) / 25.0)
        duration_reasons.append("Material use is %.0f g, increasing loss if the job fails." % material_g)
    if duration > 8 * 3600:
        duration_advice.append("Check filament supply, power stability and first layers before leaving the job unattended.")

    categories = [
        _category("adhesion", "Отлипание и деформация у стола", adhesion_score,
                  adhesion_reasons, adhesion_advice),
        _category("geometry", "Мосты, нависания и геометрия", geometry_score,
                  geometry_reasons, geometry_advice),
        _category("material", "Материал и температурный режим", thermal_score,
                  thermal_reasons, thermal_advice),
        _category("visual_quality", "Визуальные дефекты", quality_score,
                  quality_reasons, quality_advice),
        _category("job_failure", "Срыв длительной печати", duration_score,
                  duration_reasons, duration_advice),
    ]
    scores = {item["id"]: item["score"] for item in categories}
    weighted = (scores["adhesion"] * 0.27 + scores["geometry"] * 0.23 +
                scores["material"] * 0.22 + scores["visual_quality"] * 0.16 +
                scores["job_failure"] * 0.12)
    overall = _clamp(weighted * 0.8 + max(scores.values()) * 0.2)
    known = 6 - min(6, len(unknowns))
    return {
        "overall_score": overall,
        "overall_level": _level(overall),
        "summary": "Risk %d/100 (%s); this is a screening estimate, not a failure probability." %
                   (overall, _level(overall)),
        "material_family": family,
        "categories": categories,
        "unknowns": unknowns,
        "confidence": _clamp(35 + known * 10),
        "facts": {
            "object_count": len(objects),
            "max_height_mm": round(max_height, 2),
            "bounding_box_footprint_mm2": round(bbox_footprint, 2),
            "bounding_box_volume_mm3": round(bbox_volume, 2),
            "print_time_seconds": int(duration),
            "material_weight_g": round(material_g, 3),
            "printing_mode": snapshot.get("printing_mode"),
            "nozzle_diameter_mm": nozzle,
            "layer_height_mm": layer_height,
            "print_speed_mm_s": print_speed,
            "first_layer_speed_mm_s": first_layer_speed,
            "bed_surface": bed_surface,
        },
        "disclaimer": "Scores rank process risk from available evidence; they are not calibrated probabilities and do not guarantee print success.",
    }
