import os

DATABASE_DSN = os.environ.get(
    "DATABASE_DSN", "postgresql://tae_outreach:changeme@localhost:5432/tae_outreach"
)
USER_AGENT = os.environ.get("USER_AGENT", "TAE-Outreach/1.0 (contact: info@theautoexec.com)")
RATE_LIMIT_RPS = float(os.environ.get("RATE_LIMIT_RPS", "0.5"))
CM_API_KEY = os.environ.get("CM_API_KEY", "")
CM_LIST_ID = os.environ.get("CM_LIST_ID", "")
