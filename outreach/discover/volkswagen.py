import json
import logging
import time
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.volkswagen")

LOCATOR_URL = "https://aus.volkswagen.com.au/find_dealer/"
API_PATH = "/api/getMultipleDealerDetails"

# Postcodes covering all Australian metro and regional areas
POSTCODES = [
    "2000", "2100", "2150", "2250", "2300", "2500", "2600", "2640",
    "2800", "2830", "3000", "3128", "3175", "3350", "3500", "3630",
    "3820", "3930", "4000", "4101", "4211", "4350", "4670", "4700",
    "4810", "4870", "5000", "5108", "5290", "6000", "6100", "6230",
    "6530", "7000", "7250", "0800",
]


@register("volkswagen")
def discover_volkswagen(limit: int = 0) -> int:
    log.info("loading volkswagen dealer data via one-hub API (postcode sweep)")

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
        page.wait_for_timeout(3000)

        for pc in POSTCODES:
            result = page.evaluate(
                """async (pc) => {
                    try {
                        const r = await fetch(
                            `/api/getMultipleDealerDetails?brand=Volkswagen&subbrand=Volkswagen+Passenger+Vehicles,Volkswagen+Commercial+Vehicles&type=postcode&searchstring=${pc}`
                        );
                        return await r.json();
                    } catch(e) {
                        return {error: e.message};
                    }
                }""",
                pc,
            )

            if isinstance(result, dict) and result.get("error"):
                log.warning("error for postcode %s: %s", pc, result["error"])
                continue

            if isinstance(result, dict) and "dealers" in result:
                for entry in result["dealers"]:
                    dealer = entry.get("dealer", {})
                    code = dealer.get("dealerCode", "")
                    if code and code not in all_dealers:
                        all_dealers[code] = dealer
            elif isinstance(result, list) and result and not result[0].get("errorCode"):
                for entry in result:
                    if "dealers" in entry:
                        for d_entry in entry["dealers"]:
                            dealer = d_entry.get("dealer", {})
                            code = dealer.get("dealerCode", "")
                            if code and code not in all_dealers:
                                all_dealers[code] = dealer

            time.sleep(1)

        browser.close()

    dealers = list(all_dealers.values())
    if not dealers:
        log.error("no volkswagen dealers captured")
        return 0

    log.info("fetched %d unique volkswagen dealers from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("dealerName") or "").strip()
            if not name:
                continue

            state = (d.get("dealerState") or "").strip() or None
            suburb = (d.get("SalesSuberbCode") or "").strip() or None
            address = (d.get("SalesStreetCode") or d.get("dealerAddress") or "").strip() or None
            postcode = (d.get("SalesPostCode") or d.get("dealerPostcode") or "").strip() or None
            phone = (d.get("SalesPhoneCode") or d.get("phone") or "").strip() or None
            website = (d.get("dealerWebsite") or "").strip() or None
            api_email = (
                (d.get("SalesEmailCode") or "").strip().lower()
                or (d.get("FinanceEmail") or "").strip().lower()
                or None
            )

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url, api_email) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO UPDATE SET api_email = EXCLUDED.api_email "
                "WHERE dealerships.api_email IS NULL "
                "RETURNING id",
                ("volkswagen", name, address, suburb, state, postcode, phone, website, api_email),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new volkswagen dealerships", inserted)
    return inserted
