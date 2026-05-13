import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.subaru")

LOCATOR_URL = "https://www.subaru.com.au/find-a-retailer"


@register("subaru")
def discover_subaru(limit: int = 0) -> int:
    log.info("loading subaru dealer locator via playwright")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = ctx.new_page()
        page.goto(LOCATOR_URL, timeout=60000)
        page.wait_for_timeout(5000)

        dealers = page.evaluate("""
            async () => {
                const r = await fetch('/api/dealers', {
                    headers: { 'Accept': 'application/json' }
                });
                return await r.json();
            }
        """)
        browser.close()

    if not isinstance(dealers, list):
        log.error("unexpected response type: %s", type(dealers))
        return 0

    log.info("fetched %d subaru dealers from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("name") or "").strip()
            if not name or not d.get("isActive"):
                continue

            details = d.get("dealerDetails", [])
            # Use the first department entry for address/contact
            det = details[0] if details else {}

            suburb = (det.get("suburb") or "").strip() or None
            state = (d.get("state") or "").strip() or None
            website = (det.get("url") or "").strip() or None
            address = (det.get("address") or "").strip() or None
            postcode = (det.get("postCode") or "").strip() or None
            phone = (det.get("phone") or "").strip() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("subaru", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new subaru dealerships", inserted)
    return inserted
