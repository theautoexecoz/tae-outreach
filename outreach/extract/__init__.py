import logging
from urllib.parse import urlparse
from outreach.db import get_conn
from outreach.extract.email_extractor import extract_emails
from outreach.extract.name_role_extractor import extract_people, extract_jsonld_people
from outreach.extract.pattern_guesser import (
    infer_email_pattern, guess_email, filter_dealership_emails,
)

log = logging.getLogger("outreach.extract")

ROLE_NORMALISATION = {
    "dealer principal": "Dealer Principal",
    "dp": "Dealer Principal",
    "general manager": "General Manager",
    "gm": "General Manager",
    "sales manager": "Sales Manager",
    "new car sales manager": "New Car Sales Manager",
    "used car sales manager": "Used Car Sales Manager",
    "new car manager": "New Car Sales Manager",
    "used car manager": "Used Car Sales Manager",
    "service manager": "Service Manager",
    "parts manager": "Parts Manager",
    "finance manager": "Finance Manager",
    "finance & insurance manager": "Finance Manager",
    "f&i manager": "Finance Manager",
    "business manager": "Business Manager",
    "sales consultant": "Sales Consultant",
    "sales executive": "Sales Executive",
    "sales advisor": "Sales Advisor",
    "service advisor": "Service Advisor",
    "service consultant": "Service Advisor",
    "parts interpreter": "Parts Interpreter",
    "receptionist": "Receptionist",
}

PREFIX_PATTERNS = [
    "{first}.{last}",
    "{f}{last}",
    "{first}",
    "{first}{l}",
    "{last}.{first}",
    "{last}{f}",
    "{first}_{last}",
    "{first}{last}",
]


def _normalise_role(raw: str | None) -> str | None:
    if not raw:
        return None
    lower = raw.strip().lower()
    for key, norm in ROLE_NORMALISATION.items():
        if key in lower:
            return norm
    return None


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    netloc = urlparse(url).netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


def _match_email_to_name(first: str, last: str, email_by_prefix: dict[str, str]) -> str | None:
    """Try multiple prefix patterns to match a known email to a name."""
    if not first:
        return None
    f = first.lower().replace("'", "").replace("-", "")
    l = last.lower().replace("'", "").replace("-", "") if last else ""

    for pattern in PREFIX_PATTERNS:
        try:
            prefix = pattern.format(first=f, last=l, f=f[0], l=l[0] if l else "")
        except (IndexError, KeyError):
            continue
        if prefix in email_by_prefix:
            return email_by_prefix[prefix]
    return None


def run_extraction(brand: str | None = None, limit: int = 0):
    """Extract contacts from scraped team page HTML."""
    with get_conn() as conn:
        query = (
            "SELECT id, name, team_page_url, team_page_html, website_url, api_email "
            "FROM dealerships WHERE team_page_html IS NOT NULL "
            "AND scrape_state IN ('scraped', 'extracted', 'extraction_empty')"
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

    log.info("extracting contacts from %d team pages", len(rows))
    total_contacts = 0
    total_emails = 0
    dealers_with_contacts = 0

    for i, row in enumerate(rows):
        html = row["team_page_html"]
        dealer_id = row["id"]
        contacts_for_dealer = []

        # Strategy 0: JSON-LD structured data (highest quality — name, email, role in one object)
        jsonld_people = extract_jsonld_people(html)
        jsonld_emails_used = set()

        if jsonld_people:
            for person in jsonld_people:
                email = person.get("email")
                email_domain = email.split("@")[1] if email and "@" in email else None
                role_norm = _normalise_role(person.get("role_raw"))
                contacts_for_dealer.append({
                    "dealership_id": dealer_id,
                    "full_name": person["full_name"],
                    "first_name": person.get("first_name"),
                    "last_name": person.get("last_name"),
                    "role_raw": person.get("role_raw"),
                    "role_normalised": role_norm,
                    "email": email,
                    "email_domain": email_domain,
                    "email_pattern": None,
                    "confidence": "direct" if email else "direct",
                    "source": "team_page",
                    "source_detail": row["team_page_url"],
                })
                if email:
                    jsonld_emails_used.add(email)

        # HTML-based extraction (complements JSON-LD — may find additional people)
        person_emails, _ = extract_emails(html)
        people = extract_people(html)

        dealer_domain = _extract_domain(row.get("website_url"))

        # Include API email from OEM dealer locator (reveals domain + sometimes personal)
        api_email = (row.get("api_email") or "").strip().lower()
        api_email_domain = None
        if api_email and "@" in api_email:
            api_email_domain = api_email.split("@")[1]

        # Filter to emails on the dealership's domain only
        matched_emails = filter_dealership_emails(person_emails, dealer_domain)

        # Also keep emails on non-freemail domains as candidates
        # (dealer groups may use a different domain than the website)
        all_business_emails = filter_dealership_emails(person_emails)

        # Add API email to the pool if it's not a freemail
        if api_email and api_email_domain:
            from outreach.extract.pattern_guesser import FREEMAIL_DOMAINS
            if api_email_domain not in FREEMAIL_DOMAINS:
                if api_email not in all_business_emails:
                    all_business_emails.append(api_email)

        # Use dealership-domain emails for pattern inference
        pattern, pattern_domain = infer_email_pattern(matched_emails)
        # If no pattern from dealer domain, try all business emails
        if not pattern and all_business_emails:
            pattern, pattern_domain = infer_email_pattern(all_business_emails)
        # If still no pattern, try JSON-LD emails
        if not pattern and jsonld_emails_used:
            pattern, pattern_domain = infer_email_pattern(list(jsonld_emails_used))
        # If still no pattern but API email reveals a domain, use that domain
        if not pattern and api_email_domain:
            email_domain_for_guessing = api_email_domain
        else:
            email_domain_for_guessing = pattern_domain or api_email_domain or dealer_domain

        # Build prefix lookup from all known emails (HTML + JSON-LD)
        email_by_prefix = {}
        for email in all_business_emails:
            prefix = email.split("@")[0].lower()
            email_by_prefix[prefix] = email
        for email in jsonld_emails_used:
            prefix = email.split("@")[0].lower()
            email_by_prefix[prefix] = email

        jsonld_names = {c["full_name"].lower() for c in contacts_for_dealer}

        for person in people:
            # Skip people already found via JSON-LD
            if person["full_name"].lower() in jsonld_names:
                continue

            first = person.get("first_name") or ""
            last = person.get("last_name") or ""

            email = _match_email_to_name(first, last, email_by_prefix)
            confidence = "direct" if email else None

            if not email and email_domain_for_guessing and first and last:
                if pattern:
                    email = guess_email(first, last, email_domain_for_guessing, pattern)
                    confidence = "inferred"
                elif api_email_domain:
                    email = guess_email(first, last, api_email_domain, "first.last")
                    confidence = "inferred"

            email_domain = email.split("@")[1] if email and "@" in email else None
            role_norm = _normalise_role(person.get("role_raw"))

            contacts_for_dealer.append({
                "dealership_id": dealer_id,
                "full_name": person["full_name"],
                "first_name": person.get("first_name"),
                "last_name": person.get("last_name"),
                "role_raw": person.get("role_raw"),
                "role_normalised": role_norm,
                "email": email,
                "email_domain": email_domain,
                "email_pattern": pattern,
                "confidence": confidence or "direct",
                "source": "team_page",
                "source_detail": row["team_page_url"],
            })

        # Retroactive pass: apply pattern/domain to contacts added before inference ran
        guess_pattern = pattern or ("first.last" if api_email_domain else None)
        guess_domain = email_domain_for_guessing or api_email_domain
        if guess_pattern and guess_domain:
            for c in contacts_for_dealer:
                if c["email"] or not c.get("first_name") or not c.get("last_name"):
                    continue
                matched = _match_email_to_name(c["first_name"], c["last_name"], email_by_prefix)
                if matched:
                    c["email"] = matched
                    c["email_domain"] = matched.split("@")[1]
                    c["confidence"] = "direct"
                else:
                    guessed = guess_email(c["first_name"], c["last_name"], guess_domain, guess_pattern)
                    if guessed:
                        c["email"] = guessed
                        c["email_domain"] = guessed.split("@")[1]
                        c["email_pattern"] = guess_pattern
                        c["confidence"] = "inferred"

        # Orphan emails: found on page but not matched to any extracted person.
        orphan_matched = {c["email"] for c in contacts_for_dealer if c["email"]}
        all_orphan_candidates = set(all_business_emails) | jsonld_emails_used
        for email in sorted(all_orphan_candidates):
            if email in orphan_matched:
                continue
            prefix = email.split("@")[0]
            email_domain = email.split("@")[1]
            # Accept two-part prefixes with separators (first.last, first_last)
            if "." in prefix:
                parts = prefix.split(".")
            elif "_" in prefix:
                parts = prefix.split("_")
            else:
                continue
            if len(parts) != 2 or not all(p.isalpha() and len(p) >= 2 for p in parts):
                continue
            first = parts[0].title()
            last = parts[1].title()
            contacts_for_dealer.append({
                "dealership_id": dealer_id,
                "full_name": f"{first} {last}",
                "first_name": first,
                "last_name": last,
                "role_raw": None,
                "role_normalised": None,
                "email": email,
                "email_domain": email_domain,
                "email_pattern": None,
                "confidence": "direct",
                "source": "team_page",
                "source_detail": row["team_page_url"],
            })

        if contacts_for_dealer:
            dealers_with_contacts += 1
            with get_conn() as conn:
                for c in contacts_for_dealer:
                    try:
                        conn.execute(
                            "INSERT INTO contacts "
                            "(dealership_id, full_name, first_name, last_name, "
                            "role_raw, role_normalised, "
                            "email, email_domain, email_pattern, confidence, "
                            "source, source_detail) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT DO NOTHING",
                            (
                                c["dealership_id"], c["full_name"], c["first_name"],
                                c["last_name"], c["role_raw"], c["role_normalised"],
                                c["email"], c["email_domain"], c["email_pattern"],
                                c["confidence"], c["source"], c["source_detail"],
                            ),
                        )
                        total_contacts += 1
                        if c["email"]:
                            total_emails += 1
                    except Exception as e:
                        log.debug("insert error for %s: %s", c["full_name"], e)

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

        if (i + 1) % 50 == 0:
            log.info(
                "progress: %d/%d dealers, %d contacts, %d emails",
                i + 1, len(rows), total_contacts, total_emails,
            )

    log.info(
        "done: %d contacts from %d dealers (%d with emails)",
        total_contacts, dealers_with_contacts, total_emails,
    )


def run_apply_patterns():
    """Retrospective pass: apply discovered email patterns across dealerships.

    Two strategies:
    1. Same-dealer: if a dealership has contacts with and without emails,
       infer the pattern and apply it (rare — usually all-or-nothing).
    2. Cross-dealer by website domain: if dealership A has emails on domain X,
       and dealership B shares the same website_url domain but has no emails,
       apply the pattern from A to B.
    3. Cross-dealer by email domain: if dealership A's contacts use email domain
       Y (which may differ from the website domain), find other dealerships
       whose website domain matches Y and apply the pattern there.
    """
    log.info("running retrospective pattern application")

    with get_conn() as conn:
        cur = conn.execute(
            "SELECT c.dealership_id, c.email, c.email_domain, d.website_url "
            "FROM contacts c "
            "JOIN dealerships d ON c.dealership_id = d.id "
            "WHERE c.email IS NOT NULL"
        )
        email_rows = cur.fetchall()

        cur = conn.execute(
            "SELECT c.id, c.dealership_id, c.first_name, c.last_name, d.website_url "
            "FROM contacts c "
            "JOIN dealerships d ON c.dealership_id = d.id "
            "WHERE c.email IS NULL AND c.first_name IS NOT NULL AND c.last_name IS NOT NULL"
        )
        no_email_rows = cur.fetchall()

    if not no_email_rows:
        log.info("no contacts without emails to process")
        return

    # Map dealership_id -> emails
    dealer_emails: dict[int, list[str]] = {}
    for row in email_rows:
        dealer_emails.setdefault(row["dealership_id"], []).append(row["email"])

    # Map website_domain -> emails from all dealerships on that domain
    site_domain_emails: dict[str, list[str]] = {}
    for row in email_rows:
        site_domain = _extract_domain(row["website_url"])
        if site_domain:
            site_domain_emails.setdefault(site_domain, []).append(row["email"])

    # Map website_domain -> emails discovered at dealerships whose website
    # domain matches the *email* domain (not the website domain).
    # e.g. emails @glenelgbmw.com.au found at a dealer whose website is different
    # can be used for a dealer whose website IS glenelgbmw.com.au
    email_domain_patterns: dict[str, list[str]] = {}
    for row in email_rows:
        if row["email_domain"]:
            email_domain_patterns.setdefault(row["email_domain"], []).append(row["email"])

    # Group no-email contacts by dealership_id
    dealer_no_email: dict[int, list[dict]] = {}
    dealer_site_domain: dict[int, str] = {}
    for row in no_email_rows:
        dealer_no_email.setdefault(row["dealership_id"], []).append(row)
        site_domain = _extract_domain(row["website_url"])
        if site_domain:
            dealer_site_domain[row["dealership_id"]] = site_domain

    updated = 0
    domains_processed = set()

    for dealer_id, contacts in dealer_no_email.items():
        # Try to find emails to derive a pattern from, in order of specificity:
        # 1. Emails at this exact dealership
        # 2. Emails at dealerships sharing the same website domain
        # 3. Emails whose email_domain matches this dealer's website domain
        emails = dealer_emails.get(dealer_id, [])
        site_domain = dealer_site_domain.get(dealer_id)

        if not emails and site_domain:
            emails = site_domain_emails.get(site_domain, [])

        if not emails and site_domain:
            emails = email_domain_patterns.get(site_domain, [])

        if not emails:
            continue

        pattern, pattern_domain = infer_email_pattern(emails)
        if not pattern or not pattern_domain:
            continue

        domains_processed.add(pattern_domain)

        with get_conn() as conn:
            for contact in contacts:
                guessed = guess_email(
                    contact["first_name"],
                    contact["last_name"],
                    pattern_domain,
                    pattern,
                )
                if not guessed:
                    continue

                email_domain = guessed.split("@")[1]
                try:
                    conn.execute(
                        "UPDATE contacts SET email = %s, email_domain = %s, "
                        "email_pattern = %s, confidence = 'inferred' "
                        "WHERE id = %s AND email IS NULL",
                        (guessed, email_domain, pattern, contact["id"]),
                    )
                    updated += 1
                except Exception as e:
                    log.debug("pattern apply error for contact %s: %s", contact["id"], e)

    log.info(
        "retrospective patterns: %d email domains processed, %d contacts updated",
        len(domains_processed), updated,
    )


DEPARTMENT_LABELS = {
    "new sales", "used sales", "new & used sales", "new/used sales",
    "new and used sales", "pre owned sales", "pre-owned sales",
    "premium used cars", "certified pre-owned",
    "new car sales", "used car sales",
    "customer relations", "customer experience", "customer care",
    "the team", "our team", "meet the team", "team",
    "service & parts", "service & bodyshop", "service and parts",
    "parts & accessories", "parts and accessories",
    "finance, insurance & aftercare", "finance & insurance",
    "finance, insurance & car care", "finance and insurance",
    "sales department", "service department", "parts department",
    "sales team", "service team", "parts team", "admin team",
    "workshop", "bodyshop", "body shop",
    "fleet sales", "fleet department",
    "wholesale", "wholesale department",
    "enquiry form", "contact us", "contact form", "get in touch",
    "friendly staff", "our staff", "meet our staff",
    "sales manager", "service manager", "parts manager",
    "finance manager", "business manager", "general manager",
    "dealer principal", "sales consultant", "sales executive",
    "sales advisor", "service advisor", "service consultant",
    "parts interpreter", "receptionist",
    "graphic designer", "marketing coordinator",
    "account manager", "brand manager", "digital manager",
    "marketing manager", "operations manager",
    "administration manager", "administration clerk",
    "administrative assistant", "assistant accountant",
    "assistant sales manager", "assistant service manager",
    "brand and communications manager",
    "marketing and e-commerce coordinator",
    "fixed operations manager",
}

DEPARTMENT_PATTERNS = [
    r"^(new|used|pre[- ]?owned|certified|premium)\s+(car\s+)?sales?$",
    r"^(sales|service|parts|finance|fleet|admin|workshop)\s+(team|department)$",
    r"^(service|parts)\s*[&+]\s*(parts|bodyshop|accessories|service)$",
    r"^finance[,\s]*(insurance|aftercare|car care)",
    r"^customer\s+(relations|experience|care|service)$",
    r"^(the|our|meet the)\s+team$",
    r"^enquiry\s+(form|now)$",
    r"^contact\s+(us|form|dealer)$",
    r"^(friendly|our|meet our)\s+staff$",
]


def run_cleanup():
    """Remove contacts that are department labels rather than people.

    Targets contacts with no email whose full_name matches known department
    label patterns. These come from card-based extraction where the card
    container has team CSS classes but the 'name' heading is a department.
    """
    import re
    log.info("running department label cleanup")

    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, full_name FROM contacts WHERE email IS NULL"
        )
        rows = cur.fetchall()

    removed = 0
    with get_conn() as conn:
        for row in rows:
            name_lower = row["full_name"].strip().lower()

            is_dept = name_lower in DEPARTMENT_LABELS
            if not is_dept:
                for pattern in DEPARTMENT_PATTERNS:
                    if re.match(pattern, name_lower):
                        is_dept = True
                        break

            if is_dept:
                conn.execute("DELETE FROM contacts WHERE id = %s", (row["id"],))
                removed += 1

    log.info("cleanup: removed %d department label contacts", removed)
