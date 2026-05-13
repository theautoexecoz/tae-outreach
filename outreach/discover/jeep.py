import json
import logging
import time
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.jeep")

LOCATOR_URL = "https://app.fcaab.com.au/dealers/index.html?brand=jeep"
API_BASE = "https://api.fcaab.com.au/dealers/nearest"

# Dense postcode grid covering all Australian metro and regional areas
POSTCODES = [
    # NSW
    "2000", "2100", "2150", "2170", "2200", "2250", "2280", "2300",
    "2340", "2390", "2430", "2460", "2500", "2560", "2600", "2640",
    "2680", "2750", "2800", "2830", "2870", "2900",
    # VIC
    "3000", "3073", "3128", "3150", "3175", "3200", "3350", "3400",
    "3500", "3550", "3630", "3690", "3750", "3800", "3820", "3875",
    "3930", "3977",
    # QLD
    "4000", "4051", "4101", "4151", "4211", "4300", "4350", "4500",
    "4551", "4610", "4670", "4700", "4740", "4810", "4870",
    # SA
    "5000", "5042", "5085", "5108", "5162", "5250", "5290",
    # WA
    "6000", "6050", "6100", "6150", "6210", "6230", "6330", "6430",
    "6530", "6700",
    # TAS
    "7000", "7250", "7300",
    # NT
    "0800", "0820", "0870",
    # ACT
    "2600", "2615",
]


@register("jeep")
def discover_jeep(limit: int = 0) -> int:
    log.info("loading jeep dealer data via fcaab API (postcode sweep)")

    all_dealers = {}

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
        page.goto(LOCATOR_URL, timeout=30000)
        page.wait_for_timeout(5000)

        for pc in POSTCODES:
            result = page.evaluate(
                """async (pc) => {
                    try {
                        const r = await fetch(
                            `https://api.fcaab.com.au/dealers/nearest?postcode=${pc}&department=sales&division=1`
                        );
                        return await r.json();
                    } catch(e) {
                        return {error: e.message};
                    }
                }""",
                pc,
            )
            dealers = result.get("dealers", [])
            for d in dealers:
                did = d.get("id", "")
                if did and did not in all_dealers:
                    all_dealers[did] = d
            time.sleep(0.5)

        browser.close()

    dealers = list(all_dealers.values())
    if not dealers:
        log.error("no jeep dealers captured")
        return 0

    log.info("fetched %d unique jeep dealers from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("name") or "").strip()
            if not name:
                continue

            addr = d.get("address", {})
            suburb = (addr.get("city") or "").strip() or None
            state = (addr.get("state") or "").strip() or None
            address = (addr.get("line1") or "").strip() or None
            postcode = (addr.get("postal_code") or "").strip() or None
            phone = (d.get("phone") or "").strip() or None
            website = (d.get("web_site") or "").strip() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("jeep", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new jeep dealerships", inserted)
    return inserted
