"""Deterministic within-campaign ranking; never implies purchase probability."""
from .scoring import scorecard_for_display
from .candidate_ranking import search_ordered_leads


def ranked_companies(campaign, leads) -> list[dict]:
    ranked = search_ordered_leads(leads)
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
