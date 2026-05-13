import json
import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.mercedes")

LOCATOR_URL = "https://www.mercedes-benz.com.au/passengercars/mercedes-benz-cars/dealer-locator.html"


@register("mercedes")
def discover_mercedes(limit: int = 0) -> int:
    log.info("loading mercedes dealer data via oneweb API")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = ctx.new_page()

        # Capture the dealer API response from the page's own requests
        dealer_data = {"dealers": []}
        def cap_resp(response):
            if "dms-plus" in response.url and "dealers/market" in response.url and response.status == 200:
                try:
                    body = json.loads(response.text())
                    if body.get("dealers"):
                        dealer_data["dealers"] = body["dealers"]
                except Exception:
                    pass
        page.on("response", cap_resp)

        page.goto(LOCATOR_URL, timeout=30000)
        page.wait_for_timeout(10000)
        browser.close()

        result = dealer_data

    dealers = result.get("dealers", []) if isinstance(result, dict) else []
    if not dealers:
        log.error("no dealers in response")
        return 0

    log.info("fetched %d mercedes dealers from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("legalName") or "").strip()
            if not name:
                continue

            addr = d.get("address", {})
            suburb = (addr.get("city") or "").strip() or None
            state = (addr.get("stateProvince") or addr.get("region") or "").strip() or None
            address = (addr.get("addressLine1") or "").strip() or None
            postcode = (addr.get("postalCode") or "").strip() or None
            phone = None
            contact = d.get("contact", {})
            if isinstance(contact, dict):
                phone = (contact.get("phone") or "").strip() or None
            website = (d.get("url") or "").strip() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("mercedes", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new mercedes dealerships", inserted)
    return inserted
