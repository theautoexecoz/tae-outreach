import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.nissan")

LOCATOR_URL = "https://www.nissan.com.au/find-a-dealer.html"
API_URL = "https://ap.api-nissanpace.net/v2/dealers?size=500&serviceFilterType=AND&include=openingHours,departments"


@register("nissan")
def discover_nissan(limit: int = 0) -> int:
    log.info("loading nissan dealer locator via playwright")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = ctx.new_page()

        # Capture the JWT from the page's own API call
        token = {"value": None}
        def cap_req(request):
            if "api-nissanpace" in request.url:
                auth = request.headers.get("accesstoken")
                if auth:
                    token["value"] = auth
        page.on("request", cap_req)

        page.goto(LOCATOR_URL, timeout=60000)
        page.wait_for_timeout(5000)

        # Trigger a search to get the token issued
        el = page.query_selector(".dealer-locator input")
        if el:
            el.fill("Sydney")
            page.wait_for_timeout(1000)
            el.press("Enter")
            page.wait_for_timeout(5000)

        if not token["value"]:
            log.error("could not capture nissan API token")
            browser.close()
            return 0

        data = page.evaluate(f"""
            async () => {{
                const r = await fetch('{API_URL}', {{
                    headers: {{
                        'accesstoken': '{token["value"]}',
                        'Accept': 'application/json',
                    }}
                }});
                return await r.json();
            }}
        """)
        browser.close()

    dealers = data.get("dealers", []) if isinstance(data, dict) else []
    if not dealers:
        log.error("no dealers in response")
        return 0

    log.info("fetched %d nissan dealers from API", len(dealers))
    inserted = 0

    with get_conn() as conn:
        for d in dealers:
            name = (d.get("name") or "").strip()
            if not name:
                continue

            addr = d.get("address", {})
            contact = d.get("contact", {})
            websites = contact.get("websites", [])
            phones = contact.get("phones", [])

            suburb = (addr.get("city") or "").strip() or None
            state = (addr.get("stateCode") or "").strip() or None
            address = (addr.get("addressLine1") or "").strip() or None
            postcode = (addr.get("postalCode") or "").strip() or None
            website = websites[0].get("url") if websites else None
            phone = phones[0].get("value") if phones else None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("nissan", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new nissan dealerships", inserted)
    return inserted
