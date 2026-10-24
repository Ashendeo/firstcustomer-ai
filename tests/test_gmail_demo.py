"""Private same-account Gmail demo: no lead-address delivery, no blind retry."""
import smtplib
import pytest
from sqlalchemy import select

from app import settings, gmail_demo
from app.db import SessionLocal
from app.models import Campaign, Lead, StepCheckpoint


def create_ready_campaign():
    with SessionLocal() as db:
        campaign = Campaign(
            name="Gmail demo", product="Sports video analytics",
            ideal_customer="Sports tournament organizers",
            country="India", buyer_titles="Founder", status="ready",
        )
        db.add(campaign)
        db.flush()
        lead = Lead(
            campaign_id=campaign.id, company_name="Prospect A",
            domain="prospect.example",
            email="realprospect@prospect.example",
            fit_score=87, draft_subject="Sports pilot",
            draft_body="Hello, this is a demonstration message.",
            status="draft", source="crustdata",
        )
        db.add(lead)
        db.commit()
        return campaign.id


def configure_demo(monkeypatch):
    monkeypatch.setattr(settings, "GMAIL_DEMO_SENDER", "ashendrabansal@gmail.com")
    monkeypatch.setattr(settings, "GMAIL_DEMO_TO", "ashendrabansal@gmail.com")
    monkeypatch.setattr(settings, "GMAIL_DEMO_APP_PASSWORD", "abcdefghijklmnop")


def test_sender_recipient_must_match_even_when_password_present(monkeypatch):
    configure_demo(monkeypatch)
    assert gmail_demo.gmail_demo_address() == "ashendrabansal@gmail.com"
    monkeypatch.setattr(settings, "GMAIL_DEMO_TO", "prospect@example.com")
    assert gmail_demo.gmail_demo_address() == ""
    with pytest.raises(gmail_demo.GmailDemoConfigError):
        gmail_demo._smtp_send("Hi", "Hello")


def test_smtp_auth_and_envelope_only_include_owner(monkeypatch):
    configure_demo(monkeypatch)
    calls = []
    class FakeSMTP:
        def __init__(self, host, port, context, timeout):
            assert (host, port, timeout) == ("smtp.gmail.com", 465, 20)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def login(self, user, password):
            calls.append(("login", user, password))
        def send_message(self, message, from_addr, to_addrs):
            calls.append(("send", from_addr, to_addrs, message["From"],
                          message["To"], message["Subject"], message.get_content()))
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    result = gmail_demo._smtp_send("Demo subject", "Demo body")
    assert result["accepted"] is True
    assert calls[0] == ("login", "ashendrabansal@gmail.com", "abcdefghijklmnop")
    assert calls[1][1:5] == ("ashendrabansal@gmail.com",
                             ["ashendrabansal@gmail.com"],
                             "ashendrabansal@gmail.com", "ashendrabansal@gmail.com")
    assert calls[1][6] == "Demo body\n"


def test_one_demo_email_per_lead_uses_checkpoint_and_no_prospect_recipient(client, token, monkeypatch):
    configure_demo(monkeypatch)
    campaign_id = create_ready_campaign()
    sends = []
    def mocked_smtp(subject, body):
        sends.append((subject, body))
        assert "Prospect A" in subject
        assert "NOT SENT TO ANY PROSPECT" in body
        return {"accepted": True}
    monkeypatch.setattr(gmail_demo, "_smtp_send", mocked_smtp)
    assert gmail_demo.send_campaign_demo(campaign_id)["delivered_to_sandbox"] == 1
    repeat = gmail_demo.send_campaign_demo(campaign_id)
    assert repeat["delivered_to_sandbox"] == 0
    assert repeat["already_checkpointed"] == 1
    assert len(sends) == 1
    with SessionLocal() as db:
        lead = db.scalar(select(Lead).where(Lead.campaign_id == campaign_id))
        assert lead.email == "realprospect@prospect.example"
        assert lead.status == "draft"
        checkpoints = db.scalars(select(StepCheckpoint).where(
            StepCheckpoint.campaign_id == campaign_id,
            StepCheckpoint.step_key.like("demo_gmail:%"),
        )).all()
        assert len(checkpoints) == 1 and checkpoints[0].status == "complete"


def test_ambiguous_smtp_failure_never_auto_retries(client, token, monkeypatch):
    configure_demo(monkeypatch)
    campaign_id = create_ready_campaign()
    calls = []
    def smtp_fails(*args):
        calls.append(True)
        raise OSError("unknown SMTP delivery result")
    monkeypatch.setattr(gmail_demo, "_smtp_send", smtp_fails)
    assert gmail_demo.send_campaign_demo(campaign_id)["status"] == "halted"
    assert gmail_demo.send_campaign_demo(campaign_id)["status"] == "halted"
    assert len(calls) == 1


def test_gmail_sink_blocks_real_resend_and_autonomous_live(client, token, monkeypatch):
    from app.integrations import send_email, IntegrationError
    from app.outreach import ready_for_live, get_policy
    configure_demo(monkeypatch)
    campaign_id = create_ready_campaign()
    with pytest.raises(IntegrationError, match="real Resend delivery is disabled"):
        send_email("realprospect@prospect.example", "Hi",
                   "Should never leave system", "lead-id")
    with SessionLocal() as db:
        policy = get_policy(db, campaign_id)
        assert ready_for_live(policy) == "gmail_demo_sink_active"
        lead = db.scalar(select(Lead).where(Lead.campaign_id == campaign_id))
        lead_id = lead.id
    response = client.post(f"/leads/{lead_id}/send",
                           data={"csrf": token}, follow_redirects=False)
    assert response.status_code == 409
    assert "Gmail demo is active" in response.json()["detail"]
