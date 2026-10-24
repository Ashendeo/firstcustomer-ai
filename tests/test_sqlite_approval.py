"""Regression coverage for SQLITE_BUSY during human draft approval."""
import sqlite3

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import SessionLocal, engine
from app.models import AuditEvent, Lead


def make_draft(client, token):
    response = client.post("/campaigns", data={
        "csrf": token,
        "name": "Approval lock regression",
        "product": "B2B sports analysis platform",
        "ideal_customer": "Organizations running amateur tournaments",
        "country": "India",
        "buyer_titles": "Head of HR",
    }, follow_redirects=False)
    assert response.status_code == 303
    campaign_id = response.headers["location"].rsplit("/", 1)[-1]
    result = client.post(f"/campaigns/{campaign_id}/run",
                         data={"csrf": token})
    assert result.status_code == 200
    lead_id = client.get(f"/api/campaigns/{campaign_id}").json()["leads"][0]["id"]
    return lead_id


def test_sqlite_wal_allows_approval_while_reader_is_open(client, token):
    """A long-lived dashboard reader must not block a human approval write."""
    lead_id = make_draft(client, token)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 10000

    reader = sqlite3.connect(engine.url.database, timeout=1)
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM leads").fetchone()[0] >= 1
        result = client.post(f"/leads/{lead_id}/approve",
                             data={"csrf": token}, follow_redirects=False)
        assert result.status_code == 303
        with SessionLocal() as db:
            assert db.get(Lead, lead_id).status == "approved"
    finally:
        reader.rollback()
        reader.close()


def test_approval_recovers_from_one_transient_lock(client, token, monkeypatch):
    """After a failed flush, the session rolls back and safely retries."""
    lead_id = make_draft(client, token)
    original_commit = Session.commit
    attempts = []

    def sometimes_locked(session):
        if (any(isinstance(row, Lead) and row.status == "approved"
                for row in session.dirty) and not attempts):
            attempts.append("busy")
            raise OperationalError("INSERT INTO audit_events", {},
                                   sqlite3.OperationalError("database is locked"))
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", sometimes_locked)
    result = client.post(f"/leads/{lead_id}/approve",
                         data={"csrf": token}, follow_redirects=False)
    assert result.status_code == 303
    assert attempts == ["busy"]

    with SessionLocal() as db:
        assert db.get(Lead, lead_id).status == "approved"
        events = db.scalars(select(AuditEvent).where(
            AuditEvent.lead_id == lead_id, AuditEvent.action == "outreach_approved"
        )).all()
        assert len(events) == 1

    repeated = client.post(f"/leads/{lead_id}/approve",
                           data={"csrf": token}, follow_redirects=False)
    assert repeated.status_code == 303
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(
            AuditEvent.lead_id == lead_id, AuditEvent.action == "outreach_approved"
        )).all()
        assert len(events) == 1


def test_persistent_sqlite_lock_returns_actionable_error(client, token, monkeypatch):
    """Never expose a raw 500 traceback or claim an approval succeeded."""
    lead_id = make_draft(client, token)
    original_commit = Session.commit
    attempts = []

    def always_locked(session):
        if any(isinstance(row, Lead) and row.status == "approved"
               for row in session.dirty):
            attempts.append(1)
            raise OperationalError("INSERT INTO audit_events", {},
                                   sqlite3.OperationalError("database is locked"))
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", always_locked)
    result = client.post(f"/leads/{lead_id}/approve",
                         data={"csrf": token}, follow_redirects=False)
    assert result.status_code == 503
    assert "database is busy" in result.json()["detail"].lower()
    assert result.headers.get("retry-after") == "2"
    assert len(attempts) == 3

    with SessionLocal() as db:
        assert db.get(Lead, lead_id).status == "draft"
        assert not db.scalars(select(AuditEvent).where(
            AuditEvent.lead_id == lead_id, AuditEvent.action == "outreach_approved"
        )).all()
