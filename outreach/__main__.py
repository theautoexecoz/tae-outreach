import argparse
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("outreach")

BRANDS = [
    "toyota", "mitsubishi", "hyundai", "kia", "ford",
    "mazda", "nissan", "isuzu", "honda", "subaru",
    "bmw", "mercedes", "ldv", "volkswagen", "audi",
    "volvo", "jeep", "mg", "byd", "gwm",
]


def cmd_discover(args):
    from outreach.discover import run_discovery
    brands = BRANDS if args.all else [args.brand]
    for brand in brands:
        log.info("discovering dealers for %s", brand)
        run_discovery(brand, limit=args.limit)


def cmd_scrape(args):
    from outreach.scrape.team_page_finder import find_team_pages
    from outreach.scrape.team_page_scraper import scrape_team_pages
    find_team_pages(brand=args.brand, limit=args.limit)
    scrape_team_pages(brand=args.brand, limit=args.limit)


def cmd_extract(args):
    from outreach.extract import run_extraction
    run_extraction(brand=args.brand, limit=args.limit)


def cmd_stats(args):
    from outreach.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT brand_slug, scrape_state, COUNT(*) AS n "
            "FROM dealerships GROUP BY brand_slug, scrape_state ORDER BY brand_slug, scrape_state"
        )
        rows = cur.fetchall()
        if not rows:
            print("No data yet.")
            return
        print(f"\n{'Brand':<15} {'State':<20} {'Count':>6}")
        print("-" * 45)
        for r in rows:
            print(f"{r['brand_slug']:<15} {r['scrape_state']:<20} {r['n']:>6}")

        cur = conn.execute(
            "SELECT confidence, COUNT(*) AS n FROM contacts GROUP BY confidence ORDER BY confidence"
        )
        contacts = cur.fetchall()
        if contacts:
            print(f"\n{'Confidence':<15} {'Contacts':>8}")
            print("-" * 25)
            for c in contacts:
                print(f"{c['confidence']:<15} {c['n']:>8}")

        cur = conn.execute("SELECT COUNT(*) AS n FROM contacts WHERE email IS NOT NULL")
        emails = cur.fetchone()
        print(f"\nTotal emails: {emails['n']}")


def cmd_migrate(args):
    from outreach.db import run_migration
    import os
    migration_dir = os.path.join(os.path.dirname(__file__), "migrations")
    for f in sorted(os.listdir(migration_dir)):
        if f.endswith(".sql"):
            path = os.path.join(migration_dir, f)
            log.info("running migration %s", f)
            run_migration(path)
    log.info("migrations complete")


def cmd_export(args):
    from outreach.export.csv_export import export_csv
    export_csv(args.output)


def main():
    parser = argparse.ArgumentParser(prog="outreach", description="TAE Outreach — dealer contact scraper")
    sub = parser.add_subparsers(dest="command")

    p_discover = sub.add_parser("discover", help="discover dealerships from OEM locators")
    p_discover.add_argument("--brand", choices=BRANDS)
    p_discover.add_argument("--all", action="store_true")
    p_discover.add_argument("--limit", type=int, default=0)

    p_scrape = sub.add_parser("scrape", help="find and scrape team pages")
    p_scrape.add_argument("--brand")
    p_scrape.add_argument("--limit", type=int, default=0)

    p_extract = sub.add_parser("extract", help="extract contacts from scraped HTML")
    p_extract.add_argument("--brand")
    p_extract.add_argument("--limit", type=int, default=0)

    sub.add_parser("stats", help="show pipeline statistics")
    sub.add_parser("migrate", help="run database migrations")

    p_export = sub.add_parser("export", help="export contacts to CSV")
    p_export.add_argument("--output", default="contacts.csv")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "discover": cmd_discover,
        "scrape": cmd_scrape,
        "extract": cmd_extract,
        "stats": cmd_stats,
        "migrate": cmd_migrate,
        "export": cmd_export,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
