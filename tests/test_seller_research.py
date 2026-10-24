"""Research-first discovery and score-v2 regression tests (all mocked)."""
from sqlalchemy import select
from app import agent, settings
from app.db import SessionLocal
from app.models import Campaign, Lead, AuditEvent, StepCheckpoint
from app.scoring import score_company
from app.seller_research import normalize_company_domain
import app.seller_research as research


def make_campaign(client, token, domain="seller.example"):
    resp = client.post("/campaigns", data={
        "csrf": token, "name": "Seller research run",
        "seller_name": "Seller Technologies", "seller_domain": domain,
        "product": "Cloud security monitoring for businesses",
        "ideal_customer": "Businesses managing cloud environments",
        "country": "India", "buyer_titles": "Head of Security",
    }, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_company_domain_validation(client, token):
    assert normalize_company_domain("https://www.example.com/") == "example.com"
    assert normalize_company_domain("https://127.0.0.1/") == ""
    assert normalize_company_domain("http://internal.local/") == ""
    assert normalize_company_domain("https://example.com/a") == ""
    assert client.post("/campaigns", data={
        "csrf": token, "name": "Bad URL",
        "seller_domain": "http://localhost:8000",
        "product": "Business network monitoring",
        "ideal_customer": "Cloud operations teams",
    }).status_code == 422


def test_undocumented_evidence_is_not_zero():
    campaign = Campaign(ideal_customer="Cloud security businesses",
                        buyer_titles="CTO")
    bare = {"basic_info": {"name": "Acme", "primary_domain": "acme.example"}}
    card = score_company(campaign, bare, None)
    assert card["total"] is None
    assert card["evidence_confidence"] == "insufficient"
    industries = {"basic_info": {"name": "Acme",
                 "primary_domain": "acme.example",
                 "industries": ["Cloud security and operations"]}}
    second = score_company(campaign, industries, None)
    assert second["total"] is not None and second["total"] > 0
    assert "company.basic_info.industries" in second["criteria"][0]["source"]


def test_research_first_generates_grounded_query_and_uses_checkpoints(
        client, token, monkeypatch):
    campaign_id = make_campaign(client, token)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "fake")
    monkeypatch.setattr(settings, "BRAVE_SEARCH_API_KEY", "fake")
    monkeypatch.setattr(settings, "MAX_PROSPECT_ENRICHMENTS", 2)
    monkeypatch.setattr(settings, "MAX_COMPANIES", 1)
    monkeypatch.setattr(settings, "MAX_PEOPLE_SEARCHES", 1)
    calls = {"enrich": [], "search": 0, "web": 0, "llm": 0}

    class FakeCrustdata:
        def company_enrich(self, domain):
            calls["enrich"].append(domain)
            return {"basic_info": {
                "name": "Seller Technologies" if domain == "seller.example" else "Prospect",
                "primary_domain": domain,
                "description": "Cloud monitoring for enterprise security" if domain == "seller.example" else "",
                "industries": ["IT security"],
            }, "headcount": {"total": 450}}

        def company_search(self, query, country, limit, min_headcount=0, max_headcount=0):
            calls["search"] += 1
            assert query == "Organizations operating enterprise security operations teams"
            assert calls["enrich"][0] == "seller.example"  # research before search
            return [{"crustdata_company_id": 15, "basic_info": {
                "name": "Prospect", "primary_domain": "prospect.example"}}]

        def people_search(self, domain, titles, limit=2):
            return [{"basic_profile": {"name": "Buyer"},
                     "experience": {"employment_details": {
                         "current": [{"title": "Head of Security"}]}}}]

    def fake_web(query):
        calls["web"] += 1
        return [{"title": "Seller cloud operations", "url": "https://seller.example/about",
                 "snippet": "Company provides enterprise cloud security monitoring"}]

    def fake_model(instructions, payload):
        calls["llm"] += 1
        if "Plan B2B prospecting for the SELLER" in instructions:
            assert payload["company_enrichment"]["name"] == "Seller Technologies"
            assert payload["web_sources"][0]["url"] == "https://seller.example/about"
            return {
                "query": "Organizations operating enterprise security operations teams",
                "titles": ["Head of Security"],
                "summary": "Seller provides cloud security monitoring",
                "use_cases": ["Monitoring cloud environments"],
                "hypotheses": ["Security teams may seek improved alerting"],
                "search_rationale": "Seller capabilities fit enterprise security operations",
            }
        return {"fit_score": 86, "fit_reason": "IT security company",
                "subject": "Cloud security idea",
                "body": "Hello, discussing enterprise monitoring for your organization."}

    monkeypatch.setattr(agent, "Crustdata", FakeCrustdata)
    monkeypatch.setattr(research, "Crustdata", FakeCrustdata)
    monkeypatch.setattr(research, "web_search", fake_web)
    monkeypatch.setattr(agent, "llm_json", fake_model)
    with SessionLocal() as db:
        c = db.get(Campaign, campaign_id)
        c.status = "running"
        db.commit()
    agent.run_campaign(campaign_id)
    with SessionLocal() as db:
        c = db.get(Campaign, campaign_id)
        assert c.status == "ready", c.error
        assert c.search_query == "Organizations operating enterprise security operations teams"
        lead = db.scalar(select(Lead).where(Lead.campaign_id == campaign_id))
        assert lead.evidence["scorecard"]["version"] == "scorecard-v2"
        assert lead.fit_score is not None and lead.fit_score > 0
        step = db.scalar(select(StepCheckpoint).where(
            StepCheckpoint.campaign_id == campaign_id,
            StepCheckpoint.step_key == "seller_research"))
        assert step.status == "complete"
        data = step.result["value"]
        assert data["summary"] == "Seller provides cloud security monitoring"
        assert data["hypotheses"]
        assert data["sources"][0]["url"] == "https://seller.example/about"
    first = {key: list(value) if isinstance(value, list) else value
             for key, value in calls.items()}
    with SessionLocal() as db:
        db.get(Campaign, campaign_id).status = "running"
        db.commit()
    agent.run_campaign(campaign_id)
    assert calls == first  # no repeated billable requests or Qwen calls
    response = client.get(f"/campaigns/{campaign_id}")
    assert "Seller company research" in response.text
    assert "Seller provides cloud security monitoring" in response.text


def test_rescore_existing_zero_without_provider_requests(client, token):
    campaign_id = make_campaign(client, token, "")
    with SessionLocal() as db:
        lead = Lead(campaign_id=campaign_id, company_name="Legacy",
                    fit_score=0, fit_reason="Old rubric 0",
                    draft_subject="Hello", draft_body="Hello, would you like a product demo?",
                    evidence={"company_record": {"basic_info": {
                        "name": "Legacy", "primary_domain": "legacy.example",
                        "industries": ["Cloud security"]}}},
                    status="draft")
        db.add(lead)
        db.commit()
        lead_id = lead.id
    result = client.post(f"/campaigns/{campaign_id}/rescore", data={
        "csrf": token, "confirm": "RESCORE_SAVED_EVIDENCE"
    }, follow_redirects=False)
    assert result.status_code == 303
    with SessionLocal() as db:
        lead = db.get(Lead, lead_id)
        assert lead.fit_score is not None and lead.fit_score > 0
        assert lead.evidence["previous_score"] == 0
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.campaign_id == campaign_id,
            AuditEvent.action == "saved_evidence_rescored"))
        assert event.details["provider_calls"] == 0
