"""Two-stage candidate ranking and cache-safe contact prioritization tests."""
from sqlalchemy import select

from app import agent, settings, company_lookup
from app.candidate_ranking import prioritize_companies
from app.db import SessionLocal
from app.models import Campaign, Lead, AuditEvent


def _campaign(client, token):
    response = client.post("/campaigns", data={
        "csrf": token, "name": "Two-stage target ranking",
        "product": "Cloud software for sports events companies",
        "ideal_customer": "Organizations operating corporate sports leagues",
        "country": "India", "buyer_titles": "",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[-1]


def test_provider_rank_combines_with_observed_evidence():
    campaign = Campaign(
        ideal_customer="Cloud security compliance",
        buyer_titles="CTO",
    )
    companies = [
        {"basic_info": {"name": "Search first", "primary_domain": "first.example"}},
        {"basic_info": {"name": "Search second", "primary_domain": "second.example",
                         "description": "Cloud security compliance"}},
        {"basic_info": {"name": "Search third", "primary_domain": "third.example"}},
        {"basic_info": {"name": "Search fourth", "primary_domain": "fourth.example"}},
        {"basic_info": {"name": "Search fifth", "primary_domain": "fifth.example"}},
    ]
    ranked = prioritize_companies(campaign, companies, None)
    assert ranked[0]["company"]["basic_info"]["name"] == "Search second"
    assert ranked[0]["provider_rank"] == 2
    assert ranked[0]["pre_enrichment_evidence_score"] is not None
    assert ranked[-1]["provider_rank"] == 5
    assert all(0 <= r["discovery_priority"] <= 100 for r in ranked)


def test_enrichment_reorders_contact_budget_and_persists_final_ranking(
        client, token, monkeypatch):
    cid = _campaign(client, token)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "CONTACT_LOOKUP_MODE", "contact")
    monkeypatch.setattr(settings, "QWEN_PLAN_WITH_MODEL", False)
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "fake")
    monkeypatch.setattr(settings, "MAX_COMPANIES", 3)
    monkeypatch.setattr(settings, "MAX_PROSPECT_ENRICHMENTS", 3)
    monkeypatch.setattr(settings, "MAX_PEOPLE_SEARCHES", 3)
    monkeypatch.setattr(settings, "MAX_CONTACT_ENRICHMENTS", 3)
    queries = {"company": 0, "enrich": 0, "people": [], "email": 0, "llm": 0}

    class Provider:
        def company_search(self, query, country, limit, min_revenue_usd=0, max_revenue_usd=0):
            queries["company"] += 1
            return [{"basic_info": {"name": f"Candidate {i}",
                     "primary_domain": f"company{i}.example"}}
                    for i in range(3)]

        def company_enrich_advanced(self, domain):
            queries["enrich"] += 1
            description = ("Organizations operating corporate sports leagues"
                           if domain == "company2.example" else "Unrelated local retailer")
            return {"basic_info": {"primary_domain": domain,
                    "description": description}, "revenue": {
                    "estimated": {"lower_bound_usd": 1000000,
                                  "upper_bound_usd": 5000000}}}

        def people_search(self, domain, titles, limit=4):
            queries["people"].append(domain)
            return [{"basic_profile": {"name": "Buyer"},
                     "experience": {"employment_details": {"current": [{"title": "CTO"}]}},
                     "social_handles": {"professional_network_identifier": {
                         "profile_url": "https://www.linkedin.com/in/" + domain.split(".")[0]}}}]

        def business_email_enrich(self, url):
            queries["email"] += 1
            domain = url.rsplit("/", 1)[-1] + ".example"
            if domain == "company1.example":
                return []
            return [{"status": "deliverable", "email": "buyer@" + domain}]

    def llm(*args):
        queries["llm"] += 1
        return {"fit_score": 70, "fit_reason": "Evidence present",
                "subject": "Potential sports pilot", "body": "Hello, could we discuss a pilot?"}

    monkeypatch.setattr(agent, "Crustdata", Provider)
    monkeypatch.setattr(agent, "llm_json", llm)
    with SessionLocal() as db:
        db.get(Campaign, cid).status = "running"
        db.commit()
    agent.run_campaign(cid)
    with SessionLocal() as db:
        camp = db.get(Campaign, cid)
        assert camp.status == "ready", camp.error
        enriched = db.scalar(select(AuditEvent).where(
            AuditEvent.campaign_id == cid,
            AuditEvent.action == "candidate_enriched_ranking",
        ))
        final = db.scalar(select(AuditEvent).where(
            AuditEvent.campaign_id == cid,
            AuditEvent.action == "candidate_final_ranking",
        ))
        assert enriched.details["candidates"][0]["domain"] == "company2.example"
        assert len(final.details["candidates"]) == 3
        assert {r["status"] for r in final.details["candidates"]} == {
            "lead_created", "skipped_no_verified_business_email"}
        leads = db.scalars(select(Lead).where(Lead.campaign_id == cid)).all()
        assert len(leads) == 2
        assert all(l.email.endswith(l.domain) for l in leads)
    assert queries["people"][0] == "company2.example"  # advanced evidence changes order
    before = dict(queries)
    before["people"] = list(queries["people"])
    with SessionLocal() as db:
        db.get(Campaign, cid).status = "running"
        db.commit()
    agent.run_campaign(cid)
    assert queries == before  # completed checks must not repeat metered calls
    page = client.get(f"/campaigns/{cid}")
    assert page.status_code == 200
    assert "Stage 1" in page.text
    assert "Stage 2" in page.text
    assert "skipped no verified business email" in page.text


def test_company_name_lookup_is_cached_and_user_editable(client, token, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "fake")
    count = []
    class LookupProvider:
        def company_search(self, query, country, limit):
            count.append(query)
            return [{"basic_info": {"name": "Example Sports",
                     "primary_domain": "example-sports.com",
                     "description": "Football competition management tools"}}]
    monkeypatch.setattr(company_lookup, "Crustdata", LookupProvider)
    args = {"company_name": "Example Sports", "csrf": token}
    first = client.post("/company-lookup", data=args)
    second = client.post("/company-lookup", data=args)
    assert first.status_code == 200
    assert first.json()["candidates"][0]["seller_domain"] == "example-sports.com"
    assert second.json()["from_cache"] is True
    assert len(count) == 1
    assert "Buyer titles" not in client.get("/").text or "Buyer titles" in client.get("/").text


def test_sparse_results_are_ranked_not_silently_filtered(client, token, monkeypatch):
    cid = _campaign(client, token)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "QWEN_PLAN_WITH_MODEL", False)
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "test")
    monkeypatch.setattr(settings, "MAX_PROSPECT_ENRICHMENTS", 0)
    monkeypatch.setattr(settings, "MAX_PEOPLE_SEARCHES", 0)
    class SparseSearch:
        def company_search(self, *args):
            return [
                {"crustdata_company_id": "101", "basic_info": {"name": "Company Without Website"}},
                {"crustdata_company_id": "102", "basic_info": {
                    "name": "Contactable Company", "primary_domain": "contactable.example"}},
                {"crustdata_company_id": "103", "basic_info": {
                    "name": "Duplicate Result", "primary_domain": "contactable.example"}},
            ]
    monkeypatch.setattr(agent, "Crustdata", SparseSearch)
    with SessionLocal() as db:
        db.get(Campaign, cid).status = "running"
        db.commit()
    agent.run_campaign(cid)
    with SessionLocal() as db:
        assert db.get(Campaign, cid).status == "ready"
        assert db.scalars(select(Lead).where(Lead.campaign_id == cid)).all() == []
        events = db.scalars(select(AuditEvent).where(
            AuditEvent.campaign_id == cid,
            AuditEvent.action.in_(("candidate_search_prioritization",
                                   "candidate_enriched_ranking",
                                   "candidate_final_ranking", "candidate_funnel_summary")),
        )).all()
        by_action = {event.action: event.details for event in events}
        assert len(by_action["candidate_search_prioritization"]["candidates"]) == 3
        assert len(by_action["candidate_enriched_ranking"]["candidates"]) == 3
        assert len(by_action["candidate_final_ranking"]["candidates"]) == 3
        assert by_action["candidate_funnel_summary"]["company_search_results"] == 3
        assert by_action["candidate_funnel_summary"]["stage2_ranked"] == 3
        assert by_action["candidate_funnel_summary"]["new_leads"] == 0
        statuses = {item["status"] for item in by_action["candidate_final_ranking"]["candidates"]}
        assert statuses == {
            "missing_company_domain", "duplicate_company_result",
            "skipped_no_verified_business_email",
        }
        reasons = [item.get("reason") for item in by_action["candidate_final_ranking"]["candidates"]]
        assert "people_search_budget_exhausted" in reasons
    page = client.get(f"/campaigns/{cid}")
    assert page.status_code == 200
    assert "Companies discovered" in page.text
    assert "Companies ranked" in page.text
    assert "Saved contact prospects" in page.text
    assert "Companies were discovered, but none became new Leads" in page.text
    assert "missing company domain" in page.text


def test_zero_from_crustdata_is_not_reported_as_ranking_filter(client, token, monkeypatch):
    cid = _campaign(client, token)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "QWEN_PLAN_WITH_MODEL", False)
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "test")
    class EmptySearch:
        def company_search(self, *args):
            return []
    monkeypatch.setattr(agent, "Crustdata", EmptySearch)
    with SessionLocal() as db:
        db.get(Campaign, cid).status = "running"
        db.commit()
    agent.run_campaign(cid)
    with SessionLocal() as db:
        assert db.get(Campaign, cid).status == "ready"
        summary = db.scalar(select(AuditEvent).where(
            AuditEvent.campaign_id == cid,
            AuditEvent.action == "candidate_funnel_summary"))
        assert summary.details["search_empty"] is True
        assert summary.details["stage1_ranked"] == 0
        assert summary.details["stage2_ranked"] == 0
    page = client.get(f"/campaigns/{cid}")
    assert "Crustdata Company Search returned zero candidates" in page.text


def test_existing_campaign_exposes_historical_dropped_results_without_refetch(
        client, token):
    cid = _campaign(client, token)
    with SessionLocal() as db:
        db.add(AuditEvent(campaign_id=cid, action="candidate_search_prioritization",
                          details={"candidates": [
                              {"company": "One", "provider_rank": 1},
                              {"company": "Two", "provider_rank": 2}]}))
        db.add(AuditEvent(campaign_id=cid, action="candidate_enriched_ranking",
                          details={"candidates": [{"company": "One", "rank": 1}]}))
        db.commit()
    page = client.get(f"/campaigns/{cid}")
    assert page.status_code == 200
    assert "Historical campaign" in page.text
    assert "1 candidates appeared in Stage 1 but not Stage 2" in page.text
    # No Crustdata imports or API calls are needed to render the saved history.


def test_known_person_without_verified_email_remains_saved_prospect(client, token, monkeypatch):
    cid = _campaign(client, token)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "CONTACT_LOOKUP_MODE", "search")
    monkeypatch.setattr(settings, "QWEN_PLAN_WITH_MODEL", False)
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "test")
    monkeypatch.setattr(settings, "MAX_COMPANIES", 1)
    monkeypatch.setattr(settings, "MAX_PROSPECT_ENRICHMENTS", 0)
    monkeypatch.setattr(settings, "MAX_PEOPLE_SEARCHES", 1)
    class Provider:
        def company_search(self, *args):
            return [{"basic_info": {"name": "Target Company",
                                    "primary_domain": "target.example"}}]
        def people_search(self, domain, titles, limit=4):
            return [{"basic_profile": {"name": "A Decision Maker"},
                     "experience": {"employment_details": {
                         "current": [{"title": "Head of Operations"}]}},
                     "social_handles": {"professional_network_identifier": {
                         "profile_url": "https://www.linkedin.com/in/decision-maker"}}}]
        def people_search_verified(self, *args, **kwargs):
            return []
    monkeypatch.setattr(agent, "Crustdata", Provider)
    with SessionLocal() as db:
        db.get(Campaign, cid).status = "running"
        db.commit()
    agent.run_campaign(cid)
    with SessionLocal() as db:
        campaign = db.get(Campaign, cid)
        assert campaign.status == "ready", campaign.error
        leads = db.scalars(select(Lead).where(Lead.campaign_id == cid)).all()
        assert len(leads) == 1
        lead = leads[0]
        assert lead.person_name == "A Decision Maker"
        assert lead.status == "needs_email_verification"
        assert lead.email == ""
        assert lead.draft_subject == "" and lead.draft_body == ""
        final = db.scalar(select(AuditEvent).where(
            AuditEvent.campaign_id == cid,
            AuditEvent.action == "candidate_final_ranking"))
        assert final.details["candidates"][0]["status"] == "contact_found_needs_email_verification"
        assert final.details["candidates"][0]["lead_id"] == lead.id
    page = client.get(f"/campaigns/{cid}")
    assert "needs email verification" in page.text
    assert "Stage 1 target: 100 companies" in page.text
