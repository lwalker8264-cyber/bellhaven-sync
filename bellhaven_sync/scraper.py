# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""Scrapes every Bellhaven community from the public website.

It crawls the home page, the About page and every page of the directory, and collects every
/communities/<slug> link it finds anywhere. That matters: a community can be announced on the home
page before it is added to the directory (Bellhaven Meadows of Findlay was).
"""
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import config

DETAIL_RE = re.compile(r"^/communities/[a-z0-9-]+/?$")


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


class _DetailParser(HTMLParser):
    """Reads <h1> and the <dl class="detail"> dt/dd pairs; <br> becomes a line break."""

    def __init__(self):
        super().__init__()
        self.h1 = ""
        self.fields = {}
        self.badges = {}
        self._in = None
        self._dt = None
        self._buf = []
        self._in_badge = False

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "") or ""
        if tag in ("h1", "dt", "dd"):
            self._in, self._buf = tag, []
        elif tag == "br" and self._in == "dd":
            self._buf.append("\n")
        elif tag == "span" and "badge" in cls and self._in == "dd":
            self._in_badge = True
            self.badges.setdefault(self._dt, []).append("")

    def handle_endtag(self, tag):
        if tag == "span":
            self._in_badge = False
        if tag != self._in:
            return
        text = "".join(self._buf).strip()
        if tag == "h1" and not self.h1:
            self.h1 = text
        elif tag == "dt":
            self._dt = text
        elif tag == "dd" and self._dt:
            self.fields[self._dt] = text
        self._in = None

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)
        if self._in_badge and self._dt in self.badges:
            self.badges[self._dt][-1] += data.strip()


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "bellhaven-sync/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _links(html: str):
    p = _LinkParser()
    p.feed(html)
    return [urllib.parse.urlparse(h).path if h.startswith("http") else h.split("?")[0] for h in p.links], p.links


def parse_address(text: str):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    street = lines[0] if lines else ""
    m = re.match(r"^(.*?),\s*([A-Z]{2})\s+(\d{5})", lines[-1] if len(lines) > 1 else "")
    city, state, zip_ = (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
    return street, city, state, zip_


def parse_detail(html: str, slug: str, url: str):
    p = _DetailParser()
    p.feed(html)
    street, city, state, zip_ = parse_address(p.fields.get("Address", ""))
    offerings = p.badges.get("Care Offerings") or [p.fields.get("Care Offerings", "")]
    return {
        "slug": slug,
        "url": url,
        "name": p.h1,
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_,
        "care_offerings": [o for o in offerings if o],
        "administrator": p.fields.get("Administrator", ""),
        "phone": p.fields.get("Phone", ""),
    }


def scrape(base_url: str = config.BASE_URL, fetch=_get):
    detail_paths, seen_pages = set(), set()
    queue = ["/", "/about", "/communities"]
    while queue:
        page = queue.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        html = fetch(base_url + page)
        paths, raw = _links(html)
        for path in paths:
            if DETAIL_RE.match(path):
                detail_paths.add(path.rstrip("/"))
        for href in raw:  # follow directory pagination (?page=N)
            if "/communities?page=" in href or href.startswith("?page="):
                q = href.split("?", 1)[1]
                queue.append("/communities?" + q)
    locations = []
    for path in sorted(detail_paths):
        url = base_url + path
        loc = parse_detail(fetch(url), path.rsplit("/", 1)[-1], url)
        if loc["name"] and loc["street"]:
            locations.append(loc)
    return locations
