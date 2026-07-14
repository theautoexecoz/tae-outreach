#!/usr/bin/env python3
"""Reach mailbox sweep — Project Postie (TAE-2607-13).

Cold-outreach replies land at glenn@reach.theautoexec.com (the reach identity's
From/Reply-To and envelope return-path), NOT in glenn@theautoexec.com. This
sweep reads that mailbox and reconciles two auto-reply classes into the outreach
DB, idempotently:

  * BOUNCES (NDRs — "Undeliverable", 550 recipient-not-found, mailer-daemon):
    the address is dead. Mark the contact suppressed + ruled_out(stage='bounce')
    so it is NEVER re-sent (the batch planner honours `NOT suppressed`).

  * OUT-OF-OFFICE auto-replies: the send was DELIVERED (the OOO proves receipt).
    Do NOT suppress. Stamp ooo_at so the contact joins the end-of-first-pass
    follow-up cohort (GB 2026-07-14: at end of pass, if they never subscribed,
    risk one resend). Disposition stays 'sent' — untouched by this pass.

Run it on the Bedrock HOST (needs the `ventraip-tae` SSH alias to read the
Maildir, and `docker` to reach tae_outreach_db). Manual runbook step, not a
cron. Safe to re-run: bounce marks skip contacts already ruled_out(bounce);
OOO marks skip contacts whose ooo_at is already set.

    python3 outreach/postie/reach_sweep.py           # apply
    python3 outreach/postie/reach_sweep.py --dry-run  # show, change nothing
"""
import argparse
import email
import re
import subprocess
import sys
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

SSH_HOST = "ventraip-tae"
MAILDIR = "/home/theautoe/mail/reach.theautoexec.com/glenn"
DB_CONTAINER = "tae_outreach_db"
DB_USER = "tae_outreach"
DB_NAME = "tae_outreach"
SEP = "@@@REACHSWEEP-MSG@@@"

# An NDR (bounce), not a human/OOO reply.
NDR_SUBJECT = re.compile(
    r"undeliverable|mail delivery (failed|subsystem)|delivery status notification|"
    r"returned mail|failure notice|delivery has failed|could not be delivered",
    re.I,
)
NDR_FROM = re.compile(r"mailer-daemon|postmaster@|microsoft outlook|mail delivery", re.I)
# The failed address inside a DSN.
FAILED_RE = re.compile(
    r"(?:Final-Recipient|Original-Recipient|X-Failed-Recipients)\s*:\s*(?:rfc822\s*;\s*)?"
    r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+)",
    re.I,
)
# An out-of-office / automatic reply.
OOO_SUBJECT = re.compile(
    r"automatic reply|out of office|auto[\s\-]?reply|autoreply|on leave|"
    r"away from the office|annual leave|currently out",
    re.I,
)
ADDR_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+")


def _hdr(msg, key):
    return str(make_header(decode_header(msg.get(key, "") or ""))).strip()


def read_mailbox():
    """Return raw text of every message in the reach Maildir (cur + new)."""
    cmd = (
        f'for f in {MAILDIR}/cur/* {MAILDIR}/new/*; do '
        f'[ -f "$f" ] || continue; printf "\\n{SEP}\\n"; cat "$f"; done'
    )
    out = subprocess.run(
        ["ssh", SSH_HOST, cmd],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        sys.exit(f"ssh read failed: {out.stderr.strip()[:300]}")
    return [chunk for chunk in out.stdout.split(SEP) if chunk.strip()]


def classify(raw):
    """(-> kind, email, date) where kind is 'bounce' | 'ooo' | None."""
    raw = raw.lstrip("\r\n")  # the split leaves a leading newline; headers must lead
    msg = email.message_from_string(raw)
    frm = _hdr(msg, "From")
    subj = _hdr(msg, "Subject")
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    date = None
    try:
        d = parsedate_to_datetime(msg.get("Date"))
        date = d.date().isoformat() if d else None
    except Exception:
        date = None

    is_ndr = bool(NDR_SUBJECT.search(subj) or NDR_FROM.search(frm))
    if is_ndr:
        m = FAILED_RE.search(raw)
        addr = m.group(1).lower() if m else None
        return ("bounce", addr, date)

    looks_auto = (auto and auto != "no") or msg.get("X-Autoreply") or msg.get("X-Autorespond")
    if looks_auto and OOO_SUBJECT.search(subj):
        m = ADDR_RE.search(frm)
        addr = m.group(0).lower() if m else None
        return ("ooo", addr, date)
    return (None, None, None)


def _sql_literal(s):
    return "'" + s.replace("'", "''") + "'"


def build_sql(bounces, ooos):
    rows = [f"({_sql_literal(e)}, 'bounce', {('DATE '+_sql_literal(d)) if d else 'NULL'})"
            for e, d in bounces]
    rows += [f"({_sql_literal(e)}, 'ooo', {('DATE '+_sql_literal(d)) if d else 'NULL'})"
             for e, d in ooos]
    values = ",\n  ".join(rows)
    return f"""
BEGIN;
CREATE TEMP TABLE _sweep(email text, kind text, evdate date) ON COMMIT DROP;
INSERT INTO _sweep(email, kind, evdate) VALUES
  {values};

-- Bounces: never retry. Only touch contacts not already ruled_out as a bounce.
UPDATE contacts c
   SET suppressed = true,
       suppress_reason = 'hard_bounce',
       disposition = 'ruled_out',
       ruled_out_stage = 'bounce',
       ruled_out_reason = 'reach NDR: 550 recipient not found'
  FROM _sweep s
 WHERE lower(c.email) = s.email AND s.kind = 'bounce'
   AND c.ruled_out_stage IS DISTINCT FROM 'bounce';

-- OOO: stamp for end-of-first-pass follow-up. Delivered, so never suppress;
-- disposition stays 'sent'. Only stamp if not already stamped.
UPDATE contacts c
   SET ooo_at = COALESCE(s.evdate, CURRENT_DATE)
  FROM _sweep s
 WHERE lower(c.email) = s.email AND s.kind = 'ooo' AND c.ooo_at IS NULL;

-- Report.
\\echo '--- bounces now suppressed (matched contacts) ---'
SELECT s.email,
       CASE WHEN c.id IS NULL THEN 'NO CONTACT MATCH'
            ELSE c.disposition || ' / suppressed=' || c.suppressed END AS state
  FROM _sweep s LEFT JOIN contacts c ON lower(c.email) = s.email
 WHERE s.kind = 'bounce' ORDER BY s.email;
\\echo '--- OOO follow-up cohort (matched contacts) ---'
SELECT s.email,
       CASE WHEN c.id IS NULL THEN 'NO CONTACT MATCH'
            ELSE 'ooo_at=' || COALESCE(c.ooo_at::text,'(null)') END AS state
  FROM _sweep s LEFT JOIN contacts c ON lower(c.email) = s.email
 WHERE s.kind = 'ooo' ORDER BY s.email;
COMMIT;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="parse + show, change nothing")
    args = ap.parse_args()

    chunks = read_mailbox()
    bounces, ooos, ignored = {}, {}, 0
    for raw in chunks:
        kind, addr, date = classify(raw)
        if kind == "bounce" and addr:
            bounces[addr] = date
        elif kind == "ooo" and addr:
            ooos[addr] = date
        else:
            ignored += 1

    print(f"reach mailbox: {len(chunks)} messages "
          f"({len(bounces)} bounce, {len(ooos)} OOO, {ignored} ignored)")
    for e, d in sorted(bounces.items()):
        print(f"  BOUNCE  {e}  ({d or 'no date'})")
    for e, d in sorted(ooos.items()):
        print(f"  OOO     {e}  ({d or 'no date'})")

    if not bounces and not ooos:
        print("nothing to reconcile.")
        return
    if args.dry_run:
        print("\n--dry-run: no DB changes.")
        return

    sql = build_sql(list(bounces.items()), list(ooos.items()))
    proc = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-v",
         "ON_ERROR_STOP=1", "-f", "-"],
        input=sql, capture_output=True, text=True, timeout=60,
    )
    print(proc.stdout.strip())
    if proc.returncode != 0:
        sys.exit(f"psql failed: {proc.stderr.strip()[:400]}")


if __name__ == "__main__":
    main()
