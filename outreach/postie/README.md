# Project Postie — daily outreach draft assets

GB-triggered daily cold-outreach mailout. Full SOP lives in Claude's memory
(`project_postie`). This dir holds the durable, version-controlled pieces so a
run never depends on the temporary live `theautoexec.com/outreach/*.html` files.

- `intro.html` — the approved batch-1 cover-letter block (GB-locked copy).
  Spliced in immediately before the edition wrapper table (`<table class="wrapper">`),
  after `<body>`, in each day's rendered TAEDaily edition.
- `intro.txt` — plain-text fallback for the multipart/alternative draft.

- `build.py` — the pure transform (render-in → ready-to-draft-out): resolves CM
  merge tags, trims the 3 elements, splices `intro.html`. Run it on the composed
  edition each day; it is tested to be byte-identical to the hand-built output.
  `python build.py --edition edition.html --intro intro.html --out postie-today.html`

Daily edition source = NewsForge `compose_edition_html` of the latest
`status='completed'` issue (streamlined `taedaily_base.html`, ~88 KB). **The
composed edition carries raw CM `[if:MemberLevel=…]…[else]🔒 Members[endif]`
merge tags** (CM resolves them at send; a manual glenn@ send does not) — `build.py`
resolves the 8 locked-article blocks to their non-member `[else]` branch and
leaves MSO `<!--[if mso]>` comments alone.
**Trim three elements** from the day's copy before splicing (GB 2026-07-13;
never edit taedaily_base.html itself):
  1. the "Forward to a friend or colleague" CTA just ahead of the first story;
  2. the entire "DID YOU GET THIS FROM A FRIEND?" section (heading + tagline + SUBSCRIBE);
  3. the final subscriber footer div ("You're receiving TheAutoExec Daily
     because you subscribed at theautoexec.com. Unsubscribe.") — the intro's
     "just delete this email" line is the cold-email opt-out instead.
Draft via the mailtriage `imap_helper.py draft` (`--html-file/--from/--reply-to/--to`)
into glenn@ Drafts, From `glenn@reach.theautoexec.com` (reach identity is set up
+ tested in Apple Mail, GB 2026-07-13). Recipients: `confidence='direct' AND
disposition='in_play' AND cm_status='not_found'`, `ORDER BY export_batch`,
LIMIT = the day's quota; then for those N: `UPDATE contacts SET
disposition='sent', sent_at=CURRENT_DATE` (see migration `009_sent.sql`).

## Daily runbook (copy-paste)

GB runs Postie ~10am each working day with a quota N. From `~/Dev/taeN/tae-docs`
(the mailtriage helper lives there); `POSTIE=/srv/docker/tae/tae-app-services/tae-outreach/outreach/postie`,
`WORK=<a scratch dir>`, `N=<quota>`.

**Selection rules (GB 2026-07-16 — apply before drafting):**
1. **Max 2 recipients per email domain per day's batch.** Capped-out contacts stay
   `in_play` and dribble out on later days (step 3's plain LIMIT is NOT enough —
   apply the cap over the ordered queue).
2. **GB approves the day's list before any draft is made.** Present the next N+5
   candidates (20 for N=15) so he can rule some out and the batch still fills.
3. Role/shared inboxes (sales@, parts@, reception@, customersupport@, …) are never
   prospects — rule out `role_or_list_inbox` on sight, even when a person's name
   is attached to the row.
4. Cross-domain check: a prospect may already be a CM subscriber under another
   domain of the same company (seen: vwga↔volkswagen.com.au, mgmotor↔smil.com,
   toyota↔lexus/gmail). If found, mark `cm_status='active'`,
   `ruled_out_stage='subscriber'` instead of drafting.

```bash
# 1. render the latest COMPLETED edition (status='completed', NOT max issue_number)
docker exec tae_newsforge python3 -c "
import asyncio; from app.database import async_session; from app.models import Issue
from app.services.taedaily_compose import compose_edition_html; from sqlalchemy import select
async def m():
    async with async_session() as db:
        i=(await db.execute(select(Issue).where(Issue.status=='completed').order_by(Issue.issue_number.desc()).limit(1))).scalar_one()
        print(await compose_edition_html(db,i.id))
asyncio.run(m())" > $WORK/edition.html

# 2. build today's email (resolve merge tags + 3 trims + splice intro)
python3 $POSTIE/build.py --edition $WORK/edition.html --intro $POSTIE/intro.html --out $WORK/postie-today.html

# 3. select the next N prospects in priority order
docker exec tae_outreach_db psql -U tae_outreach -d tae_outreach -tAF$'\t' -c "
SELECT id,email FROM contacts
WHERE confidence='direct' AND disposition='in_play' AND cm_status='not_found' AND email IS NOT NULL
ORDER BY export_batch ASC, id ASC LIMIT $N;" > $WORK/batch.tsv

# 4. one draft per prospect; collect the ids that succeeded
: > $WORK/ok-ids.txt
while IFS=$'\t' read -r cid email; do
  out=$(python3 .claude/skills/mailtriage/imap_helper.py draft --account glenn \
    --subject "A daily automotive briefing you might find useful" \
    --from "Glenn Butler <glenn@reach.theautoexec.com>" --reply-to glenn@reach.theautoexec.com \
    --to "$email" --html-file $WORK/postie-today.html --body-file $POSTIE/intro.txt 2>&1)
  echo "$out" | grep -q '"appended_to"' && echo "$cid" >> $WORK/ok-ids.txt
done < $WORK/batch.tsv

# 5. flip exactly the drafted prospects to sent
docker exec tae_outreach_db psql -U tae_outreach -d tae_outreach -c \
  "UPDATE contacts SET disposition='sent', sent_at=CURRENT_DATE WHERE id IN ($(paste -sd, $WORK/ok-ids.txt)) AND disposition='in_play';"
```

GB reviews the drafts in Apple Mail and sends them (as the reach identity).
Credentials for the helper come from `~/.claude/.env` (`TAE_GLENN_IMAP_PASSWORD`).

## Reach mailbox sweep (run after sends land)

Cold-outreach auto-replies land at **glenn@reach.theautoexec.com** (the reach
identity's From/Reply-To + envelope return-path), NOT in glenn@theautoexec.com.
Run the sweep once a day (or when checking outreach health) to reconcile them
into the DB. It reads the Maildir over the `ventraip-tae` SSH alias and writes to
`tae_outreach_db`, so run it on the Bedrock **host**. Idempotent — safe to re-run.

```bash
python3 $POSTIE/reach_sweep.py --dry-run   # preview classification
python3 $POSTIE/reach_sweep.py             # apply
```

- **Bounces** (NDRs / 550 recipient-not-found) → contact set `suppressed=true`,
  `disposition='ruled_out'` (stage `bounce`) so it is **never re-sent**.
- **Out-of-office** replies → the send was delivered, so the contact is NOT
  suppressed; `ooo_at` is stamped and it stays `disposition='sent'`.

**End-of-first-pass OOO follow-up (GB 2026-07-14):** once the whole first pass is
sent, revisit the OOO cohort. Run `cm-dedup` to refresh `cm_status`, then:

```bash
# OOO contacts who never subscribed → risk one resend
docker exec tae_outreach_db psql -U tae_outreach -d tae_outreach -c \
  "SELECT full_name, email, send_group, ooo_at FROM contacts
   WHERE ooo_at IS NOT NULL AND cm_status='not_found' ORDER BY ooo_at;"
# to resend, flip them back into the sendable universe:
#   UPDATE contacts SET disposition='in_play' WHERE ooo_at IS NOT NULL AND cm_status='not_found';
```
