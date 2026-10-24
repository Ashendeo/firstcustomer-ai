import hashlib
import hmac
import json
import time
from urllib.parse import urlsplit, parse_qs
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Campaign, Lead
from app import settings
from app.agent import run_campaign, safe_booking_link
from app.crustdata import Crustdata


def create(client, token):
    r = client.post("/campaigns", data={
        "csrf": token,
        "name": "Pilot prospects",
        "product": "Video analytics for corporate sports tournaments",
        "ideal_customer": "Mid-sized companies running employee sports leagues",
        "country": "India",
        "buyer_titles": "Head of HR,HR Director",
        "min_headcount": "500", "max_headcount": "5000",
        "booking_url": "https://calendly.com/test-host/demo",
    }, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[-1]


def test_auth_csrf_and_demo_pipeline(client, token):
    assert client.get("/", auth=None).status_code == 401
    assert client.get("/health", auth=None).status_code == 200
    assert client.get("/").status_code == 200
    assert client.post("/campaigns", data={"csrf": "invalid"}).status_code == 422  # required fields
    campaign_id = create(client, token)
    assert client.post(f"/campaigns/{campaign_id}/run", data={"csrf": "wrong"}).status_code == 403
    assert client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token}).status_code == 200
    result = client.get(f"/api/campaigns/{campaign_id}").json()
    assert result["status"] == "ready", result
    assert len(result["leads"]) == 3
    assert all(l["source"] == "demo" for l in result["leads"])
    assert client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token}).status_code == 409
    lead_id = result["leads"][0]["id"]
    lead_page = client.get(f"/leads/{lead_id}")
    assert "fictional" in lead_page.text.lower()
    assert client.post(f"/leads/{lead_id}/send", data={"csrf": token}).status_code == 409
    edit = client.post(f"/leads/{lead_id}/draft", data={
        "csrf": token, "email": "test@example.com", "subject": "Hello team", "body": "Would you be interested in a demo of the product?"
    })
    assert edit.status_code == 200
    with SessionLocal() as db:
        assert db.get(Lead, lead_id).email == "test@example.com"
    approve = client.post(f"/leads/{lead_id}/approve", data={"csrf": token})
    assert approve.status_code == 200
    assert client.post(f"/leads/{lead_id}/send", data={"csrf": token}).status_code == 409
    with SessionLocal() as db:
        assert db.get(Lead, lead_id).status == "approved"


def test_crustdata_request_shapes(monkeypatch):
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "not-real")
    calls = []
    def fake_post(self, path, body):
        calls.append((path, body))
        return {"companies": [], "profiles": []}
    monkeypatch.setattr(Crustdata, "post", fake_post)
    api = Crustdata()
    assert api.company_search("sports programs", "India", 4) == []
    assert api.people_search("example.com", ["Head of HR", "HR Director"]) == []
    assert calls[0][0] == "/company/search"
    assert calls[0][1]["filters"]["field"] == "locations.country"
    assert calls[1][0] == "/person/search"
    assert calls[1][1]["filters"]["conditions"][0]["field"] == "experience.employment_details.current.company_website_domain"
    assert calls[1][1]["filters"]["conditions"][1]["op"] == "or"


def test_qwen_adapter(monkeypatch):
    from app import qwen_runtime
    from app.qwen_runtime import generate_json, _json_object
    assert _json_object('thinking...\\n```json\\n{"score": 5}\\n```') == {"score": 5}
    calls = []
    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        assert url == "http://localhost:11434/api/chat"
        assert json["think"] is False
        assert json["format"] == "json"
        assert json["stream"] is False
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"message": {"content": '{"query":"software firms"}'},
                        "done_reason": "stop", "eval_count": 20}
        return Response()
    monkeypatch.setattr(qwen_runtime.httpx, "post", fake_post)
    assert generate_json("Plan", {"x": 1}) == {"query": "software firms"}
    assert len(calls) == 1  # no hidden Qwen-Agent->Ollama second attempt


def test_booking_signature_and_matching(client, token, monkeypatch):
    campaign_id = create(client, token)
    client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token})
    with SessionLocal() as db:
        lead = db.scalars(select(Lead).where(Lead.campaign_id == campaign_id)).first()
        lead.email, lead.status = "person@example.org", "sent"  # emulate successful outbound for webhook test
        lead_id = lead.id
        db.commit()
    monkeypatch.setattr(settings, "CALENDLY_SIGNING_KEY", "a-strong-test-signing-key")
    payload = {
        "event": "invitee.created",
        "payload": {
            "email": "person@example.org",
            "event": "https://api.calendly.com/scheduled_events/test1",
            "tracking": {"utm_campaign": campaign_id, "utm_content": lead_id},
        },
    }
    raw = json.dumps(payload).encode()
    t = str(int(time.time()))
    sig = hmac.new(settings.CALENDLY_SIGNING_KEY.encode(), t.encode()+b"."+raw, hashlib.sha256).hexdigest()
    headers = {"Calendly-Webhook-Signature": f"t={t},v1={sig}"}
    assert client.post("/webhooks/calendly", content=raw).status_code == 401
    assert client.post("/webhooks/calendly", content=raw, headers=headers).json()["status"] == "booked"
    assert client.post("/webhooks/calendly", content=raw, headers=headers).json()["duplicate"] is True
    with SessionLocal() as db:
        assert db.get(Lead, lead_id).status == "booked"
    older = str(int(time.time())-1000)
    stale = hmac.new(settings.CALENDLY_SIGNING_KEY.encode(), older.encode()+b"."+raw, hashlib.sha256).hexdigest()
    assert client.post("/webhooks/calendly", content=raw, headers={"Calendly-Webhook-Signature": f"t={older},v1={stale}"}).status_code == 401


def test_booking_url_tracking_and_rejection():
    link = safe_booking_link("https://calendly.com/test/intro?x=1", "campaign", "lead")
    q = parse_qs(urlsplit(link).query)
    assert q["utm_content"] == ["lead"]
    assert q["utm_campaign"] == ["campaign"]
    assert safe_booking_link("https://evil.com/pay", "c", "l") == ""


def test_live_orchestrator_with_mocked_providers(client, token, monkeypatch):
    campaign_id = create(client, token)
    import app.agent as agent
    class FakeCrustdata:
        def company_search(self, query, country, limit, min_headcount=0, max_headcount=0):
            assert country == "India"
            assert (min_headcount, max_headcount) == (0, 0)
            return [{"crustdata_company_id": 9, "basic_info": {
                "name": "Sample Business", "primary_domain": "sample.example", "description": "Organizes employee sports events"
            }}]
        def people_search(self, domain, titles, limit=2):
            assert domain == "sample.example"
            return [{"crustdata_person_id": 22, "basic_profile": {"name": "Example Person"},
                     "experience": {"employment_details": {"current": [{"title": "Head of HR"}]}},
                     "social_handles": {"professional_network_identifier": {"profile_url": "https://www.linkedin.com/in/example-person"}}}]
    def fake_contact(_profile_url):
        return [{"email": "buyer@sample.example", "status": "deliverable"}]
    FakeCrustdata.business_email_enrich = lambda self, url: fake_contact(url)
    def fake_llm(instructions, payload):
        if "You plan" in instructions:
            return {"query": "mid-sized employee events tech businesses", "titles": ["Head of HR"]}
        return {"fit_score": 78, "fit_reason": "Company explicitly states it organizes employee sports events.",
                "subject": "Sports analytics pilot", "body": "Hello, would a pilot of our product be relevant to your team?"}
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "MAX_PROSPECT_ENRICHMENTS", 0)
    monkeypatch.setattr(settings, "MAX_CONTACT_ENRICHMENTS", 4)
    monkeypatch.setattr(agent, "Crustdata", FakeCrustdata)
    monkeypatch.setattr(agent, "llm_json", fake_llm)
    with SessionLocal() as db:
        c = db.get(Campaign, campaign_id)
        c.status = "running"
        db.commit()
    run_campaign(campaign_id)
    with SessionLocal() as db:
        c = db.get(Campaign, campaign_id)
        assert c.status == "ready"
        leads = db.scalars(select(Lead).where(Lead.campaign_id == campaign_id)).all()
        assert len(leads) == 1
        assert leads[0].source == "crustdata"
        assert leads[0].person_name == "Example Person"
        assert leads[0].fit_score == leads[0].evidence["scorecard"]["total"]
        assert sum(x["earned"] for x in leads[0].evidence["scorecard"]["criteria"]) == leads[0].fit_score
        assert leads[0].evidence["model_assessment"]["model_score"] == 78
        assert "Why I\'m reaching out to Sample Business" in leads[0].draft_body
        assert "Organizes employee sports events" in leads[0].draft_body
        assert "utm_content=" in leads[0].draft_body  # Prospect can book directly from the first pitch.


def test_qwen_empty_response_is_bounded(monkeypatch):
    """Even an empty model response triggers no implicit retry."""
    import pytest
    from app import qwen_runtime
    from app.qwen_runtime import QwenResponseError, generate_json
    calls = []
    def fake_post(url, json, timeout):
        calls.append(url)
        class Response:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": ""}, "done_reason": "length"}
        return Response()
    monkeypatch.setattr(qwen_runtime.httpx, "post", fake_post)
    with pytest.raises(QwenResponseError, match="empty answer"):
        generate_json("Plan", {"product": "sports"})
    assert len(calls) == 1


def test_no_planning_model_call_by_default(monkeypatch):
    import app.agent as agent
    from app.models import Campaign
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "QWEN_PLAN_WITH_MODEL", False)
    def forbid_model(*args):
        raise AssertionError("Planning should not invoke Qwen by default")
    monkeypatch.setattr(agent, "llm_json", forbid_model)
    campaign = Campaign(product="Sports analysis",
                        ideal_customer="Sports clubs and academies",
                        buyer_titles="Owner,Founder")
    assert agent.make_plan(campaign) == {
        "query": "Sports clubs and academies",
        "titles": ["Owner", "Founder"],
    }


def test_qwen_json_parser_skips_thinking_json():
    from app.qwen_runtime import _json_object
    assert _json_object('<think>{"not_final":1}</think>\n{"final":2}') == {"final": 2}
