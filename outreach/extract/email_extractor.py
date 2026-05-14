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
    "newcars", "usedcars", "tradein", "testdrive", "fleet",
    "corporate", "wholesale",
    "careers", "jobs", "hr", "recruitment",
    "workshop", "bodyshop", "panel", "detailing",
    "digital", "web", "internet", "online",
    "fixed", "aftermarket",
    "events", "feedback", "complaints",
    "demo", "delivery", "predelivery",
    "manager", "dealer", "dealership",
}

GENERIC_PATTERNS = [
    r"^(service|parts|sales|admin|reception|enquir|contact|info|booking)",
    r"^(new|used)cars?",
    r"^(trade|test)",
    r"^fleet", r"^corporate", r"^wholesale",
    r"^(career|job|recruit|hiring)",
    r"^(workshop|bodyshop|body-shop|panel)",
    r"^(digital|web|internet|online)",
    r"^(event|feedback|complaint)",
    r"^(demo|deliver)",
    r"(mazda|toyota|hyundai|nissan|subaru|bmw|audi|volvo|ford|kia)$",
    r"(honda|isuzu|mitsubishi|mercedes|volkswagen|ldv|mg|byd|gwm|jeep)$",
    r"(service|parts|sales)(mazda|toyota|hyundai|nissan|subaru|bmw|ford|kia)",
    r"(mazda|toyota|hyundai|nissan|subaru|bmw|ford|kia)(service|parts|sales)",
]

INFRASTRUCTURE_DOMAINS = {
    "google.com", "gmail.com", "facebook.com", "twitter.com",
    "instagram.com", "youtube.com", "linkedin.com",
    "gravatar.com", "wordpress.com", "wpengine.com",
    "schema.org", "w3.org", "cloudflare.com", "sentry.io",
    "example.com", "test.com", "localhost",
    "googleusercontent.com", "gstatic.com",
    "apple.com", "microsoft.com", "outlook.com",
    "hotmail.com", "yahoo.com", "aol.com",
    "mailchimp.com", "sendgrid.net", "amazonaws.com",
    "zendesk.com", "intercom.io", "hubspot.com",
    "salesforce.com", "marketo.com",
    "wixsite.com", "squarespace.com", "shopify.com",
}

NON_CONTENT_TAGS = ["script", "style", "noscript", "iframe"]


def _is_infrastructure_email(email: str) -> bool:
    domain = email.split("@")[1]
    if domain in INFRASTRUCTURE_DOMAINS:
        return True
    for infra in INFRASTRUCTURE_DOMAINS:
        if domain.endswith("." + infra):
            return True
    return False


def extract_emails(html: str) -> tuple[list[str], list[str]]:
    """Extract emails from visible page content. Returns (person_emails, generic_emails)."""
    all_emails = set()

    soup = BeautifulSoup(html, "lxml")

    # Remove non-content tags before scanning
    for tag in soup.find_all(NON_CONTENT_TAGS):
        tag.decompose()

    # Mailto links (from visible content only now)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip().lower()
            if email and "@" in email:
                all_emails.add(email)

    # Regex on visible text content only
    visible_text = soup.get_text()
    for match in EMAIL_RE.finditer(visible_text):
        email = match.group().lower()
        if email.endswith(".png") or email.endswith(".jpg") or email.endswith(".svg"):
            continue
        all_emails.add(email)

    person = []
    generic = []
    for email in sorted(all_emails):
        if _is_infrastructure_email(email):
            continue

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
