"""FirstCustomer AI: single-process FastAPI MVP with human-approved outreach."""
import hashlib
import hmac
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import settings
from .agent import audit, run_campaign
from .db import get_db
from .integrations import IntegrationError, send_email, verify_calendly_signature
from .models import AuditEvent, Campaign, Lead, ProcessedWebhook, StepCheckpoint
from .checkpoints import get_checkpoint

logger = logging.getLogger(__name__)

app = FastAPI(title="FirstCustomer AI", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
security = HTTPBasic()
root = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(root / "static")), name="static")
templates = Jinja2Templates(directory=str(root / "templates"))


def authorize(creds: HTTPBasicCredentials = Depends(security)):
    if not settings.ADMIN_PASSWORD or len(settings.ADMIN_PASSWORD) < 12:
        raise HTTPException(503, "Configure a strong ADMIN_PASSWORD")
    correct = (hmac.compare_digest(creds.username, settings.ADMIN_USERNAME)
               and hmac.compare_digest(creds.password, settings.ADMIN_PASSWORD))
    if not correct:
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic realm=FirstCustomer"})
    return creds.username


def csrf_token() -> str:
    if not settings.CSRF_SECRET or len(settings.CSRF_SECRET) < 16:
        raise HTTPException(503, "Configure CSRF_SECRET (at least 16 characters)")
    return hmac.new(settings.CSRF_SECRET.encode(), b"firstcustomer-forms-v1", hashlib.sha256).hexdigest()


def check_csrf(token: str):
    if not hmac.compare_digest(token, csrf_token()):
        raise HTTPException(403, "Invalid CSRF token")


def page(request: Request, name: str, **kwargs):
    return templates.TemplateResponse(request=request, name=name, context={
        "csrf": csrf_token(), "demo": settings.DEMO_MODE,
        "has_resend": bool(settings.RESEND_API_KEY and settings.FROM_EMAIL),
        **kwargs,
    })


def redirect(location: str):
    return RedirectResponse(location, status_code=303)


def get_campaign(db: Session, campaign_id: str):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return campaign


def get_lead(db: Session, lead_id: str):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except Exception:
        return JSONResponse({"status": "unhealthy", "db": "disconnected"}, status_code=503)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db), _: str = Depends(authorize)):
    campaigns = db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()
    return page(request, "index.html", campaigns=campaigns)


@app.post("/campaigns")
def create_campaign(
    name: str = Form(min_length=3, max_length=140),
    product: str = Form(min_length=10, max_length=2000),
    ideal_customer: str = Form(min_length=10, max_length=2000),
    country: str = Form(default="India", max_length=60),
    buyer_titles: str = Form(default="Head of HR,HR Director", max_length=250),
    min_headcount: int = Form(default=0, ge=0, le=10000000),
    max_headcount: int = Form(default=0, ge=0, le=10000000),
    booking_url: str = Form(default="", max_length=500),
    csrf: str = Form(...),
    db: Session = Depends(get_db),
    _: str = Depends(authorize),
):
    check_csrf(csrf)
    if booking_url:
        u = urlsplit(booking_url)
        if u.scheme != "https" or not (u.hostname == "calendly.com" or (u.hostname or "").endswith(".calendly.com")):
            raise HTTPException(422, "Booking link must be a valid HTTPS Calendly URL")
    if min_headcount and max_headcount and min_headcount > max_headcount:
        raise HTTPException(422, "Minimum headcount cannot exceed maximum")
    c = Campaign(name=name.strip(), product=product.strip(), ideal_customer=ideal_customer.strip(),
                 country=country.strip(), buyer_titles=buyer_titles.strip(),
                 min_headcount=min_headcount, max_headcount=max_headcount, booking_url=booking_url.strip())
    db.add(c)
    db.flush()
    audit(db, c.id, "campaign_created", details={"demo": settings.DEMO_MODE})
    db.commit()
    return redirect(f"/campaigns/{c.id}")


@app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(campaign_id: str, request: Request, db: Session = Depends(get_db), _: str = Depends(authorize)):
    campaign = get_campaign(db, campaign_id)
    leads = db.scalars(select(Lead).where(Lead.campaign_id == campaign.id).order_by(Lead.fit_score.desc())).all()
    counts = {s: sum(l.status == s for l in leads) for s in ("draft", "approved", "sending", "sent", "booked", "canceled")}
    events = db.scalars(select(AuditEvent).where(AuditEvent.campaign_id == campaign.id).order_by(AuditEvent.created_at.desc()).limit(20)).all()
    checkpoints = db.scalars(select(StepCheckpoint).where(StepCheckpoint.campaign_id == campaign.id).order_by(StepCheckpoint.created_at)).all()
    return page(request, "campaign.html", campaign=campaign, leads=leads, counts=counts, events=events, checkpoints=checkpoints)


@app.post("/campaigns/{campaign_id}/run")
def run(campaign_id: str, tasks: BackgroundTasks, csrf: str = Form(...), db: Session = Depends(get_db), _: str = Depends(authorize)):
    check_csrf(csrf)
    c = get_campaign(db, campaign_id)
    if c.status not in ("draft", "failed"):
        raise HTTPException(409, "Campaign has already been started")
    if not settings.DEMO_MODE and (not settings.CRUSTDATA_API_KEY or not settings.QWEN_MODEL or not settings.QWEN_MODEL_SERVER):
        raise HTTPException(422, "Live mode requires CRUSTDATA_API_KEY and QWEN_MODEL/QWEN_MODEL_SERVER")
    # Older campaigns could have made paid calls before checkpoints existed.
    # Do not silently repeat those legacy calls: records are insufficient to
    # reconstruct every intermediate response or original Qwen prompt.
    if c.status == "failed":
        has_checkpoint = db.scalar(select(StepCheckpoint.id).where(
            StepCheckpoint.campaign_id == c.id
        ).limit(1))
        has_old_leads = db.scalar(select(Lead.id).where(
            Lead.campaign_id == c.id
        ).limit(1))
        if not has_checkpoint and (c.search_query or has_old_leads):
            raise HTTPException(
                409,
                "Legacy campaign has paid work but no checkpoints; cannot safely "
                "retry. Start a new campaign instead of accidentally refetching."
            )
    # Preserve all existing leads and completed metered-call checkpoints.\n    # Each pending step is evaluated independently by the worker.\n    c.status, c.error = "running", ""
    audit(db, c.id, "campaign_started")
    db.commit()
    tasks.add_task(run_campaign, c.id)
    return redirect(f"/campaigns/{c.id}")


@app.post("/campaigns/{campaign_id}/checkpoints/retry")
def authorize_checkpoint_retry(
    campaign_id: str, step_key: str = Form(...),
    confirm: str = Form(...), csrf: str = Form(...), db: Session = Depends(get_db),
    _: str = Depends(authorize),
):
    """Manual, potentially chargeable replay of ONE ambiguous provider call."""
    check_csrf(csrf)
    campaign = get_campaign(db, campaign_id)
    if confirm != "POSSIBLE_DUPLICATE_CHARGE":
        raise HTTPException(422, "Confirm that replaying this call might be billed again")
    if campaign.status != "failed":
        raise HTTPException(409, "Stop the running campaign before authorizing a replay")
    step = get_checkpoint(db, campaign_id, step_key)
    if step is None:
        raise HTTPException(404, "Checkpoint not found")
    if step.status not in ("uncertain", "in_flight"):
        raise HTTPException(409, "Only uncertain/in-flight calls can be authorized for replay")
    step.status = "retry_authorized"
    audit(db, campaign_id, "checkpoint_replay_authorized",
          details={"step": step_key, "previous_attempts": step.attempts})
    db.commit()
    return redirect(f"/campaigns/{campaign_id}")


@app.post("/campaigns/{campaign_id}/recover-interrupted")
def recover_interrupted(
    campaign_id: str, confirm: str = Form(...),
    csrf: str = Form(...), db: Session = Depends(get_db),
    _: str = Depends(authorize),
):
    """Operator-only recovery after confirming the old process has stopped."""
    check_csrf(csrf)
    if confirm != "PROCESS_STOPPED":
        raise HTTPException(422, "Confirm that the previous worker has stopped")
    campaign = get_campaign(db, campaign_id)
    if campaign.status != "running":
        raise HTTPException(409, "Campaign is not marked running")
    campaign.status = "failed"
    campaign.error = ("Interrupted run marked failed by operator. "
                      "Review uncertain/in-flight checkpoints before resuming.")
    audit(db, campaign_id, "interrupted_run_recovered")
    db.commit()
    return redirect(f"/campaigns/{campaign_id}")


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(lead_id: str, request: Request, db: Session = Depends(get_db), _: str = Depends(authorize)):
    lead = get_lead(db, lead_id)
    return page(request, "lead.html", lead=lead, campaign=lead.campaign)


@app.post("/leads/{lead_id}/draft")
def edit_draft(lead_id: str, csrf: str = Form(...), email: str = Form(default="", max_length=254),
               subject: str = Form(min_length=3, max_length=160), body: str = Form(min_length=20, max_length=3000),
               db: Session = Depends(get_db), _: str = Depends(authorize)):
    check_csrf(csrf)
    lead = get_lead(db, lead_id)
    if lead.status not in ("draft", "approved"):
        raise HTTPException(409, "Cannot modify sent or booked messages")
    if email.strip():
        try:
            email = validate_email(email.strip(), check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise HTTPException(422, f"Invalid recipient email: {exc}") from exc
    lead.email = email.strip()
    lead.draft_subject = subject.strip()
    lead.draft_body = body.strip()
    lead.status = "draft"  # editing revokes any previously granted approval
    audit(db, lead.campaign_id, "outreach_edited", lead.id)
    db.commit()
    return redirect(f"/leads/{lead.id}")


@app.post("/leads/{lead_id}/approve")
def approve(lead_id: str, csrf: str = Form(...), db: Session = Depends(get_db), _: str = Depends(authorize)):
    check_csrf(csrf)
    lead = get_lead(db, lead_id)
    if lead.status != "draft":
        raise HTTPException(409, "Only drafts may be approved")
    if not lead.draft_subject or not lead.draft_body:
        raise HTTPException(422, "Missing draft")
    lead.status = "approved"
    audit(db, lead.campaign_id, "outreach_approved", lead.id)
    db.commit()
    return redirect(f"/leads/{lead.id}")


@app.post("/leads/{lead_id}/send")
def send(lead_id: str, csrf: str = Form(...), db: Session = Depends(get_db), _: str = Depends(authorize)):
    check_csrf(csrf)
    # Acquire row lock on PostgreSQL so repeated clicks cannot send concurrently.
    lead = db.scalars(select(Lead).where(Lead.id == lead_id).with_for_update()).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    if lead.status != "approved":
        raise HTTPException(409, "Approve the latest draft before sending")
    if lead.source == "demo" or settings.DEMO_MODE:
        raise HTTPException(409, "Cannot send outreach from fictional demo records")
    if not lead.email:
        raise HTTPException(422, "Enter and verify a recipient business email before sending")
    if not settings.RESEND_API_KEY or not settings.FROM_EMAIL:
        raise HTTPException(422, "Set Resend sender credentials before sending")
    lead.status = "sending"
    db.commit()
    try:
        external_id = send_email(lead.email, lead.draft_subject, lead.draft_body, lead.id)
    except IntegrationError as exc:
        lead.status = "approved"  # Resend idempotency key prevents duplicate provider send on retry
        audit(db, lead.campaign_id, "outreach_send_failed", lead.id)
        db.commit()
        raise HTTPException(502, str(exc)) from exc
    lead.provider_message_id = external_id
    lead.status = "sent"
    audit(db, lead.campaign_id, "outreach_sent", lead.id, {"provider_message_id": external_id})
    db.commit()
    return redirect(f"/leads/{lead.id}")


@app.get("/api/campaigns/{campaign_id}")
def campaign_json(campaign_id: str, db: Session = Depends(get_db), _: str = Depends(authorize)):
    c = get_campaign(db, campaign_id)
    leads = db.scalars(select(Lead).where(Lead.campaign_id == c.id)).all()
    checkpoints = db.scalars(select(StepCheckpoint).where(StepCheckpoint.campaign_id == c.id)).all()
    return {
        "id": c.id, "status": c.status, "error": c.error, "search_query": c.search_query,
        "checkpoints": [{"step": s.step_key, "status": s.status, "attempts": s.attempts} for s in checkpoints],
        "leads": [{"id": l.id, "company": l.company_name, "domain": l.domain,
                   "person": l.person_name, "score": l.fit_score, "status": l.status,
                   "source": l.source} for l in leads],
    }


@app.post("/webhooks/calendly")
async def calendly_webhook(request: Request, db: Session = Depends(get_db)):
    """Public callback. Signed with the Calendly webhook key and scoped to a known sent lead."""
    raw = await request.body()
    signature = request.headers.get("Calendly-Webhook-Signature", "")
    if not verify_calendly_signature(raw, signature):
        raise HTTPException(401, "Invalid Calendly signature")
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    action = event.get("event")
    if action not in ("invitee.created", "invitee.canceled"):
        return {"ok": True, "ignored": True}
    payload = event.get("payload") or {}
    tracking = payload.get("tracking") or {}
    lead_id = tracking.get("utm_content")
    campaign_id = tracking.get("utm_campaign")
    if not isinstance(lead_id, str) or not isinstance(campaign_id, str):
        return {"ok": True, "matched": False}
    lead = db.get(Lead, lead_id)
    invitee_email = str(payload.get("email") or "").strip().lower()
    if not (lead and lead.campaign_id == campaign_id and lead.email.lower() == invitee_email
            and lead.status in ("sent", "booked", "canceled")):
        return {"ok": True, "matched": False}
    event_key = hashlib.sha256(raw).hexdigest()  # idempotent for retries
    if db.get(ProcessedWebhook, event_key):
        return {"ok": True, "duplicate": True}
    if action == "invitee.created":
        lead.status = "booked"
        lead.booked_at = datetime.now(timezone.utc)
        lead.booking_event_uri = str(payload.get("event") or "")[:1500]
    elif action == "invitee.canceled" and lead.status == "booked":
        lead.status = "canceled"
    audit(db, lead.campaign_id, action, lead.id, {"event_uri": lead.booking_event_uri})
    db.add(ProcessedWebhook(event_key=event_key))
    db.commit()
    return {"ok": True, "matched": True, "status": lead.status}


@app.middleware("http")
async def secure_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response
