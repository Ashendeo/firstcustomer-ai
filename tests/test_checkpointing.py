"""Replay safety: completed paid work is reused, ambiguous work is gated."""
from sqlalchemy import select

from app import settings
from app import agent
from app.db import SessionLocal
from app.models import Campaign, Lead, StepCheckpoint


def create_campaign(client, token):
    r = client.post("/campaigns", data={
        "csrf": token,
        "name": "Retry-safe lead prospecting",
        "product": "Video analytics for football leagues",
        "ideal_customer": "Companies organizing employee sports tournaments",
        "country": "India",
        "buyer_titles": "HR Director",
    }, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].split("/")[-1]


def test_partial_failure_reuses_successful_crustdata_and_model_calls(client, token, monkeypatch):
    campaign_id = create_campaign(client, token)
    monkeypatch.setattr(settings, "QWEN_PLAN_WITH_MODEL", True)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "test-only-key")
    monkeypatch.setattr(settings, "MAX_COMPANIES", 2)
    monkeypatch.setattr(settings, "MAX_PEOPLE_SEARCHES", 2)
    monkeypatch.setattr(settings, "MAX_PROSPECT_ENRICHMENTS", 0)
    monkeypatch.setattr(settings, "MAX_CONTACT_ENRICHMENTS", 4)
    calls = {"company": 0, "people": 0, "email": 0, "plan": 0, "evaluate": 0}

    class FakeCrustdata:
        def company_search(self, query, country, limit, min_headcount=0, max_headcount=0):
            calls["company"] += 1
            return [
                {"crustdata_company_id": str(i), "basic_info": {
                    "name": f"Company {i}", "primary_domain": f"company{i}.example",
                    "description": "Runs an employee sports league",
                }}
                for i in range(2)
            ]

        def people_search(self, domain, titles, limit=2):
            calls["people"] += 1
            return [{"crustdata_person_id": domain, "basic_profile": {"name": "Verified Buyer"},
                     "experience": {"employment_details": {
                         "current": [{"title": "HR Director"}]}},
                     "social_handles": {"professional_network_identifier": {
                         "profile_url": "https://www.linkedin.com/in/" + domain.split(".")[0],
                     }}}]

        def business_email_enrich(self, profile_url):
            calls["email"] += 1
            domain = profile_url.rsplit("/", 1)[-1] + ".example"
            return [{"email": "buyer@" + domain, "status": "deliverable"}]

    def fake_model(instructions, payload):
        if "You plan" in instructions:
            calls["plan"] += 1
            return {"query": "corporate sports organizers", "titles": ["HR Director"]}
        calls["evaluate"] += 1
        if calls["evaluate"] == 2:
            raise RuntimeError("Qwen parse failed after inference")
        return {
            "fit_score": 75, "fit_reason": "Sports events explicitly mentioned",
            "subject": "Pilot invitation",
            "body": "Hello, we would like to discuss an optional sports technology pilot.",
        }

    monkeypatch.setattr(agent, "Crustdata", FakeCrustdata)
    monkeypatch.setattr(agent, "llm_json", fake_model)

    client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token})
    with SessionLocal() as db:
        c = db.get(Campaign, campaign_id)
        leads = db.scalars(select(Lead).where(Lead.campaign_id == campaign_id)).all()
        uncertain = db.scalar(select(StepCheckpoint).where(
            StepCheckpoint.campaign_id == campaign_id,
            StepCheckpoint.status == "uncertain",
        ))
        assert c.status == "failed"
        assert len(leads) == 2
        first_lead_id = next(l.id for l in leads if l.draft_body)
        assert uncertain is not None
        step_key = uncertain.step_key

    before = dict(calls)
    client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token})
    assert calls == before  # no accidental second charge
    with SessionLocal() as db:
        assert db.get(Campaign, campaign_id).status == "failed"

    approved = client.post(
        f"/campaigns/{campaign_id}/checkpoints/retry",
        data={"csrf": token, "step_key": step_key, "confirm": "POSSIBLE_DUPLICATE_CHARGE"},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token})

    assert calls == {"company": 1, "people": 2, "email": 2, "plan": 1, "evaluate": 3}
    with SessionLocal() as db:
        assert db.get(Campaign, campaign_id).status == "ready"
        leads = db.scalars(select(Lead).where(Lead.campaign_id == campaign_id)).all()
        assert len(leads) == 2
        assert any(l.id == first_lead_id for l in leads)
        assert all(l.draft_body for l in leads)
        cp = db.scalar(select(StepCheckpoint).where(
            StepCheckpoint.campaign_id == campaign_id,
            StepCheckpoint.step_key == step_key,
        ))
        assert cp.status == "complete" and cp.attempts == 2


def test_ambiguous_company_search_not_auto_replayed(client, token, monkeypatch):
    campaign_id = create_campaign(client, token)
    monkeypatch.setattr(settings, "QWEN_PLAN_WITH_MODEL", True)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "test-only-key")
    n = {"search": 0, "model": 0}

    class FakeCrustdata:
        def company_search(self, *args):
            n["search"] += 1
            if n["search"] == 1:
                raise TimeoutError("Provider may have processed the request")
            return []
    def fake_model(*args):
        n["model"] += 1
        return {"query": "sports companies", "titles": ["HR Director"]}

    monkeypatch.setattr(agent, "Crustdata", FakeCrustdata)
    monkeypatch.setattr(agent, "llm_json", fake_model)
    client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token})
    assert n == {"search": 1, "model": 1}
    client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token})
    assert n == {"search": 1, "model": 1}

    client.post(
        f"/campaigns/{campaign_id}/checkpoints/retry",
        data={"csrf": token, "step_key": "company_search", "confirm": "POSSIBLE_DUPLICATE_CHARGE"},
    )
    client.post(f"/campaigns/{campaign_id}/run", data={"csrf": token})
    assert n == {"search": 2, "model": 1}
    with SessionLocal() as db:
        assert db.get(Campaign, campaign_id).status == "ready"


def test_completed_checkpoint_is_reused_and_in_flight_requires_review(client, token):
    from app.checkpoints import checkpointed, CheckpointNeedsReview
    from app.models import StepCheckpoint
    import pytest
    campaign_id = create_campaign(client, token)
    with SessionLocal() as db:
        value = checkpointed(db, campaign_id, "external:a",
                             {"query": "fixed"}, lambda: {"companies": ["A"]})
        assert value == {"companies": ["A"]}
    with SessionLocal() as db:
        assert checkpointed(db, campaign_id, "external:a", {"query": "fixed"},
                            lambda: pytest.fail("Billed operation rerun")) == value
        with pytest.raises(CheckpointNeedsReview):
            checkpointed(db, campaign_id, "external:a", {"query": "changed"},
                         lambda: pytest.fail("Changed input must not run"))
        # Emulate a worker crash after starting the request, before saving its response.
        step = StepCheckpoint(campaign_id=campaign_id, step_key="external:b",
                              input_hash=__import__("app.checkpoints", fromlist=["fingerprint"]).fingerprint({"v": 1}),
                              status="in_flight", attempts=1)
        db.add(step)
        db.commit()
    with SessionLocal() as db:
        with pytest.raises(CheckpointNeedsReview):
            checkpointed(db, campaign_id, "external:b", {"v": 1},
                         lambda: pytest.fail("Crash replayed costly operation"))
