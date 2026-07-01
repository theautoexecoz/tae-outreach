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
