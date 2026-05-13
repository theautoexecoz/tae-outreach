import json
import logging
import re
import time
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.volvo")

# Volvo Cars AU is protected by Akamai WAF which blocks headless browsers.
# Fallback: use Google Maps search to find Volvo dealers in Australia.

SEARCH_LOCATIONS = [
    {"name": "Sydney", "lat": -33.8688, "lng": 151.2093},
    {"name": "Melbourne", "lat": -37.8136, "lng": 144.9631},
    {"name": "Brisbane", "lat": -27.4698, "lng": 153.0251},
    {"name": "Perth", "lat": -31.9505, "lng": 115.8605},
    {"name": "Adelaide", "lat": -34.9285, "lng": 138.6007},
    {"name": "Hobart", "lat": -42.8821, "lng": 147.3272},
    {"name": "Darwin", "lat": -12.4634, "lng": 130.8456},
    {"name": "Canberra", "lat": -35.2809, "lng": 149.1300},
    {"name": "Gold Coast", "lat": -28.0167, "lng": 153.4000},
    {"name": "Newcastle", "lat": -32.9283, "lng": 151.7817},
    {"name": "Geelong", "lat": -38.1499, "lng": 144.3617},
    {"name": "Townsville", "lat": -19.2590, "lng": 146.8169},
    {"name": "Wollongong", "lat": -34.4249, "lng": 150.8931},
    {"name": "Launceston", "lat": -41.4388, "lng": 147.1347},
]

STATE_MAP = {
    "New South Wales": "NSW", "Victoria": "VIC", "Queensland": "QLD",
    "South Australia": "SA", "Western Australia": "WA", "Tasmania": "TAS",
    "Northern Territory": "NT", "Australian Capital Territory": "ACT",
}


@register("volvo")
def discover_volvo(limit: int = 0) -> int:
    log.info("loading volvo retailer data via google maps search (akamai bypass)")

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

        for loc in SEARCH_LOCATIONS:
            try:
                url = f"https://www.google.com/maps/search/Volvo+car+dealer/@{loc['lat']},{loc['lng']},10z"
                page.goto(url, timeout=30000)
                page.wait_for_timeout(5000)

                # Scroll to load more results
                for _ in range(3):
                    page.evaluate("""() => {
                        const panel = document.querySelector('[role="feed"]');
                        if (panel) panel.scrollTop = panel.scrollHeight;
                    }""")
                    page.wait_for_timeout(2000)

                results = page.evaluate("""() => {
                    const items = document.querySelectorAll('[role="feed"] > div > div > a');
                    return Array.from(items).map(a => {
                        const label = a.getAttribute('aria-label') || '';
                        const parent = a.closest('[role="feed"] > div');
                        const text = parent ? parent.textContent : '';
                        return {name: label.trim(), text: text.substring(0, 500)};
                    }).filter(r => r.name && r.name.toLowerCase().includes('volvo'));
                }""")

                if isinstance(results, list):
                    for r in results:
                        name = r.get("name", "").strip()
                        if name and name not in all_dealers:
                            all_dealers[name] = r

                log.debug("%s: %d results", loc["name"], len(results) if results else 0)
            except Exception as e:
                log.warning("error searching volvo in %s: %s", loc["name"], e)

            time.sleep(2)

        browser.close()

    dealers = list(all_dealers.values())
    if not dealers:
        log.error("no volvo retailers captured — Akamai WAF blocked, Google Maps fallback returned no results")
        return 0

    log.info("scraped %d unique volvo retailers from google maps", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = d.get("name", "").strip()
            if not name:
                continue

            text = d.get("text", "")
            suburb = None
            state = None
            postcode = None
            address = None
            phone = None

            pc_match = re.search(r"\b(\d{4})\b", text)
            if pc_match:
                postcode = pc_match.group(1)

            for full, abbr in STATE_MAP.items():
                if abbr in text:
                    state = abbr
                    break

            ph_match = re.search(r"\(?\d{2}\)?\s?\d{4}\s?\d{4}", text)
            if ph_match:
                phone = ph_match.group(0)

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("volvo", name, address, suburb, state, postcode, phone, None),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new volvo retailers", inserted)
    return inserted
