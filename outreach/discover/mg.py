import json
import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.mg")

LOCATOR_URL = "https://mgmotor.com.au/pages/find-a-dealer"
API_URL = "https://zohoapi.mgmotor.com.au/dealer_locator-au/get_DealerApi_Data.php?file_name=all_states_dealer_locaters_AU.json"


@register("mg")
def discover_mg(limit: int = 0) -> int:
    log.info("loading mg dealer data via zoho API (captured on page load)")

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

        # Capture the Zoho API response triggered by the page itself
        captured_data = {}

        def on_resp(response):
            if "zohoapi" in response.url and response.status == 200:
                try:
                    body = json.loads(response.text())
                    if isinstance(body, dict) and "NSW" in body:
                        captured_data.update(body)
                except Exception:
                    pass

        page.on("response", on_resp)
        page.goto(LOCATOR_URL, timeout=30000)
        page.wait_for_timeout(8000)
        browser.close()

    if not captured_data:
        log.error("no mg dealer data captured from zoho API")
        return 0

    # Data structure: {STATE: {result: {Response: {...}, Dealer_List: [...]}}}
    # Each top-level key (NSW, VIC, ...) contains a result with Dealer_List
    all_dealers = []
    for state_key, state_data in captured_data.items():
        if not isinstance(state_data, dict):
            continue

        # Handle rate-limited response
        if state_data.get("code") == 2955:
            log.warning("mg API rate-limited for state %s", state_key)
            continue

        result = state_data.get("result", {})
        dealer_list = result.get("Dealer_List", [])
        if not isinstance(dealer_list, list):
            continue

        for d in dealer_list:
            if isinstance(d, dict):
                d["_state"] = state_key
                all_dealers.append(d)

    log.info("fetched %d mg dealers across states", len(all_dealers))
    if not all_dealers:
        return 0

    inserted = 0

    with get_conn() as conn:
        for d in all_dealers:
            name = (d.get("Name") or d.get("Dealer_Name") or "").strip()
            if not name:
                continue

            state = (d.get("Dealer_State") or d.get("_state") or "").strip() or None
            address = (d.get("Showroom_Address") or "").strip() or None
            phone = (d.get("Phone_Number") or "").strip() or None
            website = (d.get("MG_Website") or "").strip() or None

            # Parse suburb and postcode from Showroom_Address
            # Format: "1-11 Shandan Circuit, Albion Park Rail NSW 2527"
            suburb = None
            postcode = None
            if address:
                import re
                m = re.search(
                    r",\s*(.+?)\s+(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+(\d{4})$",
                    address,
                )
                if m:
                    suburb = m.group(1).strip()
                    postcode = m.group(3)

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("mg", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new mg dealerships", inserted)
    return inserted
