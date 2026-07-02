"""Industry-proximity classification — Email-list finalisation program §4b (TAE-2606-07).

Tags each contact with a `proximity_tier` used to order the send batches
(T1 first). Orthogonal to trust/GREEN. Rules, in order:

  dealer  source='team_page'  — franchised dealership staff (the core audience)
  T1      OEMs + importers / distributors (incl. OEM captive finance)
  T2      car-industry bodies + direct suppliers
  T3      agencies + other service providers (PR / media / marketing)
  T4      the rest (freemail, gov, non-automotive, unclassified) — safe default

Domain-based, curated from the real harvested distribution + AU automotive
knowledge. OEM matching is token-in-domain (so bmw.de, bmw.com.au, bmwgroup.com
all resolve). Idempotent; re-run after refining the sets. Freemail/gov never
promote above T4. Conservative: when unsure, T4 (never inflate proximity).
"""
import logging
import re

from outreach.db import get_conn
from outreach.extract.pattern_guesser import FREEMAIL_DOMAINS

log = logging.getLogger("outreach.enrich.proximity")

# OEM / marque brand tokens — matched as a whole word inside the domain.
OEM_TOKENS = {
    "mercedes-benz", "mercedes", "daimler", "amg", "jaguarlandrover", "jaguar",
    "landrover", "bmw", "bmwgroup", "bmwfinance", "mini", "rollsroyce", "ford",
    "lincoln", "stellantis", "fcagroup", "fca", "chrysler", "jeep", "ram",
    "dodge", "fiat", "alfaromeo", "alfa", "abarth", "maserati", "peugeot",
    "citroen", "ds", "opel", "vauxhall", "mpsa", "suzuki", "polestar", "volvo",
    "volvocars", "nissan", "infiniti", "datsun", "mazda", "renault", "dacia",
    "audi", "volkswagen", "vw", "vwga", "vwfs", "porsche", "seat", "cupra",
    "skoda", "bentley", "lamborghini", "bugatti", "toyota", "lexus", "hino",
    "daihatsu", "mitsubishi", "mmal", "mg", "mgmotor", "ldv", "maxus", "honda",
    "hyundai", "genesis", "kia", "tesla", "gwm", "haval", "chery", "omoda",
    "jaecoo", "byd", "subaru", "isuzu", "iveco", "scania", "man", "kenworth",
    "daf", "fuso", "mack", "volttrucks", "voltatrucks", "ineos", "lotus",
    "mclaren", "ferrari", "astonmartin", "cadillac", "chevrolet", "gmsv",
    "ora", "deepal", "zeekr", "smart", "cupra", "leapmotor", "xpeng", "zeekr",
    "nio", "lucid", "rivian", "smart",
}
# importers / distributors (multi-brand, AU/NZ) → also T1
IMPORTER_DOMAINS = {
    "inchcape.com.au", "ateco.com.au", "atecoautomotive.com.au",
    "lsh-auto.com.au", "lshauto.com.au", "eagers.com.au", "apeagers.com.au",
}
# captive/OEM finance patterns handled via OEM tokens (bmwfinance, vwfs, …)

# Industry bodies (peak/representative) → T2
BODY_DOMAINS = {
    "fcai.com.au", "aada.com.au", "vacc.com.au", "mtaa.com.au", "mtansw.com.au",
    "mtaq.com.au", "mtasa.com.au", "mtav.com.au", "mia.org.nz", "ancap.com.au",
    "aaaa.com.au", "evc.org.au", "electricvehiclecouncil.com.au", "aaa.asn.au",
    "carsafety.gov.au", "tmaa.com.au", "vaca.com.au",
}
# direct suppliers / automotive tech & data services → T2
SUPPLIER_TOKENS = {
    "cox", "coxautoinc", "bosch", "continental", "bridgestone", "michelin",
    "goodyear", "dunlop", "denso", "zf", "magna", "valeo", "pirelli", "castrol",
    "penrite", "narva", "redarc", "ryco", "gates", "mann", "mahle", "borgwarner",
    "aptiv", "hella", "brembo", "sachs", "bilstein", "thermoking", "webasto",
    "gates", "acdelco", "repco", "bapcor", "gpc", "supercheap",
}
# PR / media / advertising / marketing agencies → T3
AGENCY_TOKENS = {
    "wpp", "wppmedia", "havas", "havasred", "ogilvy", "spark", "sparkfoundry",
    "sparkfoundryww", "publicis", "omnicom", "edelman", "weber", "webershandwick",
    "hillandknowlton", "hkstrategies", "mccann", "ddb", "tbwa", "saatchi",
    "dentsu", "mediacom", "mindshare", "initiative", "wavemaker", "essencemediacom",
    "haystac", "influenceassociates", "poem", "thehallway", "howatson", "cummins",
    "clemenger", "redagency", "professionalpublicrelations", "ppr", "keepleft",
    "sequel", "herd", "articulate", "playpr", "thrive", "liquidideas", "hausmann",
    "n2n", "res", "engagemedia", "sensis", "carat", "zenith", "starcom", "omd",
    "phd", "um", "iprospect", "merkle", "isobar", "vmlyr", "mpsa",
}
_WORD = re.compile(r"[a-z0-9]+")


def _tokens(domain: str) -> set[str]:
    # split the registrable part into word tokens (bmwgroup.com -> {bmwgroup, com, ...})
    return set(_WORD.findall(domain.lower()))


def _tier_for(source: str, domain: str | None) -> str:
    if source == "team_page":
        return "dealer"
    if not domain:
        return "T4"
    d = domain.lower()
    if d in FREEMAIL_DOMAINS:
        return "T4"
    toks = _tokens(d)
    if d in IMPORTER_DOMAINS or toks & OEM_TOKENS:
        return "T1"
    if d in BODY_DOMAINS or toks & SUPPLIER_TOKENS:
        return "T2"
    if toks & AGENCY_TOKENS:
        return "T3"
    return "T4"


def run_classify_proximity() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, source, email_domain FROM contacts"
        ).fetchall()
        buckets: dict[str, list[int]] = {"dealer": [], "T1": [], "T2": [], "T3": [], "T4": []}
        for r in rows:
            buckets[_tier_for(r["source"], r["email_domain"])].append(r["id"])
        for tier, ids in buckets.items():
            if ids:
                conn.execute(
                    "UPDATE contacts SET proximity_tier = %s WHERE id = ANY(%s)",
                    (tier, ids),
                )
        # in_play industry breakdown (what the send actually orders on)
        inplay = {
            (r["proximity_tier"] or "?"): r["n"]
            for r in conn.execute(
                "SELECT proximity_tier, COUNT(*) AS n FROM contacts "
                "WHERE disposition='in_play' AND email IS NOT NULL GROUP BY 1"
            ).fetchall()
        }
    summary = {"classified": {k: len(v) for k, v in buckets.items()}, "in_play_emailable": inplay}
    log.info("classify-proximity: %s | in_play emailable: %s", summary["classified"], inplay)
    return summary
