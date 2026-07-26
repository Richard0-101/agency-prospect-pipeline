# services/agency_pages.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


@dataclass
class PageFetchResult:
    url: str
    status_code: int
    html: str
    soup: BeautifulSoup


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    # remove fragments
    parts = urlparse(url)
    clean = parts._replace(fragment="").geturl()
    return clean


def fetch_page(url: str, timeout: int = 20) -> PageFetchResult:
    url = normalize_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    html = resp.text or ""
    soup = BeautifulSoup(html, "lxml")
    return PageFetchResult(url=url, status_code=resp.status_code, html=html, soup=soup)


def discover_candidate_paths(base_url: str) -> List[str]:
    """
    Strategy 2: Try common portfolio/case-study pages.
    We'll also look at nav links to find these pages if present.
    """
    base_url = normalize_url(base_url)
    candidates = [
        "/work",
        "/case-studies",
        "/case-studies/",
        "/clients",
        "/portfolio",
        "/our-work",
        "/results",
        "/projects",
        "/success-stories",
        "/customer-stories",
    ]

    # Always include base
    out = [base_url]

    # Add common paths
    for p in candidates:
        out.append(urljoin(base_url, p))

    # Bonus: scan nav links for keywords
    try:
        home = fetch_page(base_url)
        links = home.soup.find_all("a", href=True)
        for a in links:
            href = a.get("href", "").strip()
            text = (a.get_text(" ", strip=True) or "").lower()
            full = urljoin(base_url, href)
            if not full.startswith(("http://", "https://")):
                continue
            if urlparse(full).netloc != urlparse(base_url).netloc:
                continue

            key = (href + " " + text).lower()
            if any(k in key for k in ["case", "work", "portfolio", "client", "customers", "results", "success"]):
                out.append(full)
    except Exception:
        pass

    # De-dupe while preserving order
    seen = set()
    uniq = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq
