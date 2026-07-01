"""OOO reply harvest — Email-list finalisation program §1a (TAE-2606-07).

Harvests the out-of-office auto-replies to the Daily Newsletter that collect in
editor@theautoexec.com's `TAE-RobotReplies` IMAP folder (~2,700). Two payloads:

  1. NEW personal contacts named as delegates in OOO bodies ("I'm on leave until
     Monday, for anything urgent contact Jane Smith on jane.smith@dealer.com.au").
     Inserted as source='ooo_reply', confidence='direct' — a published, real
     address, i.e. GREEN under the send policy.
  2. Per-domain address-format intelligence, learned by pairing each reply's From
     display-name against its local-part, aggregated into `ooo_domain_formats`.

GB rule (2026-07-01): the address that SENT the OOO is EXCLUDED as a contact. By
construction the sender received the send that triggered the auto-reply, so it is
already a newsletter subscriber, not a cold-outreach target. We mine the sender
only for domain-format intelligence, never harvest it as a contact.

Read-only IMAP (SELECT readonly + BODY.PEEK) — never marks mail \\Seen, never
deletes. Idempotent: contacts dedupe on the unique email index; domain formats
upsert per domain. Uses stdlib imaplib/email only (no new dependency).
"""
import email
import imaplib
import logging
import re
from collections import Counter, defaultdict
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr

from outreach.config import (
    OOO_IMAP_FOLDER,
    OOO_IMAP_HOST,
    OOO_IMAP_PASSWORD,
    OOO_IMAP_PORT,
    OOO_IMAP_USER,
    SELF_DOMAINS,
)
from outreach.db import get_conn
from outreach.extract import ROLE_NORMALISATION, _normalise_role
from outreach.extract.pattern_guesser import FREEMAIL_DOMAINS

log = logging.getLogger("outreach.harvest.ooo")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Two or three capitalised words — a plausible personal name in body context.
NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:['\-][A-Z][a-z]+)?(?: [A-Z][a-z]+){1,2})\b")
# Opaque local-parts that are tracking tokens / hashes / IDs, not people.
HASH_LOCAL_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)
DIGIT_RUN_RE = re.compile(r"\d{6,}")
# "domains" that are really image/asset filenames from HTML signatures
# (fb_icon@72x.png, logo@2x.jpg) — the regex mistakes them for addresses.
FILE_EXT_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|bmp|ico|css|js|woff2?|ttf)$", re.I)

# Tokens that mark a "name" from body context as actually a role/title or an
# organisation, not a person — so we don't store "Chief Financial Officer" or
# "Mahindra South Africa" as full_name.
ROLE_ORG_STOPWORDS = {
    "manager", "director", "officer", "chief", "executive", "consultant",
    "advisor", "adviser", "principal", "coordinator", "president", "head",
    "specialist", "representative", "associate", "supervisor", "lead",
    "group", "pty", "ltd", "limited", "holdings", "motors", "motor",
    "automotive", "australia", "australian", "africa", "news", "media",
    "finance", "insurance", "marketing", "sales", "service", "team",
    "department", "company", "corporation", "dealership", "dealer",
    "national", "global", "international", "region", "regional", "partners",
    "solutions", "services", "enterprises",
}

# Generic / role / system local-parts: never individuals, high complaint risk for
# cold B2B — excluded from harvest (the person's own address is what we want).
ROLE_LOCALPARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "postmaster",
    "mailer-daemon", "mailerdaemon", "abuse", "bounce", "bounces",
    "info", "sales", "service", "parts", "admin", "administration",
    "accounts", "account", "enquiries", "enquiry", "inquiries", "inquiry",
    "reception", "contact", "support", "marketing", "careers", "jobs",
    "hr", "fleet", "warranty", "bookings", "booking", "newsletter",
    "feedback", "office", "mail", "team", "help", "hello", "general",
    "customerservice", "customercare", "webmaster", "notifications",
}

# From <first,last,local> to a pattern label in the guess_email() vocabulary.
def _slug(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _detect_format(first: str, last: str, local: str) -> str | None:
    f, l = _slug(first), _slug(last)
    if not f or not l:
        return None
    local = local.lower()
    for name, gen in (
        ("first.last", f"{f}.{l}"),
        ("first_last", f"{f}_{l}"),
        ("flast", f"{f[0]}{l}"),
        ("firstl", f"{f}{l[0]}"),
        ("firstlast", f"{f}{l}"),
        ("last.first", f"{l}.{f}"),
        ("lastf", f"{l}{f[0]}"),
    ):
        if gen == local:
            return name
    return None


def _clean_email(raw: str) -> str | None:
    """Normalise a regex-matched address, or None if it isn't a real address.
    Strips leading local-part punctuation (a bullet/dash bleeding in from text)
    and rejects image/asset filenames the regex mistook for domains."""
    raw = (raw or "").strip().lower().strip(".,;:<>()[]\"'")
    local, _, domain = raw.partition("@")
    local = local.lstrip("-._+")
    if not local or not domain or "." not in domain or FILE_EXT_RE.search(domain):
        return None
    return f"{local}@{domain}"


def _hdr(msg, name: str) -> str:
    raw = msg.get(name)
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _decode_part(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _body_text(msg) -> str:
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if "attachment" in str(part.get("Content-Disposition") or "").lower():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = _decode_part(part)
            elif ctype == "text/html" and html is None:
                html = _decode_part(part)
    else:
        if msg.get_content_type() == "text/html":
            html = _decode_part(msg)
        else:
            plain = _decode_part(msg)
    if plain:
        return plain
    if html:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml").get_text("\n")
    return ""


def _name_near(text: str, pos: int) -> str | None:
    """Best-effort personal name in the ~200 chars before an address occurrence,
    then the line containing it (signature blocks read Name / Title / email)."""
    window = text[max(0, pos - 220):pos]
    matches = NAME_RE.findall(window)
    if matches:
        return matches[-1].strip()
    line_start = text.rfind("\n", 0, pos)
    line_end = text.find("\n", pos)
    line = text[(line_start + 1 if line_start != -1 else 0):(line_end if line_end != -1 else len(text))]
    m = NAME_RE.search(line)
    return m.group(1).strip() if m else None


def _role_near(text: str, pos: int) -> str | None:
    window = text[max(0, pos - 220):pos + 60].lower()
    for key in ROLE_NORMALISATION:
        if key in window:
            return key
    return None


def _split_name(full: str) -> tuple[str | None, str | None]:
    parts = full.split()
    if len(parts) < 2:
        return (parts[0] if parts else None), None
    return parts[0], parts[-1]


def _name_from_local(local: str) -> str | None:
    """Personal name derivable from a local-part, else None. Requires a separator
    so we never turn a single opaque token (jsmith) into a bogus name."""
    for sep in (".", "_"):
        if sep in local:
            parts = [p for p in local.split(sep) if p]
            if len(parts) == 2 and all(p.isalpha() and len(p) >= 2 for p in parts):
                return f"{parts[0].title()} {parts[1].title()}"
    return None


def _looks_like_person(name: str) -> bool:
    toks = name.split()
    if not (2 <= len(toks) <= 3):
        return False
    return not any(t.lower().strip(".,'") in ROLE_ORG_STOPWORDS for t in toks)


def _resolve_name(local: str, ctx_name: str | None) -> str | None:
    """Pick the most trustworthy personal name. The local-part's own structure
    (first.last / first_last) beats body context, which often catches a title or
    company name sitting near the address; context is the fallback for opaque
    locals (jsmith) and only if it looks like a person."""
    lp = _name_from_local(local)
    if lp and _looks_like_person(lp):    # rejects role-ish splits like caa.marketing
        return lp
    if ctx_name and _looks_like_person(ctx_name):
        return ctx_name
    return None


def _connect():
    if not OOO_IMAP_PASSWORD:
        raise SystemExit(
            "OOO_IMAP_PASSWORD is empty. Add editor@theautoexec.com's mailbox "
            "password to /srv/docker/tae/tae-app-services/tae-outreach/.env "
            "(1Password: TAE email accounts → editor@theautoexec.com), then "
            "re-run `ooo-harvest`."
        )
    log.info("connecting %s:%d as %s", OOO_IMAP_HOST, OOO_IMAP_PORT, OOO_IMAP_USER)
    M = imaplib.IMAP4_SSL(OOO_IMAP_HOST, OOO_IMAP_PORT)
    M.login(OOO_IMAP_USER, OOO_IMAP_PASSWORD)
    return M


def _find_folder(M) -> str:
    if OOO_IMAP_FOLDER:
        return OOO_IMAP_FOLDER
    typ, data = M.list()
    seen = []
    for raw in data or []:
        line = raw.decode(errors="replace")
        m = re.search(r'"([^"]*)"\s*$', line) or re.search(r"(\S+)\s*$", line)
        folder = m.group(1) if m else line
        seen.append(folder)
        if "robotreplies" in folder.lower():
            log.info("using folder %s", folder)
            return folder
    raise SystemExit(
        "Could not find the TAE-RobotReplies folder. Set OOO_IMAP_FOLDER in .env. "
        "Folders seen: " + ", ".join(seen)
    )


def run_ooo_harvest(limit: int = 0, dry_run: bool = False) -> dict:
    """Harvest OOO delegate contacts + per-domain format intelligence.

    limit>0 caps to the most-recent N messages (for a first monitored run).
    dry_run parses and reports but writes nothing.
    """
    M = _connect()
    try:
        folder = _find_folder(M)
        M.select(f'"{folder}"', readonly=True)
        typ, data = M.search(None, "ALL")
        ids = data[0].split() if data and data[0] else []
        if limit:
            ids = ids[-limit:]
        log.info("scanning %d message(s) in %s", len(ids), folder)

        contacts: dict[str, dict] = {}         # email -> contact (deduped in-run)
        domain_fmt: dict[str, Counter] = defaultdict(Counter)  # domain -> pattern counts
        domain_seen: Counter = Counter()       # domain -> senders seen
        domain_sample: dict[str, str] = {}     # domain -> a real sender address
        stats = Counter()

        for num in ids:
            typ, msgdata = M.fetch(num, "(BODY.PEEK[])")
            if not msgdata or not isinstance(msgdata[0], tuple):
                stats["fetch_empty"] += 1
                continue
            msg = email.message_from_bytes(msgdata[0][1])
            stats["messages"] += 1

            from_name, from_addr = parseaddr(_hdr(msg, "From"))
            from_addr = from_addr.strip().lower()
            if not from_addr or "@" not in from_addr:
                stats["no_sender"] += 1
                continue

            # (payload 2) learn this domain's address format from the sender —
            # the ONE thing we take from the sender; never harvested as a contact.
            s_local, _, s_domain = from_addr.partition("@")
            if s_domain and s_domain not in SELF_DOMAINS and s_domain not in FREEMAIL_DOMAINS:
                domain_seen[s_domain] += 1
                domain_sample.setdefault(s_domain, from_addr)
                sf, sl = _split_name(from_name)
                fmt = _detect_format(sf or "", sl or "", s_local)
                if fmt:
                    domain_fmt[s_domain][fmt] += 1

            # (payload 1) delegate/alternate addresses named in the reply.
            body = _body_text(msg)
            body_lower = body.lower()  # .lower() preserves length → positions map to `body`
            # Reply-To can name an assistant/delegate — treat as a body candidate.
            reply_to = [a.lower() for _, a in getaddresses([_hdr(msg, "Reply-To")]) if a]

            found, seen = [], set()
            for raw in (m.group(0) for m in EMAIL_RE.finditer(body)):
                ce = _clean_email(raw)
                if ce and ce not in seen:
                    seen.add(ce)
                    found.append(ce)
            for rt in reply_to:
                ce = _clean_email(rt)
                if ce and ce not in seen:
                    seen.add(ce)
                    found.append(ce)

            for cand in found:
                local, _, domain = cand.partition("@")
                if not domain:
                    continue
                if cand == from_addr:                 # GB rule: never the sender
                    stats["skip_sender"] += 1
                    continue
                if domain in SELF_DOMAINS:
                    stats["skip_self"] += 1
                    continue
                if domain in FREEMAIL_DOMAINS:
                    stats["skip_freemail"] += 1
                    continue
                if local in ROLE_LOCALPARTS or local.startswith(("noreply", "no-reply", "mailer")):
                    stats["skip_role"] += 1
                    continue
                if HASH_LOCAL_RE.match(local) or DIGIT_RUN_RE.search(local) or len(local) > 40:
                    stats["skip_opaque"] += 1    # tracking token / hash / id, not a person
                    continue

                pos = body_lower.find(cand)
                ctx_name = _name_near(body, pos) if pos != -1 else None
                full_name = _resolve_name(local, ctx_name)
                if not full_name:
                    stats["skip_unnamed"] += 1   # can't tie to a person → too risky
                    continue

                role_raw = _role_near(body, pos) if pos != -1 else None
                first, last = _split_name(full_name)
                existing = contacts.get(cand)
                if existing and existing.get("role_raw"):
                    continue  # keep first, richer capture
                contacts[cand] = {
                    "full_name": full_name,
                    "first_name": first,
                    "last_name": last,
                    "role_raw": role_raw,
                    "role_normalised": _normalise_role(role_raw),
                    "email": cand,
                    "email_domain": domain,
                    "source_detail": f"ooo_reply named by {from_addr}"[:250],
                }
                stats["candidates"] += 1

        # resolve most-common pattern per domain
        formats = {
            d: {
                "pattern": (domain_fmt[d].most_common(1)[0][0] if domain_fmt.get(d) else None),
                "pattern_count": (domain_fmt[d].most_common(1)[0][1] if domain_fmt.get(d) else 0),
                "sender_count": domain_seen[d],
                "sample_email": domain_sample.get(d),
            }
            for d in domain_seen
        }

        summary = {
            "messages": stats["messages"],
            "senders_excluded": stats["skip_sender"],
            "candidates": len(contacts),
            "domains_profiled": len(formats),
            "skipped": {
                "self": stats["skip_self"], "freemail": stats["skip_freemail"],
                "role": stats["skip_role"], "opaque": stats["skip_opaque"],
                "unnamed": stats["skip_unnamed"],
            },
            "inserted": 0,
            "domains_written": 0,
            "dry_run": dry_run,
        }

        if dry_run:
            log.info("dry-run: %d candidates, %d domains profiled (no writes)",
                     len(contacts), len(formats))
            summary["sample_contacts"] = list(contacts.values())[:15]
            return summary

        with get_conn() as conn:
            for c in contacts.values():
                cur = conn.execute(
                    "INSERT INTO contacts "
                    "(dealership_id, full_name, first_name, last_name, role_raw, "
                    " role_normalised, email, email_domain, confidence, source, source_detail) "
                    "VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, 'direct', 'ooo_reply', %s) "
                    "ON CONFLICT DO NOTHING RETURNING id",
                    (c["full_name"], c["first_name"], c["last_name"], c["role_raw"],
                     c["role_normalised"], c["email"], c["email_domain"], c["source_detail"]),
                )
                if cur.fetchone():
                    summary["inserted"] += 1
            for d, f in formats.items():
                conn.execute(
                    "INSERT INTO ooo_domain_formats "
                    "(domain, pattern, sample_email, sender_count, pattern_count, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, NOW()) "
                    "ON CONFLICT (domain) DO UPDATE SET "
                    "  pattern = EXCLUDED.pattern, sample_email = EXCLUDED.sample_email, "
                    "  sender_count = EXCLUDED.sender_count, pattern_count = EXCLUDED.pattern_count, "
                    "  updated_at = NOW()",
                    (d, f["pattern"], f["sample_email"], f["sender_count"], f["pattern_count"]),
                )
                summary["domains_written"] += 1

        log.info(
            "ooo-harvest: %d msgs, %d senders excluded, %d new contacts inserted, "
            "%d/%d domain formats written",
            stats["messages"], stats["skip_sender"], summary["inserted"],
            summary["domains_written"], len(formats),
        )
        return summary
    finally:
        try:
            M.logout()
        except Exception:
            pass
