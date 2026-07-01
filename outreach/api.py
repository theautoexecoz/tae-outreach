"""
tae-outreach — ControlTower-facing API (Project OutReach Cycle 2 §2/§4, TAE-2606-07).

A token-guarded, read-only surface over the Outreach DB, consumed by the CT-TAE
Outreach cockpit room over the internal `webservices-tae` network. Mirrors the
CT-canonical /api/v1 recipe (scraper-api / newsforge): a single shared service
token (X-CT-Api-Key), route-scoped via the router dependency (NOT global
middleware), failing closed — 503 if the token is unconfigured, 401 if
missing/invalid. No host/LAN publish; reachable only by service name + token.

Endpoints (all under /api/v1/outreach, all token-guarded):
  GET /cockpit   — composite payload the CT room renders in one fetch
  GET /summary   — cockpit overview: totals + splits by confidence/source/cm/brand
  GET /contacts  — filterable, paginated contact list (joined to dealership)
  GET /sends     — live send-monitoring panel: per-day events + rates + stop-lines
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel

DATABASE_DSN = os.environ["DATABASE_DSN"]
CT_OUTREACH_API_KEY = os.environ.get("CT_OUTREACH_API_KEY")

# Send-monitoring stop-lines (Cycle 2 §4 / "Outreach pitfalls"): pause the campaign
# above these. Complaint rate is the hard tripwire; bounce rate the hygiene signal.
COMPLAINT_STOP = 0.0005   # 0.05%
BOUNCE_STOP = 0.03        # 3%
BOUNCE_WARN = 0.02        # 2%

app = FastAPI(title="TAE Outreach API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_conn():
    return psycopg.connect(DATABASE_DSN, row_factory=dict_row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    """Ungated liveness probe for the container healthcheck."""
    return {"ok": True}


# ── Fail-closed service-token guard ─────────────────────────────────────────
def require_ct_token(x_ct_api_key: Optional[str] = Header(default=None)) -> None:
    if not CT_OUTREACH_API_KEY:
        raise HTTPException(status_code=503, detail="service token not configured")
    if not x_ct_api_key or not secrets.compare_digest(x_ct_api_key, CT_OUTREACH_API_KEY):
        raise HTTPException(status_code=401, detail="invalid or missing service token")


# ── Response models ─────────────────────────────────────────────────────────
class Count(BaseModel):
    label: Optional[str] = None
    n: int


class BrandCoverage(BaseModel):
    brand_slug: str
    dealerships: int
    contacts: int
    emailable: int


class Summary(BaseModel):
    contacts_total: int
    contacts_emailable: int
    contacts_exported: int
    dealerships_total: int
    dealerships_scraped: int
    by_confidence: list[Count]
    by_source: list[Count]
    by_cm_status: list[Count]
    by_brand: list[BrandCoverage]
    generated_at: str


class Contact(BaseModel):
    id: int
    full_name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    email_domain: Optional[str] = None
    confidence: Optional[str] = None
    source: Optional[str] = None
    source_detail: Optional[str] = None
    cm_status: Optional[str] = None
    brand_slug: Optional[str] = None
    dealership: Optional[str] = None
    suburb: Optional[str] = None
    state: Optional[str] = None
    exported: bool = False
    created_at: Optional[str] = None


class ContactPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Contact]


class SendDay(BaseModel):
    day: str
    sent: int
    delivered: int
    bounced: int
    opened: int
    unsubscribed: int
    complained: int


class SendMonitor(BaseModel):
    status: str                 # no-sends | ok | warn | stop
    total_sent: int
    total_bounced: int
    total_complained: int
    bounce_rate: float
    complaint_rate: float
    thresholds: dict
    by_day: list[SendDay]
    generated_at: str


class Cockpit(BaseModel):
    summary: Summary
    sends: SendMonitor
    contacts: ContactPage


# ── Data helpers (callable by the routes and the composite /cockpit) ─────────
def _counts(cur, sql: str) -> list[Count]:
    cur.execute(sql)
    return [Count(label=r["label"], n=r["n"]) for r in cur.fetchall()]


def _summary() -> Summary:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM contacts")
            contacts_total = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM contacts WHERE email IS NOT NULL")
            contacts_emailable = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM contacts WHERE export_batch IS NOT NULL")
            contacts_exported = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM dealerships")
            dealerships_total = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM dealerships WHERE scraped_at IS NOT NULL")
            dealerships_scraped = cur.fetchone()["n"]

            by_confidence = _counts(cur, """
                SELECT COALESCE(confidence, 'unknown') AS label, COUNT(*) AS n
                FROM contacts GROUP BY 1 ORDER BY n DESC""")
            by_source = _counts(cur, """
                SELECT COALESCE(source, 'unknown') AS label, COUNT(*) AS n
                FROM contacts GROUP BY 1 ORDER BY n DESC""")
            by_cm_status = _counts(cur, """
                SELECT COALESCE(cm_status, 'unchecked') AS label, COUNT(*) AS n
                FROM contacts GROUP BY 1 ORDER BY n DESC""")

            cur.execute("""
                SELECT d.brand_slug AS brand_slug,
                       COUNT(DISTINCT d.id) AS dealerships,
                       COUNT(c.id) AS contacts,
                       COUNT(c.email) AS emailable
                FROM dealerships d
                LEFT JOIN contacts c ON c.dealership_id = d.id
                GROUP BY d.brand_slug
                ORDER BY contacts DESC, d.brand_slug""")
            by_brand = [BrandCoverage(**r) for r in cur.fetchall()]

    return Summary(
        contacts_total=contacts_total,
        contacts_emailable=contacts_emailable,
        contacts_exported=contacts_exported,
        dealerships_total=dealerships_total,
        dealerships_scraped=dealerships_scraped,
        by_confidence=by_confidence,
        by_source=by_source,
        by_cm_status=by_cm_status,
        by_brand=by_brand,
        generated_at=_now(),
    )


def _contacts(
    brand: Optional[str] = None,
    state: Optional[str] = None,
    confidence: Optional[str] = None,
    source: Optional[str] = None,
    cm_status: Optional[str] = None,
    emailable: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> ContactPage:
    conditions: list[str] = []
    params: list = []

    if brand:
        conditions.append("d.brand_slug = %s")
        params.append(brand)
    if state:
        conditions.append("d.state = %s")
        params.append(state)
    if confidence:
        conditions.append("c.confidence = %s")
        params.append(confidence)
    if source:
        conditions.append("c.source = %s")
        params.append(source)
    if cm_status is not None:
        if cm_status in ("", "unchecked", "null"):
            conditions.append("c.cm_status IS NULL")
        else:
            conditions.append("c.cm_status = %s")
            params.append(cm_status)
    if emailable is True:
        conditions.append("c.email IS NOT NULL")
    elif emailable is False:
        conditions.append("c.email IS NULL")
    if q:
        conditions.append(
            "(c.full_name ILIKE %s OR c.email ILIKE %s OR c.email_domain ILIKE %s OR d.name ILIKE %s)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS n FROM contacts c LEFT JOIN dealerships d ON c.dealership_id = d.id {where}",
                params,
            )
            total = cur.fetchone()["n"]

            cur.execute(
                f"""
                SELECT c.id, c.full_name,
                       COALESCE(c.role_normalised, c.role_raw) AS role,
                       c.email, c.email_domain, c.confidence, c.source, c.source_detail,
                       c.cm_status, c.export_batch, c.created_at,
                       d.brand_slug, d.name AS dealership, d.suburb, d.state
                FROM contacts c
                LEFT JOIN dealerships d ON c.dealership_id = d.id
                {where}
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cur.fetchall()

    items = [
        Contact(
            id=r["id"],
            full_name=r.get("full_name"),
            role=r.get("role"),
            email=r.get("email"),
            email_domain=r.get("email_domain"),
            confidence=r.get("confidence"),
            source=r.get("source"),
            source_detail=r.get("source_detail"),
            cm_status=r.get("cm_status"),
            brand_slug=r.get("brand_slug"),
            dealership=r.get("dealership"),
            suburb=r.get("suburb"),
            state=r.get("state"),
            exported=r.get("export_batch") is not None,
            created_at=r["created_at"].isoformat() if r.get("created_at") else None,
        )
        for r in rows
    ]
    return ContactPage(total=total, limit=limit, offset=offset, items=items)


def _sends(days: int = 30) -> SendMonitor:
    thresholds = {
        "complaint_stop": COMPLAINT_STOP,
        "bounce_warn": BOUNCE_WARN,
        "bounce_stop": BOUNCE_STOP,
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_char(occurred_at AT TIME ZONE 'Australia/Melbourne', 'YYYY-MM-DD') AS day,
                       COUNT(*) FILTER (WHERE event = 'sent')          AS sent,
                       COUNT(*) FILTER (WHERE event = 'delivered')     AS delivered,
                       COUNT(*) FILTER (WHERE event = 'bounced')       AS bounced,
                       COUNT(*) FILTER (WHERE event = 'opened')        AS opened,
                       COUNT(*) FILTER (WHERE event = 'unsubscribed')  AS unsubscribed,
                       COUNT(*) FILTER (WHERE event = 'complained')    AS complained
                FROM send_events
                WHERE occurred_at >= NOW() - (%s || ' days')::interval
                GROUP BY 1 ORDER BY 1 DESC
                """,
                (days,),
            )
            by_day = [SendDay(**r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT COUNT(*) FILTER (WHERE event = 'sent')       AS sent,
                       COUNT(*) FILTER (WHERE event = 'bounced')    AS bounced,
                       COUNT(*) FILTER (WHERE event = 'complained') AS complained
                FROM send_events
                """
            )
            tot = cur.fetchone()

    total_sent = tot["sent"] or 0
    total_bounced = tot["bounced"] or 0
    total_complained = tot["complained"] or 0
    bounce_rate = (total_bounced / total_sent) if total_sent else 0.0
    complaint_rate = (total_complained / total_sent) if total_sent else 0.0

    if total_sent == 0:
        status = "no-sends"
    elif complaint_rate >= COMPLAINT_STOP or bounce_rate >= BOUNCE_STOP:
        status = "stop"
    elif bounce_rate >= BOUNCE_WARN:
        status = "warn"
    else:
        status = "ok"

    return SendMonitor(
        status=status,
        total_sent=total_sent,
        total_bounced=total_bounced,
        total_complained=total_complained,
        bounce_rate=round(bounce_rate, 5),
        complaint_rate=round(complaint_rate, 5),
        thresholds=thresholds,
        by_day=by_day,
        generated_at=_now(),
    )


# ── Router (route-scoped token guard) ───────────────────────────────────────
v1 = APIRouter(prefix="/api/v1/outreach", dependencies=[Depends(require_ct_token)])


@v1.get("/cockpit", response_model=Cockpit)
def cockpit():
    """Everything the CT Outreach room renders, in one guarded fetch."""
    return Cockpit(summary=_summary(), sends=_sends(30), contacts=_contacts(limit=100))


@v1.get("/summary", response_model=Summary)
def summary():
    return _summary()


@v1.get("/contacts", response_model=ContactPage)
def contacts(
    brand: Optional[str] = None,
    state: Optional[str] = None,
    confidence: Optional[str] = None,
    source: Optional[str] = None,
    cm_status: Optional[str] = None,
    emailable: Optional[bool] = None,
    q: Optional[str] = Query(None, description="search name / email / domain / dealership"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return _contacts(brand, state, confidence, source, cm_status, emailable, q, limit, offset)


@v1.get("/sends", response_model=SendMonitor)
def sends(days: int = Query(30, ge=1, le=365)):
    return _sends(days)


app.include_router(v1)
