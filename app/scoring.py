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


def _observed_company_text(company: dict) -> tuple[str, list[str]]:
    info = company.get("basic_info") or {}
    fields = ["description", "headline", "tagline", "summary", "industries",
              "specialties", "industry", "keywords"]
    parts, paths = [], []
    for field in fields:
        item = info.get(field)
        if not item:
            continue
        if isinstance(item, list):
            text = " ".join(str(v) for v in item if isinstance(v, (str, int)))
        elif isinstance(item, str):
            text = item
        else:
            continue
        if text.strip():
            parts.append(text[:1500])
            paths.append("company.basic_info." + field)
    return " ".join(parts), paths


def score_company(campaign, company: dict, person: dict | None) -> dict:
    """V2: use all observed descriptive attributes; NULL when unscorable."""
    info = company.get("basic_info") or {}
    text, sources = _observed_company_text(company)
    observed = tokens(text)
    desired = tokens(campaign.ideal_customer)
    matched = sorted(observed & desired)
    match_ratio = len(matched) / len(desired) if desired else 0.0
    icp = round(40 * min(1, match_ratio / 0.45)) if observed else 0

    raw_h = (company.get("headcount") or {}).get("total")
    try:
        headcount = int(raw_h) if raw_h is not None else None
    except (TypeError, ValueError):
        headcount = None
    min_h = int(campaign.min_headcount or 0)
    max_h = int(campaign.max_headcount or 0)
    if headcount is None:
        size, size_note = 0, "Headcount missing: no size points awarded"
    elif (min_h and headcount < min_h) or (max_h and headcount > max_h):
        size, size_note = 0, f"Observed {headcount} is outside {min_h or 'any'}–{max_h or 'any'}"
    elif not min_h and not max_h:
        size, size_note = 10, "No target-size constraint; observed headcount earns neutral half credit"
    else:
        size, size_note = 20, f"Observed {headcount} is within target size range"

    title = current_role(person)
    preferred = [t.strip().lower() for t in campaign.buyer_titles.split(",") if t.strip()]
    role = 0
    if title and any(t == title.lower() for t in preferred):
        role = 20
    elif title and any(t in title.lower() or title.lower() in t for t in preferred):
        role = 12

    domain = str(info.get("primary_domain") or "")
    profile = (person or {}).get("basic_profile") or {}
    coverage = (10 if text else 0) + (5 if domain else 0) + (5 if profile else 0)
    criteria = [
        {"key": "icp", "name": "Ideal-customer evidence", "earned": icp, "max": 40,
         "explanation": f"{len(matched)} of {len(desired)} ICP terms observed across supplied descriptive fields",
         "source": ", ".join(sources) or "company.basic_info (none returned)",
         "evidence": ", ".join(matched[:14]) or "No verified overlap"},
        {"key": "size", "name": "Company headcount fit", "earned": size, "max": 20,
         "explanation": size_note, "source": "company.headcount.total",
         "evidence": str(headcount) if headcount is not None else "Missing"},
        {"key": "buyer", "name": "Decision-maker role fit", "earned": role, "max": 20,
         "explanation": ("Observed role: " + title) if title else "No buyer title returned",
         "source": "person.experience.employment_details.current.title",
         "evidence": title[:120] if title else "Missing"},
        {"key": "coverage", "name": "Evidence completeness", "earned": coverage, "max": 20,
         "explanation": "Descriptive evidence (10), domain (5), named person profile (5)",
         "source": "company.basic_info / person.basic_profile",
         "evidence": ", ".join(v for v, ok in [
             ("company descriptors", text), ("domain", domain), ("person", profile)] if ok) or "None"},
    ]
    gaps = []
    if not text: gaps.append("No company industry, description or headline was returned")
    if headcount is None: gaps.append("Headcount unavailable")
    if not title: gaps.append("No target contact title identified")
    if not matched: gaps.append("No direct lexical overlap with ideal customer; semantic relevance is not independently verified")
    scorable = bool(text or headcount is not None or title)
    score = sum(item["earned"] for item in criteria) if scorable else None
    certainty = "insufficient" if not scorable else "limited" if len(gaps) >= 2 else "partial" if gaps else "reasonable"
    return {
        "version": "scorecard-v2", "total": score, "scorable": scorable,
        "criteria": criteria, "gaps": gaps,
        "evidence_confidence": certainty,
        "summary": (f"Observed match: {len(matched)} ICP terms; "
                    f"headcount: {headcount if headcount is not None else 'unknown'}; "
                    f"buyer role: {role}/20" if scorable
                    else "Insufficient company/role evidence to assign a defensible score"),
        "ranking_basis": "Observable evidence rubric, not a purchase likelihood",
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
