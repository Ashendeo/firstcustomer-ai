"""One charge-aware Crustdata company name lookup shared across campaign creations.

Never auto-select an ambiguous result. The UI asks the operator to choose the
correct company; Qwen is not invoked during prefill. Prospect targeting is
researched later using the selected company domain and product.
"""
import hashlib
import re

from sqlalchemy.exc import IntegrityError

from . import settings
from .crustdata import Crustdata, CrustdataError
from .market_skill import market_category
from .buyer_persona import infer_buying_committee
from .models import SellerLookup


def normalize_name(text: str) -> str:
    return " ".join((text or "").strip().casefold().split())[:160]


def suggest_fields(row: dict) -> dict:
    info = row.get("basic_info") or {}
    name = str(info.get("name") or "")[:180]
    domain = str(info.get("primary_domain") or "").lower().removeprefix("www.")[:180]
    description = str(info.get("description") or info.get("headline") or "")[:1600]
    if not name or not domain:
        return {}
    class Seller:
        product = description
        ideal_customer = ""
    category = market_category(Seller(), {"company_data": {"description": description}})
    inference = infer_buying_committee(description)
    committee = inference["committee"]
    return {
        "seller_name": name,
        "seller_domain": domain,
        "product": description or f"Products and services offered by {name}; details need verification.",
        "ideal_customer": f"Organizations with a demonstrable need for {description[:190]}" if description else "",
        "buyer_titles": ", ".join(person["role"] for person in committee),
        "market_domain": category,
        "buyer_problem_domain": inference["problem_domain"],
        "buyer_role_confidence": inference["confidence"],
        "buyer_role_evidence": inference["evidence"],
        "buyer_role_reason": inference["reason"],
        "role_hypotheses": committee,
        "limitations": (["Description missing: edit the product and ideal-customer fields"]
                        if not description else [
                            "Initial ideal customers and roles are unverified hypotheses; "
                            "final market skill will research these again"
                        ]),
    }


def lookup_company(db, name: str) -> dict:
    normalized = normalize_name(name)
    if len(normalized) < 3:
        raise ValueError("Enter at least three characters")
    key = hashlib.sha256(("seller-company-lookup-v1|" + normalized).encode()).hexdigest()
    cached = db.get(SellerLookup, key)
    if cached:
        if cached.status == "complete":
            return {"candidates": cached.results, "from_cache": True}
        raise RuntimeError("Company lookup may have been charged already; review the cached request")
    if settings.DEMO_MODE:
        # No paid calls, explicitly synthetic.
        return {"candidates": [], "from_cache": False, "message": "Company lookup requires live Crustdata access"}
    if not settings.CRUSTDATA_API_KEY:
        raise CrustdataError("Configure CRUSTDATA_API_KEY to prefill company details")
    cached = SellerLookup(lookup_key=key, normalized_query=normalized,
                          status="in_flight", results={})
    db.add(cached)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise RuntimeError("Company lookup already running in another session")
    try:
        companies = Crustdata().company_search(
            query=f'Company named {name.strip()[:160]}', country="", limit=5)
        options = []
        seen = set()
        for company in companies[:5]:
            item = suggest_fields(company)
            if item and item["seller_domain"] not in seen:
                seen.add(item["seller_domain"])
                options.append(item)
        cached = db.get(SellerLookup, key)
        cached.results = options
        cached.status = "complete"
        db.commit()
        return {"candidates": options, "from_cache": False}
    except Exception as exc:
        db.rollback()
        cached = db.get(SellerLookup, key)
        cached.status = "uncertain"
        cached.error = type(exc).__name__
        db.commit()
        raise
