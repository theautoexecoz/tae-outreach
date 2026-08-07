#!/usr/bin/env python3
"""Fail closed if an approved Postie recipient already appears in Sent history.

The Outreach database remains the operational ledger, but the TAE Mail-Archiver
is the independent record of what GB actually sent. This check runs after list
approval and before any draft append, catching a stale or corrupted Outreach
disposition even when every database-side selector agrees.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


ARCHIVE_CONTAINER = "tae-mailarchive-db"
ARCHIVE_USER = "tae_mailarchive"
ARCHIVE_DB = "tae_mailarchive"
POSTIE_BODY_MARKER = "publicly listed business contact details"
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+$")


def load_batch(path: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    seen_ids: set[int] = set()
    seen_emails: set[str] = set()
    for line_no, raw in enumerate(Path(path).read_text().splitlines(), 1):
        if not raw.strip():
            continue
        fields = raw.split("\t")
        if len(fields) < 2:
            raise ValueError(f"{path}:{line_no}: expected tab-separated id and email")
        try:
            contact_id = int(fields[0].strip())
        except ValueError as exc:
            raise ValueError(f"{path}:{line_no}: invalid contact id") from exc
        recipient = fields[1].strip().lower()
        if not EMAIL_RE.fullmatch(recipient):
            raise ValueError(f"{path}:{line_no}: invalid email address")
        if contact_id in seen_ids or recipient in seen_emails:
            raise ValueError(f"{path}:{line_no}: duplicate id or email")
        rows.append((contact_id, recipient))
        seen_ids.add(contact_id)
        seen_emails.add(recipient)
    if not rows:
        raise ValueError(f"{path}: batch is empty")
    return rows


def build_query(rows: list[tuple[int, str]]) -> str:
    def sql_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    values = ",\n    ".join(
        f"({contact_id},{sql_literal(email)})"
        for contact_id, email in rows
    )
    marker = POSTIE_BODY_MARKER.replace("'", "''")
    return f"""
WITH candidates(id,email) AS (
  VALUES
    {values}
)
SELECT c.id,c.email,to_char(e."SentDate",'YYYY-MM-DD HH24:MI:SS'),e."Subject"
FROM candidates c
JOIN mail_archiver."ArchivedEmails" e
  ON position(c.email in lower(e."To")) > 0
JOIN mail_archiver."MailAccounts" a ON a."Id"=e."MailAccountId"
WHERE lower(a."EmailAddress")='glenn@theautoexec.com'
  AND e."IsOutgoing"
  AND (e."Body" ILIKE '%{marker}%' OR e."HtmlBody" ILIKE '%{marker}%')
ORDER BY c.id,e."SentDate";
"""


def query_archive(rows: list[tuple[int, str]], runner=subprocess.run) -> list[list[str]]:
    result = runner(
        [
            "docker", "exec", "-i", ARCHIVE_CONTAINER,
            "psql", "-v", "ON_ERROR_STOP=1", "-U", ARCHIVE_USER,
            "-d", ARCHIVE_DB, "-tAF", "\t",
        ],
        input=build_query(rows),
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Sent-archive query failed: {detail}")
    return [line.split("\t", 3) for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Block a Postie batch containing any previously sent recipient"
    )
    parser.add_argument("--batch", required=True, help="tab-separated contact id and email file")
    args = parser.parse_args()
    try:
        rows = load_batch(args.batch)
        matches = query_archive(rows)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"POSTIE PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 2
    if matches:
        print(
            f"POSTIE PREFLIGHT BLOCKED: {len(matches)} prior sent record(s) found",
            file=sys.stderr,
        )
        for contact_id, email, sent_at, subject in matches:
            print(f"  {contact_id}\t{email}\t{sent_at}\t{subject}", file=sys.stderr)
        return 1
    print(f"Postie Sent-archive preflight passed: {len(rows)} approved recipients, zero prior sends")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
