import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.isuzu")

LOCATOR_URL = "https://www.isuzuute.com.au/find-a-dealer"

STATE_MAP = {
    "New South Wales": "NSW", "Victoria": "VIC", "Queensland": "QLD",
    "South Australia": "SA", "Western Australia": "WA", "Tasmania": "TAS",
    "Northern Territory": "NT", "Australian Capital Territory": "ACT",
}


@register("isuzu")
def discover_isuzu(limit: int = 0) -> int:
    log.info("loading isuzu dealer locator via playwright")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(LOCATOR_URL, timeout=30000)
        page.wait_for_timeout(3000)

        result = page.evaluate("""
            async () => {
                const r = await fetch('/isuzuapi/Dealer/GetAllDealers');
                return await r.json();
            }
        """)
        browser.close()

    dealers = result.get("Dealers", [])
    if not dealers:
        log.error("no dealers in response")
        return 0

    log.info("fetched %d isuzu dealers from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("Name") or "").strip()
            if not name or not d.get("IsActive"):
                continue

            # Address and contact are nested inside department objects
            addr = {}
            website = None
            phone = None
            for dept_key in ("Sales", "Service", "Parts"):
                dept = d.get(dept_key)
                if not dept or not dept.get("IsActive"):
                    continue
                dept_addr = dept.get("Address", {})
                if dept_addr.get("Suburb") and not addr:
                    addr = dept_addr
                contact = dept.get("Contact", {})
                ws = contact.get("Website")
                if isinstance(ws, dict) and ws.get("Url") and not website:
                    website = ws["Url"]
                elif isinstance(ws, str) and ws and not website:
                    website = ws
                if contact.get("Phone") and not phone:
                    phone = str(contact["Phone"])

            suburb = (addr.get("Suburb") or "").strip() or None
            raw_state = (addr.get("State") or "").strip()
            state = STATE_MAP.get(raw_state, raw_state) or None
            address = (addr.get("Street") or "").strip() or None
            postcode = (addr.get("PostCode") or "").strip() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("isuzu", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new isuzu dealerships", inserted)
    return inserted
