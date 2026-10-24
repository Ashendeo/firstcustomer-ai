"""Thin adapter. Neither scraping nor Crustdata credentials are required in demo mode."""
import httpx
from urllib.parse import urlsplit
from . import settings


class CrustdataError(RuntimeError):
    pass


class CrustdataPermissionError(CrustdataError):
    """Definitive provider entitlement denial, not a transient request failure."""
    pass


class Crustdata:
    def __init__(self):
        if not settings.CRUSTDATA_API_KEY:
            raise CrustdataError("CRUSTDATA_API_KEY is required in live mode")
        self.headers = {
            "Authorization": f"Bearer {settings.CRUSTDATA_API_KEY}",
            "x-api-version": "2025-11-01",
        }

    def post(self, path: str, data: dict) -> dict:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post("https://api.crustdata.com" + path, headers=self.headers, json=data)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            # Do not log the authorization header or untrusted response body.
            if exc.response.status_code == 403:
                raise CrustdataPermissionError(
                    f"Crustdata HTTP 403 at {path}: endpoint access not enabled for this API key"
                ) from exc
            raise CrustdataError(f"Crustdata HTTP {exc.response.status_code} at {path}") from exc
        except httpx.RequestError as exc:
            raise CrustdataError(f"Crustdata request to {path} failed") from exc

    @staticmethod
    def company_search_request(query: str, country: str, limit: int,
                               min_revenue_usd: int = 0,
                               max_revenue_usd: int = 0) -> dict:
        """Retrieve candidate firms; apply revenue bounds only after enrichment.

        Company Search does not guarantee an estimated-revenue field across all
        plans, so don't send an unverified filter that might 422. The revenue
        bounds are applied to observed enriched revenue during scoring.
        """
        data = {
            "search": {"query": query[:600], "mode": "hybrid"},
            "fields": ["basic_info.name", "basic_info.primary_domain",
                       "basic_info.description"],
            "limit": min(limit, 100),
        }
        if country:
            data["filters"] = {"field": "locations.country", "type": "=",
                               "value": country}
        return data

    def company_search(self, query: str, country: str, limit: int,
                       min_revenue_usd: int = 0,
                       max_revenue_usd: int = 0) -> list[dict]:
        request = self.company_search_request(query, country, limit,
                                              min_revenue_usd, max_revenue_usd)
        response = self.post("/company/search", request)
        return response.get("companies", [])

    @staticmethod
    def people_search_request(domain: str, titles: list[str], limit: int = 2) -> dict:
        if not domain:
            return {}
        filters = [
            {"field": "experience.employment_details.current.company_website_domain", "type": "=", "value": domain}
        ]
        title_filters = [
            {"field": "experience.employment_details.current.title", "type": "(.)", "value": t}
            for t in titles[:4] if t.strip()
        ]
        if title_filters:
            filters.append({"op": "or", "conditions": title_filters})
        return {
            "filters": {"op": "and", "conditions": filters},
            "fields": ["basic_profile.name", "basic_profile.headline",
                       "experience.employment_details.current.name",
                       "experience.employment_details.current.title",
                       "experience.employment_details.current.company_website",
                       "social_handles.professional_network_identifier.profile_url",
                       "crustdata_person_id"],
            "limit": min(limit, 20),
        }

    def people_search(self, domain: str, titles: list[str], limit: int = 2) -> list[dict]:
        request = self.people_search_request(domain, titles, limit)
        if not request:
            return []
        response = self.post("/person/search", request)
        return response.get("profiles", [])

    @staticmethod
    def people_search_verified_request(domain: str, titles: list[str],
                                       limit: int = 12) -> dict:
        """Use the indexed *current-role* email-verified flag to raise yield.

        This flag is a search hint, not delivery validation: still call
        /person/contact/enrich and require status=deliverable.
        """
        request = Crustdata.people_search_request(domain, titles, limit)
        if not request:
            return {}
        condition = {
            "field": "experience.employment_details.current.business_email_verified",
            "type": "=", "value": True,
        }
        request["filters"]["conditions"].append(condition)
        return request

    def people_search_verified(self, domain: str, titles: list[str],
                               limit: int = 12) -> list[dict]:
        request = self.people_search_verified_request(domain, titles, limit)
        if not request:
            return []
        response = self.post("/person/search", request)
        return response.get("profiles", [])

    def business_email_enrich_batch(self, profile_urls: list[str]) -> dict[str, list[dict]]:
        """Up to 25 official LinkedIn profile identifiers per paid request.

        The response is keyed by 'matched_on', not by input position.
        Missing profiles map to an empty list, never to a guessed address.
        """
        urls = []
        for url in profile_urls:
            parsed = urlsplit(url)
            if (parsed.scheme == "https" and parsed.hostname in
                    ("linkedin.com", "www.linkedin.com") and
                    parsed.path.startswith("/in/")):
                normalized = "https://www.linkedin.com" + parsed.path.rstrip("/")
                if normalized not in urls:
                    urls.append(normalized)
        if not urls:
            return {}
        if len(urls) > 25:
            raise ValueError("Crustdata contact enrichment supports at most 25 identifiers")
        response = self.post("/person/contact/enrich", {
            "professional_network_profile_urls": urls,
            "fields": ["contact.business_emails"],
        })
        if not isinstance(response, list):
            raise CrustdataError("Unexpected batch business-email enrichment response")
        found = {url: [] for url in urls}
        for item in response:
            if not isinstance(item, dict):
                continue
            matched = str(item.get("matched_on") or "")
            parsed = urlsplit(matched)
            identity = ("https://www.linkedin.com" + parsed.path.rstrip("/")
                        if parsed.path.startswith("/in/") else "")
            if identity not in found:
                continue
            for match in item.get("matches") or []:
                emails = ((match.get("person_data") or {}).get("contact") or {}).get(
                    "business_emails") or []
                found[identity].extend(record for record in emails
                                       if isinstance(record, dict))
        return found

    def business_email_enrich(self, profile_url: str) -> list[dict]:
        """Use ONLY the documented contact subfield to minimize Crustdata credits.

        Requires contact-enrich permission; a 403 is surfaced, not silently
        retried or replaced with guessed addresses.
        """
        parsed = urlsplit(profile_url)
        if (parsed.scheme != "https" or parsed.hostname not in
                ("linkedin.com", "www.linkedin.com") or not parsed.path.startswith("/in/")):
            return []
        response = self.post("/person/contact/enrich", {
            "professional_network_profile_urls": [profile_url],
            "fields": ["contact.business_emails"],
        })
        if not isinstance(response, list):
            raise CrustdataError("Crustdata contact enrichment response was not a list")
        matches = (response[0] or {}).get("matches") or [] if response else []
        emails = []
        for match in matches:
            person_data = match.get("person_data") or {}
            emails += ((person_data.get("contact") or {}).get("business_emails") or [])
        return [e for e in emails if isinstance(e, dict)]

    @staticmethod
    def advanced_enrich_fields() -> list[str]:
        """Documented section names; premium technographics is opt-in."""
        fields = ["basic_info", "taxonomy", "headcount", "revenue",
                  "funding", "hiring", "news", "locations", "people"]
        if settings.CRUSTDATA_INCLUDE_TECHNOGRAPHICS:
            fields.append("technographics")
        return fields

    def company_enrich(self, domain: str, *, advanced: bool = False) -> dict:
        """Exact-domain company enrichment, with an optional rich signal bundle."""
        fields = (self.advanced_enrich_fields() if advanced else
                  ["basic_info", "headcount", "locations", "hiring"])
        response = self.post("/company/enrich", {
            "domains": [domain], "exact_match": True, "fields": fields,
        })
        if not isinstance(response, list):
            raise CrustdataError("Unexpected company enrichment response shape")
        matching = (response[0] or {}).get("matches") or [] if response else []
        for match in sorted(matching, key=lambda x: x.get("confidence_score") or 0,
                            reverse=True):
            company = (match or {}).get("company_data") or {}
            found = ((company.get("basic_info") or {}).get("primary_domain") or "").lower()
            if found.removeprefix("www.") == domain.lower().removeprefix("www."):
                return company
        return {}

    def company_enrich_advanced(self, domain: str) -> dict:
        return self.company_enrich(domain, advanced=True)
