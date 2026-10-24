"""Adaptive Crustdata contact discovery with charge-safe checkpoints.

The initial title-constrained people search stays in the campaign workflow.
Only when that pass finds no exact-domain deliverable work email do we
try a broader current-employer-only search, with an independent call cap.
No guesswork, scraped addresses, catch-all, personal email, or auto LinkedIn.
"""
from urllib.parse import urlsplit

from . import settings
from .checkpoints import checkpointed, fingerprint, get_checkpoint
from .crustdata import Crustdata as RequestBuilder, CrustdataPermissionError
from .discovery_trace import search_trace
from .market_skill import contact_role_match, prioritize_contacts
from .outreach import choose_verified_email
from .scoring import current_role


def verified_business_email_from_search(person: dict, domain: str) -> str:
    """Accept an actual verified work address from People Search, never a flag.

    Supports common native people-search representations; search fields vary
    across Crustdata plans. Explicit validation is required for all shapes.
    """
    domain = domain.lower().removeprefix("www.")
    sources = []
    contact = person.get("contact") or {}
    sources.extend(contact.get("business_emails") or [])
    sources.extend(person.get("business_emails") or [])
    for employer in person.get("current_employers") or []:
        if not isinstance(employer, dict):
            continue
        domains = employer.get("employer_company_website_domain") or []
        if isinstance(domains, str):
            domains = [domains]
        if domain not in [str(d).lower().removeprefix("www.") for d in domains]:
            continue
        business = employer.get("business_emails") or {}
        if isinstance(business, dict):
            sources.extend({"email": address, **(metadata if isinstance(metadata, dict) else {})}
                           for address, metadata in business.items())
    if isinstance(contact.get("business_email"), str):
        sources.append({"email": contact["business_email"],
                        "status": contact.get("business_email_status")})
    for record in sources:
        if not isinstance(record, dict):
            continue
        address = str(record.get("email") or record.get("value") or "").lower().strip()
        status = str(record.get("status") or record.get("verification_status") or "").lower()
        if (status in ("deliverable", "verified") and
                address.count("@") == 1 and address.rsplit("@", 1)[1] == domain):
            return address
    return ""


def company_people_profiles(company: dict, domain: str) -> list[dict]:
    """Normalize company-level decision makers into people search candidates."""
    people = company.get("people") or {}
    if isinstance(people, list):
        groups = [people]
    elif isinstance(people, dict):
        groups = [people.get(k) or [] for k in (
            "decision_makers", "executives", "founders", "key_people", "leadership")]
    else:
        groups = []
    candidates, seen = [], set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for entry in group:
            if not isinstance(entry, dict):
                continue
            person = entry.get("person_data") or entry.get("person") or entry
            if not isinstance(person, dict):
                continue
            info = person.get("basic_profile") or {}
            name = str(info.get("name") or person.get("name") or "").strip()
            if not name:
                continue
            normalized = dict(person)
            normalized.setdefault("basic_profile", {"name": name})
            title = str(person.get("title") or person.get("current_title") or "")
            if title and not normalized.get("experience"):
                normalized["experience"] = {
                    "employment_details": {"current": [{
                        "name": (company.get("basic_info") or {}).get("name", ""),
                        "title": title, "company_website": domain
                    }]}
                }
            identity = profile_url(normalized) or name.lower()
            if identity not in seen:
                seen.add(identity)
                candidates.append(normalized)
    return candidates


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
    contact_budget.setdefault("permission_denied", False)
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
            if contact_budget["permission_denied"]:
                examined.append(contact_candidate_preview(person, skill, "contact_api_permission_denied"))
                continue
            url = profile_url(person)
            if url and url in attempted_urls:
                continue
            if url:
                attempted_urls.add(url)
            reason = "no_profile_url"
            search_email = verified_business_email_from_search(person, domain)
            if not same_current_employer(person, domain):
                reason = "current_employer_mismatch"
            elif search_email:
                chosen, email = person, search_email
                reason = "verified_work_email_from_people_search"
            elif settings.CONTACT_LOOKUP_MODE == "search":
                reason = "search_profile_lacks_verified_email"
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
                    try:
                        records = checkpointed(
                            db, campaign_id, key,
                            {"revision": "candidate-email-v1", "profile": url,
                             "company_domain": domain, "field": "contact.business_emails"},
                            lambda url=url: api.business_email_enrich(url),
                        )
                    except CrustdataPermissionError:
                        # 403 is a plan/entitlement denial, not a missing email.
                        # No additional contact-enrichment requests should be sent
                        # during this campaign run. The inner checkpoint stays
                        # uncertain for explicit operator review.
                        contact_budget["permission_denied"] = True
                        reason = "contact_api_permission_denied"
                        audit(db, campaign_id, "crustdata_contact_access_denied", details={
                            "endpoint": "/person/contact/enrich",
                            "domain": domain,
                            "action": "stopped_contact_enrichment_for_run",
                            "resolution": "enable_endpoint_access_with_crustdata",
                        })
                        db.commit()
                        examined.append(contact_candidate_preview(person, skill, reason))
                        break
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
        if chosen or settings.DEMO_MODE or contact_budget["permission_denied"]:
            break
        if verified_only and not hasattr(api, "people_search_verified"):
            continue
        if not hasattr(api, "people_search"):
            continue
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
        "reason": ("verified_business_email_from_search" if chosen and settings.CONTACT_LOOKUP_MODE == "search" else "verified_deliverable_company_email" if chosen
                   else ("contact_api_permission_denied" if contact_budget["permission_denied"]
                         else "contact_enrichment_budget_exhausted" if
                         contact_budget["used"] >= settings.MAX_CONTACT_ENRICHMENTS
                         else "no_matching_deliverable_business_email")),
    }
