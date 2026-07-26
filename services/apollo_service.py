# services/apollo_service.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import requests

APOLLO_BASE = "https://api.apollo.io/api/v1"


def _headers(api_key: str) -> dict:
    return {"X-Api-Key": api_key, "Content-Type": "application/json"}


def _safe_post(url: str, api_key: str, payload: dict, timeout: int = 30) -> dict:
    r = requests.post(url, json=payload, headers=_headers(api_key), timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_company_size(api_key: str, domain: str) -> str | None:
    url = f"{APOLLO_BASE}/organizations/search"
    payload = {"q_organization_domains_list": [domain], "page": 1, "per_page": 1}
    data = _safe_post(url, api_key, payload)
    orgs = data.get("organizations") or []
    if not orgs:
        return None
    org = orgs[0]

    # Apollo often returns numeric employee_count / estimated_num_employees
    for key in ["employee_count", "estimated_num_employees", "organization_num_employees"]:
        if org.get(key):
            return str(org.get(key))
    if org.get("organization_num_employees_range"):
        return str(org.get("organization_num_employees_range"))
    return None


def _people_api_search(api_key: str, domain: str, per_page: int = 25) -> List[Dict[str, Any]]:
    """
    People API Search (API optimized) - does NOT return email by design, last name may be withheld.
    """
    url = f"{APOLLO_BASE}/mixed_people/api_search"
    payload = {
        "q_organization_domains_list": [domain],
        "page": 1,
        "per_page": min(max(per_page, 1), 50),
        "person_seniorities": ["owner", "founder", "c_suite", "vp", "head", "director", "manager"],
        "person_titles": [
            "marketing", "growth", "demand generation", "performance marketing",
            "paid media", "digital marketing", "content marketing",
            "revenue", "sales", "partnerships", "business development"
        ],
        "include_similar_titles": True,
        # ask for people who have emails if possible
        "contact_email_status": ["verified", "unverified", "likely to engage"],
    }

    data = _safe_post(url, api_key, payload)
    people = data.get("people") or data.get("contacts") or data.get("results") or []
    return people if isinstance(people, list) else []


def _extract_email(enriched: Dict[str, Any]) -> Optional[str]:
    if not isinstance(enriched, dict):
        return None

    # common places
    if enriched.get("email"):
        return enriched["email"]

    for k in ["emails", "personal_emails"]:
        v = enriched.get(k)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.strip():
                    return item.strip()

    person = enriched.get("person")
    if isinstance(person, dict):
        if person.get("email"):
            return person["email"]
        v = person.get("personal_emails")
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.strip():
                    return item.strip()

    return None


def _enrich_person_by_id(api_key: str, person_id: str) -> Dict[str, Any] | None:
    """
    People Enrichment by Apollo Person ID.
    This is the best shot at getting full name + email (if available on your plan).
    """
    url = f"{APOLLO_BASE}/people/match"
    payload = {
        "id": person_id,
        # Try to reveal emails if your key/plan allows:
        "reveal_personal_emails": True,
        "reveal_phone_number": False,
    }

    try:
        data = _safe_post(url, api_key, payload)
    except requests.HTTPError:
        return None

    # Response can be person or match
    if isinstance(data.get("person"), dict):
        return data["person"]
    if isinstance(data.get("match"), dict):
        return data["match"]
    return data if isinstance(data, dict) else None


def fetch_top_marketing_contacts(api_key: str, domain: str, limit: int = 5) -> List[Dict[str, Any]]:
    raw_people = _people_api_search(api_key, domain, per_page=50)

    # Prefer senior + has_email
    def score(p: Dict[str, Any]) -> int:
        s = 0
        title = (p.get("title") or "").lower()
        if "chief" in title or "cmo" in title:
            s += 50
        if "vp" in title:
            s += 40
        if "head" in title:
            s += 30
        if "director" in title:
            s += 20
        if p.get("has_email"):
            s += 25
        return s

    raw_people = [p for p in raw_people if isinstance(p, dict) and p.get("id")]
    raw_people.sort(key=score, reverse=True)

    picked = raw_people[: max(limit, 1)]

    results: List[Dict[str, Any]] = []
    for p in picked:
        pid = p.get("id")
        enriched = _enrich_person_by_id(api_key, pid) if pid else None

        # Name: prefer enriched full name if present
        full_name = None
        if isinstance(enriched, dict):
            full_name = enriched.get("name")
            if not full_name:
                fn = enriched.get("first_name")
                ln = enriched.get("last_name")
                if fn or ln:
                    full_name = f"{(fn or '').strip()} {(ln or '').strip()}".strip()

        if not full_name:
            # fallback: only first_name available in search results
            full_name = (p.get("first_name") or "Not available").strip()

        title = (p.get("title") or "Not available").strip()
        linkedin = None
        if isinstance(enriched, dict):
            linkedin = enriched.get("linkedin_url") or None

        email = _extract_email(enriched) if isinstance(enriched, dict) else None

        results.append(
            {
                "name": full_name,
                "title": title,
                "email": email,
                "linkedin_url": linkedin,
            }
        )

    return results
