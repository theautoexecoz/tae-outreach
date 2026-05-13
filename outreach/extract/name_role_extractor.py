import re
import logging
from bs4 import BeautifulSoup, Tag

log = logging.getLogger("outreach.extract.name_role")

SKIP_NAMES = {
    "team", "staff", "our team", "meet the team", "meet our team",
    "about us", "about", "contact", "enquiry", "form", "buy",
    "discover", "offers", "finance", "insurance", "service",
    "parts", "new", "used", "careers", "subscribe",
    "hatch & sedans", "electric & hybrids", "suvs & crossovers",
}

# Vehicle model prefixes/patterns to filter out
VEHICLE_PATTERNS = [
    r"^mazda\b", r"^toyota\b", r"^hyundai\b", r"^nissan\b", r"^subaru\b",
    r"^kia\b", r"^ford\b", r"^isuzu\b", r"^bmw\b", r"^mercedes\b",
    r"^volkswagen\b", r"^audi\b", r"^volvo\b", r"^jeep\b", r"^honda\b",
    r"^mitsubishi\b", r"^ldv\b", r"^mg\b", r"^byd\b", r"^gwm\b",
    r"^all[- ]new\b", r"^new\s+\w+\s+(mx|cx|bt|cr|hr|rav|hilux|ranger)",
    r"\b(suv|sedan|hatch|ute|van|wagon|coupe|cab)\b",
    r"\b(cx-\d|mx-\d|bt-\d|cr-v|hr-v|rav4|corolla|camry|hilux|ranger)\b",
    r"\bseats?\b", r"\bcab\b",
]

ROLE_KEYWORDS = [
    "dealer principal", "general manager", "sales manager",
    "service manager", "parts manager", "finance manager",
    "business manager", "used car manager", "new car manager",
    "sales consultant", "sales executive", "sales advisor",
    "service advisor", "service consultant", "receptionist",
    "manager", "director", "consultant", "advisor", "executive",
    "technician", "controller", "coordinator",
]


def _looks_like_name(text: str) -> bool:
    """Check if text looks like a person's name."""
    text = text.strip()
    if not text or len(text) < 3 or len(text) > 50:
        return False
    if text.lower() in SKIP_NAMES:
        return False
    # Names are typically 2-4 words, capitalised
    words = text.split()
    if len(words) < 1 or len(words) > 5:
        return False
    # At least first word should be capitalised
    if not words[0][0].isupper():
        return False
    # Should not contain common non-name chars
    if any(c in text for c in ["@", "(", ")", "$", "%", "!", "?"]):
        return False
    # Should not be all uppercase (likely a heading)
    if text.isupper() and len(text) > 10:
        return False
    # Filter vehicle models and non-name patterns
    for pattern in VEHICLE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return False
    return True


def _looks_like_role(text: str) -> bool:
    """Check if text looks like a job role."""
    text = text.strip().lower()
    if not text or len(text) > 60:
        return False
    return any(kw in text for kw in ROLE_KEYWORDS)


def _split_name(full_name: str) -> tuple[str | None, str | None]:
    """Split a full name into first and last name."""
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    elif len(parts) == 1:
        return parts[0], None
    return None, None


def extract_people(html: str) -> list[dict]:
    """Extract name/role pairs from team page HTML.

    Tries multiple strategies:
    1. Structured cards (divs with name heading + role paragraph)
    2. Heading + next-sibling role text
    3. List items with name/role patterns
    """
    soup = BeautifulSoup(html, "lxml")
    people = []
    seen_names = set()

    # Strategy 1: find card-like containers with both a name and role
    for container in soup.find_all(["div", "li", "article", "section"]):
        classes = " ".join(container.get("class", []))
        if not any(kw in classes.lower() for kw in ["team", "staff", "member", "person", "profile", "card", "employee", "bio"]):
            continue

        headings = container.find_all(["h2", "h3", "h4", "h5", "strong"])
        for heading in headings:
            name = heading.get_text(strip=True)
            if not _looks_like_name(name):
                continue

            role = ""
            # Look for role in siblings or children
            for sibling in heading.find_next_siblings(["p", "span", "div"])[:3]:
                candidate = sibling.get_text(strip=True)
                if _looks_like_role(candidate):
                    role = candidate
                    break
                if candidate and len(candidate) < 40 and not _looks_like_name(candidate):
                    role = candidate
                    break

            name_key = name.lower()
            if name_key not in seen_names:
                seen_names.add(name_key)
                first, last = _split_name(name)
                people.append({
                    "full_name": name,
                    "first_name": first,
                    "last_name": last,
                    "role_raw": role or None,
                })

    # Strategy 2: standalone headings followed by role text (no card wrapper)
    if not people:
        for heading in soup.find_all(["h2", "h3", "h4", "h5"]):
            name = heading.get_text(strip=True)
            if not _looks_like_name(name):
                continue

            role = ""
            sibling = heading.find_next_sibling(["p", "span", "div"])
            if sibling:
                candidate = sibling.get_text(strip=True)
                if len(candidate) < 50:
                    role = candidate

            name_key = name.lower()
            if name_key not in seen_names:
                seen_names.add(name_key)
                first, last = _split_name(name)
                people.append({
                    "full_name": name,
                    "first_name": first,
                    "last_name": last,
                    "role_raw": role or None,
                })

    # Strategy 3: look for patterns like "Name - Role" or "Name | Role" in text
    if not people:
        text = soup.get_text()
        for match in re.finditer(r'([A-Z][a-z]+ [A-Z][a-z]+)\s*[-–|]\s*(.{5,40})', text):
            name = match.group(1).strip()
            role = match.group(2).strip()
            if _looks_like_name(name):
                name_key = name.lower()
                if name_key not in seen_names:
                    seen_names.add(name_key)
                    first, last = _split_name(name)
                    people.append({
                        "full_name": name,
                        "first_name": first,
                        "last_name": last,
                        "role_raw": role,
                    })

    return people
