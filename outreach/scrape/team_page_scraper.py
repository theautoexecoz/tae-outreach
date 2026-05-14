import logging
import re
from outreach.db import get_conn
from outreach.http import http_get

log = logging.getLogger("outreach.scrape.team_page_scraper")

EMPTY_MAILTO_RE = re.compile(r'href=["\']mailto:["\']', re.IGNORECASE)


def _needs_playwright(html: str) -> bool:
    """Check if the httpx-fetched HTML has empty mailto: links that JS would populate."""
    return bool(EMPTY_MAILTO_RE.search(html))


def _fetch_with_playwright(url: str) -> str:
    """Fetch a page using Playwright headless Chromium to execute JS."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        html = page.content()
        browser.close()
    return html


def scrape_team_pages(brand: str | None = None, limit: int = 0):
    """Fetch and store the HTML of discovered team pages."""
    with get_conn() as conn:
        query = (
            "SELECT id, name, team_page_url FROM dealerships "
            "WHERE scrape_state = 'team_found' AND team_page_url IS NOT NULL"
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

    log.info("scraping %d team pages", len(rows))
    scraped = 0
    failed = 0
    playwright_used = 0

    for i, row in enumerate(rows):
        try:
            r = http_get(row["team_page_url"])
            html = r.text

            if _needs_playwright(html):
                log.debug("empty mailto detected for %s, retrying with playwright", row["name"])
                html = _fetch_with_playwright(row["team_page_url"])
                playwright_used += 1

            with get_conn() as conn:
                conn.execute(
                    "UPDATE dealerships SET team_page_html = %s, scrape_state = 'scraped', "
                    "scraped_at = NOW() WHERE id = %s",
                    (html, row["id"]),
                )
            scraped += 1

        except Exception as e:
            log.debug("failed to scrape %s (%s): %s", row["name"], row["team_page_url"], e)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE dealerships SET scrape_state = 'scrape_failed' WHERE id = %s",
                    (row["id"],),
                )
            failed += 1

        if (i + 1) % 25 == 0:
            log.info(
                "progress: %d/%d scraped, %d failed, %d via playwright",
                i + 1, len(rows), failed, playwright_used,
            )

    log.info(
        "done: %d pages scraped, %d failed, %d via playwright",
        scraped, failed, playwright_used,
    )


def rescrape_empty_mailtos(brand: str | None = None, limit: int = 0):
    """Re-scrape already-scraped pages that have empty mailto links using Playwright."""
    with get_conn() as conn:
        query = (
            "SELECT id, name, team_page_url, team_page_html FROM dealerships "
            "WHERE team_page_html IS NOT NULL AND team_page_url IS NOT NULL "
            "AND scrape_state IN ('scraped', 'extracted', 'extraction_empty')"
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

    candidates = [r for r in rows if _needs_playwright(r["team_page_html"])]
    log.info("found %d pages with empty mailto links (of %d checked)", len(candidates), len(rows))

    if not candidates:
        return

    rescraped = 0
    failed = 0

    for i, row in enumerate(candidates):
        try:
            html = _fetch_with_playwright(row["team_page_url"])

            with get_conn() as conn:
                conn.execute(
                    "UPDATE dealerships SET team_page_html = %s, scrape_state = 'scraped', "
                    "scraped_at = NOW() WHERE id = %s",
                    (html, row["id"]),
                )
            rescraped += 1

        except Exception as e:
            log.debug("playwright failed for %s: %s", row["name"], e)
            failed += 1

        if (i + 1) % 10 == 0:
            log.info("progress: %d/%d rescraped, %d failed", i + 1, len(candidates), failed)

    log.info("done: %d pages rescraped via playwright, %d failed", rescraped, failed)
