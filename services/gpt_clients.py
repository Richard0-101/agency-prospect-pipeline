import json
import re
from typing import List, Dict, Any, Optional

from openai import OpenAI


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    """
    Robust JSON extractor: model must output JSON, but if it wraps text, we still recover.
    """
    text = (text or "").strip()

    # direct parse
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # find first [...] block
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def gpt_refine_clients(raw_items: List[Dict[str, Any]], agency_url: str, model: str) -> List[Dict[str, Any]]:
    """
    Input: raw scraped items (noisy), each like:
      {client_name, evidence, confidence, source_page, click_url, logo_src}

    Output: top 10 clean prospects with:
      {company, domain}

    IMPORTANT:
    - GPT must ONLY output entries it is confident are real companies.
    - If the domain cannot be confidently determined, output null for that field.
    """
    client = OpenAI()

    # Reduce tokens: extract names safely from ClientCandidate objects
    candidates = []
    for it in raw_items:
        if isinstance(it, dict):
            name = (it.get("client_name") or it.get("name") or "").strip()
        else:
            # ClientCandidate dataclass
            name = (getattr(it, "name", "") or "").strip()

        if name:
            candidates.append(name)

    # Deduplicate before GPT
    dedup = []
    seen = set()
    for n in candidates:
        k = re.sub(r"[^a-z0-9]+", "", n.lower())
        if k and k not in seen:
            seen.add(k)
            dedup.append(n)

    # Cap to keep GPT fast + high quality
    dedup = dedup[:200]


    prompt = f"""
You are cleaning noisy scraped text from a marketing agency website.

Goal:
Return ONLY the agency’s real CLIENT / CUSTOMER company names (not services, not partners badges, not tools like Mailchimp, not awards, not employees, not generic words).

You must return at most 10 companies that you are MOST confident are real companies.

For each company, also return:
- domain: the official website domain ONLY if you are confident (else null)

Rules:
- Output MUST be a JSON array only. No other text.
- Each item must have keys: company, domain
- domain must be like "njit.edu" or "rolls-royce.com" (NO https, NO paths)
- If unsure about the domain: set it to null.
- Prefer well-known brands when available.
- Never include the agency’s own name.

Agency URL: {agency_url}

Raw candidates:
{json.dumps(dedup)}
""".strip()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return ONLY valid JSON array. Be conservative. If unsure, omit or set null."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    data = _extract_json_array(resp.choices[0].message.content)

    out = []
    for item in data:
        company = (item.get("company") or "").strip()
        domain = item.get("domain", None)

        if not company:
            continue

        # normalize domain
        if isinstance(domain, str):
            domain = domain.strip().lower()
            domain = re.sub(r"^https?://", "", domain)
            domain = domain.split("/")[0]
            if not re.search(r"\.", domain):
                domain = None
        else:
            domain = None

        out.append({
            "company": company,
            "domain": domain,
        })

    # ensure max 10
    return out[:10]
