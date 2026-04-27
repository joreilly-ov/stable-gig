"""Vertical configuration — swap domain labels, categories, and AI prompts
based on the VERTICAL environment variable.

Supported verticals:
  home_repair    (default) — home-repair tradesmen marketplace
  vehicle_damage           — vehicle damage assessment for garages / panel beaters
"""

from app.config import settings

# ---------------------------------------------------------------------------
# home_repair
# ---------------------------------------------------------------------------
_HOME_REPAIR: dict = {
    "app_title":       "Home Repair Analyser",
    "owner_label":     "homeowner",
    "provider_label":  "contractor",
    "providers_label": "contractors",
    "job_label":       "repair job",

    # Categories recognised by POST /analyse/photos
    "photo_categories": frozenset({
        "plumbing", "electrical", "structural", "damp", "roofing", "general",
    }),

    # Activities recognised by POST /jobs  (superset of photo categories)
    "job_activities": frozenset({
        "plumbing", "electrical", "structural", "damp", "roofing",
        "carpentry", "painting", "tiling", "flooring", "heating_hvac",
        "glazing", "landscaping", "general",
    }),

    # Frontend display list — order matters
    "categories_display": [
        {"value": "plumbing",   "label": "Plumbing",   "icon": "🔧"},
        {"value": "electrical", "label": "Electrical", "icon": "⚡"},
        {"value": "structural", "label": "Structural", "icon": "🏗️"},
        {"value": "damp",       "label": "Damp",       "icon": "💧"},
        {"value": "roofing",    "label": "Roofing",    "icon": "🏠"},
        {"value": "general",    "label": "General",    "icon": "🛠️"},
    ],

    "system_intro": (
        "You are an expert multi-trade diagnostic engineer with 30 years of hands-on experience "
        "in plumbing, electrical, structural, damp, roofing, and general home repair.\n\n"
        "You will be shown between 1 and 5 photographs submitted by a homeowner, each taken from a "
        "different perspective to enable Multi-Perspective Triangulation. Analyse every image in the "
        "context of the others to produce a single, confident diagnosis.\n\n"
        "The customer has provided this description:\n"
        '"{description}"{category_hint}\n'
    ),

    "image_roles": [
        (
            "Wide Shot",
            "Identify the room/area and describe the general location of the problem within it. "
            "Note the room type, approximate scale, and any relevant surrounding context.",
        ),
        (
            "Close-up",
            "Identify the specific component, material, or area of damage in precise detail. "
            "Describe the exact nature of the fault — cracks, leaks, burns, rust, mould, rot, etc.",
        ),
        (
            "Scale / Context",
            "Look for brand names, model or serial numbers, pipe diameters, cable gauges, "
            "or any measurement references that help identify the exact part or specification needed.",
        ),
        (
            "Supplemental A",
            "Use this additional angle to resolve ambiguity from the first three images. "
            "Flag any new evidence or contradictions that change the diagnosis.",
        ),
        (
            "Supplemental B",
            "Final supporting view. Integrate any additional evidence into the overall diagnosis.",
        ),
    ],

    "task_breakdown_role":     "professional home repair project planner",
    "task_breakdown_provider": "tradesperson",
}


# ---------------------------------------------------------------------------
# vehicle_damage
# ---------------------------------------------------------------------------
_VEHICLE_DAMAGE: dict = {
    "app_title":       "Vehicle Damage Analyser",
    "owner_label":     "vehicle owner",
    "provider_label":  "garage",
    "providers_label": "garages",
    "job_label":       "repair job",

    "photo_categories": frozenset({
        "bodywork", "mechanical", "electrical", "tyres", "windscreen", "interior", "general",
    }),

    "job_activities": frozenset({
        "bodywork", "mechanical", "electrical", "tyres", "windscreen", "interior", "general",
    }),

    "categories_display": [
        {"value": "bodywork",   "label": "Bodywork",   "icon": "🚗"},
        {"value": "mechanical", "label": "Mechanical", "icon": "⚙️"},
        {"value": "electrical", "label": "Electrical", "icon": "⚡"},
        {"value": "tyres",      "label": "Tyres",      "icon": "🔄"},
        {"value": "windscreen", "label": "Windscreen", "icon": "🪟"},
        {"value": "interior",   "label": "Interior",   "icon": "🪑"},
        {"value": "general",    "label": "General",    "icon": "🛠️"},
    ],

    "system_intro": (
        "You are an expert automotive damage assessor and panel beater with 30 years of hands-on "
        "experience diagnosing vehicle bodywork, mechanical, electrical, and interior damage for "
        "insurance claims and repair quotations.\n\n"
        "You will be shown between 1 and 5 photographs submitted by a vehicle owner, each taken "
        "from a different perspective to enable Multi-Perspective Triangulation. Analyse every "
        "image in the context of the others to produce a single, confident damage assessment.\n\n"
        "The customer has provided this description:\n"
        '"{description}"{category_hint}\n'
    ),

    "image_roles": [
        (
            "Overview Shot",
            "Identify the vehicle make, model, and colour if visible. Describe the overall damage "
            "extent — which panels or areas are affected and the approximate scale of damage.",
        ),
        (
            "Close-up",
            "Examine the specific damage area in precise detail. Describe dents, creases, "
            "scratches, paint damage, cracks, rust, or structural deformation. Estimate depth "
            "and affected area.",
        ),
        (
            "Reference / Part ID",
            "Look for visible part numbers, VIN plate, panel stampings, tyre sidewall markings, "
            "or specification labels that help identify the exact part or grade needed for repair "
            "or replacement.",
        ),
        (
            "Supplemental A",
            "Use this additional angle to resolve ambiguity from the first three images. "
            "Flag any new damage, structural concern, or contradictions that change the assessment.",
        ),
        (
            "Supplemental B",
            "Final supporting view. Integrate any additional evidence into the overall damage "
            "assessment and confirm the repair scope.",
        ),
    ],

    "task_breakdown_role":     "professional automotive repair project planner",
    "task_breakdown_provider": "technician",
}


# ---------------------------------------------------------------------------
# Registry + accessor
# ---------------------------------------------------------------------------
_CONFIGS: dict[str, dict] = {
    "home_repair":    _HOME_REPAIR,
    "vehicle_damage": _VEHICLE_DAMAGE,
}


def get_vertical_config() -> dict:
    """Return the active vertical config dict. Raises ValueError for unknown verticals."""
    name = settings.vertical
    cfg = _CONFIGS.get(name)
    if cfg is None:
        raise ValueError(
            f"Unknown VERTICAL '{name}'. Must be one of: {sorted(_CONFIGS)}"
        )
    return cfg
