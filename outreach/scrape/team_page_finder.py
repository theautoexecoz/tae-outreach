import logging
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from outreach.db import get_conn
from outreach.http import http_get

log = logging.getLogger("outreach.scrape.team_page_finder")

TEAM_PATH_PATTERNS = [
    r"/our-team", r"/meet-the-team", r"/meet-our-team",
    r"/the-team", r"/team", r"/staff", r"/people",
    r"/about-us/team", r"/about-us/our-team", r"/about-us/staff",
    r"/about/team", r"/about/our-team", r"/about/staff",
]

TEAM_LINK_KEYWORDS = [
    "our team", "meet the team", "meet our team", "the team",
    "our staff", "our people", "team members",
]

EXCLUDED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js"}


def _score_link(href: str, text: str) -> int:
    """Score a link's likelihood of being a team page. Higher = better."""
    href_lower = href.lower()
    text_lower = text.lower().strip()
    score = 0

    for pattern in TEAM_PATH_PATTERNS:
        if re.search(pattern, href_lower):
            score += 10
            if href_lower.endswith(pattern) or href_lower.endswith(pattern + "/"):
                score += 5
            break

    for kw in TEAM_LINK_KEYWORDS:
        if kw in text_lower:
            score += 8
            break

    if "about" in href_lower and score == 0:
        if "about" in text_lower:
            score += 2

    ext = urlparse(href_lower).path.rsplit(".", 1)[-1] if "." in urlparse(href_lower).path else ""
    if f".{ext}" in EXCLUDED_EXTENSIONS:
        score = 0

    return score


def find_team_page_url(website_url: str) -> str | None:
    """Given a dealer website URL, find the team/staff page URL."""
    try:
        r = http_get(website_url)
    except Exception as e:
        log.debug("failed to fetch %s: %s", website_url, e)
        return None

    soup = BeautifulSoup(r.text, "lxml")
    base_domain = urlparse(website_url).netloc

    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue

        full_url = urljoin(website_url, href)
        if urlparse(full_url).netloc != base_domain:
            continue

        text = a.get_text(strip=True)
        score = _score_link(full_url, text)
        if score > 0:
            candidates.append((score, full_url))

    if not candidates:
        return None

    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def find_team_pages(brand: str | None = None, limit: int = 0):
    """Find team page URLs for all discovered dealerships."""
    with get_conn() as conn:
        query = (
            "SELECT id, name, website_url FROM dealerships "
            "WHERE scrape_state = 'discovered' AND website_url IS NOT NULL"
        )
        params = []
        if brand:
            query += " AND brand_slug = %s"
            params.append(brand)
        query += " ORDER BY id"
        if limit:
            query += " LIMIT %s"
            params.append(limit)

        cur = conn.execute(query, params)
        rows = cur.fetchall()

    log.info("scanning %d dealership websites for team pages", len(rows))
    found = 0
    no_team = 0

    for i, row in enumerate(rows):
        team_url = find_team_page_url(row["website_url"])

        with get_conn() as conn:
            if team_url:
                conn.execute(
                    "UPDATE dealerships SET team_page_url = %s, scrape_state = 'team_found' WHERE id = %s",
                    (team_url, row["id"]),
                )
                found += 1
                log.debug("found team page for %s: %s", row["name"], team_url)
            else:
                conn.execute(
                    "UPDATE dealerships SET scrape_state = 'no_team_page' WHERE id = %s",
                    (row["id"],),
                )
                no_team += 1

        if (i + 1) % 25 == 0:
            log.info("progress: %d/%d scanned, %d found, %d no team page", i + 1, len(rows), found, no_team)

    log.info("done: %d team pages found, %d without team pages", found, no_team)
