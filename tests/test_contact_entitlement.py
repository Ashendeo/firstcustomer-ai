"""A denied Crustdata contact entitlement must not destroy discovered companies."""
from app import settings
from app.contact_discovery import discover_contact
from app.crustdata import CrustdataPermissionError
from app.db import SessionLocal
from app.models import Campaign, AuditEvent
from sqlalchemy import select


def test_403_stops_contact_calls_and_preserves_ranked_people(client, token, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "CONTACT_LOOKUP_MODE", "contact")
    campaign = Campaign(name="Contact access denied", product="Business software for teams",
                        ideal_customer="B2B companies")
    with SessionLocal() as db:
        db.add(campaign)
        db.commit()
        cid = campaign.id
    person = lambda name: {
        "basic_profile": {"name": name},
        "experience": {"employment_details": {"current": [{"title": "Head of Operations"}]}},
        "social_handles": {"professional_network_identifier": {
            "profile_url": "https://www.linkedin.com/in/" + name.lower()}}
    }
    class DeniedAPI:
        calls = 0
        def business_email_enrich(self, url):
            self.calls += 1
            raise CrustdataPermissionError("403 permission denied")
        def people_search_verified(self, domain, titles, limit=12):
            raise AssertionError("Do not spend more after entitlement denial")
        def people_search(self, domain, titles, limit=12):
            raise AssertionError("Do not spend more after entitlement denial")
    api = DeniedAPI()
    contact_budget, fallback_budget = {"used": 0}, {"used": 0}
    def audit(db, cid, action, details=None):
        db.add(AuditEvent(campaign_id=cid, action=action, details=details or {}))
    with SessionLocal() as db:
        result = discover_contact(
            db, cid, "example.com", "abcdef123", [person("Alice"), person("Bob")],
            {}, api, audit, contact_budget, fallback_budget)
        assert result["person"] is None and not result["email"]
        assert result["reason"] == "contact_api_permission_denied"
        assert result["candidates"]
        assert contact_budget["permission_denied"] is True
        assert api.calls == 1
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "crustdata_contact_access_denied")) is not None
