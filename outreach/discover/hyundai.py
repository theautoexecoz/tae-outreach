import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.hyundai")

LOCATOR_URL = "https://www.hyundai.com/au/en/find-a-dealer.html"
API_PATH = "/content/api/au/hyundai/pcm1/v1/dealer"


@register("hyundai")
def discover_hyundai(limit: int = 0) -> int:
    log.info("loading hyundai dealer locator via playwright")

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

    log.info("fetched %d hyundai dealers from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("tradingName") or "").strip()
            if not name:
                continue

            suburb = (d.get("suburb") or "").strip() or None
            state = (d.get("state") or "").strip() or None
            website = (d.get("webAddress") or d.get("url") or "").strip() or None
            address = (d.get("streetName") or "").strip() or None
            postcode = (d.get("postcode") or "").strip() or None
            phone = (d.get("phone") or "").strip() or None
            api_email = (d.get("email") or "").strip().lower() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url, api_email) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO UPDATE SET api_email = EXCLUDED.api_email "
                "WHERE dealerships.api_email IS NULL "
                "RETURNING id",
                ("hyundai", name, address, suburb, state, postcode, phone, website, api_email),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new hyundai dealerships", inserted)
    return inserted
