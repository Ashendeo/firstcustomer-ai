"""Campaign-level autonomous email outreach with fail-closed safeguards.

The operator authorizes a policy once, not every draft. Live mode is
deliberately inaccessible until configured and explicitly activated.
No LinkedIn automation, follow-up spam, or inferred consent is implemented.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import re
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from . import settings
from .candidate_ranking import provider_rank, OUTREACH_TOP_N, outreach_leads
from .checkpoints import checkpointed, CheckpointNeedsReview
from .crustdata import Crustdata, CrustdataError
from .db import SessionLocal
from .integrations import IntegrationError, send_email
from .models import (Campaign, Lead, OutreachPolicy, OutreachAttempt,
                     Suppression, AuditEvent)


PERSONAL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "icloud.com", "proton.me", "aol.com",
}
UNSAFE_PHRASES = ("guaranteed revenue", "guaranteed results", "we know you need",
                  "we noticed your budget", "urgent payment")


def normalized_email(email: str) -> str:
    try:
        return validate_email(email.strip(), check_deliverability=False).normalized.lower()
    except (EmailNotValidError, ValueError, AttributeError):
        return ""


def get_policy(db, campaign_id: str) -> OutreachPolicy:
    policy = db.get(OutreachPolicy, campaign_id)
    if policy is None:
        policy = OutreachPolicy(
            campaign_id=campaign_id, auto_send=True, min_score=75,
            daily_limit=10, prepare_linkedin_drafts=False,
            mode="simulation", legal_basis="", organization_type="",
            sender_postal_address="", reply_to="",
        )
        db.add(policy)
        db.commit()
    return policy


def jurisdiction_decision(campaign: Campaign, policy: OutreachPolicy) -> str:
    """A conservative screening rule, NOT a legal determination."""
    country = campaign.country.strip().lower()
    consent = policy.legal_basis == "express_consent"
    if country in ("united states", "usa", "us"):
        if not consent and policy.legal_basis != "documented_us_b2b":
            return "us_basis_missing"
    elif country in ("united kingdom", "uk", "great britain", "gb"):
        if not consent and not (
            policy.organization_type == "corporation"
            and policy.legal_basis == "documented_uk_corporate_lia"
        ):
            return "uk_requires_corporate_basis_or_consent"
    elif not consent:
        return "other_country_requires_documented_consent"
    return "allow"


def policy_decision(db, campaign: Campaign, lead: Lead,
                    policy: OutreachPolicy, *, require_email: bool = True) -> str:
    if not policy.auto_send:
        return "auto_send_disabled"
    if policy.prepare_linkedin_drafts:
        return "linkedin_not_supported"
    if lead.source != "crustdata":
        return "not_live_lead"
    rank = provider_rank(lead)
    if rank is None:
        return "missing_search_rank"
    if rank > OUTREACH_TOP_N:
        return "outside_search_top_twenty"
    if lead.status != "draft":
        return "already_processed"
    if not lead.draft_subject or not lead.draft_body:
        return "missing_draft"
    if any(phrase in (lead.draft_subject + lead.draft_body).lower()
           for phrase in UNSAFE_PHRASES):
        return "unverified_outreach_claims"
    country_rule = jurisdiction_decision(campaign, policy)
    if country_rule != "allow":
        return country_rule
    email = normalized_email(lead.email)
    if not email:
        return "missing_verified_business_email" if require_email else "needs_enrichment"
    domain = email.split("@", 1)[1]
    company_domain = (lead.domain or "").lower().removeprefix("www.")
    if (domain in PERSONAL_DOMAINS or not company_domain or
            domain != company_domain):
        return "business_email_domain_mismatch"
    if db.get(Suppression, email) is not None:
        return "globally_suppressed"
    previously = db.scalar(select(OutreachAttempt).where(
        OutreachAttempt.email == email).limit(1))
    if previously or db.get(OutreachAttempt, lead.id):
        return "contacted_or_reserved"
    return "eligible"


def unsubscribe_signature(lead_id: str) -> str:
    return hmac.new(settings.CSRF_SECRET.encode(),
                    ("outreach-unsubscribe-v1:" + lead_id).encode(),
                    hashlib.sha256).hexdigest()


def valid_unsubscribe_signature(lead_id: str, signature: str) -> bool:
    return bool(settings.CSRF_SECRET and
                hmac.compare_digest(unsubscribe_signature(lead_id), signature))


def outreach_footer(lead: Lead, policy: OutreachPolicy) -> str:
    url = settings.PUBLIC_BASE_URL.rstrip("/") + "/unsubscribe/" + lead.id
    url += "/" + unsubscribe_signature(lead.id)
    return ("\n\n---\n"
            "Commercial outreach from " + settings.FROM_EMAIL + ". To opt out of further outreach, visit: " + url
            + "\nPostal address: " + policy.sender_postal_address.strip())


def ready_for_live(policy: OutreachPolicy) -> str:
    from .gmail_demo import gmail_demo_address
    if gmail_demo_address():
        return "gmail_demo_sink_active"
    gmail_live = settings.GMAIL_LIVE_ENABLED and settings.GMAIL_ACCOUNT and settings.GMAIL_APP_PASSWORD
    if not gmail_live and (not settings.RESEND_API_KEY or not settings.FROM_EMAIL):
        return "missing_resend_configuration"
    if not settings.CRUSTDATA_API_KEY:
        return "missing_crustdata_configuration"
    if not gmail_live and not settings.RESEND_WEBHOOK_SECRET:
        return "resend_webhook_secret_required"
    if not settings.CSRF_SECRET or len(settings.CSRF_SECRET) < 16:
        return "missing_signing_secret"
    url = urlsplit(settings.PUBLIC_BASE_URL)
    if url.scheme != "https" or not url.netloc:
        return "public_https_url_required"
    if not normalized_email(policy.reply_to):
        return "reply_to_required"
    if len(policy.sender_postal_address.strip()) < 12:
        return "postal_address_required"
    return "allow"


def preview_outreach(campaign_id: str) -> list[dict]:
    """Pure read-only simulation: NO Crustdata, Qwen or Resend calls."""
    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            return []
        policy = get_policy(db, campaign_id)
        leads = db.scalars(select(Lead).where(
            Lead.campaign_id == campaign_id)).all()
        leads = outreach_leads(leads)
        return [{
            "lead_id": lead.id, "company": lead.company_name,
            "score": lead.fit_score,
            "email": normalized_email(lead.email),
            "decision": policy_decision(db, campaign, lead, policy),
            "mode": "simulation",
        } for lead in leads]


def choose_verified_email(records: list[dict], domain: str) -> str:
    domain = (domain or "").lower().removeprefix("www.")
    for record in records:
        if record.get("status") != "deliverable":
            continue
        email = normalized_email(str(record.get("email") or ""))
        if email and domain and email.split("@", 1)[1] == domain:
            return email
    return ""


def enrich_contact(db, campaign: Campaign, lead: Lead, api) -> str:
    if not lead.person_profile:
        return ""
    # Enrichment is a chargeable provider call, checkpointed before dispatch.
    records = checkpointed(
        db, campaign.id, "contact:" + lead.id,
        {"revision": "work-email-v1", "profile": lead.person_profile,
         "field": "contact.business_emails"},
        lambda: api.business_email_enrich(lead.person_profile),
    )
    email = choose_verified_email(records, lead.domain)
    if email:
        lead.email = email
        db.commit()
    return email


def run_outreach(campaign_id: str) -> dict:
    """Execute eligible first contacts once; called after discovery or manually.

    No retries of unknown send outcomes. The unique (day, slot) reservation
    prevents double booking any of the ten global UTC daily slots.
    """
    result = {"sent": 0, "held": 0, "simulation": False, "reasons": []}
    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        if not campaign or campaign.status != "ready":
            return {**result, "error": "campaign_not_ready"}
        policy = get_policy(db, campaign_id)
        if policy.mode != "live" or not policy.authorized_at:
            return {**result, "simulation": True, "preview": preview_outreach(campaign_id)}
        if not policy.auto_send or ready_for_live(policy) != "allow":
            return {**result, "error": ready_for_live(policy)}
        if jurisdiction_decision(campaign, policy) != "allow":
            return {**result, "error": jurisdiction_decision(campaign, policy)}
        api = Crustdata()
        leads = db.scalars(select(Lead).where(
            Lead.campaign_id == campaign_id)).all()
        leads = outreach_leads(leads)
        day = datetime.now(timezone.utc).date().isoformat()
        for lead in leads:
            # Check the global quota BEFORE potentially paying for an email
            # enrichment that cannot be used today.
            active_slots = set(db.scalars(select(OutreachAttempt.day_slot).where(
                OutreachAttempt.day_utc == day)).all())
            if len(active_slots) >= min(10, policy.daily_limit):
                result["held"] += 1
                result["reasons"].append("daily_cap_reached")
                break
            reason = policy_decision(db, campaign, lead, policy, require_email=False)
            if reason not in ("eligible", "needs_enrichment"):
                result["held"] += 1
                result["reasons"].append(reason)
                continue
            if reason == "needs_enrichment":
                if not lead.person_profile:
                    result["held"] += 1
                    result["reasons"].append("no_profile_for_enrichment")
                    continue
                try:
                    enrich_contact(db, campaign, lead, api)
                except Exception as exc:
                    # Never retry an ambiguous chargeable request automatically.
                    result["held"] += 1
                    result["reasons"].append(type(exc).__name__)
                    if isinstance(exc, (CrustdataError, CheckpointNeedsReview)):
                        # Missing permission/credits likely affects every lead;
                        # don't issue additional provider calls in this run.
                        break
                    continue
            reason = policy_decision(db, campaign, lead, policy)
            if reason != "eligible":
                result["held"] += 1
                result["reasons"].append(reason)
                continue
            taken = set(db.scalars(select(OutreachAttempt.day_slot).where(
                OutreachAttempt.day_utc == day)).all())
            # Strict global cap 10/day even with multiple simultaneous campaigns.
            slot = next((i for i in range(1, min(10, policy.daily_limit) + 1)
                         if i not in taken), None)
            if slot is None:
                result["held"] += 1
                result["reasons"].append("daily_cap_reached")
                break
            recipient = normalized_email(lead.email)
            attempt = OutreachAttempt(
                lead_id=lead.id, campaign_id=campaign.id,
                email=recipient, day_utc=day, day_slot=slot,
                state="reserved", provider_id="",
            )
            db.add(attempt)
            try:
                db.commit()  # persist reservation BEFORE a possible email send
            except IntegrityError:
                db.rollback()
                result["held"] += 1
                result["reasons"].append("concurrent_reservation")
                continue
            try:
                if settings.GMAIL_LIVE_ENABLED:
                    from . import gmail_conversations as gmail
                    conv = gmail.start_conversation(db, lead, sandbox=False, reserved=True)
                    msg = next(m for m in gmail.messages(db, lead.id) if m.kind == "pitch")
                    gmail.dispatch(db, conv, msg)
                    if msg.state != "sent":
                        raise IntegrationError("Gmail first contact held")
                    external_id = msg.message_id
                else:
                    external_id = send_email(
                        recipient, lead.draft_subject,
                        lead.draft_body + outreach_footer(lead, policy),
                        lead.id, reply_to=policy.reply_to,
                    )
            except Exception:
                # Resend may have accepted the request before a timeout. Do not
                # re-send automatically, even using the same idempotency key.
                attempt.state = "uncertain"
                db.commit()
                result["held"] += 1
                result["reasons"].append("send_outcome_uncertain")
                continue
            attempt.provider_id = external_id
            attempt.state = "sent"
            lead.provider_message_id = external_id
            lead.status = "sent"
            db.add(AuditEvent(campaign_id=campaign.id, lead_id=lead.id,
                              action="autonomous_email_sent",
                              details={"provider": "Resend"}))
            db.commit()
            result["sent"] += 1
        db.add(AuditEvent(
            campaign_id=campaign.id, action="outreach_worker_completed",
            details={
                "sent": result["sent"], "held": result["held"],
                "reasons": list(dict.fromkeys(result["reasons"]))[:8],
                "mode": "live",
            },
        ))
        db.commit()
    return result

