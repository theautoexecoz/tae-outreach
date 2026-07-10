"""Stage 6 — Campaign Monitor dedup.

Before any contact is exported we must know whether CM already knows that email,
in any state, on any list:

  active        -> already a TAE subscriber; never re-email
  unsubscribed  -> explicitly opted out; never email
  deleted       -> deleted or hard-bounced; emailing risks deliverability
  not_found     -> genuinely new -> the exportable universe

This dumps the whole CM subscriber universe (every list, every state) into
local sets, then stamps `contacts.cm_status`. Read-only against CM — it never
writes to Campaign Monitor. Idempotent: re-running just re-stamps.

Needs CM_API_KEY in .env (1Password: TAE/CM/API-Key — the same key tae-cm-sync
uses). CM_LIST_ID is NOT needed here (that's the Stage-7 export target).
"""
import logging
import time
import httpx

from outreach.config import CM_API_KEY
from outreach.db import get_conn

log = logging.getLogger("outreach.enrich.cm_dedup")

CM_BASE = "https://api.createsend.com/api/v3.3"
PAGE_SIZE = 1000
# CM subscriber-state endpoint -> the cm_status we record. bounced+deleted both
# collapse to 'deleted' (both mean "do not send").
STATE_ENDPOINTS = {
    "active": "active",
    "unsubscribed": "unsubscribed",
    "bounced": "deleted",
    "deleted": "deleted",
}
# Precedence when an email appears in more than one state across lists: the
# most-restrictive/most-current wins. active beats unsubscribed beats deleted.
STATUS_PRECEDENCE = {"active": 3, "unsubscribed": 2, "deleted": 1}


def _client(api_key: str) -> httpx.Client:
    return httpx.Client(timeout=60, auth=(api_key, "x"),
                        headers={"User-Agent": "TAE-Outreach/1.0"})


def _get(c: httpx.Client, path: str, **params):
    for attempt in range(3):
        r = c.get(f"{CM_BASE}{path}", params=params or None)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:  # rate limited — CM asks us to back off
            time.sleep(2 ** attempt * 2)
            continue
        raise RuntimeError(f"CM {path} -> {r.status_code}: {r.text[:200]}")
    raise RuntimeError(f"CM {path} -> repeated 429s")


def _dump_state(c: httpx.Client, list_id: str, endpoint: str) -> list[str]:
    """All EmailAddresses on one list in one state, across all pages."""
    emails: list[str] = []
    page = 1
    while True:
        data = _get(c, f"/lists/{list_id}/{endpoint}.json",
                    page=page, pagesize=PAGE_SIZE,
                    orderfield="email", orderdirection="asc")
        for row in data.get("Results", []):
            em = (row.get("EmailAddress") or "").strip().lower()
            if em:
                emails.append(em)
        total_pages = data.get("NumberOfPages", 1) or 1
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.3)  # be polite to the CM API
    return emails


def build_exclusion_sets(api_key: str) -> dict[str, set[str]]:
    """{cm_status: {emails}} dumped from every list in every state."""
    sets: dict[str, set[str]] = {"active": set(), "unsubscribed": set(), "deleted": set()}
    with _client(api_key) as c:
        clients = _get(c, "/clients.json")
        log.info("CM clients: %s", ", ".join(cl["Name"] for cl in clients))
        for cl in clients:
            lists = _get(c, f"/clients/{cl['ClientID']}/lists.json")
            for lst in lists:
                lid, lname = lst["ListID"], lst["Name"]
                for endpoint, status in STATE_ENDPOINTS.items():
                    emails = _dump_state(c, lid, endpoint)
                    if emails:
                        sets[status].update(emails)
                        log.info("  %-34s %-12s %d", lname[:34], endpoint, len(emails))
    log.info("CM universe: active=%d unsubscribed=%d deleted/bounced=%d",
             len(sets["active"]), len(sets["unsubscribed"]), len(sets["deleted"]))
    return sets


def run_cm_dedup() -> dict:
    """Stamp contacts.cm_status against the CM subscriber universe. Returns a summary."""
    if not CM_API_KEY:
        raise SystemExit(
            "CM_API_KEY is empty. Populate it in "
            "/srv/docker/tae/tae-app-services/tae-outreach/.env from 1Password "
            "(TAE/CM/API-Key — same key as tae-cm-sync), then re-run `cm-dedup`."
        )

    sets = build_exclusion_sets(CM_API_KEY)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, lower(email) AS email FROM contacts WHERE email IS NOT NULL"
        ).fetchall()

        # Classify each contact by the most-restrictive state its email matches.
        buckets: dict[str, list[int]] = {"active": [], "unsubscribed": [], "deleted": [], "not_found": []}
        for r in rows:
            em = r["email"]
            status = "not_found"
            best = 0
            for st in ("active", "unsubscribed", "deleted"):
                if em in sets[st] and STATUS_PRECEDENCE[st] > best:
                    status, best = st, STATUS_PRECEDENCE[st]
            buckets[status].append(r["id"])

        for status, ids in buckets.items():
            if ids:
                conn.execute(
                    "UPDATE contacts SET cm_status = %s WHERE id = ANY(%s)",
                    (status, ids),
                )

    summary = {k: len(v) for k, v in buckets.items()}
    summary["active_names_captured"] = capture_active_names(CM_API_KEY)
    summary["total_with_email"] = len(rows)
    log.info(
        "cm-dedup done: %d emails — active=%d unsubscribed=%d deleted=%d NEW(not_found)=%d",
        summary["total_with_email"], summary["active"], summary["unsubscribed"],
        summary["deleted"], summary["not_found"],
    )
    return summary


def name_key(full_or_first, last=None):
    """Order-independent normalised key for matching a person by name.

    Lowercased alpha tokens, sorted, joined. Returns "" if fewer than 2 tokens
    (a single name is too weak to match on). "Jane Smith" == "Smith, Jane".
    """
    import re as _re
    if last is not None:
        raw = f"{full_or_first or ''} {last or ''}"
    else:
        raw = full_or_first or ""
    raw = raw.replace(",", " ")
    toks = [t for t in _re.sub(r"[^a-z ]", " ", raw.lower()).split() if len(t) > 1]
    return " ".join(sorted(toks)) if len(toks) >= 2 else ""


def capture_active_names(api_key: str) -> int:
    """Populate cm_active_subscribers with (email, name, name_key) for every
    ACTIVE subscriber, so Newspress contacts can be cross-checked by NAME. The
    active-subscriber record trumps: a person already subscribing under any
    address/domain is not cold-emailed again (GB, 2026-07-10). Idempotent."""
    seen: dict[str, tuple] = {}
    with _client(api_key) as c:
        for cl in _get(c, "/clients.json"):
            for lst in _get(c, f"/clients/{cl['ClientID']}/lists.json"):
                page = 1
                while True:
                    data = _get(c, f"/lists/{lst['ListID']}/active.json",
                                page=page, pagesize=PAGE_SIZE,
                                orderfield="email", orderdirection="asc")
                    for row in data.get("Results", []):
                        em = (row.get("EmailAddress") or "").strip().lower()
                        nm = (row.get("Name") or "").strip()
                        if em:
                            seen[em] = (nm, name_key(nm))
                    if page >= (data.get("NumberOfPages", 1) or 1):
                        break
                    page += 1
                    time.sleep(0.3)
    with get_conn() as conn:
        conn.execute("TRUNCATE cm_active_subscribers")
        for em, (nm, key) in seen.items():
            first, _, last = nm.partition(" ")
            conn.execute(
                "INSERT INTO cm_active_subscribers (email, first_name, last_name, name_key) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (email) DO UPDATE SET "
                "  first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name, "
                "  name_key=EXCLUDED.name_key, captured_at=now()",
                (em, first or None, last or None, key or None),
            )
    n_named = sum(1 for _e, (_n, k) in seen.items() if k)
    log.info("captured %d active subscribers (%d with a usable name key)", len(seen), n_named)
    return len(seen)
