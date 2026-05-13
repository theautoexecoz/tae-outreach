from dataclasses import dataclass


@dataclass
class Dealership:
    brand_slug: str
    name: str
    address: str | None = None
    suburb: str | None = None
    state: str | None = None
    postcode: str | None = None
    phone: str | None = None
    website_url: str | None = None


@dataclass
class Contact:
    dealership_id: int
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    role_raw: str | None = None
    role_normalised: str | None = None
    email: str | None = None
    email_domain: str | None = None
    phone: str | None = None
    confidence: str = "direct"
    source: str = "team_page"
    source_detail: str | None = None
