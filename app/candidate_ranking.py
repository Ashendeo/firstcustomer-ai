"""Two-stage prioritization for ranked Crustdata company search.

Semantic/hybrid Company Search already returns relevance-ordered rows.
That API rejects a separate `sorts` field, so retain its ordinal as an
explicit signal and use a light-weight first-pass evidence score *before*
expensive enrichment. Later, the market skill scores enriched companies
fully and the UI ranks those verified-contact leads by their final rubric.
"""
from .market_scoring import score_market_fit
from .scoring import score_company


def prioritize_companies(campaign, companies: list[dict], skill: dict | None) -> list[dict]:
    scored = []
    for position, company in enumerate(companies, start=1):
        # Provider rank is a RELATIVE ordinal, never a purchase probability.
        provider_relevance = round(100 * (len(companies) - position + 1) /
                                   max(1, len(companies)))
        card = (score_market_fit(campaign, company, None, skill)
                if skill else score_company(campaign, company, None))
        observed = card.get("total")
        # Use provider relevance as the dominant prior when records are
        # sparse; observed evidence can promote particularly strong matches.
        evidence = observed if isinstance(observed, int) else 0
        priority = round(0.70 * provider_relevance + 0.30 * evidence, 1)
        scored.append({
            "company": company, "provider_rank": position,
            "provider_relevance_index": provider_relevance,
            "pre_enrichment_evidence_score": observed,
            "discovery_priority": priority,
        })
    return sorted(scored, key=lambda item: (-item["discovery_priority"],
                                             item["provider_rank"]))


def report_priorities(entries: list[dict]) -> list[dict]:
    return [{
        "company": str((row["company"].get("basic_info") or {}).get("name") or ""),
        "domain": str((row["company"].get("basic_info") or {}).get("primary_domain") or ""),
        "provider_rank": row["provider_rank"],
        "priority_rank": index,
        "priority_index": row["discovery_priority"],
        "pre_enrichment_evidence_score": row["pre_enrichment_evidence_score"],
    } for index, row in enumerate(entries, 1)]
