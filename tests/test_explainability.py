"""Score provenance, ranking and query trace regression tests; no paid API calls."""
from sqlalchemy import select
from app import settings
from app.db import SessionLocal
from app.models import AuditEvent, Campaign, Lead
from app.scoring import score_company, scorecard_for_display
from app.ranking import ranked_companies
from app.crustdata import Crustdata


def test_scoring_breakdown_sums_exactly_and_cites_observed_fields():
    campaign = Campaign(
        product="Employee sports engagement",
        ideal_customer="Corporate sports organizers",
        buyer_titles="Head of HR",
        min_headcount=100, max_headcount=2000,
    )
    company = {"basic_info": {
        "name": "Acme", "description": "Corporate sports organizers offering events",
        "primary_domain": "acme.example",
    }, "headcount": {"total": 1000}}
    person = {"basic_profile": {"name": "Example"},
              "experience": {"employment_details": {"current": [{"title": "Head of HR"}]}}}
    result = score_company(campaign, company, person)
    assert result["total"] == 100
    assert sum(c["earned"] for c in result["criteria"]) == 100
    assert {c["max"] for c in result["criteria"]} == {40, 20}
    assert all(c["source"] and c["explanation"] for c in result["criteria"])
    poor = score_company(campaign, {"basic_info": {"name": "Unknown"}}, None)
    assert poor["total"] is None
    assert poor["scorable"] is False
    assert poor["gaps"]
    assert poor["evidence_confidence"] == "limited"


def test_historical_model_score_not_relabelled_as_rubric():
    campaign = Campaign(ideal_customer="Corporate sports organizers",
                        buyer_titles="Head of HR", min_headcount=50)
    lead = Lead(company_name="Prior Lead", fit_score=82, evidence={
        "company_record": {"basic_info": {"name": "Prior Lead",
                                         "description": "Sports events"}},
    })
    diagnostic = scorecard_for_display(campaign, lead)
    assert diagnostic["reconstructed"] is True
    assert lead.fit_score == 82
    assert diagnostic["total"] != lead.fit_score


def test_ranking_is_stable_and_identifies_gaps():
    campaign = Campaign(ideal_customer="Corporate sports organizers",
                        buyer_titles="Head of HR")
    leads = [
        Lead(id="bbb", company_name="Beta", fit_score=80, evidence={}),
        Lead(id="aaa", company_name="Alpha", fit_score=90, evidence={}),
    ]
    ranking = ranked_companies(campaign, leads)
    assert [r["rank"] for r in ranking] == [1, 2]
    assert [r["lead"].company_name for r in ranking] == ["Alpha", "Beta"]
    assert all(r["main_gap"] for r in ranking)


def test_discovery_trace_records_actual_request_and_attributes(client, token):
    campaign = client.post("/campaigns", data={
        "csrf": token, "name": "Trace test",
        "product": "Sports video analytics for businesses",
        "ideal_customer": "Corporate sports organizers",
        "country": "India", "buyer_titles": "Head of HR",
        "min_headcount": 100, "max_headcount": 2000,
    }, follow_redirects=False)
    campaign_id = campaign.headers["location"].rsplit("/", 1)[-1]
    assert client.post(f"/campaigns/{campaign_id}/run",
                       data={"csrf": token}).status_code == 200
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(
            AuditEvent.campaign_id == campaign_id,
            AuditEvent.action == "crustdata_company_query",
        )).all()
        assert len(events) == 1
        d = events[0].details
        assert d["request"] == Crustdata.company_search_request(
            "Corporate sports organizers", "India", settings.MAX_COMPANIES,
            100, 2000,
        )
        assert d["result_count"] == 3
        assert d["provider_calls_this_run"] == 0  # demo
        assert "basic_info.name" in d["returned_attributes"]
        people = db.scalars(select(AuditEvent).where(
            AuditEvent.campaign_id == campaign_id,
            AuditEvent.action == "crustdata_people_query",
        )).all()
        assert people
        assert people[0].details["request"]["fields"]
        leads = db.scalars(select(Lead).where(
            Lead.campaign_id == campaign_id,
        )).all()
        assert all(l.evidence.get("scorecard") for l in leads)
    page = client.get(f"/campaigns/{campaign_id}")
    assert page.status_code == 200
    assert "Crustdata execution trace" in page.text
    assert "Company ranking and comparison" in page.text
    lid = client.get(f"/api/campaigns/{campaign_id}").json()["leads"][0]["id"]
    detail = client.get(f"/leads/{lid}")
    assert "Evidence rubric total" in detail.text
    assert "Source:" in detail.text
