import base64
import hashlib
import hmac
import json
import time
from sqlalchemy import select

from app import settings, outreach
from app.db import SessionLocal
from app.models import (Campaign, Lead, OutreachPolicy, Suppression,
                        OutreachAttempt, InboundReply, ProcessedResendEvent)
from app.resend_events import handle_resend_event, classify_reply


def campaign_with_leads(number=1, country="US", *, include_email=True):
    with SessionLocal() as db:
        campaign = Campaign(
            name="Autonomy policy test", product="Technology for sports companies",
            ideal_customer="Corporate sports organizers", country=country,
            buyer_titles="CTO", status="ready", booking_url="",
        )
        db.add(campaign)
        db.flush()
        for n in range(number):
            db.add(Lead(
                campaign_id=campaign.id, company_name=f"Club {n}",
                domain=f"club{n}.example", person_name="Example Person",
                person_profile="https://www.linkedin.com/in/example-person",
                email=f"contact@club{n}.example" if include_email else "",
                fit_score=90, fit_reason="Evidence based",
                draft_subject="Sports analytics pilot",
                draft_body="Hello, would you be interested in a sports technology pilot?",
                source="crustdata", status="draft",
            ))
        db.commit()
        return campaign.id


def configure_live(campaign_id, monkeypatch, *, country="US"):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-only")
    monkeypatch.setattr(settings, "CRUSTDATA_API_KEY", "fake-for-tests")
    monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", "whsec_" + base64.b64encode(b"webhook-testing-secret").decode())
    monkeypatch.setattr(settings, "FROM_EMAIL", "sender@mydomain.example")
    with SessionLocal() as db:
        policy = outreach.get_policy(db, campaign_id)
        policy.mode = "live"
        policy.auto_send = True
        policy.min_score = 85
        policy.daily_limit = 10
        policy.legal_basis = "documented_us_b2b" if country == "US" else "express_consent"
        policy.organization_type = "corporation"
        policy.reply_to = "reply@mydomain.example"
        policy.sender_postal_address = "123 Sample Street, Example City, USA"
        from datetime import datetime, timezone
        policy.authorized_at = datetime.now(timezone.utc)
        db.commit()


def test_default_policy_is_simulation_even_with_autosend_true(client):
    campaign_id = campaign_with_leads()
    preview = outreach.preview_outreach(campaign_id)
    assert len(preview) == 1
    assert preview[0]["mode"] == "simulation"
    with SessionLocal() as db:
        policy = outreach.get_policy(db, campaign_id)
        assert policy.auto_send is True
        assert policy.min_score == 85
        assert policy.daily_limit == 10
        assert policy.prepare_linkedin_drafts is False
        assert policy.mode == "simulation"


def test_jurisdiction_fail_closed_and_sender_preconditions(client, monkeypatch):
    campaign_id = campaign_with_leads(country="UK")
    with SessionLocal() as db:
        policy = outreach.get_policy(db, campaign_id)
        campaign = db.get(Campaign, campaign_id)
        assert outreach.jurisdiction_decision(campaign, policy) != "allow"
        policy.legal_basis = "documented_uk_corporate_lia"
        policy.organization_type = "individual"
        assert outreach.jurisdiction_decision(campaign, policy) != "allow"
        policy.organization_type = "corporation"
        assert outreach.jurisdiction_decision(campaign, policy) == "allow"
        assert outreach.ready_for_live(policy) != "allow"


def test_live_worker_enriches_once_and_never_re_sends(client, monkeypatch):
    campaign_id = campaign_with_leads(include_email=False)
    configure_live(campaign_id, monkeypatch)
    counters = {"contact": 0, "send": 0}

    class FakeCrustdata:
        def business_email_enrich(self, profile):
            counters["contact"] += 1
            return [{"email": "contact@club0.example", "status": "deliverable"},
                    {"email": "other@gmail.com", "status": "deliverable"}]

    monkeypatch.setattr(outreach, "Crustdata", FakeCrustdata)
    def fake_send(to, subject, body, lead_id, *, reply_to):
        counters["send"] += 1
        assert to == "contact@club0.example"
        assert "unsubscribe/" in body
        assert "123 Sample Street" in body
        assert reply_to == "reply@mydomain.example"
        return "sent-test-id"
    monkeypatch.setattr(outreach, "send_email", fake_send)
    first = outreach.run_outreach(campaign_id)
    second = outreach.run_outreach(campaign_id)
    assert first["sent"] == 1
    assert second["sent"] == 0
    assert counters == {"contact": 1, "send": 1}
    with SessionLocal() as db:
        assert db.scalar(select(OutreachAttempt)).state == "sent"
        assert db.scalar(select(Lead)).status == "sent"


def test_global_ten_per_day_cap_and_suppression(client, monkeypatch):
    campaign_id = campaign_with_leads(number=12)
    configure_live(campaign_id, monkeypatch)
    class FakeCrustdata: pass
    monkeypatch.setattr(outreach, "Crustdata", FakeCrustdata)
    sent = []
    monkeypatch.setattr(outreach, "send_email", lambda to, subject, body, lead_id, *, reply_to: sent.append(to) or f"provider-{len(sent)}")
    with SessionLocal() as db:
        db.add(Suppression(email="contact@club0.example", reason="optout"))
        db.commit()
    result = outreach.run_outreach(campaign_id)
    assert result["sent"] == 10
    assert len(sent) == 10
    assert "contact@club0.example" not in sent
    with SessionLocal() as db:
        slots = db.scalars(select(OutreachAttempt.day_slot)).all()
        assert sorted(slots) == list(range(1, 11))


def test_live_activation_requires_operator_and_real_config(client, token, monkeypatch):
    campaign_id = campaign_with_leads()
    page = client.get(f"/campaigns/{campaign_id}")
    assert page.status_code == 200
    policy = client.post(f"/campaigns/{campaign_id}/outreach/policy", data={
        "csrf": token, "auto_send": "true", "min_score": "85", "daily_limit": "10",
        "legal_basis": "documented_us_b2b", "organization_type": "corporation",
        "sender_postal_address": "123 Sample Street, Example City, USA",
        "reply_to": "reply@mydomain.example",
    }, follow_redirects=False)
    assert policy.status_code == 303
    refused = client.post(f"/campaigns/{campaign_id}/outreach/activate", data={
        "csrf": token, "confirm": "I_AUTHORIZE_LIVE_OUTREACH"
    }, follow_redirects=False)
    assert refused.status_code == 422
    configure_live(campaign_id, monkeypatch)
    with SessionLocal() as db:
        p = db.get(OutreachPolicy, campaign_id)
        p.mode = "simulation"
        p.authorized_at = None
        db.commit()
    # Activation automatically kicks off eligible outreach for ready campaigns;
    # suppress external calls in this integration test.
    sent = []
    monkeypatch.setattr(outreach, "send_email", lambda to, subject, body, lead_id, *, reply_to: sent.append(to) or "activation-provider")
    allowed = client.post(f"/campaigns/{campaign_id}/outreach/activate", data={
        "csrf": token, "confirm": "I_AUTHORIZE_LIVE_OUTREACH",
    }, follow_redirects=False)
    assert allowed.status_code == 303
    assert len(sent) == 1


def _sign(payload, secret, event_id):
    timestamp = str(int(time.time()))
    mac = hmac.new(secret, (event_id + "." + timestamp + ".").encode() + payload,
                   hashlib.sha256).digest()
    return {"svix-id": event_id, "svix-timestamp": timestamp,
            "svix-signature": "v1," + base64.b64encode(mac).decode()}


def test_signed_delivery_and_inbound_optout(client, token, monkeypatch):
    campaign_id = campaign_with_leads()
    configure_live(campaign_id, monkeypatch)
    with SessionLocal() as db:
        lead = db.scalar(select(Lead))
        lead.status = "sent"
        db.add(OutreachAttempt(lead_id=lead.id, campaign_id=campaign_id,
                               email=lead.email, day_utc="2026-10-24",
                               day_slot=1, state="sent", provider_id="provider-xyz"))
        db.commit()
    secret = base64.b64decode(settings.RESEND_WEBHOOK_SECRET[6:])
    bounce = json.dumps({"type": "email.bounced", "data": {
        "email_id": "provider-xyz", "to": ["contact@club0.example"],
    }}).encode()
    signed = _sign(bounce, secret, "delivery-001")
    assert client.post("/webhooks/resend", content=bounce).status_code == 401
    ok = client.post("/webhooks/resend", content=bounce, headers=signed)
    assert ok.status_code == 200 and ok.json()["status"] == "processed"
    assert client.post("/webhooks/resend", content=bounce, headers=signed).json()["status"] == "duplicate"
    with SessionLocal() as db:
        assert db.get(Suppression, "contact@club0.example").reason == "bounced"

    monkeypatch.setattr("app.resend_events.get_inbound_email", lambda _: {
        "from": "Example Person <contact@club0.example>",
        "text": "Please unsubscribe and do not contact us.",
    })
    inbound = json.dumps({"type": "email.received", "data": {
        "email_id": "inbound-msg-12345", "from": "contact@club0.example"
    }}).encode()
    result = client.post("/webhooks/resend", content=inbound,
                         headers=_sign(inbound, secret, "incoming-001"))
    assert result.status_code == 200
    with SessionLocal() as db:
        reply = db.get(InboundReply, "inbound-msg-12345")
        # Bounced contacts are not considered active deliveries, so inbound
        # events do not falsely identify a reply as part of this campaign.
        assert reply is None


def test_inbound_classification():
    assert classify_reply("Please remove me from your list") == "opt_out"
    assert classify_reply("Let's talk next week") == "interested"
    assert classify_reply("Thanks for the note") == "needs_review"


def test_scheduler_requires_secret_and_ignores_simulation(client, monkeypatch):
    campaign_id = campaign_with_leads()
    monkeypatch.setattr(settings, "OUTREACH_CRON_SECRET", "a-very-long-test-cron-secret-123")
    assert client.post("/outreach/tick").status_code == 401
    good = client.post("/outreach/tick", headers={
        "x-outreach-cron-secret": "a-very-long-test-cron-secret-123"
    })
    assert good.status_code == 200
    assert good.json() == {"status": "scheduled", "campaigns": 0}
    with SessionLocal() as db:
        assert db.get(OutreachPolicy, campaign_id) is None


def test_inbound_opt_out_suppresses_and_unsubscribe_requires_post(client, monkeypatch):
    campaign_id = campaign_with_leads()
    configure_live(campaign_id, monkeypatch)
    with SessionLocal() as db:
        lead = db.scalar(select(Lead))
        lead.status = "sent"
        lead_id = lead.id
        db.add(OutreachAttempt(
            lead_id=lead.id, campaign_id=campaign_id,
            email=lead.email, day_utc="2026-10-24", day_slot=1,
            state="sent", provider_id="test-sent-provider",
        ))
        db.commit()
    from app.outreach import unsubscribe_signature
    token = unsubscribe_signature(lead_id)
    assert client.get(f"/unsubscribe/{lead_id}/{token}").status_code == 200
    with SessionLocal() as db:
        assert db.get(Suppression, "contact@club0.example") is None
    monkeypatch.setattr("app.resend_events.get_inbound_email", lambda _: {
        "from": "Example Person <contact@club0.example>",
        "text": "Please unsubscribe and stop emailing me."
    })
    raw = json.dumps({"type": "email.received", "data": {
        "email_id": "received-optout-001", "from": "contact@club0.example"
    }}).encode()
    secret = base64.b64decode(settings.RESEND_WEBHOOK_SECRET[6:])
    response = client.post("/webhooks/resend", content=raw,
                           headers=_sign(raw, secret, "incoming-optout-001"))
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(Suppression, "contact@club0.example").reason == "reply_opt_out"
        assert db.get(InboundReply, "received-optout-001").classification == "opt_out"
        assert db.get(Lead, lead_id).status == "unsubscribed"
    assert client.post(f"/unsubscribe/{lead_id}/{token}").status_code == 200
