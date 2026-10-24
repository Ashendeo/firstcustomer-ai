"""Safe Gmail inbox sink for demos, completely separate from live Resend.

Requires personal Gmail 2-Step Verification and an App Password (OAuth is
recommended for production). We deliberately never use a prospect's email
as recipient, and never permit a different recipient than the authenticated
sender. Every outbound SMTP attempt uses a durable, write-ahead checkpoint.
"""
from email.message import EmailMessage
import hashlib
import smtplib
import ssl

from sqlalchemy import select

from . import settings
from .checkpoints import checkpointed
from .db import SessionLocal
from .models import Campaign, Lead, AuditEvent


class GmailDemoConfigError(RuntimeError):
    pass


def gmail_demo_address() -> str:
    if settings.GMAIL_LIVE_ENABLED and settings.GMAIL_ACCOUNT:
        return ""
    sender = settings.GMAIL_DEMO_SENDER
    recipient = settings.GMAIL_DEMO_TO
    if not (sender and sender == recipient and sender.endswith("@gmail.com")
            and settings.GMAIL_DEMO_APP_PASSWORD
            and len(settings.GMAIL_DEMO_APP_PASSWORD) == 16):
        return ""
    return sender


def _smtp_send(subject: str, body: str) -> dict:
    account = gmail_demo_address()
    if not account:
        raise GmailDemoConfigError(
            "Set GMAIL_DEMO_SENDER and GMAIL_DEMO_TO to the same Gmail account "
            "and GMAIL_DEMO_APP_PASSWORD to its 16-character app password"
        )
    message = EmailMessage()
    message["From"] = account
    message["To"] = account
    message["Subject"] = subject[:180]
    message.set_content(body)
    # Gmail SMTP supports SSL port 465. A timeout or disconnect can leave
    # delivery uncertain, so the checkpoint MUST NOT be automatically retried.
    with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                          context=ssl.create_default_context(), timeout=20) as smtp:
        smtp.login(account, settings.GMAIL_DEMO_APP_PASSWORD)
        smtp.send_message(message, from_addr=account, to_addrs=[account])
    return {"transport": "gmail_demo_smtp", "accepted": True}


def send_campaign_demo(campaign_id: str) -> dict:
    if not gmail_demo_address():
        raise GmailDemoConfigError("Gmail sandbox not configured")
    outcome = {"delivered_to_sandbox": 0, "already_checkpointed": 0,
               "failed": 0, "status": "complete"}
    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None or campaign.status != "ready":
            raise GmailDemoConfigError("Complete campaign discovery before a demo send")
        leads = db.scalars(select(Lead).where(
            Lead.campaign_id == campaign_id,
            Lead.draft_body != "",
        ).order_by(Lead.fit_score.desc())).all()
        from .sales_pitch import sales_pitch
        for lead in leads[:10]:  # deliberately bounded
            key = "demo_gmail:" + lead.id
            from .checkpoints import get_checkpoint
            existing = get_checkpoint(db, campaign_id, key)
            if existing and existing.status == "complete":
                outcome["already_checkpointed"] += 1
                continue
            # A changed draft / previous uncertain call cannot silently send again.
            subject = "[FirstCustomer DEMO] " + lead.company_name + " â€” " + lead.draft_subject
            body = ("SANDBOX EMAIL â€” NOT SENT TO ANY PROSPECT\n"
                    "Original target: " + lead.company_name + "\n"
                    "Model score: " + str(lead.fit_score) + "/100\n\n" + sales_pitch(campaign, lead))
            try:
                checkpointed(
                    db, campaign_id, key,
                    {"revision": "gmail-demo-v1", "sender": gmail_demo_address(),
                     "draft_hash": hashlib.sha256(
                         (subject + body).encode("utf-8")).hexdigest()},
                    lambda: _smtp_send(subject, body),
                )
                outcome["delivered_to_sandbox"] += 1
            except Exception as exc:
                # Even SMTP auth errors remain uncertain: no repeated requests
                # unless an operator explicitly authorizes the checkpoint.
                outcome["failed"] += 1
                outcome["status"] = "halted"
                outcome["error"] = type(exc).__name__
                break
        db.add(AuditEvent(campaign_id=campaign_id, action="gmail_demo_completed",
                          details={k: v for k, v in outcome.items() if k != "error"}))
        db.commit()
    return outcome

