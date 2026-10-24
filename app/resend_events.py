"""Verified, idempotent Resend delivery and inbound-email processing."""
import base64
import hashlib
import hmac
import json
import re
import time
from email.utils import parseaddr

import httpx
from sqlalchemy import select

from . import settings
from .models import (Lead, OutreachAttempt, Suppression,
                     ProcessedResendEvent, InboundReply)


def verify_signature(payload: bytes, headers) -> bool:
    """Svix standard signature used by Resend; validate raw body and timestamp."""
    secret = settings.RESEND_WEBHOOK_SECRET
    event_id = headers.get("svix-id", "")
    timestamp = headers.get("svix-timestamp", "")
    supplied = headers.get("svix-signature", "")
    if not (secret.startswith("whsec_") and event_id and timestamp and supplied):
        return False
    try:
        if abs(int(time.time()) - int(timestamp)) > 300:
            return False
        secret_bytes = base64.b64decode(secret[6:])
        data = event_id.encode() + b"." + timestamp.encode() + b"." + payload
        expected = base64.b64encode(hmac.new(secret_bytes, data, hashlib.sha256).digest()).decode()
        return any(
            hmac.compare_digest(part[3:], expected)
            for part in supplied.split()
            if part.startswith("v1,")
        )
    except (ValueError, TypeError):
        return False


def normalize_sender(sender: str) -> str:
    return parseaddr(sender)[1].strip().lower()


def suppress(db, email: str, reason: str):
    value = normalize_sender(email)
    if not value or "@" not in value:
        return
    if not db.get(Suppression, value):
        db.add(Suppression(email=value, reason=reason))


def classify_reply(body: str) -> str:
    """Conservative deterministic triage; never send an automatic reply."""
    normalized = re.sub(r"\s+", " ", body.lower())[:2000]
    if any(term in normalized for term in (
        "unsubscribe", "remove me", "stop emailing", "do not contact",
        "don't email me", "opt out", "not interested, stop",
    )):
        return "opt_out"
    if any(term in normalized for term in (
        "interested", "book a demo", "schedule a call", "send more information",
        "let's talk", "lets talk", "can we meet",
    )):
        return "interested"
    return "needs_review"


def get_inbound_email(email_id: str) -> dict:
    """Webhook carries metadata only; retrieve full body with signed API key."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,150}", email_id):
        raise ValueError("Unexpected Resend inbound email id")
    response = httpx.get(
        "https://api.resend.com/emails/receiving/" + email_id,
        headers={"Authorization": "Bearer " + settings.RESEND_API_KEY},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def handle_resend_event(db, raw: bytes, headers) -> dict:
    if not verify_signature(raw, headers):
        return {"status": "invalid_signature"}
    event_id = headers["svix-id"]
    if db.get(ProcessedResendEvent, event_id):
        return {"status": "duplicate"}
    event = json.loads(raw)
    kind = str(event.get("type") or "")
    data = event.get("data") or {}
    email_id = str(data.get("email_id") or data.get("id") or "")
    recipient_list = data.get("to") or []
    recipient = normalize_sender(recipient_list[0]) if recipient_list else ""
    # Delivery updates only if matching actual provider ID and recipient.
    attempt = db.scalar(select(OutreachAttempt).where(
        OutreachAttempt.provider_id == email_id,
        OutreachAttempt.email == recipient,
    )) if email_id and recipient else None

    if kind in ("email.delivered", "email.bounced", "email.complained",
                "email.failed", "email.suppressed") and attempt:
        if kind == "email.delivered":
            attempt.state = "delivered"
        elif kind in ("email.bounced", "email.complained", "email.suppressed"):
            attempt.state = kind.rsplit(".", 1)[-1]
            suppress(db, recipient, attempt.state)
        elif kind == "email.failed":
            attempt.state = "failed"
    elif kind in ("contact.updated", "contact.unsubscribed"):
        if data.get("unsubscribed") is True:
            suppress(db, str(data.get("email") or ""), "unsubscribe")
    elif kind == "email.received":
        # Safe matching: only already-contacted exact senders; never treat
        # arbitrary emails as permission to contact someone else.
        inbound = get_inbound_email(email_id)
        sender = normalize_sender(str(inbound.get("from") or data.get("from") or ""))
        record = db.scalar(select(OutreachAttempt).where(
            OutreachAttempt.email == sender,
            OutreachAttempt.state.in_(("sent", "delivered")),
        ).order_by(OutreachAttempt.created_at.desc())) if sender else None
        if record is not None:
            body = str(inbound.get("text") or "")[:8000]
            outcome = classify_reply(body)
            if not db.get(InboundReply, email_id):
                db.add(InboundReply(
                    message_id=email_id, lead_id=record.lead_id,
                    sender=sender, classification=outcome,
                    excerpt=body[:350],
                ))
            lead = db.get(Lead, record.lead_id)
            if outcome == "opt_out":
                suppress(db, sender, "reply_opt_out")
                if lead:
                    lead.status = "unsubscribed"
            elif lead:
                lead.status = "replied"
    db.add(ProcessedResendEvent(event_id=event_id, event_type=kind))
    db.commit()
    return {"status": "processed", "event": kind}
