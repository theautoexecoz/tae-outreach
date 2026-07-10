"""Email deliverability verification, done in-house.

Two passes, because they carry very different risk:

- `run_dns` — DNS only. Syntax, MX lookup, provider classification, dead-domain
  and role-address detection. Zero mail sent, no external mail server touched
  beyond a DNS query. Safe to run any time. This alone kills provably-dead
  addresses and flags the gateway majority as unverifiable-by-anyone.

- `run_probe` — SMTP RCPT probing, self-hosted domains ONLY. Opens an SMTP
  session to the domain's mail server, tests whether it is catch-all (accepts a
  random address), and if not, checks each real mailbox with RCPT TO — then
  hangs up before DATA, so nothing is ever delivered. Skips M365/Google/gateways
  entirely: they accept-all or block probes, so probing them is noise and a mild
  reputation risk for no signal.

Neither pass sends email. Results are written to the `contacts.verify_*` columns.
"""
import logging
import random
import re
import smtplib
import socket
import string
import time

import dns.resolver

from outreach.config import SELF_DOMAINS
from outreach.db import get_conn

log = logging.getLogger("outreach.verify")

# The envelope sender for probes: an empty MAIL FROM (<>) is the standard,
# backscatter-safe probe sender. HELO identifies us honestly.
PROBE_HELO = "bedrock.theautoexec.com"
PROBE_MAIL_FROM = ""  # smtplib sends MAIL FROM:<>

SYNTAX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLE_LOCALPARTS = {
    "info", "sales", "admin", "office", "contact", "enquiries", "enquiry",
    "hello", "support", "marketing", "media", "press", "reception", "accounts",
    "service", "help", "team", "mail", "noreply", "no-reply",
}

# MX substrings → provider. Gateways here are NOT probed per-mailbox.
GATEWAYS = {
    "google": "Google", "googlemail": "Google",
    "outlook": "Microsoft365", "microsoft": "Microsoft365", "office365": "Microsoft365",
    "mimecast": "Mimecast", "pphosted": "Proofpoint", "proofpoint": "Proofpoint",
    "messagelabs": "Symantec", "symantec": "Symantec",
}

_resolver = dns.resolver.Resolver()
_resolver.timeout = 4
_resolver.lifetime = 6


def classify_mx(mx_hosts: list[str]) -> str:
    joined = " ".join(mx_hosts).lower()
    for needle, name in GATEWAYS.items():
        if needle in joined:
            return name
    if "secureserver" in joined:
        return "GoDaddy"
    if any(k in joined for k in ("cpanel", "websitewelcome", "hostinger", "mxrouting")):
        return "cPanel-ish"
    return "other/self"


def resolve_mx(domain: str) -> tuple[list[str], str]:
    """Return (mx_hosts_by_priority, status). status is '' if it has MX."""
    try:
        ans = _resolver.resolve(domain, "MX")
        hosts = [r.exchange.to_text().rstrip(".") for r in sorted(ans, key=lambda r: r.preference)]
        return hosts, ""
    except dns.resolver.NXDOMAIN:
        return [], "nxdomain"
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        # No MX: some domains accept mail on the A record, but for outreach we
        # treat no-MX as undeliverable rather than gamble.
        return [], "no_mx"
    except Exception as exc:  # timeout, etc.
        return [], f"dns_error:{exc.__class__.__name__}"


def _is_gateway(provider: str) -> bool:
    return provider in ("Google", "Microsoft365", "Mimecast", "Proofpoint", "Symantec")


def run_dns(limit: int | None = None, only_group: str | None = None) -> dict:
    """DNS-only pass. Safe. Populates provider/MX/dead-domain/role for every row."""
    where = ["email IS NOT NULL", "disposition = 'in_play'", "NOT suppressed"]
    if only_group:
        where.append("send_group = %(g)s")
    sql = f"SELECT id, email, email_domain FROM contacts WHERE {' AND '.join(where)} ORDER BY email_domain"
    if limit:
        sql += f" LIMIT {int(limit)}"

    mx_cache: dict[str, tuple[list[str], str, str]] = {}
    stats: dict[str, int] = {}
    with get_conn() as conn:
        rows = conn.execute(sql, {"g": only_group}).fetchall()
        for r in rows:
            email = (r["email"] or "").strip().lower()
            domain = (r["email_domain"] or email.split("@")[-1]).strip().lower()
            local = email.split("@")[0] if "@" in email else ""

            if not SYNTAX.match(email) or domain in SELF_DOMAINS:
                _write(conn, r["id"], "bad_syntax", None, None, None, "syntax/self")
                stats["bad_syntax"] = stats.get("bad_syntax", 0) + 1
                continue

            if domain not in mx_cache:
                hosts, err = resolve_mx(domain)
                provider = classify_mx(hosts) if hosts else "dead"
                mx_cache[domain] = (hosts, err, provider)
            hosts, err, provider = mx_cache[domain]

            if not hosts:
                status, detail = "dead_domain", err
            elif local in ROLE_LOCALPARTS:
                status, detail = "role", "role inbox"
            elif _is_gateway(provider):
                status, detail = "unknown_gateway", "per-mailbox unverifiable"
            else:
                status, detail = "unknown", "self-hosted; probe to resolve"

            _write(conn, r["id"], status, provider, hosts[0] if hosts else None, None, detail)
            stats[status] = stats.get(status, 0) + 1
    return stats


# ── SMTP probe (self-hosted only) ────────────────────────────────────────────

def _rcpt(server: smtplib.SMTP, address: str) -> tuple[int, str]:
    try:
        code, msg = server.rcpt(address)
        return code, msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)
    except smtplib.SMTPServerDisconnected:
        raise
    except Exception as exc:
        return 0, str(exc)


def _probe_domain(mx_host: str, addresses: list[str]) -> dict[str, tuple[str, str]]:
    """Return {address: (status, detail)}. One SMTP session per domain."""
    out: dict[str, tuple[str, str]] = {}
    try:
        server = smtplib.SMTP(mx_host, 25, timeout=10, local_hostname=PROBE_HELO)
    except (socket.timeout, OSError) as exc:
        return {a: ("unknown", f"connect_fail:{exc.__class__.__name__}") for a in addresses}

    try:
        server.ehlo_or_helo_if_needed()
        server.mail(PROBE_MAIL_FROM)

        # Catch-all test: a random address that cannot exist.
        rnd = "".join(random.choices(string.ascii_lowercase, k=16))
        domain = addresses[0].split("@")[-1]
        cc_code, _ = _rcpt(server, f"{rnd}@{domain}")
        if cc_code in (250, 251):
            return {a: ("catchall", "domain accepts any address") for a in addresses}

        for a in addresses:
            code, msg = _rcpt(server, a)
            if code in (250, 251):
                out[a] = ("deliverable", f"{code}")
            elif code in (550, 551, 553):
                out[a] = ("undeliverable", f"{code} {msg[:60]}")
            elif 400 <= code < 500:
                out[a] = ("unknown", f"greylist/temp {code}")
            else:
                out[a] = ("unknown", f"code {code}")
    except smtplib.SMTPServerDisconnected:
        for a in addresses:
            out.setdefault(a, ("unknown", "server hung up (probe-blocked)"))
    except Exception as exc:  # noqa: BLE001
        for a in addresses:
            out.setdefault(a, ("unknown", f"probe_error:{exc.__class__.__name__}"))
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return out


def run_probe(limit_domains: int | None = None, only_group: str | None = None,
              rps: float = 0.5) -> dict:
    """SMTP-probe the self-hosted domains left 'unknown' by the DNS pass."""
    where = ["verify_status = 'unknown'", "verify_mx IS NOT NULL", "email IS NOT NULL"]
    if only_group:
        where.append("send_group = %(g)s")
    sql = (f"SELECT email_domain, verify_mx, array_agg(email) AS emails, array_agg(id) AS ids "
           f"FROM contacts WHERE {' AND '.join(where)} GROUP BY email_domain, verify_mx "
           f"ORDER BY email_domain")
    if limit_domains:
        sql += f" LIMIT {int(limit_domains)}"

    stats: dict[str, int] = {}
    delay = 1.0 / rps if rps > 0 else 0
    with get_conn() as conn:
        domains = conn.execute(sql, {"g": only_group}).fetchall()
        log.info("probing %d self-hosted domain(s)", len(domains))
        for d in domains:
            emails = [e.strip().lower() for e in d["emails"]]
            id_by_email = dict(zip(emails, d["ids"]))
            verdicts = _probe_domain(d["verify_mx"], emails)
            for email, (status, detail) in verdicts.items():
                cid = id_by_email[email]
                catchall = status == "catchall"
                _write(conn, cid, status, None, None, catchall, detail, keep_provider=True)
                stats[status] = stats.get(status, 0) + 1
            conn.commit()
            if delay:
                time.sleep(delay)
    return stats


def _write(conn, cid, status, provider, mx, catchall, detail, keep_provider=False):
    sets = ["verify_status = %(s)s", "verify_detail = %(d)s", "verify_checked_at = now()"]
    params = {"s": status, "d": detail, "id": cid}
    if not keep_provider:
        sets.append("verify_provider = %(p)s")
        params["p"] = provider
        sets.append("verify_mx = %(mx)s")
        params["mx"] = mx
    if catchall is not None:
        sets.append("verify_catchall = %(c)s")
        params["c"] = catchall
    conn.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id = %(id)s", params)


def summary() -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT verify_status, count(*) AS n FROM contacts "
            "WHERE email IS NOT NULL AND disposition='in_play' AND NOT suppressed "
            "GROUP BY verify_status ORDER BY n DESC"
        ).fetchall()
