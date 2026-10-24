"""Preserve Crustdata search order while calculating informational fit scores."""
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
        priority = provider_relevance
        scored.append({
            "company": company, "provider_rank": position,
            "provider_relevance_index": provider_relevance,
            "pre_enrichment_evidence_score": observed,
            "discovery_priority": priority,
        })
    return scored  # Search order determines outreach; evidence scores are informational.


OUTREACH_TOP_N = 5


def provider_rank(lead):
    rank = (lead.evidence or {}).get("search_prioritization", {}).get("provider_rank")
    return rank if type(rank) is int and rank > 0 else None


def search_ordered_leads(leads):
    return sorted(leads, key=lambda lead: (
        provider_rank(lead) is None, provider_rank(lead) or 0,
        lead.company_name.casefold(), lead.id,
    ))


def outreach_leads(leads):
    # Strict top five original results: unavailable contacts do not promote #6.
    selected, seen = [], set()
    for lead in search_ordered_leads(leads):
        rank = provider_rank(lead)
        identity = (lead.domain or lead.company_name).lower().removeprefix("www.")
        if rank is not None and rank <= OUTREACH_TOP_N and rank not in seen and identity not in seen:
            selected.append(lead)
            seen.update((rank, identity))
    return selected


def report_priorities(entries: list[dict]) -> list[dict]:
    return [{
        "company": str((row["company"].get("basic_info") or {}).get("name") or ""),
        "domain": str((row["company"].get("basic_info") or {}).get("primary_domain") or ""),
        "provider_rank": row["provider_rank"],
        "priority_rank": index,
        "priority_index": row["discovery_priority"],
        "pre_enrichment_evidence_score": row["pre_enrichment_evidence_score"],
    } for index, row in enumerate(entries, 1)]
