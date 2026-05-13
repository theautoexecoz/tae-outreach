import re
import logging

log = logging.getLogger("outreach.extract.pattern_guesser")


def infer_email_pattern(known_emails: list[str]) -> str | None:
    """Given known emails from a domain, infer the naming pattern.

    Returns a pattern string like 'first.last', 'firstl', 'first', etc.
    """
    if not known_emails:
        return None

    domain = known_emails[0].split("@")[1]
    prefixes = [e.split("@")[0] for e in known_emails if e.split("@")[1] == domain]

    if not prefixes:
        return None

    # Check for common patterns
    dot_count = sum(1 for p in prefixes if "." in p)
    if dot_count > len(prefixes) / 2:
        return "first.last"

    # Check if prefixes look like first initial + last name (e.g. jsmith)
    initial_last = sum(1 for p in prefixes if len(p) > 2 and p[0].isalpha() and p[1:].isalpha())
    if initial_last > len(prefixes) / 2:
        avg_len = sum(len(p) for p in prefixes) / len(prefixes)
        if avg_len > 4:
            return "flast"
        return "first"

    return None


def guess_email(first_name: str, last_name: str | None, domain: str, pattern: str) -> str | None:
    """Generate a probable email from name and pattern."""
    if not first_name or not domain:
        return None

    first = first_name.lower().strip()
    last = (last_name or "").lower().strip()

    if pattern == "first.last" and last:
        return f"{first}.{last}@{domain}"
    elif pattern == "flast" and last:
        return f"{first[0]}{last}@{domain}"
    elif pattern == "first":
        return f"{first}@{domain}"
    elif pattern == "firstl" and last:
        return f"{first}{last[0]}@{domain}"

    return None
