#!/usr/bin/env python3
"""Project Postie — turn a composed TAEDaily edition into the outreach email body.

Pure transform (no DB, no network): takes the NewsForge-composed edition HTML +
the intro cover letter, returns the ready-to-draft outreach HTML. Kept as a
tested script so the daily run never re-derives this fragile email-HTML surgery.

Daily flow:
  1. Render the latest **completed** edition in the newsforge container:
       docker exec tae_newsforge python3 -c "import asyncio;from app.database import async_session; \
       from app.models import Issue;from app.services.taedaily_compose import compose_edition_html; \
       from sqlalchemy import select;\nasync def m():\n async with async_session() as db:\n  i=(await db.execute(select(Issue).where(Issue.status=='completed').order_by(Issue.issue_number.desc()).limit(1))).scalar_one();print(await compose_edition_html(db,i.id))\nasyncio.run(m())" > edition.html
     (Use status='completed', NOT max(issue_number) — that is the next day's in-progress shell.)
  2. python build.py --edition edition.html --intro intro.html --out postie-today.html
  3. Draft per recipient via the mailtriage helper; flip disposition->sent, sent_at=CURRENT_DATE.

What this does:
  - Resolves the 8 CM `[if:MemberLevel=...]...[else]X[endif]` locked-article
    conditionals to their [else] (non-member) branch. CM resolves these at send;
    a manual glenn@ send does not, so raw they render as literal junk. MSO
    `<!--[if mso]>...<![endif]-->` comments are left untouched.
  - Trims 3 elements (GB 2026-07-13): the "Forward to a friend or colleague" CTA,
    the "DID YOU GET THIS FROM A FRIEND?" section, and the subscriber/unsubscribe
    footer div. Each removed as a balanced block (byte-exact elsewhere).
  - Splices the intro cover letter in before the edition wrapper table.
"""
import argparse
import re
import sys

PADLOCK = '\U0001f512'
LAYOUT_ANCHOR = '<div class="layout one-col fixed-width stack"'
WRAPPER_MARKER = '<table bgcolor="#ffffff" cellpadding="0" cellspacing="0" class="wrapper"'


def resolve_member_tags(html: str) -> str:
    """[if:MemberLevel=…]…[else]X[endif] -> X (the non-member branch), and strip
    the padlock so the locked-article button reads just "Members" (GB 2026-07-13)."""
    def _else(m):
        text = m.group(1)
        # strip the padlock so the locked-article button reads just "Members"
        return text.replace(PADLOCK+'\xa0','').replace(PADLOCK+' ','').replace(PADLOCK,'')
    out = re.sub(r'\[if:MemberLevel=.*?\[else\](.*?)\[endif\]', _else, html, flags=re.S)
    residue = re.findall(r'\[if:|\[elseif:|\[else\]', out)
    if residue:
        raise SystemExit(f"unresolved CM merge tags remain: {residue[:5]}")
    return out


def _balanced_div_end(html: str, start: int) -> int:
    depth = 0
    for m in re.finditer(r'<div\b|</div>', html[start:]):
        depth += -1 if m.group().startswith('</') else 1
        if depth == 0:
            return start + m.end()
    raise SystemExit("unbalanced <div> while trimming")


def _remove_block(html: str, needle: str, anchor: str) -> str:
    ni = html.index(needle)
    start = html.rfind(anchor, 0, ni)
    if start == -1:
        raise SystemExit(f"trim anchor {anchor!r} not found before {needle!r}")
    return html[:start] + html[_balanced_div_end(html, start):]


def trim_three(html: str) -> str:
    html = _remove_block(html, "forwardtomyfriend.com", LAYOUT_ANCHOR)
    html = _remove_block(html, "DID YOU GET THIS FROM A FRIEND", LAYOUT_ANCHOR)
    html = _remove_block(html, "receiving TheAutoExec Daily because you subscribed", "<div")
    for gone in ("Forward to a friend or colleague", "DID YOU GET THIS FROM A FRIEND",
                 "because you subscribed at theautoexec.com"):
        if gone in html:
            raise SystemExit(f"trim failed, still present: {gone!r}")
    return html


def splice_intro(html: str, intro: str) -> str:
    i = html.index(WRAPPER_MARKER)
    out = html[:i] + intro + "\n" + html[i:]
    # copy-stable marker (survives We->I and other wording edits)
    if out.count("publicly listed business contact details") != 1:
        raise SystemExit("intro splice sanity failed")
    return out


def build(edition_html: str, intro_html: str) -> str:
    html = resolve_member_tags(edition_html)
    html = trim_three(html)
    html = splice_intro(html, intro_html)
    if len(html.encode()) >= 102_400:
        print(f"WARNING: {len(html.encode())} bytes >= 102400 (Gmail clip threshold)", file=sys.stderr)
    return html


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Postie outreach email from a composed edition")
    ap.add_argument("--edition", required=True, help="NewsForge-composed edition HTML")
    ap.add_argument("--intro", required=True, help="intro cover-letter HTML block")
    ap.add_argument("--out", required=True, help="output path for the ready-to-draft HTML")
    a = ap.parse_args()
    edition = open(a.edition, encoding="utf-8").read()
    intro = open(a.intro, encoding="utf-8").read()
    out = build(edition, intro)
    open(a.out, "w", encoding="utf-8").write(out)
    print(f"wrote {a.out}: {len(out.encode())} bytes (Gmail-clip-safe: {len(out.encode()) < 102_400})")


if __name__ == "__main__":
    main()
