# FirstCustomer AI — FastAPI / Railway / PostgreSQL MVP

A **single FastAPI service** that turns a product description into a researched lead list and human-approved B2B outreach. Uses Crustdata for company/person discovery, a local open-weight Qwen model served by Ollama for search planning and grounded fit assessments, Resend for optional *explicitly approved* emails, and Calendly booking links + signed webhooks for confirmed meetings.

**Demo mode is offline and fully fictional.** The app is an early-stage single-operator MVP, not a multi-tenant sales automation product. It does not autonomously negotiate replies, verify business emails, or guarantee meetings.

## Architecture

```text
Browser + HTTP Basic authentication
       |
FastAPI (one Railway web service, one worker)
  |-- Campaign forms / lead approval / admin API
  |-- agent.py (bounded bounded Ollama JSON evaluation (optional planning))
  |-- crustdata.py (current Company & People Search REST APIs)
  |-- integrations.py (approved Resend sends; signed Calendly webhook)
  |-- Postgres (campaigns, leads, audit log, webhook deduplication)
  +-- FastAPI BackgroundTasks (in-process; restart interrupts job)
```

## Quick start (no external credentials)

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Set ADMIN_PASSWORD to a fresh random string >= 12 chars and
# CSRF_SECRET to a different random string >= 16 chars.
alembic upgrade head
uvicorn app.main:app --reload
```

Open **http://localhost:8000** and sign in via the browser HTTP Basic dialog with `ADMIN_USERNAME` and `ADMIN_PASSWORD`. Create a campaign, click **Run discovery agent**, review fictional leads, edit/approve email drafts. Sending is intentionally disabled in demo mode.

Run automated tests:

```bash
pip install pytest
python -m pytest -q
```

## Local Qwen model setup on Windows

Install [Ollama](https://ollama.com/download/windows), then in PowerShell run:\n\n```powershell\nollama pull qwen3:4b\nollama list\n```\n\nOllama usually serves its OpenAI-compatible API at `http://localhost:11434/v1`. The app uses httpx to call Ollama's native structured JSON API directly. Each evaluation makes exactly one request with `think=false` and `format=json`, and a configured timeout. Qwen-Agent is not required for these simple structured tasks. For the first test use `QWEN_MODEL=qwen3:4b` and `QWEN_MODEL_SERVER=http://localhost:11434/v1` in `.env`. Run `alembic upgrade head` and start FastAPI as before. On memory-constrained Windows machines, choose a smaller supported Qwen model and update both Ollama and `.env`. If Ollama cannot return valid JSON, the step fails clearly without initiating a second model call. Qwen-based query planning is disabled by default.\n\n## Real Crustdata + Qwen-Agent mode

Set:

```dotenv
DEMO_MODE=false
CRUSTDATA_API_KEY=<secret>
QWEN_MODEL=qwen3:4b
QWEN_MODEL_SERVER=http://localhost:11434/v1
QWEN_TIMEOUT_SECONDS=120
QWEN_NUM_PREDICT=900
QWEN_PLAN_WITH_MODEL=false
MAX_COMPANIES=5
MAX_PEOPLE_SEARCHES=3
```

Run again. Crustdata Company Search returns company records, then Crustdata People Search filters people by current employer domain and title. The LLM constructs a bounded search plan and analyzes supplied records. Optional headcount ranges are enforced as Crustdata structured search filters (premium charges apply). One lead is created per company. A missing decision-maker **stays missing**, rather than being invented. The app deliberately does **not** automatically enrich private emails or guess them from names.

**Important costs:** Crustdata charges credits for returned records and premium field groups. Person Search title and experience fields may be premium. Locally hosted Qwen has no per-token API bill, but consumes local CPU/GPU, RAM and electricity. Hard limits cap records and people searches, but there is no full accounting-budget circuit breaker yet.

## Qwen troubleshooting: repeated fallback messages / slow inference

Earlier versions called Qwen-Agent first and then retried via Ollama, potentially doing two generations per lead. The new runtime uses **one** native Ollama request per lead, disables thinking explicitly, and limits request time. Crustdata company search runs directly from your ideal-customer description without an initial model request.

On Windows, stop the old FastAPI process (Ctrl+C), install updated source, then check Ollama:

```powershell
ollama ps
ollama list
```

Restart FastAPI with `MAX_COMPANIES=1` and `MAX_PEOPLE_SEARCHES=1` for the first live test. Adjust `QWEN_TIMEOUT_SECONDS` if CPU inference is slow, or use a smaller model. Be careful retrying old uncheckpointed campaigns: Crustdata searches can be charged twice. Merge the separate durable-checkpointing PR before repeatedly rerunning existing campaigns.

Independent model test:

```powershell
$body = @{model="qwen3:4b";stream=$false;think=$false;format="json";messages=@(@{role="user";content='Return JSON with key "ok" and value true.'})} | ConvertTo-Json -Depth 10
(Invoke-RestMethod -Uri "http://localhost:11434/api/chat" -Method Post -ContentType "application/json" -Body $body).message.content
```

## Optional email sending

Configure a domain with [Resend](https://resend.com/docs) and set:

```dotenv
RESEND_API_KEY=<secret>
FROM_EMAIL=Your Team <hello@yourverifieddomain.com>
```

In **live mode only**, open a lead, supply a **verified, permitted business email address**, review/edit the message, click **Save** then **Approve** then **Send**. Every send is a separate manual action. The app never sends automatically from discovery, and *never* sends messages to demo leads. It uses a stable Resend idempotency key per lead. The provider send ID is persisted in PostgreSQL. Respect local anti-spam requirements and opt-outs; do not cold-email anyone without a lawful basis.

## Optional Calendly meeting tracking

1. Create a Calendly event type and put its HTTPS URL in the campaign form, e.g. `https://calendly.com/your-handle/15min`.
2. Generated outreach receives a UTM-tagged booking URL with `utm_campaign=<campaign UUID>` and `utm_content=<lead UUID>`.
3. Configure a **Calendly webhook subscription** to `https://YOUR-RAILWAY-DOMAIN/webhooks/calendly`, for `invitee.created` and `invitee.canceled`, using a webhook signing key. Calendly currently requires a paid Standard, Teams or Enterprise plan for webhooks.
4. Set `CALENDLY_SIGNING_KEY` to the same secret in your Railway service's variables.
5. An incoming event must pass the Calendly HMAC signature check (including 180-second replay window), identify a lead by tracking IDs, and match its **exact emailed recipient**. Only previously sent leads are eligible for booked status; duplicate webhooks are ignored.

The current event log stores the scheduled Calendly event URI, but does not fetch event time or manage reschedules as a full workflow. Booking status means Calendly reported a reservation linked to the contacted recipient, not that a sales conversation took place.

## Deploy on Railway

1. Push the folder to a **private** GitHub repository (do not commit `.env`).
2. At [Railway](https://railway.com), **New Project** → deploy the GitHub repository. The root `Dockerfile` starts the app and runs `alembic upgrade head`.
3. Add a PostgreSQL service in the same Railway project.
4. In the FastAPI service **Variables** tab configure:

   ```text
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   DEMO_MODE=true
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=<fresh long random secret>
   CSRF_SECRET=<another fresh random secret>
   PUBLIC_BASE_URL=https://YOUR-RAILWAY-DOMAIN
   ```

   Change `Postgres` if your database service has a different name. The app converts Railway's Postgres URL to the psycopg 3 driver URI.
5. Under the FastAPI service **Settings → Networking**, click **Generate Domain**. `GET /health` is its configured health check and queries PostgreSQL.
6. Visit the generated HTTPS URL. For live mode, set `DEMO_MODE=false`, add your Crustdata key and configure a reachable Qwen model server; deploy again. **Railway does not share your Windows localhost**. Run Ollama on an authenticated private model-serving service and set `QWEN_MODEL_SERVER` to its reachable private address. Do not expose unauthenticated Ollama directly to the internet.
7. Do not use `--workers > 1` or >1 replicated container with the in-process job engine. Prefer a dedicated durable queue (Cloud Tasks, Dramatiq, or Temporal) before scaling horizontally.

*Railway deploy credentials are not present in this repository, and no deployment is performed automatically.* Hosting, database, API and email expenses are separate.

## Source layout

```text
app/main.py            Routes, approval policy, signed webhook, HTTP Basic + CSRF
app/agent.py           Bounded campaign orchestrator + evidence-based AI assessment
app/crustdata.py       Current versioned Crustdata search adapters
app/integrations.py    Resend email sender, Calendly webhook signature verification
app/models.py          Campaign, Lead, AuditEvent, ProcessedWebhook
app/templates/         Responsive browser UI
alembic/versions/      Initial schema migration
Dockerfile             Railway-compatible container
railway.json           Docker build & /health check
```

## Security / known gaps

- HTTP Basic with TLS is acceptable for **single-operator hackathon testing**; implement proper login/session management for multi-user use. The app rejects weak/missing admin passwords, requires a CSRF token on forms, and locks down the default OpenAPI pages.
- Never publish raw private records or use untrusted web text as executable agent instructions; AI output is advisory and editable. The evidence view is behind admin authentication.
- Do not expose this public service in live mode before configuring strong credentials. Add request rate limiting, retention/deletion handling, hardened CSP, row-level tenancy boundaries, and a privacy review for production.
- `BackgroundTasks` is **not a durable queue**. A Railway restart during discovery can leave status `running`; restoring interrupted jobs needs a dedicated task runner or reset procedure. For the demo, keep the result limit small.
- No automated reply monitoring or negotiation, no calendar availability check, no business-email enrichment, and no autonomous sending. Those belong in a later iteration.
- One-row-per-company prospect design; a real CRM needs multiple contacts and detailed sales engagement histories.

## Primary API docs

- [Crustdata Company Search](https://docs.crustdata.com/company-docs/search/introduction)
- [Crustdata People Search](https://docs.crustdata.com/person-docs/search/introduction)
- [Calendly webhook signatures](https://developer.calendly.com/api-docs/overview/webhooks/webhook-signatures)
- [Railway FastAPI deployment](https://docs.railway.com/guides/fastapi)
- [Railway PostgreSQL](https://docs.railway.com/databases/postgresql)
- [Resend email API](https://resend.com/docs/api-reference/emails/send-email)
