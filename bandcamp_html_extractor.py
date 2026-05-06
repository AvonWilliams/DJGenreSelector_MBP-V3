"""
Pure Bandcamp HTML tag extractor (no Picard imports).

Bandcamp album pages often include tags as:

    <div class="tralbumData tralbum-tags ...">
        <a class="tag">electronic</a>
        ...
    </div>

This module provides a unit-testable helper to extract those tag strings.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import List


class _BandcampTagAnchorParser(HTMLParser):
    def __init__(self) -> None:
        # convert_charrefs=True by default in modern Python; keep explicit for clarity
        super().__init__(convert_charrefs=True)
        self._in_tag_anchor = False
        self._buf: List[str] = []
        self.tags: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        attr_dict = dict(attrs or [])
        cls = attr_dict.get("class", "") or ""
        # class may contain multiple tokens; match token "tag"
        tokens = {t.strip().lower() for t in str(cls).split() if t.strip()}
        if "tag" in tokens:
            self._in_tag_anchor = True
            self._buf = []

    def handle_endtag(self, tag: str):
        if tag.lower() != "a":
            return
        if not self._in_tag_anchor:
            return
        raw = unescape("".join(self._buf)).strip()
        if raw:
            self.tags.append(raw)
        self._in_tag_anchor = False
        self._buf = []

    def handle_data(self, data: str):
        if self._in_tag_anchor and data:
            self._buf.append(data)


def extract_bandcamp_tags_from_html(html: str) -> List[str]:
    """
    Extract Bandcamp tags from HTML.

    Strategy:
    - Prefer a lightweight DOM-ish parse via stdlib HTMLParser.
    - Fallback to a simple regex if parsing fails.

    Normalization:
    - strip whitespace
    - preserve original case
    - de-duplicate (preserving first occurrence order)
    """
    if not html:
        return []

    # 1) HTMLParser pass
    try:
        parser = _BandcampTagAnchorParser()
        parser.feed(html)
        parsed = [t.strip() for t in parser.tags if t and t.strip()]
    except Exception:
        parsed = []

    # 2) Regex fallback (only if parser found nothing)
    if not parsed:
        # As requested: <a class="tag"[^>]*>([^<]+)</a>
        # (Bandcamp often uses exactly class="tag" for tag anchors)
        rx = re.compile(r'<a\s+class=["\']tag["\'][^>]*>([^<]+)</a>', re.IGNORECASE)
        parsed = [unescape(m).strip() for m in rx.findall(html) if m and m.strip()]

    # De-dupe while preserving order
    out: List[str] = []
    seen = set()
    for t in parsed:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out

