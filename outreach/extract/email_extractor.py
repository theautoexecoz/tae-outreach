import re
import logging
from bs4 import BeautifulSoup

log = logging.getLogger("outreach.extract.email")

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

GENERIC_PREFIXES = {
    "info", "sales", "service", "parts", "admin", "reception",
    "enquiries", "contact", "noreply", "no-reply", "accounts",
    "marketing", "support", "hello", "enquiry", "booking",
    "bookings", "general", "finance", "warranty", "body",
    "servicemazda", "partsmazda", "mazda", "newcars", "usedcars",
    "tradein", "testdrive", "fleet", "corporate",
}

GENERIC_PATTERNS = [
    r"^(service|parts|sales|admin|reception|enquir|contact|info|booking)",
    r"mazda$", r"toyota$", r"hyundai$", r"nissan$", r"subaru$",
    r"^(new|used)cars?$", r"^(trade|test)", r"^fleet$", r"^corporate$",
]


def extract_emails(html: str) -> tuple[list[str], list[str]]:
    """Extract emails from HTML. Returns (person_emails, generic_emails)."""
    all_emails = set()

    # Mailto links
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip().lower()
            if email and "@" in email:
                all_emails.add(email)

    # Regex across full HTML (catches emails in text, data attributes, etc.)
    for match in EMAIL_RE.finditer(html):
        email = match.group().lower()
        # Skip obvious non-emails
        if email.endswith(".png") or email.endswith(".jpg") or email.endswith(".svg"):
            continue
        if "@sentry" in email or "@cloudflare" in email or "@example" in email:
            continue
        all_emails.add(email)

    person = []
    generic = []
    for email in sorted(all_emails):
        prefix = email.split("@")[0]
        is_generic = prefix in GENERIC_PREFIXES
        if not is_generic:
            for pat in GENERIC_PATTERNS:
                if re.search(pat, prefix, re.IGNORECASE):
                    is_generic = True
                    break
        if is_generic:
            generic.append(email)
        else:
            person.append(email)

    return person, generic
