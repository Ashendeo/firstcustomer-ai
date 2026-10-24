"""Evidence-backed deterministic prospect scorecards, v1.

A lead's 0-100 total is EXACTLY the sum of the displayed criteria, not
a model-produced probability of purchase. Model rationale may be kept
separately as commentary but cannot override the evidence-based score.
"""
import re

STOP = {"and","the","that","with","from","this","have","will","into","their",
        "for","our","you","your","are","who","what","where","when","companies",
        "company","business","businesses","organizations","providing","looking",
        "want","could","would","they","teams","team","about","using"}


def tokens(value) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9]{2,}", str(value or "").lower())
    return {s for s in words if s not in STOP}


def current_role(person: dict | None) -> str:
    roles = (((person or {}).get("experience") or {})
             .get("employment_details") or {}).get("current") or []
    if isinstance(roles, list) and roles:
        return str((roles[0] or {}).get("title") or "")
    if isinstance(roles, dict):
        return str(roles.get("title") or "")
    return ""


def score_company(campaign, company: dict, person: dict | None) -> dict:
    info = company.get("basic_info") or {}
    description = str(info.get("description") or "")
    domain = str(info.get("primary_domain") or "")
    observed = tokens(description)
    desired = tokens(campaign.ideal_customer)
    matched = sorted(observed & desired)
    # Lexical agreement only; it's NOT a semantic assertion that a buyer is ready.
    ratio = len(matched) / len(desired) if desired else 0
    icp = round(40 * min(1, ratio / 0.45)) if description else 0

    h = (company.get("headcount") or {}).get("total")
    try:
        headcount = int(h) if h is not None else None
    except (TypeError, ValueError):
        headcount = None
    minimum = int(campaign.min_headcount or 0)
    maximum = int(campaign.max_headcount or 0)
    if headcount is None:
        size, size_reason = 0, "No observed headcount; no size-fit points"
    elif minimum and headcount < minimum or maximum and headcount > maximum:
        size, size_reason = 0, f"Observed {headcount} outside target {minimum or 'any'}–{maximum or 'any'}"
    elif not minimum and not maximum:
        size, size_reason = 10, f"Observed {headcount}; campaign has no size requirement (neutral)"
    else:
        size, size_reason = 20, f"Observed {headcount} within requested range"

    title = current_role(person)
    wanted = [x.strip().lower() for x in campaign.buyer_titles.split(",") if x.strip()]
    role_score = (20 if title and any(t == title.lower() for t in wanted)
                  else 12 if title and any(t in title.lower() or title.lower() in t
                                           for t in wanted)
                  else 0)
    role_reason = ("Observed role: " + title if title else "No decision-maker title observed")
    if title and role_score == 0:
        role_reason += " (not matched to requested titles)"

    complete = (10 if description else 0) + (5 if domain else 0) + (5 if (person or {}).get("basic_profile") else 0)
    criteria = [
        {"key": "icp", "name": "Ideal-customer evidence", "earned": icp, "max": 40,
         "explanation": (f"{len(matched)} of {len(desired)} unique target terms found in company description"
                         if description else "No description available"),
         "source": "company.basic_info.description", "evidence": ", ".join(matched[:14]) or "No shared terms"},
        {"key": "size", "name": "Company headcount fit", "earned": size, "max": 20,
         "explanation": size_reason, "source": "company.headcount.total",
         "evidence": str(headcount) if headcount is not None else "Missing"},
        {"key": "buyer", "name": "Decision-maker role fit", "earned": role_score, "max": 20,
         "explanation": role_reason,
         "source": "person.experience.employment_details.current.title",
         "evidence": title[:120] if title else "Missing"},
        {"key": "coverage", "name": "Evidence completeness", "earned": complete, "max": 20,
         "explanation": "Description (10), domain (5), person profile (5), only when present",
         "source": "company.basic_info / person.basic_profile",
         "evidence": ", ".join(k for k, val in [("description", description), ("domain", domain),
                            ("person profile", (person or {}).get("basic_profile"))] if val) or "No details"}
    ]
    score = sum(c["earned"] for c in criteria)
    gaps = []
    if not description: gaps.append("Company description missing")
    if headcount is None: gaps.append("Headcount unavailable")
    if not title: gaps.append("No target contact identified")
    if not matched: gaps.append("No direct keyword overlap with ideal-customer description")
    certainty = "limited" if len(gaps) >= 2 else "partial" if gaps else "reasonable"
    return {
        "version": "scorecard-v1", "total": score, "criteria": criteria,
        "gaps": gaps, "evidence_confidence": certainty,
        "summary": (f"Matched {len(matched)} ICP terms; headcount: "
                    f"{headcount if headcount is not None else 'unknown'}; "
                    f"buyer-role points: {role_score}/20"),
        "ranking_basis": "Evidence rubric; not a calibrated buying probability",
    }


def scorecard_for_display(campaign, lead) -> dict:
    existing = (lead.evidence or {}).get("scorecard")
    if existing:
        return {**existing, "reconstructed": False}
    # Historical model scores must remain unchanged. Reconstruct a *separate*
    # evidence diagnostic without pretending its math produced that score.
    evidence = lead.evidence or {}
    preview = score_company(campaign, evidence.get("company_record") or {},
                            evidence.get("person_record") or {})
    return {**preview, "reconstructed": True}
