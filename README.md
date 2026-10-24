# FirstCustomer AI — FastAPI / Railway / PostgreSQL MVP

A **single FastAPI service** that turns a product description into a researched lead list and human-approved B2B outreach. Uses Crustdata for company/person discovery, Qwen-Agent with an open-weight Qwen model for search planning and grounded fit assessments, Resend for optional *explicitly approved* emails, and Calendly booking links + signed webhooks for confirmed meetings.

**Demo mode is offline and fully fictional.** The app is an early-stage single-operator MVP, not a multi-tenant sales automation product. It does not autonomously negotiate replies, verify business emails, or guarantee meetings.

## Architecture

```text
Browser + HTTP Basic authentication
       |
FastAPI (one Railway web service, one worker)
  |-- Campaign forms / lead approval / admin API
  |-- agent.py (bounded Qwen-Agent planning + evaluating)
  |-- crustdata.py (current Company & People Search REST APIs)
  |-- integrations.py (approved Resend sends; signed Calendly webhook)
  |-- Postgres (campaigns, leads, audit log, webhook deduplication)
  +-- FastAPI BackgroundTasks (in-process; interruptions can be manually recovered)
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

Install [Ollama](https://ollama.com/download/windows), then in PowerShell run:\n\n```powershell\nollama pull qwen3:4b\nollama list\n```\n\nOllama usually serves its OpenAI-compatible API at `http://localhost:11434/v1`. Qwen-Agent is installed from `requirements.txt` and connects to this server. For the first test use `QWEN_MODEL=qwen3:4b` and `QWEN_MODEL_SERVER=http://localhost:11434/v1` in `.env`. Run `alembic upgrade head` and start FastAPI as before. On memory-constrained Windows machines, choose a smaller supported Qwen model and update both Ollama and `.env`. The generated JSON can be imperfect; failures appear in campaign logs rather than inventing lead data.\n\n## Real Crustdata + Qwen-Agent mode

Set:

```dotenv
DEMO_MODE=false
CRUSTDATA_API_KEY=<secret>
QWEN_MODEL=qwen3:4b
QWEN_MODEL_SERVER=http://localhost:11434/v1
MAX_COMPANIES=5
MAX_PEOPLE_SEARCHES=3
```

Run again. Crustdata Company Search returns company records, then Crustdata People Search filters people by current employer domain and title. The LLM constructs a bounded search plan and analyzes supplied records. Optional headcount ranges are enforced as Crustdata structured search filters (premium charges apply). One lead is created per company. A missing decision-maker **stays missing**, rather than being invented. The app deliberately does **not** automatically enrich private emails or guess them from names.

**Important costs:** Crustdata charges credits for returned records and premium field groups. Person Search title and experience fields may be premium. Locally hosted Qwen has no per-token API bill, but consumes local CPU/GPU, RAM and electricity. Hard limits cap records and people searches, but there is no full accounting-budget circuit breaker yet.

## Safe retries and paid-call checkpointing

After updating the repository, run the schema migration **before restarting FastAPI**:

```powershell
cd C:\\Code\\firstcustomer-ai
.\\.venv\\Scripts\\alembic.exe upgrade head
.\\.venv\\Scripts\\python.exe -m pytest -q
.\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --reload
```

The `step_checkpoints` table stores one write-ahead record per metered operation and campaign:
- `plan` — model-generated search query and titles
- `company_search` — entire Crustdata company list
- `people:<hash>` — Crustdata people search per unique company
- `evaluation:<hash>` — model-produced qualification and email draft per lead

Each record stores a hash of exact inputs (including model identifier/prompt revision where relevant), execution state, attempt count, and successful JSON response. The app commits `in_flight` **before** calling an external service and commits the response as `complete` immediately afterward. Completed results are reused on the same campaign and never automatically re-fetched. Existing draft/approved/sent leads are preserved.

**Charge ambiguity is unavoidable without provider-side idempotency or reconciliation.** A timeout, process crash, or failed database commit after the provider processed a request can leave an `in_flight` / `uncertain` checkpoint. The app **will not automatically reissue that call**. In the campaign screen, inspect the checkpoint and explicitly accept possible duplicate charges before authorizing its retry; then click `Retry discovery`. A blocked retry makes no paid calls for the blocked step. A model's internal Ollama fallback can still run more than one local inference *within a single authorized step*.

For jobs left `running` after a server restart, verify the previous worker is stopped before clicking `Recover interrupted run`. This is a **single-worker hackathon design**, not a distributed lease-based scheduler; a robust production deployment requires distributed locks/leases and a durable queue.

Pre-checkpoint campaigns that already have saved work cannot be safely reconstructed in full. Their automatic retry is blocked; create a new campaign rather than silently repeating unknown provider calls.

Checkpoints are per campaign, not across campaigns. They retain raw company/person records, which can contain personal data: secure the database, set a retention policy, and avoid storing data beyond permitted use. Any deliberate refresh or changed query should be treated as a new, potentially billable campaign. The current implementation does not yet estimate provider charges or enforce a monetary budget.

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
