import json
import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.bmw")

LOCATOR_URL = "https://www.bmw.com.au/en/fastlane/dealer-locator.html"
API_URL = "https://c2b-services.bmw.com/c2b-localsearch/services/api/v4/clients/BMWSTAGE2_DLO/-/pois?brand=BMW_BMWM&category=BM&language=en&unit=km&cached=off&lat=0&lng=0&maxResults=700&showAll=true&country=AU"


@register("bmw")
def discover_bmw(limit: int = 0) -> int:
    log.info("loading bmw dealer data via c2b-services API")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = ctx.new_page()
        page.goto(LOCATOR_URL, timeout=30000)
        page.wait_for_timeout(5000)

        result = page.evaluate(f"""
            async () => {{
                const r = await fetch('{API_URL}');
                return await r.json();
            }}
        """)
        browser.close()

    pois = result.get("data", {}).get("pois", []) if isinstance(result, dict) else []
    if not pois:
        log.error("no dealers in response")
        return 0

    STATE_MAP = {
        "New South Wales": "NSW", "Victoria": "VIC", "Queensland": "QLD",
        "South Australia": "SA", "Western Australia": "WA", "Tasmania": "TAS",
        "Northern Territory": "NT", "Australian Capital Territory": "ACT",
    }

    log.info("fetched %d bmw dealers from API", len(pois))
    inserted = 0

    with get_conn() as conn:
        for d in pois:
            name = (d.get("name") or "").strip()
            if not name:
                continue

            suburb = (d.get("city") or "").strip() or None
            raw_state = (d.get("state") or "").strip()
            state = STATE_MAP.get(raw_state, raw_state) or None
            address = (d.get("street") or "").strip() or None
            postcode = (d.get("postalCode") or "").strip() or None

            attrs = d.get("attributes", {})
            phone = (attrs.get("phone") or "").strip() or None
            website = (attrs.get("homepage") or "").strip() or None
            api_email = (attrs.get("mail") or "").strip().lower() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url, api_email) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO UPDATE SET api_email = EXCLUDED.api_email "
                "WHERE dealerships.api_email IS NULL "
                "RETURNING id",
                ("bmw", name, address, suburb, state, postcode, phone, website, api_email),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new bmw dealerships", inserted)
    return inserted
