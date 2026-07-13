# Project Postie — daily outreach draft assets

GB-triggered daily cold-outreach mailout. Full SOP lives in Claude's memory
(`project_postie`). This dir holds the durable, version-controlled pieces so a
run never depends on the temporary live `theautoexec.com/outreach/*.html` files.

- `intro.html` — the approved batch-1 cover-letter block (GB-locked copy).
  Spliced in immediately before the edition wrapper table (`<table class="wrapper">`),
  after `<body>`, in each day's rendered TAEDaily edition.
- `intro.txt` — plain-text fallback for the multipart/alternative draft.

Daily edition source = NewsForge `compose_edition_html` of the latest
`status='completed'` issue (streamlined `taedaily_base.html`, ~88 KB).
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
