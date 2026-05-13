import json
import logging
import re
import time
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.gwm")

LOCATOR_URL = "https://www.gwmanz.com/au/dealer-locator/"

# Cities to search to cover all Australian GWM dealers
SEARCH_CITIES = [
    "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide",
    "Hobart", "Darwin", "Canberra", "Gold Coast", "Newcastle",
    "Geelong", "Townsville", "Cairns", "Toowoomba", "Ballarat",
    "Bendigo", "Wollongong", "Launceston", "Rockhampton", "Dubbo",
    "Wagga Wagga", "Bunbury", "Albany", "Mackay", "Shepparton",
    "Tamworth", "Albury", "Warrnambool", "Orange", "Gladstone",
]

SCRAPE_JS = """() => {
    const containers = document.querySelectorAll('.dealer-card-container');
    return Array.from(containers).map(card => {
        const nameEl = card.querySelector('.dealer-card-body-name__store');
        const addrEl = card.querySelector('.dealer-card-body-address');
        const phoneEl = card.querySelector('.dealer-card-body-details-contact a[href*="tel"]');
        const emailEl = card.querySelector('.dealer-card-body-details-contact a[href*="mailto"]');

        const name = nameEl ? nameEl.textContent.trim() : '';
        const addrText = addrEl ? addrEl.textContent.trim() : '';
        const phone = phoneEl ? phoneEl.textContent.trim() : '';
        const email = emailEl ? emailEl.textContent.trim() : '';

        return {name, address: addrText, phone, email};
    });
}"""


def _parse_gwm_address(addr_text):
    """Parse '403 Pacific Highway, Artarmon, NSW, 2064' into parts."""
    parts = [p.strip() for p in addr_text.split(",")]
    if len(parts) >= 4:
        street = parts[0]
        suburb = parts[1]
        state = parts[2]
        postcode = parts[3]
        return {"address": street, "suburb": suburb, "state": state, "postcode": postcode}
    elif len(parts) == 3:
        street = parts[0]
        # Last part might be "NSW 2064" or "2064"
        m = re.match(r"([A-Z]{2,3})\s*(\d{4})", parts[2])
        if m:
            return {"address": street, "suburb": parts[1], "state": m.group(1), "postcode": m.group(2)}
    return {"address": addr_text, "suburb": None, "state": None, "postcode": None}


@register("gwm")
def discover_gwm(limit: int = 0) -> int:
    log.info("loading gwm dealer data via DOM scraping (city sweep)")

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

        for city in SEARCH_CITIES:
            try:
                page.goto(LOCATOR_URL, timeout=30000)
                page.wait_for_timeout(4000)

                search_input = page.query_selector(".dealer-locator-search__input")
                if not search_input:
                    continue

                search_input.fill(city)
                page.wait_for_timeout(2000)

                pac_items = page.query_selector_all(".pac-item")
                if pac_items:
                    pac_items[0].click()
                    page.wait_for_timeout(4000)

                    cards = page.evaluate(SCRAPE_JS)
                    if isinstance(cards, list):
                        for d in cards:
                            name = d.get("name", "").strip()
                            if name and name not in all_dealers:
                                all_dealers[name] = d
            except Exception as e:
                log.warning("error searching gwm for %s: %s", city, e)

            time.sleep(2)

        browser.close()

    dealers = list(all_dealers.values())
    if not dealers:
        log.error("no gwm dealers captured")
        return 0

    log.info("scraped %d unique gwm dealers", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = d.get("name", "").strip()
            if not name:
                continue

            parsed = _parse_gwm_address(d.get("address", ""))
            suburb = parsed["suburb"]
            state = parsed["state"]
            address = parsed["address"]
            postcode = parsed["postcode"]
            phone = d.get("phone", "").strip() or None
            website = None  # GWM cards don't expose dealer website URLs

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("gwm", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new gwm dealerships", inserted)
    return inserted
