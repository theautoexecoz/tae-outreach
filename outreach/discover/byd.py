import json
import logging
import re
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.byd")

LOCATOR_URL = "https://bydautomotive.com.au/find-us"

SCRAPE_JS = """() => {
    const items = document.querySelectorAll('li.pickup, li.service');
    return Array.from(items).map(item => {
        const h3 = item.querySelector('h3');
        const name = h3 ? h3.textContent.trim() : '';

        const badge = item.querySelector('.badge-success');
        const locType = badge ? badge.textContent.trim() : '';

        const link = item.querySelector('a[href*="http"]');
        const website = link ? link.href : '';

        // Get address from the raw text — it follows the badge text
        const rawText = item.textContent.trim();

        return {name, locType, website, rawText};
    });
}"""


def _parse_byd_entry(raw_text, name):
    """Parse address from BYD raw text like:
    'BYD AlbanyExperience Centre Closest 54 Aberdeen St,Albany WA 6330 Visit Dealer WebsiteShow more'
    """
    # Find the address pattern: street,suburb STATE postcode
    m = re.search(
        r"(\d+[\w\s/-]+(?:St|Rd|Ave|Hwy|Dr|Pl|Cres|Ct|Way|Blvd|Tce|Pde|Lane|Ln|Circuit|Crescent|Street|Road|Avenue|Highway|Drive|Place|Court|Terrace|Parade)[^,]*),\s*([A-Za-z\s]+?)\s+(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+(\d{4})",
        raw_text,
        re.IGNORECASE,
    )
    if m:
        return {
            "address": m.group(1).strip(),
            "suburb": m.group(2).strip(),
            "state": m.group(3).upper(),
            "postcode": m.group(4),
        }

    # Fallback: look for comma-separated with state+postcode
    m2 = re.search(
        r"([^,]+),\s*([A-Za-z\s]+?)\s+(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+(\d{4})",
        raw_text,
    )
    if m2:
        addr = m2.group(1).strip()
        # Remove the name and badge text from the address if present
        if name and addr.startswith(name):
            addr = addr[len(name):]
        # Remove common badge text
        for prefix in ["Experience Centre", "Megastore", "Service Centre", "Closest"]:
            addr = addr.replace(prefix, "").strip()
        return {
            "address": addr.strip(),
            "suburb": m2.group(2).strip(),
            "state": m2.group(3).upper(),
            "postcode": m2.group(4),
        }

    return None


@register("byd")
def discover_byd(limit: int = 0) -> int:
    log.info("loading byd experience centre data via DOM scraping")

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
        page.wait_for_timeout(8000)

        locations = page.evaluate(SCRAPE_JS)
        browser.close()

    if not locations:
        log.error("no byd locations found in DOM")
        return 0

    log.info("scraped %d byd locations from page", len(locations))
    inserted = 0

    with get_conn() as conn:
        for loc in locations:
            name = loc.get("name", "").strip()
            if not name:
                continue

            raw_text = loc.get("rawText", "")
            parsed = _parse_byd_entry(raw_text, name)

            suburb = parsed["suburb"] if parsed else None
            state = parsed["state"] if parsed else None
            address = parsed["address"] if parsed else None
            postcode = parsed["postcode"] if parsed else None
            website = loc.get("website", "").strip() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("byd", name, address, suburb, state, postcode, None, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new byd locations", inserted)
    return inserted
