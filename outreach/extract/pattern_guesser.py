import logging
from collections import Counter

log = logging.getLogger("outreach.extract.pattern_guesser")

FREEMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "aol.com", "mail.com",
    "protonmail.com", "fastmail.com", "zoho.com",
    "bigpond.com", "bigpond.net.au", "optusnet.com.au",
    "internode.on.net", "ozemail.com.au", "adam.com.au",
    "tpg.com.au", "dodo.com.au", "westnet.com.au",
}


def filter_dealership_emails(emails: list[str], dealership_domain: str | None = None) -> list[str]:
    """Filter emails to only those likely belonging to the dealership."""
    result = []
    for email in emails:
        domain = email.split("@")[1]
        if domain in FREEMAIL_DOMAINS:
            continue
        if dealership_domain and domain != dealership_domain:
            continue
        result.append(email)
    return result


def infer_email_pattern(known_emails: list[str]) -> tuple[str | None, str | None]:
    """Given known emails from a domain, infer the naming pattern.

    Returns (pattern, domain) where pattern is like 'first.last', 'flast', etc.
    Groups by domain and uses the most common domain.
    """
    if not known_emails:
        return None, None

    domain_counts = Counter(e.split("@")[1] for e in known_emails)
    domain = domain_counts.most_common(1)[0][0]
    prefixes = [e.split("@")[0] for e in known_emails if e.split("@")[1] == domain]

    if not prefixes:
        return None, None

    dot_count = sum(1 for p in prefixes if "." in p)
    underscore_count = sum(1 for p in prefixes if "_" in p)

    if dot_count > len(prefixes) / 2:
        return "first.last", domain

    if underscore_count > len(prefixes) / 2:
        return "first_last", domain

    # Need at least 2 emails to distinguish flast from first
    if len(prefixes) >= 2:
        all_alpha = [p for p in prefixes if p.isalpha() and len(p) > 2]
        if len(all_alpha) >= 2:
            avg_len = sum(len(p) for p in all_alpha) / len(all_alpha)
            if avg_len > 5:
                return "flast", domain
            elif avg_len <= 5:
                return "first", domain

    return None, domain


def guess_email(first_name: str, last_name: str | None, domain: str, pattern: str) -> str | None:
    """Generate a probable email from name and pattern."""
    if not first_name or not domain:
        return None

    first = first_name.lower().strip()
    last = (last_name or "").lower().strip()
    # Remove hyphens/apostrophes from names for email generation
    first = first.replace("'", "").replace("-", "").replace(" ", "")
    last = last.replace("'", "").replace("-", "").replace(" ", "")

    if not first:
        return None

    if pattern == "first.last" and last:
        return f"{first}.{last}@{domain}"
    elif pattern == "first_last" and last:
        return f"{first}_{last}@{domain}"
    elif pattern == "flast" and last:
        return f"{first[0]}{last}@{domain}"
    elif pattern == "first":
        return f"{first}@{domain}"
    elif pattern == "firstl" and last:
        return f"{first}{last[0]}@{domain}"
    elif pattern == "lastf" and last:
        return f"{last}{first[0]}@{domain}"
    elif pattern == "last.first" and last:
        return f"{last}.{first}@{domain}"
    elif pattern == "firstlast" and last:
        return f"{first}{last}@{domain}"

    return None
