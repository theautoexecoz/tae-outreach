import logging
from outreach.discover import register

log = logging.getLogger("outreach.discover.toyota")


@register("toyota")
def discover_toyota(limit: int = 0) -> int:
    log.warning("toyota discovery module not yet implemented — needs Cloudflare bypass")
    return 0
