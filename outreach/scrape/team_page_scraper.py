import logging
from outreach.db import get_conn
from outreach.http import http_get

log = logging.getLogger("outreach.scrape.team_page_scraper")


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

    for i, row in enumerate(rows):
        try:
            r = http_get(row["team_page_url"])
            html = r.text

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
            log.info("progress: %d/%d scraped, %d failed", i + 1, len(rows), failed)

    log.info("done: %d pages scraped, %d failed", scraped, failed)
