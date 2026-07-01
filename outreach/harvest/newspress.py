"""Newspress Australia scrape — Email-list finalisation program §1b (TAE-2606-07).

Harvests PR / marketing contacts from OEM press releases on newspressaustralia.com.
Net-new audience beyond dealerships — feeds the industry-proximity T1/T2 tiers
(OEMs + importers, industry bodies + suppliers).

The site is a Nuxt SPA over a Laravel API (`/newspress-api`). The browsable release
LIST (`/releases/get-releases`) is behind a Sanctum media login — we call it with a
logged-in session's Cookie header (`NEWSPRESS_COOKIE`, runtime -e, not persisted).
Individual releases (`/public/releases/get-release/{id}`) are PUBLIC. So: list the
release ids (authenticated), then fetch + parse each release (public).

Each release `content` carries a contact block in one of two shapes:
  - a column-oriented Word table (row1 names / row2 companies / row3 phones /
    row4 emails; each contact is a column) — the table names people even when the
    email local-part can't (flast+digit, e.g. ctwelft1@ford.com → Christina Twelftree);
  - inline text ("… Leanne Blanckenberg … Email: leanne.blanckenberg@bmw.com.au") —
    here the local-part itself resolves the name.
We parse both, reusing the OOO harvester's email/name helpers.

Contacts land as source='newspress', confidence='direct' (published → GREEN),
company + release title in source_detail, dealership_id NULL. Idempotent (unique
email index). A browser UA is required (a bot UA is served an empty SPA shell).
"""
import logging
import re
import time
from collections import Counter

import httpx
from bs4 import BeautifulSoup

from outreach.config import (
    NEWSPRESS_BASE,
    NEWSPRESS_COOKIE,
    NEWSPRESS_RPS,
    NEWSPRESS_UA,
    SELF_DOMAINS,
)
from outreach.db import get_conn
from outreach.extract import _normalise_role
from outreach.extract.pattern_guesser import FREEMAIL_DOMAINS
from outreach.harvest.ooo import (
    DIGIT_RUN_RE,
    EMAIL_RE,
    HASH_LOCAL_RE,
    ROLE_LOCALPARTS,
    _clean_email,
    _name_from_local,
    _name_near,
    _resolve_name,
    _role_near,
    _split_name,
)

log = logging.getLogger("outreach.harvest.newspress")

API = NEWSPRESS_BASE.rstrip("/") + "/newspress-api"
# Newspress's own domains are never harvested (their newsroom / info@ addresses).
NP_SELF_DOMAINS = set(SELF_DOMAINS) | {"newspressaustralia.com", "newspress.com"}
CONTACT_HEADER_RE = re.compile(
    r"\b(contacts?|media enqu|media contact|further information|press (?:contact|office))\b", re.I
)
# PR/media shared inboxes on top of the generic OOO role set — not individuals.
NP_ROLE_TOKENS = set(ROLE_LOCALPARTS) | {
    "media", "pr", "press", "comms", "communications", "communication",
    "newsroom", "publicrelations", "mediarelations", "corporateaffairs",
    "prteam", "mediateam", "pressoffice", "media.relations",
}
# Tokens that mean a capitalised phrase is a role/label/company, not a person's name.
NAME_STOPWORDS = {
    # company / org
    "australia", "australian", "pty", "ltd", "limited", "group", "holdings",
    "inc", "corp", "corporation", "company", "co", "motors", "motor",
    "automotive", "consulting", "partners", "solutions", "services",
    "enterprises", "ogilvy", "communications", "communication",
    # role / label
    "manager", "director", "officer", "chief", "executive", "consultant",
    "advisor", "adviser", "principal", "coordinator", "president", "head",
    "specialist", "representative", "associate", "supervisor", "lead",
    "relations", "public", "product", "external", "corporate", "affairs",
    "media", "press", "marketing", "sales", "service", "finance", "insurance",
    "newsroom", "department", "division", "office", "team",
    "mobile", "telephone", "phone", "email", "enquiries", "enquiry",
    "contact", "contacts", "national", "global", "international",
    "region", "regional",
}


def _good_name(t: str) -> bool:
    """A real person's name: 2-3 tokens, none a role/label/company word."""
    if not t:
        return False
    toks = [w.lower().strip(".,'") for w in t.split()]
    if not (2 <= len(toks) <= 3):
        return False
    return not any(w in NAME_STOPWORDS for w in toks)


def _is_name(t: str) -> bool:
    """Capitalised, person-shaped cell (for locating the table's name row)."""
    if not t or not all(re.match(r"^[A-Z][a-zA-Z'\-]+$", w) for w in t.split()):
        return False
    return _good_name(t)


def _disallowed(local: str, domain: str) -> str | None:
    """Reason this address is not a harvestable personal contact, else None."""
    if domain in NP_SELF_DOMAINS:
        return "self"
    if domain in FREEMAIL_DOMAINS:
        return "freemail"
    first_tok = re.split(r"[._\-]", local, 1)[0]
    if local in NP_ROLE_TOKENS or first_tok in NP_ROLE_TOKENS or local.startswith(("noreply", "no-reply", "mailer")):
        return "role"
    if HASH_LOCAL_RE.match(local) or DIGIT_RUN_RE.search(local) or len(local) > 40:
        return "opaque"
    return None


def _parse_table_contacts(soup) -> dict[str, str]:
    """{email: name} from column-oriented contact tables (name row aligned to email row)."""
    out: dict[str, str] = {}
    for table in soup.find_all("table"):
        if "@" not in table.get_text():
            continue
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for td in tr.find_all(["td", "th"]):
                a = td.find("a", href=re.compile(r"^mailto:", re.I))
                cells.append(("mail", a["href"][7:]) if a else ("text", td.get_text(" ", strip=True)))
            rows.append(cells)
        hi = next(
            (i for i, r in enumerate(rows) if CONTACT_HEADER_RE.search(" ".join(c[1] for c in r))),
            None,
        )
        data = rows[hi + 1:] if hi is not None else rows
        ei = next((i for i, r in enumerate(data) if any("@" in c[1] for c in r)), None)
        if ei is None:
            continue
        erow = data[ei]
        nrow = next(
            (data[i] for i in range(ei) if any(_is_name(c[1]) for c in data[i])), None
        )
        for c in range(len(erow)):
            kind, val = erow[c]
            m = EMAIL_RE.search(val)
            email = _clean_email(val if kind == "mail" else (m.group(0) if m else ""))
            if not email:
                continue
            name = nrow[c][1] if nrow and c < len(nrow) and _is_name(nrow[c][1]) else None
            if name and email not in out:
                out[email] = name
            else:
                out.setdefault(email, name)
    return out


def _parse_release(content_html: str) -> list[dict]:
    """(email, full_name, role_raw) contacts from one release's content."""
    if not content_html:
        return []
    soup = BeautifulSoup(content_html, "lxml")
    table_names = _parse_table_contacts(soup)          # authoritative names (incl. flast+digit)
    text = soup.get_text(" ")
    text_lower = text.lower()

    found: dict[str, dict] = {}
    # every business email in the release, from the table map + a flat scan
    candidates = set(table_names)
    for m in EMAIL_RE.finditer(text):
        ce = _clean_email(m.group(0))
        if ce:
            candidates.add(ce)

    for email in candidates:
        local, _, domain = email.partition("@")
        if not domain or _disallowed(local, domain):
            continue
        table_name = table_names.get(email)
        pos = text_lower.find(email)
        ctx_name = _name_near(text, pos) if pos != -1 else None
        full_name = table_name or _resolve_name(local, ctx_name)
        if not full_name or not _good_name(full_name):   # reject role/label/company "names"
            continue
        role_raw = _role_near(text, pos) if pos != -1 else None
        first, last = _split_name(full_name)
        found[email] = {
            "email": email, "email_domain": domain, "full_name": full_name,
            "first_name": first, "last_name": last,
            "role_raw": role_raw, "role_normalised": _normalise_role(role_raw),
        }
    return list(found.values())


def _client(cookie: str) -> httpx.Client:
    headers = {"User-Agent": NEWSPRESS_UA, "Accept": "application/json",
               "Referer": NEWSPRESS_BASE.rstrip("/") + "/releases"}
    if cookie:
        headers["Cookie"] = cookie
        m = re.search(r"XSRF-TOKEN=([^;]+)", cookie)
        if m:
            from urllib.parse import unquote
            headers["X-XSRF-TOKEN"] = unquote(m.group(1))
    return httpx.Client(timeout=30, headers=headers, follow_redirects=True)


def _get_json(c: httpx.Client, path: str, params: dict | None = None) -> dict | None:
    for attempt in range(3):
        r = c.get(API + path, params=params)
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        if r.status_code == 429:
            time.sleep(2 ** attempt * 2)
            continue
        if r.status_code == 404:
            return None
        raise RuntimeError(f"newspress {path} -> {r.status_code}: {r.text[:160]}")
    return None


def _list_ids(c: httpx.Client, max_pages: int, per_page: int = 100) -> list[int]:
    """Authenticated release-id list (Laravel paginator), newest first, across pages."""
    ids: list[int] = []
    page = 1
    while True:
        data = _get_json(c, "/releases/get-releases",
                         {"page": page, "perPage": per_page, "order": "desc"})
        if not data:
            raise RuntimeError(
                "release list returned nothing — is NEWSPRESS_COOKIE a valid logged-in "
                "session? (the list route is auth-gated; a bad/expired cookie 404s "
                "'Unauthenticated')."
            )
        block = data.get("data") or {}          # Laravel paginator object
        items = (block.get("data") if isinstance(block, dict) else block) or []
        for it in items:
            rid = it.get("id") if isinstance(it, dict) else None
            if rid:
                ids.append(int(rid))
        last = block.get("last_page") if isinstance(block, dict) else None
        total = block.get("total") if isinstance(block, dict) else None
        log.info("list page %d/%s: +%d ids (%d/%s)", page, last or "?", len(items), len(ids), total or "?")
        if not items or (last and page >= last) or (max_pages and page >= max_pages):
            break
        page += 1
        time.sleep(1.0 / NEWSPRESS_RPS if NEWSPRESS_RPS > 0 else 0)
    return list(dict.fromkeys(ids))            # de-dupe, keep newest-first order


def run_newspress_harvest(limit: int = 0, dry_run: bool = False,
                          max_pages: int = 0, only_id: int | None = None) -> dict:
    """Harvest PR contacts from Newspress releases.

    only_id: fetch + parse a single public release (no cookie needed) — for testing.
    limit>0 caps the number of releases processed; max_pages caps list pagination.
    """
    stats = Counter()
    contacts: dict[str, dict] = {}

    with _client(NEWSPRESS_COOKIE) as c:
        if only_id is not None:
            ids = [only_id]
        else:
            if not NEWSPRESS_COOKIE:
                raise SystemExit(
                    "NEWSPRESS_COOKIE is empty. Provide a logged-in Newspress session's "
                    "Cookie header at runtime, e.g. `docker compose run --rm "
                    "-e NEWSPRESS_COOKIE=\"<cookie>\" app python -m outreach newspress-harvest`. "
                    "In the browser: log in → DevTools → Network → open a "
                    "/newspress-api/releases request → copy the Cookie header (or Copy as cURL)."
                )
            ids = _list_ids(c, max_pages=max_pages)
        if limit:
            ids = ids[:limit]
        log.info("processing %d release(s)", len(ids))

        for i, rid in enumerate(ids):
            try:
                data = _get_json(c, f"/public/releases/get-release/{rid}")
            except Exception as e:            # one bad release must not abort a long run
                log.warning("release %s fetch error: %s", rid, e)
                stats["errors"] += 1
                data = None
            rel = (data or {}).get("data") if data else None
            if not rel:
                stats["missing"] += 1
                continue
            stats["releases"] += 1
            if i and i % 250 == 0:
                log.info("progress %d/%d releases, %d unique contacts", i, len(ids), len(contacts))
            client_name = ((rel.get("client") or {}).get("name") or "").strip()
            title = (rel.get("title") or "").strip()
            detail = f"newspress: {client_name} — {title}"[:250] if client_name else f"newspress: {title}"[:250]
            for person in _parse_release(rel.get("content") or ""):
                stats["candidates"] += 1
                person["source_detail"] = detail
                contacts.setdefault(person["email"], person)
            if only_id is None and i and NEWSPRESS_RPS > 0:
                time.sleep(1.0 / NEWSPRESS_RPS)

    summary = {
        "releases": stats["releases"], "missing": stats["missing"],
        "errors": stats["errors"], "contacts": len(contacts),
        "inserted": 0, "dry_run": dry_run,
    }
    if dry_run:
        log.info("dry-run: %d releases, %d unique contacts (no writes)",
                 stats["releases"], len(contacts))
        summary["sample_contacts"] = list(contacts.values())[:20]
        return summary

    with get_conn() as conn:
        for p in contacts.values():
            cur = conn.execute(
                "INSERT INTO contacts "
                "(dealership_id, full_name, first_name, last_name, role_raw, "
                " role_normalised, email, email_domain, confidence, source, source_detail) "
                "VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, 'direct', 'newspress', %s) "
                "ON CONFLICT DO NOTHING RETURNING id",
                (p["full_name"], p["first_name"], p["last_name"], p["role_raw"],
                 p["role_normalised"], p["email"], p["email_domain"], p["source_detail"]),
            )
            if cur.fetchone():
                summary["inserted"] += 1
    log.info("newspress-harvest: %d releases → %d unique contacts, %d new inserted",
             stats["releases"], len(contacts), summary["inserted"])
    return summary
