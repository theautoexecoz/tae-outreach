import json
import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.ldv")

PAGE_DATA_URL = "https://www.ldvautomotive.com.au/page-data/locate-a-dealer/page-data.json"


@register("ldv")
def discover_ldv(limit: int = 0) -> int:
    log.info("loading ldv dealer data from gatsby page-data")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.ldvautomotive.com.au/locate-a-dealer/", timeout=30000)
        page.wait_for_timeout(3000)

        result = page.evaluate("""
            async () => {
                const r = await fetch('/page-data/locate-a-dealer/page-data.json');
                return await r.json();
            }
        """)
        browser.close()

    dealers = (
        result.get("result", {})
        .get("data", {})
        .get("allCmsBrandDealer", {})
        .get("nodes", [])
    )
    if not dealers:
        log.error("no dealers found in page-data")
        return 0

    log.info("fetched %d ldv dealers from page-data", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("name") or "").strip()
            if not name:
                continue

            suburb = (d.get("suburb") or "").strip() or None
            state = (d.get("state") or "").strip() or None
            address = (d.get("address") or "").strip() or None
            postcode = (d.get("postcode") or "").strip() or None
            phone = (d.get("phone") or "").strip() or None
            website = (d.get("contactUrl") or d.get("url") or "").strip() or None
            api_email = (d.get("email") or "").strip().lower() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url, api_email) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO UPDATE SET api_email = EXCLUDED.api_email "
                "WHERE dealerships.api_email IS NULL "
                "RETURNING id",
                ("ldv", name, address, suburb, state, postcode, phone, website, api_email),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new ldv dealerships", inserted)
    return inserted
