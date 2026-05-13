import json
import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.mitsubishi")

LOCATOR_URL = "https://www.mitsubishi-motors.com.au/buying-tools/locate-a-dealer.html"
GRAPHQL_URL = "https://store.mitsubishi-motors.com.au/graphql"

QUERY = "query GetAllDealerLocations($location: LocationRequest!, $pageSize: Int = 500) { stockists(location: $location, pageSize: $pageSize, currentPage: 1) { locations { identifier name address { city phone postcode region street } } } }"

# Capital/major city coordinates with large radius
SEARCH_POINTS = [
    (-33.87, 151.21, 2000, "SYDNEY"),
    (-37.81, 144.96, 3000, "MELBOURNE"),
    (-27.47, 153.03, 4000, "BRISBANE"),
    (-31.95, 115.86, 6000, "PERTH"),
    (-34.93, 138.60, 5000, "ADELAIDE"),
    (-42.88, 147.33, 7000, "HOBART"),
    (-12.46, 130.85, 800, "DARWIN"),
    (-35.28, 149.13, 2600, "CANBERRA"),
    (-19.26, 146.82, 4810, "TOWNSVILLE"),
    (-23.38, 150.51, 4700, "ROCKHAMPTON"),
]


@register("mitsubishi")
def discover_mitsubishi(limit: int = 0) -> int:
    log.info("loading mitsubishi dealer locator via playwright")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()
        page.goto(LOCATOR_URL, timeout=30000)
        page.wait_for_timeout(5000)

        all_dealers = {}
        for lat, lng, postcode, suburb in SEARCH_POINTS:
            try:
                payload = json.dumps({
                    "operationName": "GetAllDealerLocations",
                    "variables": {
                        "pageSize": 500,
                        "location": {
                            "radius": 2000,
                            "dealer_type": "sales",
                            "lat": lat,
                            "lng": lng,
                            "postcode": postcode,
                            "suburb": suburb,
                        },
                    },
                    "query": QUERY,
                })
                result = page.evaluate(
                    "async (payload) => {"
                    "  const r = await fetch('" + GRAPHQL_URL + "', {"
                    "    method: 'POST',"
                    "    headers: {'Content-Type': 'application/json'},"
                    "    body: payload"
                    "  });"
                    "  return await r.json();"
                    "}",
                    payload,
                )
                locs = (
                    result.get("data", {}).get("stockists", {}).get("locations", [])
                )
                for loc in locs:
                    ident = loc.get("identifier")
                    if ident and ident not in all_dealers:
                        all_dealers[ident] = loc
            except Exception as e:
                log.warning("mitsubishi query failed for %s: %s", suburb, e)

        browser.close()

    log.info("fetched %d unique mitsubishi dealers", len(all_dealers))
    if not all_dealers:
        return 0

    inserted = 0
    with get_conn() as conn:
        for d in all_dealers.values():
            name = (d.get("name") or "").strip()
            if not name:
                continue

            addr = d.get("address", {})
            suburb = (addr.get("city") or "").strip() or None
            state = (addr.get("region") or "").strip() or None
            address = (addr.get("street") or "").strip() or None
            postcode = (addr.get("postcode") or "").strip() or None
            phone = (addr.get("phone") or "").strip() or None
            website = (d.get("website") or "").strip() or None

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                ("mitsubishi", name, address, suburb, state, postcode, phone, website),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new mitsubishi dealerships", inserted)
    return inserted
