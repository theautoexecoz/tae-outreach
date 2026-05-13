import json
import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.kia")

LOCATOR_URL = "https://www.kia.com/au/shopping-tools/find-a-dealer.html"


@register("kia")
def discover_kia(limit: int = 0) -> int:
    log.info("loading kia dealer locator via playwright (stealth mode)")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = ctx.new_page()

        # Capture the dealer API response from the page's own request
        dealer_data = []
        def cap_resp(response):
            if "findDealer" in response.url and response.status == 200:
                try:
                    body = json.loads(response.text())
                    di = body.get("dataInfo")
                    if isinstance(di, list):
                        dealer_data.extend(di)
                except Exception:
                    pass
        page.on("response", cap_resp)

        page.goto(LOCATOR_URL, timeout=60000)
        page.wait_for_timeout(15000)
        browser.close()

    if not dealer_data:
        log.error("no kia dealer data captured")
        return 0

    log.info("fetched %d kia dealers from API", len(dealer_data))
    inserted = 0

    with get_conn() as conn:
        for d in dealer_data:
            name = (d.get("dealerNm") or "").strip()
            if not name:
                continue

            suburb = (d.get("addr") or "").strip() or None
            state = (d.get("area") or "").strip() or None
            address = (d.get("addrSC") or d.get("addr") or "").strip() or None
            postcode = None
            # Extract postcode from address if present
            if address:
                import re
                pc_match = re.search(r'\b(\d{4})\b', address)
                if pc_match:
                    postcode = pc_match.group(1)
            phone = (d.get("phone") or "").strip() or None
            website = (d.get("homepage") or "").strip() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("kia", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new kia dealerships", inserted)
    return inserted
