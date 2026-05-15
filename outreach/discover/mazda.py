import json
import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.mazda")

LOCATOR_URL = "https://www.mazda.com.au/find-a-dealer/"
API_PATH = "/api/dealers?lat=-25.2744&lng=133.7751&radius=5000"


@register("mazda")
def discover_mazda(limit: int = 0) -> int:
    log.info("loading mazda dealer locator via playwright")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(LOCATOR_URL, timeout=45000)
        page.wait_for_timeout(3000)

        dealers = page.evaluate(f"""
            async () => {{
                const resp = await fetch('{API_PATH}');
                return await resp.json();
            }}
        """)
        browser.close()

    if not isinstance(dealers, list):
        log.error("unexpected response type: %s", type(dealers))
        return 0

    log.info("fetched %d mazda dealers from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = d.get("name", "").strip()
            if not name:
                continue

            suburb = d.get("suburb", "").strip() or None
            state = d.get("state", "").strip() or None
            website = d.get("website", "").strip() or None
            address = d.get("address", "").strip() or None
            postcode = d.get("postCode", "").strip() or None
            phone = None
            api_email = None
            for dept in d.get("departments", []):
                if not phone and dept.get("phone"):
                    phone = dept["phone"]
                if not api_email and dept.get("email"):
                    api_email = dept["email"].strip().lower()

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url, api_email) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO UPDATE SET api_email = EXCLUDED.api_email "
                "WHERE dealerships.api_email IS NULL "
                "RETURNING id",
                ("mazda", name, address, suburb, state, postcode, phone, website, api_email),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new mazda dealerships", inserted)
    return inserted
