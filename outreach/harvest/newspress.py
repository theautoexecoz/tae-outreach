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
from datetime import date, datetime, timedelta, timezone
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
    _detect_format,
    _slug,
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



# ── Appointment harvesting (TAE-2607-25, GB 2026-07-28) ────────────────────
#
# A staff-appointment release names the INCOMING person but almost never
# publishes their address — they have not started yet. The address we can see is
# the PR contact's. Where that PR contact works for the OEM itself (not its
# agency), their local-part reveals the employer's address format, and we can
# extrapolate the appointee's likely address.
#
# Everything produced here is a GUESS. It is written with confidence='inferred'
# and source='newspress_appointment', which keeps it out of Project Postie's
# send universe (that selector requires confidence='direct'). Promoting a guess
# to sendable is GB's decision, not this harvester's.

APPOINTMENT_TITLE_RE = re.compile(
    r"\b(appoint\w*|joins|joining|named|welcomes|welcomed|promot\w*|"
    r"new (?:chief|head|director|manager|general manager|ceo|md|managing director)|"
    r"steps? (?:up|into)|takes? (?:up|over|the reins)|succeeds?)\b", re.I)

# Ordered most-specific first. Each must capture the person as group 'name';
# 'role' where the sentence gives it.
_APPT_PATTERNS = [
    re.compile(r"(?:has |have )?appointed\s+(?P<name>[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){1,2})"
               r"(?:\s+(?:as|to)\s+(?:its |the |a |an )?(?P<role>[^.,;()\n]{3,60}))?"),
    re.compile(r"(?P<name>[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){1,2})\s+has been appointed"
               r"(?:\s+(?:as|to)\s+)?(?:its |the |a |an )?(?P<role>[^.,;()\n]{3,60})?"),
    re.compile(r"(?P<name>[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){1,2})\s+(?:has |will )?"
               r"join(?:s|ed|ing)?\b[^.,;\n]{0,40}?\s+as\s+(?:its |the |a |an )?(?P<role>[^.,;()\n]{3,60})"),
    re.compile(r"(?:the )?appointment of\s+(?P<name>[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){1,2})"
               r"(?:\s+(?:as|to)\s+(?:its |the |a |an )?(?P<role>[^.,;()\n]{3,60}))?"),
    re.compile(r"(?P<name>[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){1,2})\s+has been named"
               r"(?:\s+(?:as|to)\s+)?(?:its |the |a |an )?(?P<role>[^.,;()\n]{3,60})?"),
    re.compile(r"(?:welcomes?|welcomed)\s+(?P<name>[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){1,2})"
               r"(?:\s+(?:as|to)\s+(?:its |the |a |an )?(?P<role>[^.,;()\n]{3,60}))?"),
]

# Agency / third-party domains: their address format tells us nothing about the
# OEM the appointee is joining. Seeded from the agencies actually seen across
# the Newspress corpus and the OOO harvest; extend as new ones appear.
AGENCY_DOMAIN_TOKENS = {
    "ogilvy", "havas", "havasmedia", "omnicom", "omc", "sparkfoundry",
    "sparkfoundryww", "starcom", "publicis", "wpp", "vmlyr", "mccann",
    "edelman", "webershandwick", "weber", "hillandknowlton", "hkstrategies",
    "fleishman", "fleishmanhillard", "bcw", "burson", "golin", "ketchum",
    "porternovelli", "cannings", "sefiani", "prcomms", "poemgroup",
    "thecampaignpalace", "clemenger", "initiative", "mediacom", "essencemediacom",
    "wavemaker", "zenithmedia", "carat", "dentsu", "iprospect", "mindshare",
    "pitchpr", "keepleft", "haystac", "herdmsl", "msl", "n2n", "poem",
    "connectedcontent",
}


def _client_tokens(client_name: str) -> set[str]:
    """Meaningful tokens of the release's client (the OEM), for in-house tests."""
    drop = {"australia", "australian", "pty", "ltd", "limited", "group", "motors",
            "motor", "automotive", "cars", "car", "company", "co", "inc", "corp",
            "corporation", "holdings", "the", "and", "of", "new", "zealand", "anz"}
    toks = {re.sub(r"[^a-z0-9]", "", t.lower()) for t in (client_name or "").split()}
    return {t for t in toks if t and t not in drop and len(t) >= 3}


def _is_inhouse(domain: str, client_name: str) -> bool:
    """True when this address belongs to the OEM issuing the release, not its agency.

    Deliberately strict: an unrecognised domain is treated as NOT in-house, so an
    unknown agency produces no extrapolation rather than a wrong one.
    """
    if not domain:
        return False
    labels = domain.lower().split(".")
    core = {l for l in labels if l not in ("com", "au", "net", "org", "co", "nz")}
    if core & AGENCY_DOMAIN_TOKENS:
        return False
    ctoks = _client_tokens(client_name)
    if not ctoks:
        return False
    for c in ctoks:
        for l in core:
            if c == l or (len(c) >= 4 and (c in l or l in c)):
                return True
    return False


def _parse_appointments(content_html: str, title: str) -> list[dict]:
    """Incoming people named in an appointment release (name + role, no email)."""
    if not content_html:
        return []
    blob = f"{title or ''}. "
    soup = BeautifulSoup(content_html, "lxml")
    text = soup.get_text(" ")
    if not APPOINTMENT_TITLE_RE.search(blob + text[:1500]):
        return []
    out: dict[str, dict] = {}
    # Only the opening of a release announces the appointment; later paragraphs
    # quote other executives, whose names must not be harvested as appointees.
    head = (blob + text)[:1800]
    for pat in _APPT_PATTERNS:
        for m in pat.finditer(head):
            name = (m.group("name") or "").strip()
            if not _good_name(name):
                continue
            role = None
            try:
                role = (m.group("role") or "").strip() or None
            except IndexError:
                pass
            if role:
                role = re.sub(r"\s+", " ", role).strip(" .,-")
                # Trim the trailing clauses these sentences habitually carry:
                # "... Director at Toyota Australia", "... Manager from March",
                # "... Officer effective 1 September".
                role = re.split(r"\s+(?:at|for|of the|with)\s+[A-Z]", role)[0]
                role = re.split(r"\s+(?:from|effective|commencing|starting|beginning)\b",
                                role, flags=re.I)[0]
                role = role.strip(" .,-")
                if not role or len(role.split()) > 8:
                    role = None
            out.setdefault(name, {"full_name": name, "role_raw": role})
            if role and not out[name].get("role_raw"):
                out[name]["role_raw"] = role
    return list(out.values())


def _apply_format(fmt: str, first: str, last: str) -> str | None:
    f, l = _slug(first), _slug(last)
    if not f or not l:
        return None
    return {
        "first.last": f"{f}.{l}", "first_last": f"{f}_{l}", "flast": f"{f[0]}{l}",
        "firstl": f"{f}{l[0]}", "firstlast": f"{f}{l}", "last.first": f"{l}.{f}",
        "lastf": f"{l}{f[0]}", "first": f,
    }.get(fmt)


def _extrapolate(appointees: list[dict], people: list[dict], client_name: str) -> list[dict]:
    """Derive appointee addresses from an in-house PR contact's own format.

    Returns [] unless exactly one address format is observed among the in-house
    contacts — two conflicting formats mean we do not know the convention, and a
    coin-flip guess is worse than nothing.
    """
    if not appointees:
        return []
    fmts: dict[str, set[str]] = {}
    for p in people:
        dom = p.get("email_domain") or ""
        if not _is_inhouse(dom, client_name):
            continue
        local = (p["email"].partition("@")[0])
        fmt = _detect_format(p.get("first_name") or "", p.get("last_name") or "", local)
        if fmt:
            fmts.setdefault(dom, set()).add(fmt)
    out = []
    for dom, seen in fmts.items():
        if len(seen) != 1:
            log.info("ambiguous address format for %s (%s) — no extrapolation",
                     dom, ", ".join(sorted(seen)))
            continue
        fmt = next(iter(seen))
        for a in appointees:
            first, last = _split_name(a["full_name"])
            local = _apply_format(fmt, first or "", last or "")
            if not local:
                continue
            email = f"{local}@{dom}"
            if _disallowed(local, dom):
                continue
            out.append({
                "email": email, "email_domain": dom, "full_name": a["full_name"],
                "first_name": first, "last_name": last,
                "role_raw": a.get("role_raw"),
                "role_normalised": _normalise_role(a.get("role_raw")),
                "email_pattern": fmt,
            })
    return out

# Newspress release-date field is discovered at runtime (schema varies); try the
# usual names and parse ISO-ish. Returns a date or None.
_DATE_FIELDS = ("published_at", "publishedAt", "release_date", "releaseDate",
                "date", "created_at", "createdAt", "publish_date", "live_date")


def _release_date(rel: dict) -> "date | None":
    for k in _DATE_FIELDS:
        v = rel.get(k)
        if not v:
            continue
        txt = str(v)[:19].replace("T", " ").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(txt[:len(("%Y-%m-%d %H:%M:%S" if " " in txt else "%Y-%m-%d"))], fmt).date()
            except ValueError:
                continue
    return None


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
                          max_pages: int = 0, only_id: int | None = None,
                          months_back: int = 0, appointments: bool = True) -> dict:
    """Harvest PR contacts from Newspress releases.

    only_id: fetch + parse a single public release (no cookie needed) — for testing.
    limit>0 caps the number of releases processed; max_pages caps list pagination.
    """
    stats = Counter()
    contacts: dict[str, dict] = {}
    derived: dict[str, dict] = {}   # extrapolated appointee addresses (guesses)
    cutoff = None
    if months_back:
        today = datetime.now(timezone.utc).date()
        cutoff = today - timedelta(days=int(months_back * 30.44))
        log.info("recency gate: keeping releases on/after %s (last %d months)", cutoff, months_back)

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
            rel_date = _release_date(rel)
            if cutoff and rel_date and rel_date < cutoff:
                # List is newest-first, so once a release predates the window we
                # can stop entirely (contacts already collected are within it).
                stats["out_of_window"] += 1
                log.info("reached release older than %d months (%s) — stopping", months_back, rel_date)
                break
            if i and i % 250 == 0:
                log.info("progress %d/%d releases, %d unique contacts", i, len(ids), len(contacts))
            client_name = ((rel.get("client") or {}).get("name") or "").strip()
            title = (rel.get("title") or "").strip()
            detail = f"newspress: {client_name} — {title}"[:250] if client_name else f"newspress: {title}"[:250]
            people = _parse_release(rel.get("content") or "")
            if appointments:
                appts = _parse_appointments(rel.get("content") or "", title)
                if appts:
                    stats["appointment_releases"] += 1
                    stats["appointees_named"] += len(appts)
                for d in _extrapolate(appts, people, client_name):
                    stats["extrapolated"] += 1
                    d["source_detail"] = f"appointment via {detail}"[:250]
                    d["source_date"] = rel_date
                    prev = derived.get(d["email"])
                    if prev is None:
                        derived[d["email"]] = d
                    elif rel_date and (prev.get("source_date") is None or rel_date > prev["source_date"]):
                        prev["source_date"] = rel_date
            for person in people:
                stats["candidates"] += 1
                person["source_detail"] = detail
                person["source_date"] = rel_date
                # keep the NEWEST release date if the same email recurs
                existing = contacts.get(person["email"])
                if existing is None:
                    contacts[person["email"]] = person
                elif rel_date and (existing.get("source_date") is None or rel_date > existing["source_date"]):
                    existing["source_date"] = rel_date
            if only_id is None and i and NEWSPRESS_RPS > 0:
                time.sleep(1.0 / NEWSPRESS_RPS)

    # A published address always wins over a guess at the same address.
    for e in list(derived):
        if e in contacts:
            del derived[e]
            stats["extrapolated_already_published"] += 1

    summary = {
        "releases": stats["releases"], "missing": stats["missing"],
        "errors": stats["errors"], "contacts": len(contacts),
        "appointment_releases": stats["appointment_releases"],
        "appointees_named": stats["appointees_named"],
        "extrapolated": len(derived),
        "extrapolated_already_published": stats["extrapolated_already_published"],
        "inserted": 0, "inserted_derived": 0, "dry_run": dry_run,
    }
    if dry_run:
        log.info("dry-run: %d releases, %d unique contacts, %d extrapolated (no writes)",
                 stats["releases"], len(contacts), len(derived))
        summary["sample_contacts"] = list(contacts.values())[:20]
        summary["sample_extrapolated"] = list(derived.values())[:20]
        return summary

    with get_conn() as conn:
        for p in contacts.values():
            cur = conn.execute(
                "INSERT INTO contacts "
                "(dealership_id, full_name, first_name, last_name, role_raw, "
                " role_normalised, email, email_domain, confidence, source, source_detail, source_date) "
                "VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, 'direct', 'newspress', %s, %s) "
                "ON CONFLICT (email) WHERE email IS NOT NULL DO UPDATE SET "
                "  source_date = GREATEST(contacts.source_date, EXCLUDED.source_date) "
                "RETURNING (xmax = 0) AS inserted",
                (p["full_name"], p["first_name"], p["last_name"], p["role_raw"],
                 p["role_normalised"], p["email"], p["email_domain"], p["source_detail"],
                 p.get("source_date")),
            )
            row = cur.fetchone()
            if row and row.get("inserted"):
                summary["inserted"] += 1

        # Extrapolated appointee addresses are GUESSES: confidence='inferred'
        # and a distinct source, so Postie's confidence='direct' selector never
        # picks them up without a deliberate decision by GB.
        for p in derived.values():
            cur = conn.execute(
                "INSERT INTO contacts "
                "(dealership_id, full_name, first_name, last_name, role_raw, "
                " role_normalised, email, email_domain, confidence, source, "
                " source_detail, source_date, email_pattern) "
                "VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, 'inferred', "
                " 'newspress_appointment', %s, %s, %s) "
                "ON CONFLICT (email) WHERE email IS NOT NULL DO NOTHING "
                "RETURNING id",
                (p["full_name"], p["first_name"], p["last_name"], p["role_raw"],
                 p["role_normalised"], p["email"], p["email_domain"],
                 p["source_detail"], p.get("source_date"), p.get("email_pattern")),
            )
            if cur.fetchone():
                summary["inserted_derived"] += 1

    log.info("newspress-harvest: %d releases → %d published contacts (%d new), "
             "%d appointment releases naming %d people → %d extrapolated (%d new)",
             stats["releases"], len(contacts), summary["inserted"],
             stats["appointment_releases"], stats["appointees_named"],
             len(derived), summary["inserted_derived"])
    return summary
