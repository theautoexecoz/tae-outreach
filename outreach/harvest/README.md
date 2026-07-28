# Harvesters

Two sources of contacts that are neither scraped dealer team pages nor manual adds.

## `ooo.py` — out-of-office delegate harvest

Mines the auto-replies to TAEDaily that collect in editor@'s `TAE-RobotReplies`
folder. Two payloads: colleagues named as delegates in the OOO body (inserted as
contacts), and per-domain address-format intelligence.

GB rule: the address that SENT the OOO is never harvested — by construction they
already receive the newsletter.

```bash
# OOO_IMAP_PASSWORD is not in the container env; pipe it in from ~/.claude/.env
# (TAE_EDITOR_IMAP_PASSWORD) so it never lands in shell history.
docker exec -i tae_outreach_api sh -c \
  'IFS= read -r OOO_IMAP_PASSWORD; export OOO_IMAP_PASSWORD; \
   python -m outreach ooo-harvest --dry-run'
```

**Clear `TAE-RobotReplies` after every ingest** (GB 2026-07-23, reaffirmed
2026-07-28). It is an airlock: delete and expunge in place, never move to
Archive — Archive syncs into the mail-archiver and clogs person-search, and
RobotReplies is excluded from that sync. The mailtriage `ooo-to-robotreplies`
rule refills it daily. Full daily sequence: `postie/README.md`.

## `newspress.py` — press-release harvest

Harvests PR/marketing contacts from newspressaustralia.com releases, plus
(since 2026-07-28) the people named in staff-appointment releases.

### Running it

The release **list** endpoint needs a logged-in session cookie, which GB
supplies per run. A single release by id is public and needs no cookie.

```bash
# 12-month re-scrape (TAE-2607-25). The list is newest-first, so months_back
# stops the walk early rather than filtering afterwards.
docker exec -e NEWSPRESS_COOKIE="<cookie>" tae_outreach_api \
  python -m outreach newspress-harvest --months-back 12 --dry-run

# single public release, no cookie — for testing the parsers
docker exec tae_outreach_api python -m outreach newspress-harvest --id <id> --dry-run
```

Get the cookie in the browser: log in → DevTools → Network → open any
`/newspress-api/releases` request → copy the `Cookie` header.

### Why a recency gate

PR people move roles constantly, and OEM domains are Microsoft 365 / catch-all,
so `verify_status` confirms the *domain* while the *person* has gone. Three
consecutive top-of-batch contacts were ruled out on one Postie run in July 2026
for exactly this. Project Postie therefore skips `source='newspress'` entirely;
a bounded, recent re-scrape is what makes the source usable again.

### Appointment harvesting and address extrapolation

A staff-appointment release names the **incoming** person but almost never
publishes their address — they have not started. The address on the page belongs
to the PR contact. Where that PR contact works for the OEM itself, their
local-part reveals the employer's address convention, and the appointee's likely
address follows.

The guard rails, in order:

1. **The release must look like an appointment** (title or opening text), and
   only the first ~1,800 characters are scanned — later paragraphs quote other
   executives, who are not appointees.
2. **The PR contact must be in-house, not the agency.** `_is_inhouse()` matches
   the address domain against the release client's own name, and rejects a
   seeded list of agency domains outright. An unrecognised domain is treated as
   *not* in-house, so an unknown agency yields no guess rather than a wrong one.
3. **The address format must be unambiguous.** If two in-house contacts on the
   same domain use different conventions, nothing is extrapolated — a coin-flip
   guess is worse than nothing.
4. **A published address always beats a guess** at the same address.

Extrapolated rows are written with `confidence='inferred'` and
`source='newspress_appointment'`. **Project Postie's selector requires
`confidence='direct'`, so these are never emailed** without a deliberate
decision by GB. They are leads, not verified contacts.

Disable with `--no-appointments`.
