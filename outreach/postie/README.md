# Project Postie — daily outreach draft assets

GB-triggered daily cold-outreach mailout. Full SOP lives in Claude's memory
(`project_postie`). This dir holds the durable, version-controlled pieces so a
run never depends on the temporary live `theautoexec.com/outreach/*.html` files.

- `intro.html` — the approved batch-1 cover-letter block (GB-locked copy).
  Spliced in immediately before the edition wrapper table (`<table class="wrapper">`),
  after `<body>`, in each day's rendered TAEDaily edition.
- `intro.txt` — plain-text fallback for the multipart/alternative draft.

Daily edition source = NewsForge `compose_edition_html` of the latest
`status='completed'` issue (streamlined `taedaily_base.html`, ~88 KB). Trim the
"Forward to a friend or colleague" CTA and the "DID YOU GET THIS FROM A FRIEND?"
section before splicing. Draft via the mailtriage `imap_helper.py draft`
(`--html-file/--from/--reply-to/--to`) into glenn@ Drafts, From
`glenn@reach.theautoexec.com`. Recipients: `confidence='direct' AND
disposition='in_play' AND cm_status='not_found'`, `ORDER BY export_batch`,
LIMIT = the day's quota; then flip those to `disposition='sent'` + note the day.
