"""Adaptive Crustdata contact discovery with charge-safe checkpoints.

The initial title-constrained people search stays in the campaign workflow.
Only when that pass finds no exact-domain deliverable work email do we
try a broader current-employer-only search, with an independent call cap.
No guesswork, scraped addresses, catch-all, personal email, or auto LinkedIn.
"""
from urllib.parse import urlsplit

from . import settings
from .checkpoints import checkpointed, fingerprint, get_checkpoint
from .crustdata import Crustdata as RequestBuilder
from .discovery_trace import search_trace
from .market_skill import contact_role_match, prioritize_contacts
from .outreach import choose_verified_email
from .scoring import current_role


def profile_url(person: dict) -> str:
    social = (person.get("social_handles") or {}).get("professional_network_identifier") or {}
    url = str(social.get("profile_url") or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.linkedin.com", "linkedin.com"):
        return ""
    if not parsed.path.startswith("/in/") or len(parsed.path) <= 4:
        return ""
    return f"https://www.linkedin.com{parsed.path.rstrip('/')}"


def same_current_employer(person: dict, domain: str) -> bool:
    """When current-role company_website exists, reject mismatches explicitly."""
    current = (((person.get("experience") or {}).get("employment_details") or {})
               .get("current") or [])
    if isinstance(current, dict):
        current = [current]
    seen_domains = []
    for role in current:
        if not isinstance(role, dict):
            continue
        website = str(role.get("company_website") or "").strip()
        if not website:
            continue
        parsed = urlsplit(website if "://" in website else "https://" + website)
        if parsed.hostname:
            seen_domains.append(parsed.hostname.lower().removeprefix("www."))
    # No field in search result: trust Crustdata's current-employer filter,
    # but never trust an explicit mismatch.
    return not seen_domains or domain.lower().removeprefix("www.") in seen_domains


def contact_candidate_preview(person: dict, skill: dict, status: str) -> dict:
    basic = person.get("basic_profile") or {}
    match = contact_role_match(person, skill) if skill else {
        "match": 0, "function": "", "target_role": "", "reason": "Uncategorized buyer"}
    return {
        "name": str(basic.get("name") or "")[:140],
        "title": current_role(person)[:120],
        "profile_url": profile_url(person),
        "buying_role": match.get("function", ""),
        "target_role": match.get("target_role", ""),
        "buying_role_reason": match.get("reason", ""),
        "match": match.get("match", 0),
        "verification": status,
    }


def discover_contact(db, campaign_id: str, domain: str, discovery_key: str,
                     primary_people: list[dict], skill: dict, api,
                     audit, contact_budget: dict, fallback_budget: dict) -> dict:
    """Return best verified contact (if any) and ranked alternatives.

    Budgets count only NEW metered provider calls; completed checkpoints can be
    reused even when the new-call budget is exhausted.
    """
    examined = []
    attempted_urls = set()
    chosen = None
    email = ""
    def order_contacts(group):
        ranked = prioritize_contacts(group, skill) if skill else list(group)
        return sorted(ranked, key=lambda p: not bool(
            (p.get("contact") or {}).get("has_business_email")))
    primary = order_contacts(primary_people)

    def check_group(group: list[dict], phase: str):
        nonlocal chosen, email
        for person in group:
            url = profile_url(person)
            if url and url in attempted_urls:
                continue
            if url:
                attempted_urls.add(url)
            reason = "no_profile_url"
            if not same_current_employer(person, domain):
                reason = "current_employer_mismatch"
            elif not url:
                reason = "no_profile_url"
            else:
                key = ("candidate_email:" + discovery_key[:24] + ":" +
                       fingerprint({"profile": url})[:16])
                old = get_checkpoint(db, campaign_id, key)
                cached = bool(old and old.status == "complete")
                if not cached and contact_budget["used"] >= settings.MAX_CONTACT_ENRICHMENTS:
                    reason = "contact_enrichment_budget_exhausted"
                else:
                    if not cached:
                        contact_budget["used"] += 1
                    records = checkpointed(
                        db, campaign_id, key,
                        {"revision": "candidate-email-v1", "profile": url,
                         "company_domain": domain, "field": "contact.business_emails"},
                        lambda url=url: api.business_email_enrich(url),
                    )
                    matched = choose_verified_email(records, domain)
                    if matched:
                        email = matched
                        chosen = person
                        reason = "verified_deliverable_company_email"
                    elif any(str(v.get("status") or "").lower() == "deliverable"
                             for v in records if isinstance(v, dict)):
                        reason = "deliverable_email_wrong_company_domain"
                    elif any(str(v.get("status") or "").lower() == "catch_all"
                             for v in records if isinstance(v, dict)):
                        reason = "catch_all_only_unverified"
                    elif any(str(v.get("status") or "").lower() == "unknown"
                             for v in records if isinstance(v, dict)):
                        reason = "unknown_deliverability"
                    else:
                        reason = "no_deliverable_business_email"
                    audit(db, campaign_id, "crustdata_contact_enrichment", details={
                        "domain": domain,
                        "from_checkpoint": cached,
                        "deliverable_company_email_found": bool(matched),
                        "provider_calls_this_run": 0 if cached else 1,
                        "requested_fields": ["contact.business_emails"],
                        "contact_phase": phase,
                        "verification": reason,
                    })
                    db.commit()
            examined.append(contact_candidate_preview(person, skill, reason))
            if chosen:
                break

    check_group(primary, "title_filtered")

    secondary = []
    # Verified current-employer email index first; employer-wide discovery
    # second. Both use domain hard constraints and independent checkpoints.
    for phase, prefix, verified_only in [
        ("verified_employee_fallback", "people_verified_fallback:", True),
        ("employer_only_fallback", "people_fallback:", False),
    ]:
        if chosen or settings.DEMO_MODE:
            break
        key = prefix + discovery_key[:32]
        old = get_checkpoint(db, campaign_id, key)
        cached = bool(old and old.status == "complete")
        if (not cached and
                (fallback_budget["used"] >= settings.MAX_PEOPLE_FALLBACK_SEARCHES
                 or contact_budget["used"] >= settings.MAX_CONTACT_ENRICHMENTS)):
            break
        if not cached:
            fallback_budget["used"] += 1
        request = (RequestBuilder.people_search_verified_request(
            domain, [], limit=settings.PEOPLE_FALLBACK_RESULTS)
            if verified_only else RequestBuilder.people_search_request(
                domain, [], limit=settings.PEOPLE_FALLBACK_RESULTS))
        def search():
            if verified_only:
                return api.people_search_verified(
                    domain, [], limit=settings.PEOPLE_FALLBACK_RESULTS)
            return api.people_search(
                domain, [], limit=settings.PEOPLE_FALLBACK_RESULTS)
        records = checkpointed(
            db, campaign_id, key,
            {"revision": "people-verified-fallback-v1" if verified_only else "people-fallback-v1",
             "domain": domain, "titles": [], "limit": settings.PEOPLE_FALLBACK_RESULTS,
             **({"email_verified": True} if verified_only else {})},
            search,
        )
        audit(db, campaign_id, "crustdata_people_fallback", details={
            **search_trace("/person/search", request, records, cached),
            "domain": domain, "phase": phase,
            "reason": "No deliverable business email from earlier candidate tier",
        })
        db.commit()
        secondary.extend(records)
        check_group(order_contacts(records), phase)

    candidates = sorted(examined, key=lambda x: (-x["match"], x["name"].lower()))
    best_people = order_contacts(primary + secondary)
    return {
        "person": chosen,
        "best_person": chosen or (best_people[0] if best_people else None),
        "email": email,
        "candidates": candidates[:8],
        "people_considered": len(primary) + len(secondary),
        "fallback_used": bool(secondary),
        "fallback_profiles": len(secondary),
        "reason": ("verified_deliverable_company_email" if chosen
                   else ("contact_enrichment_budget_exhausted" if
                         contact_budget["used"] >= settings.MAX_CONTACT_ENRICHMENTS
                         else "no_matching_deliverable_business_email")),
    }
