"""Deterministic within-campaign ranking; never implies purchase probability."""
from .scoring import scorecard_for_display


def ranked_companies(campaign, leads) -> list[dict]:
    ranked = sorted(leads, key=lambda lead: (
        -lead.fit_score,
        -(lead.evidence or {}).get("scorecard", {}).get("criteria", [{}])[0].get("earned", 0),
        lead.company_name.casefold(),
        lead.id,
    ))
    rows = []
    for rank, lead in enumerate(ranked, 1):
        rubric = scorecard_for_display(campaign, lead)
        earned = sorted(rubric["criteria"], key=lambda item: -item["earned"])
        strengths = [x["name"] for x in earned if x["earned"] > 0]
        rows.append({
            "rank": rank,
            "lead": lead,
            "scorecard": rubric,
            "top_strength": strengths[0] if strengths else "No supporting factors",
            "main_gap": rubric["gaps"][0] if rubric["gaps"] else "No missing fields identified",
            "score_source": "legacy_model" if rubric["reconstructed"] else "evidence_rubric",
        })
    return rows
