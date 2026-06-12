"""Job-requirements knowledge base — shipped example data for the Lisa persona.

The Lisa persona (``personas/lisa.md``) uses ``lookup_job_requirements`` to
fetch must-have / nice-to-have skills for the role a candidate applied to,
so the agent can probe fit during the screening without reading the
criteria back to the candidate.

The positions and figures below are illustrative only — swap them out for
your own data source (DB, REST API, Cosmos DB, etc.) before shipping to
production. Or remove this whole tool and replace it with whatever your
persona actually needs.
"""

from __future__ import annotations

POSITIONS: dict[str, dict] = {
    "Cashier": {
        "title": "Cashier (Kassierer/in)",
        "department": "Front-end / Checkout",
        "type": "Full-time / Part-time",
        "must_have": [
            "Reliability and punctuality",
            "Friendly customer interaction",
            "Basic math / cash handling skills",
            "Willingness to work shifts (early, late, Saturdays)",
            "Ability to stand for extended periods",
        ],
        "nice_to_have": [
            "Previous cashier or retail experience",
            "Familiarity with POS systems",
            "German language skills (B1+)",
        ],
        "typical_shifts": "6:00–14:00 or 14:00–22:00, rotating Saturdays",
        "min_hours_week": 20,
        "salary_range": "€12.50–€14.50/hr depending on experience",
        "what_we_look_for": (
            "Someone who enjoys interacting with customers, stays calm under "
            "pressure during busy hours, and is dependable with their schedule."
        ),
    },
    "Sales Associate": {
        "title": "Sales Associate (Verkäufer/in)",
        "department": "Sales floor",
        "type": "Full-time / Part-time",
        "must_have": [
            "Customer service orientation",
            "Willingness to restock shelves and maintain displays",
            "Physical fitness (lifting, standing, walking)",
            "Flexibility with shift schedules",
            "Team player mindset",
        ],
        "nice_to_have": [
            "Retail or supermarket experience",
            "Product knowledge (grocery, fresh produce)",
            "German language skills (B1+)",
        ],
        "typical_shifts": "6:00–14:00 or 14:00–22:00, rotating Saturdays",
        "min_hours_week": 20,
        "salary_range": "€12.50–€15.00/hr depending on experience",
        "what_we_look_for": (
            "Someone who takes pride in a well-organized store, proactively "
            "helps customers, and works well as part of a team."
        ),
    },
    "Warehouse Clerk": {
        "title": "Warehouse Clerk (Lagerist/in)",
        "department": "Logistics / Back-of-house",
        "type": "Full-time",
        "must_have": [
            "Physical fitness (heavy lifting up to 20kg regularly)",
            "Reliability and attention to detail",
            "Willingness to start early (deliveries arrive 5–6 AM)",
            "Basic organizational skills",
        ],
        "nice_to_have": [
            "Forklift certification",
            "Experience in warehouse / logistics",
            "Knowledge of inventory management systems",
        ],
        "typical_shifts": "5:00–13:00 (primarily early shifts)",
        "min_hours_week": 35,
        "salary_range": "€13.00–€16.00/hr depending on experience",
        "what_we_look_for": (
            "Someone physically strong, well-organized, and who enjoys "
            "working behind the scenes to keep the store running smoothly."
        ),
    },
    "Shift Leader": {
        "title": "Shift Leader (Schichtleiter/in)",
        "department": "Store operations",
        "type": "Full-time",
        "must_have": [
            "2+ years retail experience",
            "Leadership / team coordination skills",
            "Problem-solving under pressure",
            "Full shift flexibility (early, late, weekends, holidays)",
            "Strong customer orientation",
        ],
        "nice_to_have": [
            "Previous supervisory experience",
            "Knowledge of labor regulations",
            "Training / mentoring experience",
            "German language skills (B2+)",
        ],
        "typical_shifts": "Rotating — all shift types including weekends",
        "min_hours_week": 38,
        "salary_range": "€15.00–€19.00/hr depending on experience",
        "what_we_look_for": (
            "A natural leader who can motivate a small team, handle "
            "escalations calmly, and keep operations running during their shift."
        ),
    },
    "Part-time": {
        "title": "Part-time Associate (Teilzeitkraft)",
        "department": "Various (assigned based on store needs)",
        "type": "Part-time / Mini-job (€520)",
        "must_have": [
            "Reliability — even part-timers are critical to coverage",
            "Friendly and helpful attitude",
            "Flexibility for at least 2–3 shifts per week",
        ],
        "nice_to_have": [
            "Any retail or customer service experience",
            "Student or career-changer background (welcome!)",
        ],
        "typical_shifts": "Flexible — often afternoons, evenings, or Saturdays",
        "min_hours_week": 10,
        "salary_range": "€12.50–€14.00/hr",
        "what_we_look_for": (
            "Someone reliable who can commit to a regular schedule, "
            "even if it's just a few shifts per week."
        ),
    },
}


def get_requirements_for(position: str) -> dict | None:
    """Look up a position by name (case-insensitive, partial match)."""
    key_lower = position.strip().lower()
    for name, reqs in POSITIONS.items():
        if key_lower in name.lower() or name.lower() in key_lower:
            return {**reqs, "_key": name}
    return None


def list_open_positions() -> list[str]:
    """Return the list of currently open position names."""
    return list(POSITIONS.keys())
