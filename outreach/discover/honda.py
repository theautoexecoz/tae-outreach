import json
import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.honda")

LOCATOR_URL = "https://www.honda.com.au/findahondacentre"
API_PATH = "/api/locateDealer/Dealerships/get"


@register("honda")
def discover_honda(limit: int = 0) -> int:
    log.info("loading honda centre data via sitecore API")

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
        page.goto(LOCATOR_URL, timeout=45000)
        page.wait_for_timeout(5000)

        # Fetch all dealers (no params returns all, sorted by distance from origin)
        dealers = page.evaluate(f"""
            async () => {{
                const r = await fetch("{API_PATH}");
                return await r.json();
            }}
        """)
        browser.close()

    if not isinstance(dealers, list):
        log.error("unexpected response type: %s", type(dealers))
        return 0

    log.info("fetched %d honda centres from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("Name") or "").strip()
            if not name:
                continue

            state = (d.get("State") or "").strip() or None

            # Get primary location details
            locations = d.get("Locations", [])
            primary = None
            for loc in locations:
                if loc.get("isPrimary"):
                    primary = loc
                    break
            if not primary and locations:
                primary = locations[0]

            if primary:
                suburb = (primary.get("AddressSuburb") or "").strip() or None
                address = (primary.get("AddressLine1") or "").strip() or None
                postcode = (primary.get("AddressPostcode") or "").strip() or None
                phone = (primary.get("Phone") or "").strip() or None
            else:
                suburb = None
                address = None
                postcode = None
                phone = None

            website_path = (d.get("Website") or "").strip()
            website = None
            if website_path:
                if website_path.startswith("http"):
                    website = website_path
                else:
                    website = f"https://www.honda.com.au{website_path}"

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("honda", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new honda centres", inserted)
    return inserted
