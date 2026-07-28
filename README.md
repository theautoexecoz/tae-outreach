# tae-outreach — Project Outreach

Cold-outreach contact pipeline for TAE: discovery, scraping, extraction, verification,
deduplication against Campaign Monitor and WordPress, batch planning, and the daily
Project Postie mailout.

## Runtime — the container is the only runtime

Run every CLI command inside `tae_outreach_api`:

```bash
docker exec tae_outreach_api python -m outreach <command>
docker exec tae_outreach_api python -m outreach --help
```

There is **no host virtualenv**. One existed at `.venv/` and was decommissioned on
2026-07-28: Bedrock's system Python moved 3.13 → 3.14, the venv's `bin/python`
symlinks to system `python3`, and a 3.14 interpreter cannot see `lib/python3.13/`,
so every import failed. Nothing depended on it (no cron, no systemd unit, no
script), and rebuilding it would have created a second dependency set to keep in
step with `requirements.txt`. Do not recreate it — add what you need to the image.

Commands that read a file (for example `wp-dedup --emails-file`) need that file
inside the container:

```bash
docker exec -i tae_outreach_api sh -c 'cat > /tmp/wp-emails.txt' < local-file.txt
docker exec tae_outreach_api python -m outreach wp-dedup --emails-file /tmp/wp-emails.txt
```

Secrets not baked into the container env (for example `OOO_IMAP_PASSWORD`, which
`ooo-harvest` needs) are passed in at call time from `~/.claude/.env` — pipe them
via stdin rather than `-e` on the command line so they never land in shell history
or process listings.

## Layout

- `outreach/` — the package: `discover/`, `scrape/`, `extract/`, `enrich/`, `verify/`,
  `harvest/`, `dedup/`, `export/`, `postie/`, plus `migrations/`.
- `outreach/postie/` — Project Postie assets and the daily runbook (`postie/README.md`).
- `data/` — mounted at `/app/data`; working files, not version-controlled.

## Database

Postgres `tae_outreach` in `tae_outreach_db`. Migrations under `outreach/migrations/`,
applied with `docker exec tae_outreach_api python -m outreach migrate`.
