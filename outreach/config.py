import os

DATABASE_DSN = os.environ.get(
    "DATABASE_DSN", "postgresql://tae_outreach:changeme@127.0.0.1:5433/tae_outreach"
)
USER_AGENT = os.environ.get("USER_AGENT", "TAE-Outreach/1.0 (contact: info@theautoexec.com)")
RATE_LIMIT_RPS = float(os.environ.get("RATE_LIMIT_RPS", "0.5"))
CM_API_KEY = os.environ.get("CM_API_KEY", "")
CM_LIST_ID = os.environ.get("CM_LIST_ID", "")

# ── OOO reply harvest (Email-list finalisation §1a) ──────────────────────────
# Reads the out-of-office auto-replies to the Daily Newsletter that collect in
# editor@theautoexec.com's `TAE-RobotReplies` IMAP folder. Read-only IMAP.
# Password: 1Password (TAE email accounts → editor@theautoexec.com); host is the
# cPanel mail server. Leave OOO_IMAP_FOLDER blank to auto-detect the folder.
OOO_IMAP_HOST = os.environ.get("OOO_IMAP_HOST", "mail.theautoexec.com")
OOO_IMAP_PORT = int(os.environ.get("OOO_IMAP_PORT", "993"))
OOO_IMAP_USER = os.environ.get("OOO_IMAP_USER", "editor@theautoexec.com")
OOO_IMAP_PASSWORD = os.environ.get("OOO_IMAP_PASSWORD", "")
OOO_IMAP_FOLDER = os.environ.get("OOO_IMAP_FOLDER", "")

# Our own domains — addresses here are never harvested as outreach contacts.
SELF_DOMAINS = {
    d.strip().lower()
    for d in os.environ.get("SELF_DOMAINS", "theautoexec.com").split(",")
    if d.strip()
}

# ── Newspress Australia scrape (Email-list finalisation §1b) ─────────────────
# PR/marketing contacts from OEM press releases. The browsable release list is
# behind a Newspress media login (Laravel Sanctum, cookie-based); individual
# releases are public. NEWSPRESS_COOKIE is a logged-in session's Cookie header
# (session + XSRF-TOKEN), taken at runtime via -e — not persisted. A browser UA
# is REQUIRED (a bot UA is served an empty SPA shell).
NEWSPRESS_BASE = os.environ.get("NEWSPRESS_BASE", "https://newspressaustralia.com")
NEWSPRESS_COOKIE = os.environ.get("NEWSPRESS_COOKIE", "")
NEWSPRESS_UA = os.environ.get(
    "NEWSPRESS_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36",
)
NEWSPRESS_RPS = float(os.environ.get("NEWSPRESS_RPS", "1.5"))
