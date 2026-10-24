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


def test_company_enrichment_people_provide_verified_contact_without_contact_endpoint(
        client, token, monkeypatch):
    from app.contact_discovery import company_people_profiles
    monkeypatch.setattr(settings, "CONTACT_LOOKUP_MODE", "search")
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    enriched = {
        "basic_info": {"name": "Example Inc", "primary_domain": "example.com"},
        "people": {"decision_makers": [{
            "name": "Jane Buyer", "title": "Head of Procurement",
            "contact": {"business_emails": [
                {"email": "jane@example.com", "status": "deliverable"}
            ]}
        }]}
    }
    profiles = company_people_profiles(enriched, "example.com")
    assert len(profiles) == 1
    class SearchOnly:
        def people_search_verified(self, *args, **kwargs):
            raise AssertionError("Should not call people search")
    with SessionLocal() as db:
        campaign = Campaign(name="Company-first contacts", product="Procurement suite",
                            ideal_customer="Enterprise procurement")
        db.add(campaign)
        db.commit()
        result = discover_contact(db, campaign.id, "example.com", "uniqueidentity",
                                  profiles, {}, SearchOnly(), lambda *args, **kwargs: None,
                                  {"used": 0}, {"used": 0})
        assert result["email"] == "jane@example.com"
        assert result["person"] is not None


def test_company_contacts_with_no_verification_are_not_leads(client, token, monkeypatch):
    from app.contact_discovery import company_people_profiles
    from app.contact_discovery import verified_business_email_from_search
    data = {"people": {"founders": [{
        "name": "Person Without Email", "title": "Founder",
        "contact": {"business_emails": [
            {"email": "founder@example.com", "status": "unknown"}
        ]}
    }]}}
    people = company_people_profiles(data, "example.com")
    assert len(people) == 1
    assert not verified_business_email_from_search(people[0], "example.com")
