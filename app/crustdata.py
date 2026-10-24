"""Thin adapter. Neither scraping nor Crustdata credentials are required in demo mode."""
import httpx
from . import settings


class CrustdataError(RuntimeError):
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
            raise CrustdataError(f"Crustdata HTTP {exc.response.status_code} at {path}") from exc
        except httpx.RequestError as exc:
            raise CrustdataError(f"Crustdata request to {path} failed") from exc

    def company_search(self, query: str, country: str, limit: int, min_headcount: int = 0, max_headcount: int = 0) -> list[dict]:
        data = {
            "search": {"query": query[:600], "mode": "hybrid"},
            "fields": ["basic_info.name", "basic_info.primary_domain", "basic_info.description", "headcount.total"],
            "limit": min(limit, 10),
        }
        conditions = []
        if country:
            conditions.append({"field": "locations.country", "type": "=", "value": country})
        if min_headcount:
            conditions.append({"field": "headcount.total", "type": "=>", "value": min_headcount})
        if max_headcount:
            conditions.append({"field": "headcount.total", "type": "=<", "value": max_headcount})
        if len(conditions) == 1:
            data["filters"] = conditions[0]
        elif conditions:
            data["filters"] = {"op": "and", "conditions": conditions}
        response = self.post("/company/search", data)
        return response.get("companies", [])

    def people_search(self, domain: str, titles: list[str], limit: int = 2) -> list[dict]:
        if not domain or not titles:
            return []
        filters = [
            {"field": "experience.employment_details.current.company_website_domain", "type": "=", "value": domain}
        ]
        title_filters = [
            {"field": "experience.employment_details.current.title", "type": "(.)", "value": t}
            for t in titles[:4] if t.strip()
        ]
        if title_filters:
            filters.append({"op": "or", "conditions": title_filters})
        response = self.post("/person/search", {
            "filters": {"op": "and", "conditions": filters},
            "fields": ["basic_profile.name", "basic_profile.headline",
                       "experience.employment_details.current.name",
                       "experience.employment_details.current.title",
                       "social_handles.professional_network_identifier.profile_url"],
            "limit": min(limit, 2),
        })
        return response.get("profiles", [])
