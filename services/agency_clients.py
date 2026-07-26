# services/agency_clients.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup

from services.agency_pages import fetch_page, discover_candidate_paths, normalize_url


@dataclass
class ClientCandidate:
    name: str
    source_url: str
    evidence: str  # "logo_alt" | "logo_filename" | "case_study_text"
    confidence: str  # "High" | "Med" | "Low"
    domain: str | None = None
    click_url: str | None = None
    logo_src: str | None = None


# --- Filters (fast + deterministic) ---
BAD_TOKENS = [
    "icon", "ico", "sprite", "arrow", "menu", "hamburger", "phone", "email", "chat",
    "rating", "review", "testimonial", "stars", "award", "badge",
    "shield", "graph", "line", "btn", "button", "hero", "banner",
    "header", "footer", "nav", "social", "facebook", "linkedin", "instagram", "twitter",
    "google", "clutch", "g2", "gartner",
    "svg", "png", "jpg", "jpeg", "webp", "gif",
]

GOOD_HINTS = ["logo", "client", "customers", "brands", "rectangle", "square", "partner"]


def clean_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\b(logo|rectangle|square|icon|icons|brand)\b", "", s, flags=re.I).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def looks_like_client(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    if len(n) < 3:
        return False
    if any(tok in n for tok in BAD_TOKENS):
        return False
    if n in {"home", "about", "contact", "services", "work", "portfolio", "clients"}:
        return False
    if not re.search(r"[a-zA-Z]", n):
        return False
    return True


def brand_confidence(raw: str) -> str:
    r = (raw or "").lower()
    if any(h in r for h in ["logo", "client", "customers", "brands"]):
        return "High"
    return "Med"


def absolute_url(base: str, href: str) -> str | None:
    href = (href or "").strip()
    if not href:
        return None
    try:
        return normalize_url(urljoin(base, href))
    except Exception:
        return None


def infer_domain_from_click_url(click_url: str | None) -> str | None:
    if not click_url:
        return None
    try:
        host = urlparse(click_url).netloc.strip().lower()
        if not host:
            return None
        host = host.replace("www.", "")
        # ignore obvious non-company hosts
        if any(bad in host for bad in ["facebook.com", "linkedin.com", "instagram.com", "twitter.com", "x.com", "youtube.com"]):
            return None
        return host
    except Exception:
        return None


def find_nearest_anchor(img_tag) -> Optional[str]:
    """
    If an <img> is wrapped by <a href=...>, return that href.
    """
    try:
        parent_a = img_tag.find_parent("a")
        if parent_a and parent_a.get("href"):
            return parent_a.get("href")
    except Exception:
        pass
    return None


# --- Strategy 1: logo extraction (alt + filename), plus click_url if available ---
def extract_clients_from_logos(soup: BeautifulSoup, page_url: str) -> List[ClientCandidate]:
    out: List[ClientCandidate] = []

    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        src = (img.get("src") or "").strip()
        href = find_nearest_anchor(img)
        click_url = absolute_url(page_url, href) if href else None
        logo_src = absolute_url(page_url, src) if src else None

        # (A) alt-based
        if alt:
            cand = clean_name(alt)
            if looks_like_client(cand):
                domain = infer_domain_from_click_url(click_url)
                out.append(ClientCandidate(
                    name=cand,
                    source_url=page_url,
                    evidence="logo_alt",
                    confidence=brand_confidence(alt),
                    domain=domain,
                    click_url=click_url,
                    logo_src=logo_src,
                ))

        # (B) filename-based
        if src:
            filename = src.split("/")[-1]
            filename = re.sub(r"\?.*$", "", filename)
            filename = re.sub(r"\.[a-zA-Z0-9]+$", "", filename)  # remove extension
            cand2 = clean_name(filename)
            if looks_like_client(cand2):
                conf = "High" if any(h in filename.lower() for h in GOOD_HINTS) else "Med"
                domain = infer_domain_from_click_url(click_url)
                out.append(ClientCandidate(
                    name=cand2,
                    source_url=page_url,
                    evidence="logo_filename",
                    confidence=conf,
                    domain=domain,
                    click_url=click_url,
                    logo_src=logo_src,
                ))

    return out


# --- Strategy 2: case-study / portfolio extraction (heading/card text), plus click_url if available ---
def extract_clients_from_text_blocks(soup: BeautifulSoup, page_url: str) -> List[ClientCandidate]:
    out: List[ClientCandidate] = []

    blocks = []
    for sel in ["section", "article", "div", "li"]:
        blocks.extend(soup.find_all(sel))

    agency_host = urlparse(page_url).netloc.lower()

    for b in blocks:
        txt = b.get_text(" ", strip=True)
        if not txt or len(txt) > 140:
            continue

        # Prefer headings
        h = b.find(["h1", "h2", "h3", "h4"])
        a = b.find("a")

        if h:
            name = clean_name(h.get_text(" ", strip=True))
        else:
            name = clean_name(a.get_text(" ", strip=True) if a else txt)

        if not looks_like_client(name):
            continue

        # Avoid capturing the agency itself
        if agency_host and agency_host.split(".")[0] in name.lower():
            continue

        click_url = absolute_url(page_url, a.get("href")) if (a and a.get("href")) else None
        domain = infer_domain_from_click_url(click_url)

        out.append(ClientCandidate(
            name=name,
            source_url=page_url,
            evidence="case_study_text",
            confidence="Med",
            domain=domain,
            click_url=click_url,
            logo_src=None,
        ))

    return out


def dedupe_candidates(cands: List[ClientCandidate]) -> List[ClientCandidate]:
    """
    Keep the "best" record for each brand:
    - prefer High confidence
    - prefer one that has a domain
    - prefer one that has a click_url
    """
    best: Dict[str, ClientCandidate] = {}

    def score(c: ClientCandidate) -> int:
        s = 0
        if c.confidence == "High":
            s += 3
        elif c.confidence == "Med":
            s += 2
        else:
            s += 1
        if c.domain:
            s += 3
        if c.click_url:
            s += 2
        return s

    for c in cands:
        key = re.sub(r"[^a-z0-9]+", "", c.name.lower())
        if not key:
            continue
        if key not in best or score(c) > score(best[key]):
            best[key] = c

    return list(best.values())


def get_clients_for_agency(agency_url: str, max_pages: int = 6) -> List[ClientCandidate]:
    agency_url = normalize_url(agency_url)

    candidates: List[ClientCandidate] = []
    pages = discover_candidate_paths(agency_url)[:max_pages]

    for u in pages:
        try:
            res = fetch_page(u)
            if res.status_code >= 400:
                continue

            candidates.extend(extract_clients_from_logos(res.soup, res.url))

            hint = u.lower()
            if any(k in hint for k in ["work", "case", "portfolio", "client", "result", "success", "project"]):
                candidates.extend(extract_clients_from_text_blocks(res.soup, res.url))

        except Exception:
            continue

    candidates = dedupe_candidates(candidates)

    # bump confidence if brand appears multiple times across pages
    counts: Dict[str, int] = {}
    for c in candidates:
        k = re.sub(r"[^a-z0-9]+", "", c.name.lower())
        counts[k] = counts.get(k, 0) + 1

    for c in candidates:
        k = re.sub(r"[^a-z0-9]+", "", c.name.lower())
        if counts.get(k, 0) >= 2:
            c.confidence = "High"

    return candidates