import re
import json
import logging
from bs4 import BeautifulSoup

log = logging.getLogger("outreach.extract.name_role")

SKIP_NAMES = {
    "team", "staff", "our team", "meet the team", "meet our team",
    "about us", "about", "contact", "enquiry", "form", "buy",
    "discover", "offers", "finance", "insurance", "service",
    "parts", "new", "used", "careers", "subscribe",
    "hatch & sedans", "electric & hybrids", "suvs & crossovers",
    "sales", "gallery", "hours", "directions", "location",
    "reviews", "blog", "news", "vehicles", "inventory",
    "specials", "accessories", "recall", "calculator",
    "home", "search", "menu", "close", "back", "next",
    "previous", "more", "view", "read", "learn",
    "legal", "privacy", "terms", "sitemap", "login",
    "company", "buying", "selling", "trading", "booking",
    "workshop", "bodyshop", "fleet", "corporate",
    "overview", "features", "pricing", "models", "range",
    "book a service", "take a test drive", "trading hours",
    "quick enquiry", "get a quote", "find a dealer",
    "request a quote", "build and price", "compare",
    "peace of mind", "customer care", "roadside assist",
    "genuine parts", "genuine accessories",
}

VEHICLE_MODEL_NAMES = {
    "seltos", "tucson", "venue", "compass", "impreza",
    "outback", "forester", "levorg", "wrx", "brz", "crosstrek",
    "corolla", "camry", "hilux", "landcruiser", "yaris", "kluger",
    "rav4", "prado", "fortuner", "granvia", "hiace", "supra",
    "ranger", "everest", "escape", "endura", "puma", "bronco",
    "mustang", "transit", "territory", "wildtrak",
    "cerato", "sportage", "sorento", "carnival", "stonic",
    "niro", "picanto", "stinger", "ev6", "ev9", "seltos",
    "outlander", "asx", "eclipse", "triton", "pajero", "delica",
    "qashqai", "pathfinder", "patrol", "navara", "juke",
    "leaf", "ariya", "x-trail",
    "cx-3", "cx-5", "cx-8", "cx-9", "cx-30", "cx-60", "cx-90",
    "mx-5", "mx-30", "bt-50", "mazda2", "mazda3", "mazda6",
    "d-max", "mu-x",
    "civic", "accord", "jazz", "cr-v", "hr-v", "wr-v", "zr-v",
    "haval", "cannon", "tank", "ora", "jolion", "dargo",
    "atto", "dolphin", "seal", "yuan", "tang", "han",
    "wrangler", "gladiator", "grand cherokee", "cherokee",
    "t60", "t90", "deliver", "ev60",
    "zs", "hs", "mg3", "mg4",
    "xc40", "xc60", "xc90", "s60", "v60", "c40", "ex30", "ex90",
    "a3", "a4", "a5", "a6", "a7", "a8", "q3", "q5", "q7", "q8",
    "e-tron", "rs3", "rs5", "rs6", "tt",
    "golf", "polo", "tiguan", "touareg", "amarok", "t-roc", "t-cross",
    "arteon", "passat", "caddy", "crafter", "transporter",
    "3 series", "5 series", "7 series", "x1", "x3", "x5", "x7",
    "ix", "i4", "i5", "i7", "m3", "m4", "m5", "z4",
    "a-class", "c-class", "e-class", "s-class", "cla", "gla",
    "glb", "glc", "gle", "gls", "eqa", "eqb", "eqc", "eqe", "eqs",
    "cooper", "countryman", "clubman", "paceman",
    "clio", "captur", "koleos", "megane", "arkana", "kangoo",
    "swift", "vitara", "jimny", "baleno", "ignis", "s-cross",
    "omoda", "jaecoo",
    "ioniq", "ioniq 5", "ioniq 5 n", "ioniq 5 n line", "ioniq 6", "ioniq 9",
    "kona", "palisade", "santa fe", "staria", "sonata",
    "sonata n line", "i30", "i30 n",
}

VEHICLE_PATTERNS = [
    r"^mazda\b", r"^toyota\b", r"^hyundai\b", r"^nissan\b", r"^subaru\b",
    r"^kia\b", r"^ford\b", r"^isuzu\b", r"^bmw\b", r"^mercedes\b",
    r"^volkswagen\b", r"^audi\b", r"^volvo\b", r"^jeep\b", r"^honda\b",
    r"^mitsubishi\b", r"^ldv\b", r"^mg\b", r"^byd\b", r"^gwm\b",
    r"^mini\b", r"^renault\b", r"^suzuki\b", r"^omoda\b", r"^jaecoo\b",
    r"^all[- ]new\b", r"^new\s+\w+\s+(mx|cx|bt|cr|hr|rav|hilux|ranger)",
    r"\b(suv|sedan|hatch|hatchback|ute|utes|van|vans|wagon|coupe|cab|convertible)\b",
    r"\b(suvs|sedans|hatches|coupes|wagons|convertibles)\b",
    r"\b(cx-\d|mx-\d|bt-\d|cr-v|hr-v|rav4|corolla|camry|hilux|ranger)\b",
    r"\bseats?\b", r"\bcab\b",
    r"\bhybrid\b", r"\belectric\b", r"\bev\b", r"\bphev\b",
    r"^(new|used|demo)\s+(car|vehicle)",
]

ROLE_KEYWORDS = [
    "dealer principal", "general manager", "sales manager",
    "service manager", "parts manager", "finance manager",
    "business manager", "used car manager", "new car manager",
    "sales consultant", "sales executive", "sales advisor",
    "service advisor", "service consultant", "receptionist",
    "manager", "director", "consultant", "advisor", "executive",
    "technician", "controller", "coordinator", "accountant",
    "apprentice", "detailer", "valuator", "estimator",
    "interpreter", "foreman", "specialist", "analyst",
    "principal",
]

CARD_CLASS_KEYWORDS = [
    "team", "staff", "member", "person", "profile", "card",
    "employee", "bio", "personnel", "crew", "people",
    "leadership", "management", "et_pb_team_member",
    "author", "avatar", "headshot",
]

NON_CONTENT_TAGS = ["script", "style", "nav", "header", "footer", "noscript", "iframe"]


def _strip_non_content(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(NON_CONTENT_TAGS):
        tag.decompose()


TITLE_WORDS = {
    "manager", "consultant", "advisor", "executive", "director",
    "principal", "coordinator", "controller", "specialist",
    "technician", "apprentice", "receptionist", "interpreter",
    "clerk", "assistant", "accountant", "foreman", "estimator",
    "valuator", "detailer", "analyst", "supervisor", "officer",
    "team", "department", "admin", "care", "operations",
    "customer", "fixed", "new", "used", "pre-owned",
    "sales", "service", "parts", "finance", "insurance",
    "business", "general", "brand", "communications",
    "delivery", "fleet", "wholesale", "digital", "marketing",
    "senior", "junior", "chief", "head", "lead",
}


def _looks_like_name(text: str) -> bool:
    text = text.strip()
    if not text or len(text) < 3 or len(text) > 50:
        return False
    if text.lower() in SKIP_NAMES:
        return False
    if text.lower() in VEHICLE_MODEL_NAMES:
        return False
    words = text.split()
    if len(words) < 2 or len(words) > 5:
        return False
    if not words[0][0].isupper():
        return False
    if any(c in text for c in ["@", "(", ")", "$", "%", "!", "?", "#", "+"]):
        return False
    if text.isupper() and len(text) > 10:
        return False
    for pattern in VEHICLE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return False
    if re.match(r'^\d', text):
        return False
    if any(w.lower().endswith(":") for w in words):
        return False
    # Reject if it looks like a job title (majority of words are title-words)
    title_word_count = sum(1 for w in words if w.lower().rstrip("s,") in TITLE_WORDS)
    if title_word_count > len(words) / 2:
        return False
    # Reject known brand+location patterns (e.g. "Darwin Jeep", "Moss Vale Nissan")
    if words[-1].lower() in {
        "jeep", "bmw", "audi", "volvo", "ford", "kia", "mazda", "nissan",
        "toyota", "hyundai", "honda", "subaru", "isuzu", "mitsubishi",
        "volkswagen", "mercedes", "ldv", "mg", "byd", "gwm", "mini",
        "renault", "suzuki",
    }:
        return False
    return True


def _looks_like_role(text: str) -> bool:
    text = text.strip().lower()
    if not text or len(text) > 60:
        return False
    if len(text) < 4:
        return False
    for kw in ROLE_KEYWORDS:
        idx = text.find(kw)
        if idx == -1:
            continue
        before = text[:idx].strip()
        if before and not before.endswith((" ", "-", "/", "&", ",")):
            continue
        return True
    return False


def _split_name(full_name: str) -> tuple[str | None, str | None]:
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    elif len(parts) == 1:
        return parts[0], None
    return None, None


def extract_jsonld_people(html: str) -> list[dict]:
    """Extract people from JSON-LD structured data (schema.org Person type).

    Must run on raw HTML before script tags are stripped.
    Returns contacts with email and role already attached.
    """
    people = []
    seen_names = set()
    soup = BeautifulSoup(html, "lxml")

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        items = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") != "Person":
                continue
            name = item.get("name", "").strip()
            if not name or len(name) < 3:
                continue
            name_key = name.lower()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)

            first, last = _split_name(name)
            email = (item.get("email") or "").strip().lower() or None
            role = (item.get("jobTitle") or "").strip() or None
            phone_raw = item.get("telephone")
            if isinstance(phone_raw, list):
                phone = phone_raw[0] if phone_raw else None
            else:
                phone = phone_raw
            people.append({
                "full_name": name,
                "first_name": first,
                "last_name": last,
                "role_raw": role,
                "email": email,
                "phone": phone,
            })

    if people:
        log.info("JSON-LD: extracted %d people", len(people))
    return people


def extract_people(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    _strip_non_content(soup)
    people = []
    seen_names = set()

    # Strategy 1: card-like containers with both a name and role
    for container in soup.find_all(["div", "li", "article", "section"]):
        classes = " ".join(container.get("class", []))
        if not any(kw in classes.lower() for kw in CARD_CLASS_KEYWORDS):
            continue

        headings = container.find_all(["h2", "h3", "h4", "h5", "strong", "b"])
        for heading in headings:
            name = heading.get_text(strip=True)
            if not _looks_like_name(name):
                continue

            role = ""
            for sibling in heading.find_next_siblings(["p", "span", "div"])[:3]:
                candidate = sibling.get_text(strip=True)
                if _looks_like_role(candidate):
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

    # Strategy 2: headings followed by a recognisable role (no card wrapper)
    if not people:
        for heading in soup.find_all(["h2", "h3", "h4", "h5"]):
            name = heading.get_text(strip=True)
            if not _looks_like_name(name):
                continue

            role = ""
            for sibling in heading.find_next_siblings(["p", "span", "div"])[:3]:
                candidate = sibling.get_text(strip=True)
                if _looks_like_role(candidate):
                    role = candidate
                    break

            if not role:
                continue

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

    # Strategy 3: "Name - Role" or "Name | Role" patterns in text
    if not people:
        text = soup.get_text()
        for match in re.finditer(r'([A-Z][a-z]+ [A-Z][a-z]+)\s*[-–|]\s*(.{5,40})', text):
            name = match.group(1).strip()
            role = match.group(2).strip()
            if _looks_like_name(name) and _looks_like_role(role):
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
