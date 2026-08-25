from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from mtg_ingestion.config import settings

_TXT_LINK_TEXT = "TXT"


def _find_current_txt_url(client: httpx.Client) -> str:
    """Scrape the rules page for today's Comprehensive Rules .txt link.

    Wizards publishes the rules as a dated file (e.g. "MagicCompRules
    20260819.txt") and swaps the link on this page every time a new version
    ships. There's no stable "latest.txt" URL, so we resolve it fresh on
    every fetch instead of hardcoding a filename that will go stale.
    """
    response = client.get(settings.rules_page_url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for link in soup.find_all("a"):
        href = link.get("href", "")
        if link.get_text(strip=True).upper() == _TXT_LINK_TEXT and href.endswith(".txt"):
            return href

    raise RuntimeError(
        f"Could not find a Comprehensive Rules .txt link on {settings.rules_page_url}. "
        "Wizards may have changed the page layout -- check it manually."
    )


def fetch_rules_text(raw_dir: Path | None = None) -> Path:
    """Download the current Comprehensive Rules text file.

    Returns the path to the saved raw file.
    """
    raw_dir = raw_dir or settings.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    headers = {"User-Agent": settings.user_agent}
    with httpx.Client(
        timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True
    ) as client:
        txt_url = _find_current_txt_url(client)
        # Wizards' href sometimes contains a literal space before the file
        # extension (e.g. "MagicCompRules 20260819.txt"), which isn't valid
        # in a URL -- percent-encode it before requesting.
        txt_url = txt_url.replace(" ", "%20")
        response = client.get(txt_url)
        response.raise_for_status()
        content = response.content

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = raw_dir / f"rules_{timestamp}.txt"
    dest.write_bytes(content)
    return dest
