import logging

log = logging.getLogger("outreach.discover")

BRAND_MODULES = {}


def register(slug):
    def decorator(fn):
        BRAND_MODULES[slug] = fn
        return fn
    return decorator


def run_discovery(brand_slug: str, limit: int = 0):
    if brand_slug not in BRAND_MODULES:
        log.warning("no discovery module for %s yet", brand_slug)
        return 0
    fn = BRAND_MODULES[brand_slug]
    return fn(limit=limit)


# Import brand modules to trigger registration
from outreach.discover import toyota  # noqa: F401, E402
from outreach.discover import mazda  # noqa: F401, E402
from outreach.discover import hyundai  # noqa: F401, E402
from outreach.discover import subaru  # noqa: F401, E402
from outreach.discover import nissan  # noqa: F401, E402
from outreach.discover import isuzu  # noqa: F401, E402
from outreach.discover import mitsubishi  # noqa: F401, E402
from outreach.discover import kia  # noqa: F401, E402
from outreach.discover import bmw  # noqa: F401, E402
from outreach.discover import mercedes  # noqa: F401, E402
from outreach.discover import ldv  # noqa: F401, E402
