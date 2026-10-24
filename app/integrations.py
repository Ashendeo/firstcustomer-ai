"""Email and inbound Calendly webhook verification."""
import hashlib
import hmac
import time
import httpx
from . import settings


class IntegrationError(RuntimeError):
    pass


def send_email(to: str, subject: str, body: str, lead_id: str, *, reply_to: str = "") -> str:
    from .gmail_demo import gmail_demo_address
    if gmail_demo_address():
        raise IntegrationError("Gmail demo sink active; real Resend delivery is disabled")
    if settings.DEMO_MODE:
        raise IntegrationError("Sending disabled in DEMO_MODE")
    if not settings.RESEND_API_KEY or not settings.FROM_EMAIL:
        raise IntegrationError("Set RESEND_API_KEY and FROM_EMAIL")
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}",
                     "Idempotency-Key": "lead-outreach-v1-" + lead_id},
            json={**{"from": settings.FROM_EMAIL, "to": [to], "subject": subject, "text": body},
                  **({"reply_to": reply_to} if reply_to else {})},
            timeout=20,
        )
        response.raise_for_status()
        provider_id = str(response.json().get("id") or "")
        if not provider_id:
            raise IntegrationError("Email service accepted request without a provider ID; send outcome requires reconciliation")
        return provider_id
    except httpx.HTTPStatusError as exc:
        raise IntegrationError(f"Email service returned HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise IntegrationError("Email service unavailable") from exc


def verify_calendly_signature(raw: bytes, signature: str) -> bool:
    if not settings.CALENDLY_SIGNING_KEY or not signature:
        return False
    try:
        pairs = dict(part.split("=", 1) for part in signature.split(",") if "=" in part)
        timestamp_str, supplied = pairs["t"], pairs["v1"]
        timestamp = int(timestamp_str)
        if abs(int(time.time()) - timestamp) > 180:
            return False
        expected = hmac.new(
            settings.CALENDLY_SIGNING_KEY.encode(),
            timestamp_str.encode() + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, supplied)
    except (ValueError, KeyError, TypeError):
        return False
