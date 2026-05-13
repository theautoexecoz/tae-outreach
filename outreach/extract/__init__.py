import logging
from outreach.db import get_conn
from outreach.extract.email_extractor import extract_emails
from outreach.extract.name_role_extractor import extract_people
from outreach.extract.pattern_guesser import infer_email_pattern, guess_email

log = logging.getLogger("outreach.extract")


def run_extraction(brand: str | None = None, limit: int = 0):
    """Extract contacts from scraped team page HTML."""
    with get_conn() as conn:
        query = (
            "SELECT id, name, team_page_url, team_page_html, website_url "
            "FROM dealerships WHERE scrape_state = 'scraped' AND team_page_html IS NOT NULL"
        )
        params = []
        if brand:
            query += " AND brand_slug = %s"
            params.append(brand)
        query += " ORDER BY id"
        if limit:
            query += " LIMIT %s"
            params.append(limit)

        cur = conn.execute(query, params)
        rows = cur.fetchall()

    log.info("extracting contacts from %d scraped team pages", len(rows))
    total_contacts = 0
    total_emails = 0
    dealers_with_contacts = 0

    for i, row in enumerate(rows):
        html = row["team_page_html"]
        dealer_id = row["id"]

        # Extract emails and people
        person_emails, generic_emails = extract_emails(html)
        people = extract_people(html)

        # Determine the email domain from the dealer website
        domain = None
        if row.get("website_url"):
            from urllib.parse import urlparse
            domain = urlparse(row["website_url"]).netloc
            if domain.startswith("www."):
                domain = domain[4:]

        # If we have emails, try to infer the pattern
        pattern = infer_email_pattern(person_emails) if person_emails else None

        # Match emails to people where possible
        email_by_name = {}
        if person_emails and domain:
            for email in person_emails:
                prefix = email.split("@")[0]
                email_by_name[prefix.lower()] = email

        contacts_for_dealer = []

        # Add people found with name extraction
        for person in people:
            first = person.get("first_name") or ""
            last = person.get("last_name") or ""

            # Try to match an email
            email = None
            confidence = "direct"

            # Check exact prefix matches
            if first and last and domain:
                for try_prefix in [
                    f"{first}.{last}".lower(),
                    f"{first[0]}{last}".lower(),
                    f"{first}".lower(),
                    f"{first}{last[0]}".lower() if last else None,
                ]:
                    if try_prefix and try_prefix in email_by_name:
                        email = email_by_name[try_prefix]
                        break

            # If no match and we have a pattern, guess
            if not email and pattern and domain and first:
                email = guess_email(first, last, domain, pattern)
                confidence = "inferred"

            email_domain = email.split("@")[1] if email and "@" in email else None

            contacts_for_dealer.append({
                "dealership_id": dealer_id,
                "full_name": person["full_name"],
                "first_name": person.get("first_name"),
                "last_name": person.get("last_name"),
                "role_raw": person.get("role_raw"),
                "email": email,
                "email_domain": email_domain,
                "confidence": confidence,
                "source": "team_page",
                "source_detail": row["team_page_url"],
            })

        # Add orphan emails (found on page but not matched to a person)
        matched_emails = {c["email"] for c in contacts_for_dealer if c["email"]}
        for email in person_emails:
            if email not in matched_emails:
                prefix = email.split("@")[0]
                email_domain = email.split("@")[1]
                # Generate a name from the email prefix — but only if it looks like a person
                name_from_prefix = prefix.replace(".", " ").replace("-", " ").title()
                words = name_from_prefix.split()
                # Skip if the prefix doesn't look like a person's name
                if len(words) > 3 or len(prefix) > 25 or not prefix[0].isalpha():
                    continue
                first = words[0] if words else None
                last = " ".join(words[1:]) if len(words) > 1 else None
                contacts_for_dealer.append({
                    "dealership_id": dealer_id,
                    "full_name": name_from_prefix,
                    "first_name": first,
                    "last_name": last,
                    "role_raw": None,
                    "email": email,
                    "email_domain": email_domain,
                    "confidence": "direct",
                    "source": "team_page",
                    "source_detail": row["team_page_url"],
                })

        # Insert contacts
        if contacts_for_dealer:
            dealers_with_contacts += 1
            with get_conn() as conn:
                for c in contacts_for_dealer:
                    try:
                        conn.execute(
                            "INSERT INTO contacts "
                            "(dealership_id, full_name, first_name, last_name, role_raw, "
                            "email, email_domain, confidence, source, source_detail) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT DO NOTHING",
                            (
                                c["dealership_id"], c["full_name"], c["first_name"],
                                c["last_name"], c["role_raw"], c["email"],
                                c["email_domain"], c["confidence"], c["source"],
                                c["source_detail"],
                            ),
                        )
                        total_contacts += 1
                        if c["email"]:
                            total_emails += 1
                    except Exception as e:
                        log.debug("insert error for %s: %s", c["full_name"], e)

            # Update dealership state
            with get_conn() as conn:
                conn.execute(
                    "UPDATE dealerships SET scrape_state = 'extracted' WHERE id = %s",
                    (dealer_id,),
                )
        else:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE dealerships SET scrape_state = 'extraction_empty' WHERE id = %s",
                    (dealer_id,),
                )

        if (i + 1) % 25 == 0:
            log.info(
                "progress: %d/%d dealers, %d contacts, %d emails",
                i + 1, len(rows), total_contacts, total_emails,
            )

    log.info(
        "done: %d contacts from %d dealers (%d with emails)",
        total_contacts, dealers_with_contacts, total_emails,
    )
