"""Deterministic company-scoring engine parameterized by a cached market skill.

Scores are evidence ranks, NOT buying probabilities.  A news mention, hiring
activity, or funding round is a *timing proxy*, not proof of intent. Missing
records score 0 for that criterion; entirely unobservable companies are
UNSCORED (NULL), not wrongly classified as poor prospects.
"""
from datetime import date, datetime, timezone
from .scoring import current_role


def _texts(value, depth=0):
    if depth > 4:
        return []
    if isinstance(value, str):
        return [value.lower()[:1200]]
    if isinstance(value, list):
        return [v for item in value[:30] for v in _texts(item, depth + 1)]
    if isinstance(value, dict):
        return [v for _, item in list(value.items())[:30]
                for v in _texts(item, depth + 1)]
    return []


def _matches(terms, text):
    return sorted({str(term).lower() for term in terms if isinstance(term, str)
                   and len(term.strip()) > 2 and str(term).lower() in text})


def _int(value):
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _recent(value, months=18):
    if not isinstance(value, str):
        return False
    try:
        event = date.fromisoformat(value[:10])
        return 0 <= (datetime.now(timezone.utc).date() - event).days <= months * 31
    except ValueError:
        return False


def revenue_usd_range(company: dict):
    """Retrieve estimated annual revenue range in USD; no employee proxy."""
    data = company.get("revenue") or {}
    if not isinstance(data, dict):
        return None, None
    def numeric(keys):
        for key in keys:
            value = data.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                number = float(str(value).replace(",", "").replace("$", ""))
            except (TypeError, ValueError):
                continue
            if 0 <= number < 1e16:
                return number
        return None
    lower = numeric(("estimated_revenue_lower_usd", "estimated_revenue_min_usd",
                     "revenue_min_usd", "lower_usd", "min_usd"))
    upper = numeric(("estimated_revenue_higher_usd", "estimated_revenue_upper_usd",
                     "estimated_revenue_max_usd", "revenue_max_usd", "upper_usd", "max_usd"))
    point = numeric(("estimated_revenue_usd", "annual_revenue_usd", "revenue_estimate_usd"))
    if lower is None and upper is None:
        return (point, point)
    if lower is None: lower = upper
    if upper is None: upper = lower
    return lower, upper


def score_market_fit(campaign, company: dict, person: dict | None,
                     skill: dict) -> dict:
    info = company.get("basic_info") or {}
    taxonomy = company.get("taxonomy") or {}
    headcount = company.get("headcount") or {}
    hiring = company.get("hiring") or {}
    news = company.get("news") or {}
    funding = company.get("funding") or {}
    revenue = company.get("revenue") or {}
    tech = company.get("technographics") or {}
    weights = skill["weights"]

    profile_text = " ".join(_texts({
        "description": info.get("description"),
        "markets": info.get("markets"),
        "taxonomy": taxonomy,
    }))
    # Observed needs are textual *relevance* only, never presumed purchase plans.
    need_text = " ".join(_texts({
        "description": info.get("description"), "taxonomy": taxonomy,
        "hiring": hiring.get("recent_openings") if isinstance(hiring, dict) else None,
        "news": news,
        "technographics": tech.get("top_technologies"),
    }))
    industries = _matches(skill.get("industry_terms", []), profile_text)
    needs = _matches(skill.get("need_terms", []), need_text)
    def fraction(matches, desired):
        # Strong evidence is multiple independent keyword hits; no credit for
        # absent fields. At most 1.0, full credit at 2 distinct matches.
        return min(1.0, len(matches) / min(2, max(1, len(desired)))) if desired else 0
    industry = round(weights["industry"] * fraction(industries, skill.get("industry_terms", [])))
    need = round(weights["need"] * fraction(needs, skill.get("need_terms", [])))

    lower_usd, upper_usd = revenue_usd_range(company)
    low, high = int(campaign.min_revenue_usd or 0), int(campaign.max_revenue_usd or 0)
    if lower_usd is None:
        size, size_reason = 0, "Annual revenue (USD) unavailable; no financial-scale points"
    elif (low and upper_usd < low) or (high and lower_usd > high):
        size, size_reason = 0, (
            f"Estimated revenue USD {lower_usd:,.0f}–{upper_usd:,.0f} outside range")
    elif low or high:
        fully_inside = ((not low or lower_usd >= low) and
                        (not high or upper_usd <= high))
        size = weights["scale"] if fully_inside else round(weights["scale"] / 2)
        size_reason = (f"Estimated annual revenue USD {lower_usd:,.0f}–{upper_usd:,.0f}; "
                       + ("range inside target" if fully_inside else
                          "estimate partly overlaps target range"))
    else:
        size, size_reason = round(weights["scale"] / 2), (
            f"Estimated annual revenue USD {lower_usd:,.0f}–{upper_usd:,.0f}; "
            "no revenue bounds specified (neutral)")

    observed_events = []
    try:
        yoy = float((headcount.get("growth_percent") or {}).get("yoy") or 0)
    except (TypeError, ValueError):
        yoy = 0
    if yoy >= 10:
        observed_events.append(f"Headcount YoY growth {yoy:g}%")
    last_raise = funding.get("last_fundraise_date")
    if _recent(last_raise):
        observed_events.append(f"Recent funding date: {last_raise[:10]}")
    hiring_matches = _matches(skill.get("hiring_terms", []),
                              " ".join(_texts(hiring.get("recent_openings"))))
    if hiring_matches:
        observed_events.append("Hiring keywords: " + ", ".join(hiring_matches[:4]))
    articles = news if isinstance(news, list) else (news.get("articles") or news.get("recent_articles") or [])
    recent_news = [x for x in articles[:12] if isinstance(x, dict)
                   and _recent(x.get("article_publish_date"))]
    news_hits = _matches(skill.get("news_terms", []), " ".join(_texts(recent_news)))
    if news_hits:
        observed_events.append("Recent news keywords: " + ", ".join(news_hits[:4]))
    timing = round(weights["timing"] * min(1.0, len(observed_events) / 3))

    title = current_role(person)
    buyer_terms = skill.get("buyer_titles", []) or [
        x.strip() for x in campaign.buyer_titles.split(",") if x.strip()]
    matched_roles = [t for t in buyer_terms if isinstance(t, str) and
                     t.lower().strip() in title.lower()] if title else []
    buyer = (weights["buyer"] if matched_roles else
             round(weights["buyer"] * .5) if title else 0)

    completeness = sum(bool(x) for x in (
        profile_text.strip(), lower_usd is not None,
        bool(hiring or funding or revenue or news), bool(title)
    ))
    coverage = round(weights["coverage"] * completeness / 4)
    criteria = [
        {"key": "industry", "name": "Market / industry fit", "earned": industry,
         "max": weights["industry"], "source": "company.basic_info + taxonomy",
         "explanation": "Observed keywords: " + (", ".join(industries) or "none")},
        {"key": "scale", "name": "Estimated annual revenue fit (USD)", "earned": size,
         "max": weights["scale"], "source": "company.revenue (estimated USD range)",
         "explanation": size_reason},
        {"key": "need", "name": "Potential problem relevance", "earned": need,
         "max": weights["need"], "source": "description, taxonomy, hiring, news, technographics",
         "explanation": "Observed keywords: " + (", ".join(needs) or "none") +
                        "; relevance does not establish a buying need"},
        {"key": "timing", "name": "Observable timing proxies", "earned": timing,
         "max": weights["timing"], "source": "headcount.growth_percent.yoy, funding, hiring, news",
         "explanation": "; ".join(observed_events) or "No attributable recent trigger observed"},
        {"key": "buyer", "name": "Buying-role match", "earned": buyer,
         "max": weights["buyer"], "source": "person.experience.employment_details.current.title",
         "explanation": "Observed title: " + title if title else "No buyer role identified"},
        {"key": "coverage", "name": "Evidence completeness", "earned": coverage,
         "max": weights["coverage"], "source": "observed data presence across 4 signal groups",
         "explanation": f"{completeness}/4 evidence groups populated"},
    ]
    # A bare name/domain is not evidence of target fitness.
    scorable = bool(profile_text.strip() or lower_usd is not None or title or observed_events)
    total = sum(item["earned"] for item in criteria) if scorable else None
    gaps = []
    if not profile_text.strip(): gaps.append("No industry or company description")
    if lower_usd is None: gaps.append("Estimated annual revenue USD unavailable")
    if not observed_events: gaps.append("No observed buying-timing proxies (not proof of no intent)")
    if not title: gaps.append("No buying committee member identified")
    return {
        "version": "market-skill-v1",
        "market_domain": skill["market_domain"],
        "weights": weights,
        "total": total,
        "criteria": criteria,
        "gaps": gaps,
        "evidence_confidence": ("insufficient" if not scorable else
                                "limited" if len(gaps) >= 2 else "partial"),
        "summary": (f"{skill['market_domain']}: {len(industries)} market keywords, "
                    f"{len(needs)} problem-context keywords, "
                    f"{len(observed_events)} timing proxies; score {total if total is not None else 'unavailable'}"),
        "ranking_basis": "Domain-specific observable B2B fit, NOT verified intent or probability",
        "sources": skill.get("source_urls", []),
    }
