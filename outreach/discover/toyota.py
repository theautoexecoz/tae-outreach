import logging
from playwright.sync_api import sync_playwright
from outreach.discover import register
from outreach.db import get_conn

log = logging.getLogger("outreach.discover.toyota")

LOCATOR_URL = "https://www.toyota.com.au/find-a-dealer"

# Postcodes covering all of Australia at intervals
_POSTCODES = []
_POSTCODES += [str(p) for p in range(2000, 3000, 15)]  # NSW
_POSTCODES += [str(p) for p in range(3000, 4000, 15)]  # VIC
_POSTCODES += [str(p) for p in range(4000, 5000, 15)]  # QLD
_POSTCODES += [str(p) for p in range(5000, 5900, 20)]  # SA
_POSTCODES += [str(p) for p in range(6000, 6900, 20)]  # WA
_POSTCODES += [str(p) for p in range(7000, 7400, 15)]  # TAS
_POSTCODES += [str(p).zfill(4) for p in range(800, 900, 15)]  # NT
_POSTCODES += ["2600", "2610", "2615", "2620"]  # ACT


@register("toyota")
def discover_toyota(limit: int = 0) -> int:
    log.info("loading toyota dealer locator via playwright (%d postcodes)", len(_POSTCODES))

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
        page.goto(LOCATOR_URL, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(15000)

        if "moment" in page.title().lower():
            log.error("blocked by cloudflare challenge")
            browser.close()
            return 0

        all_dealers = {}
        for i, pc in enumerate(_POSTCODES):
            try:
                addr = page.evaluate(f"""
                    async () => {{
                        const r = await fetch('/main/api/v1/toyotaforms/address/dealeraddress/{pc}?maxresults=1');
                        if (!r.ok) return null;
                        return await r.json();
                    }}
                """)
                if not addr or not addr.get("results"):
                    continue

                suburb = addr["results"][0]["suburb"].replace("'", "\\'")
                result = page.evaluate(f"""
                    async () => {{
                        const r = await fetch('/main/api/v1/toyotaforms/info/dealers/{pc}/{suburb}?dealerOptIn=false');
                        if (!r.ok) return null;
                        return await r.json();
                    }}
                """)
                if result and result.get("results"):
                    for d in result["results"]:
                        code = d.get("dealerCode")
                        if code and code not in all_dealers:
                            all_dealers[code] = d
            except Exception:
                pass

            if (i + 1) % 100 == 0:
                log.info("progress: %d/%d postcodes, %d unique dealers", i + 1, len(_POSTCODES), len(all_dealers))

        browser.close()

    log.info("fetched %d unique toyota dealers", len(all_dealers))
    inserted = 0

    with get_conn() as conn:
        for d in all_dealers.values():
            name = (d.get("name") or "").strip()
            if not name:
                continue

            cur = conn.execute(
                "INSERT INTO dealerships "
                "(brand_slug, name, address, suburb, state, postcode, phone, website_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (brand_slug, name, suburb) DO NOTHING "
                "RETURNING id",
                (
                    "toyota",
                    name,
                    (d.get("address") or "").strip() or None,
                    (d.get("city") or "").strip() or None,
                    (d.get("state") or "").strip() or None,
                    (d.get("postCode") or "").strip() or None,
                    (d.get("telephone") or "").strip() or None,
                    (d.get("webSite") or "").strip() or None,
                ),
            )
            if cur.fetchone():
                inserted += 1

            if limit and inserted >= limit:
                break

    log.info("inserted %d new toyota dealerships", inserted)
    return inserted
