#!/usr/bin/env python3
"""Index monthly mixtapes and their tracklists."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path


DEFAULT_DB = Path("crate-digger.sqlite3")
USER_AGENT = "crate-digger/0.1 (+https://github.com/zpencerguy/crate-digger)"
SOUNDCLOUD_OEMBED = "https://soundcloud.com/oembed"
DEFAULT_1001_CRAWL_DELAY = 8.0
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

URL_RE = re.compile(r"https?://[^\s<>\"]+")
TIME_RE = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})")
CLIENT_ID_RE = re.compile(r'client_id:"([A-Za-z0-9]{20,40})"')
SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="([^"]+)"')
SOUNDCLOUD_USER_API_RE = re.compile(r"https://api\.soundcloud\.com/users/soundcloud%3Ausers%3A(\d+)")
TRACK_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<num>\d{1,3})(?:[\).]\s*|-\s+|\s+))?
    (?:\[?(?P<time>(?:(?:\d{1,2}:)?\d{1,2}:\d{2}))\]?\s*)?
    (?P<body>.+?)
    \s*$
    """,
    re.VERBOSE,
)
TRACKLIST_LINE_RE = re.compile(
    r"^\s*(?:\d{1,3}(?:[\).]\s*|-\s+|\s+)|\[?(?:(?:\d{1,2}:)?\d{1,2}:\d{2})\]?\s+)"
)
MONTH_RE = re.compile(
    r"\b("
    + "|".join(MONTHS)
    + r")\b(?:\s*(?:\+|and|&)\s*\b("
    + "|".join(MONTHS)
    + r")\b)?\s+(\d{4})",
    re.IGNORECASE,
)
PUBLISHED_RE = re.compile(r"published on (\d{4})-(\d{2})-\d{2}T", re.IGNORECASE)
MIXESDB_TRACK_RE = re.compile(r"^#\s+(?:\[(?P<time>[^\]]+)\]\s+)?(?P<body>.+)$")
MIXESDB_PLAYER_RE = re.compile(r"https://soundcloud\.com/[^\s}|]+")
ONE_THOUSAND_ONE_HOSTS = {"www.1001tracklists.com", "1001tracklists.com", "1001.tl"}
ONE_THOUSAND_ONE_TRACK_SELECTOR = 'div.tlpItem, tr.tlpItem, [itemtype*="MusicRecording"]'


class TracklistsChallengeError(RuntimeError):
    pass


class LinkParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self._active_href: str | None = None
        self._active_text: list[str] = []
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._active_href = href
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._active_href is None:
            return
        text = " ".join("".join(self._active_text).split())
        self.links.append(
            {
                "text": html.unescape(text),
                "url": urllib.parse.urljoin(self.page_url, self._active_href),
            }
        )
        self._active_href = None
        self._active_text = []


class OneThousandOneTracklistParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_track = False
        self._track_depth = 0
        self._active_text_field: str | None = None
        self._active_text: list[str] = []
        self._current: dict[str, str] | None = None
        self.tracks: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = set((attrs_dict.get("class") or "").split())
        if tag == "tr" and "tlpItem" in classes:
            self._in_track = True
            self._track_depth = 1
            self._current = {}
            return
        if not self._in_track:
            return

        itemprop = attrs_dict.get("itemprop")
        content = attrs_dict.get("content")
        if itemprop and content and self._current is not None:
            self._current[itemprop] = html.unescape(content).strip()
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        self._track_depth += 1
        if classes.intersection({"cueValue", "trackValue"}) or itemprop in {"byArtist", "name"}:
            self._active_text_field = itemprop or next(iter(classes.intersection({"cueValue", "trackValue"})))
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_text_field:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._in_track:
            return
        if self._active_text_field:
            text = " ".join("".join(self._active_text).split())
            if text and self._current is not None and self._active_text_field not in self._current:
                self._current[self._active_text_field] = html.unescape(text).strip()
            self._active_text_field = None
            self._active_text = []
        self._track_depth -= 1
        if self._track_depth <= 0:
            self._finish_track()

    def close(self) -> None:
        super().close()
        if self._in_track:
            self._finish_track()

    def _finish_track(self) -> None:
        current = self._current or {}
        artist = current.get("byArtist") or None
        title = current.get("name") or current.get("trackValue") or ""
        cue = normalize_1001_time(current.get("cueValue"))
        if title:
            position = len(self.tracks) + 1
            raw_text = f"{position}. "
            if cue:
                raw_text += f"[{cue}] "
            if artist:
                raw_text += f"{artist} - {title}"
            else:
                raw_text += title
            self.tracks.append(
                {
                    "position": position,
                    "cue_seconds": seconds_from_time(cue),
                    "artist": artist,
                    "title": title,
                    "raw_text": raw_text,
                }
            )
        self._in_track = False
        self._track_depth = 0
        self._active_text_field = None
        self._active_text = []
        self._current = None


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mixtapes (
    id INTEGER PRIMARY KEY,
    soundcloud_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    uploader TEXT,
    month TEXT,
    release_date TEXT,
    series TEXT,
    description TEXT,
    tracklist_url TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    mixtape_id INTEGER NOT NULL REFERENCES mixtapes(id) ON DELETE CASCADE,
    position INTEGER,
    cue_seconds INTEGER,
    artist TEXT,
    title TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    UNIQUE(mixtape_id, position, raw_text)
);

CREATE TABLE IF NOT EXISTS track_metadata (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_url TEXT,
    source_track_id TEXT,
    bpm TEXT,
    musical_key TEXT,
    genre TEXT,
    label TEXT,
    release_title TEXT,
    release_date TEXT,
    confidence TEXT,
    raw_json TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(track_id, source)
);

CREATE INDEX IF NOT EXISTS tracks_artist_idx ON tracks(artist);
CREATE INDEX IF NOT EXISTS tracks_title_idx ON tracks(title);
CREATE INDEX IF NOT EXISTS mixtapes_month_idx ON mixtapes(month);
CREATE INDEX IF NOT EXISTS track_metadata_track_idx ON track_metadata(track_id);
CREATE INDEX IF NOT EXISTS track_metadata_source_idx ON track_metadata(source);
"""


class SoundCloudTracksPageParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        parsed = urllib.parse.urlparse(page_url)
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.profile_path = parsed.path.removesuffix("/tracks").rstrip("/")
        self._active_href: str | None = None
        self._active_text: list[str] = []
        self._pending_track: dict[str, str] | None = None
        self.tracks: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href")
        if not href:
            return
        self._active_href = href
        self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)
        elif self._pending_track is not None:
            match = PUBLISHED_RE.search(data)
            if match:
                self._pending_track["published_month"] = f"{match.group(1)}-{match.group(2)}"
                self.tracks.append(self._pending_track)
                self._pending_track = None

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._active_href is None:
            return
        href = self._active_href
        title = " ".join("".join(self._active_text).split())
        self._active_href = None
        self._active_text = []
        if title and self._is_track_href(href):
            self._pending_track = {
                "title": html.unescape(title),
                "url": urllib.parse.urljoin(self.base_url, href),
            }

    def close(self) -> None:
        super().close()
        if self._pending_track is not None:
            self.tracks.append(self._pending_track)
            self._pending_track = None

    def _is_track_href(self, href: str) -> bool:
        parsed = urllib.parse.urlparse(href)
        path = parsed.path.rstrip("/")
        if not path.startswith(self.profile_path + "/"):
            return False
        leaf = path.rsplit("/", 1)[-1]
        return leaf not in {"tracks", "albums", "sets", "likes", "comments"}


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    ensure_column(conn, "mixtapes", "release_date", "TEXT")
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def fetch_soundcloud_oembed(url: str) -> dict[str, str]:
    params = urllib.parse.urlencode({"format": "json", "url": url})
    request = urllib.request.Request(
        f"{SOUNDCLOUD_OEMBED}?{params}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_robots_parser(url: str) -> urllib.robotparser.RobotFileParser:
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser(robots_url)
    parser.parse(fetch_text(robots_url).splitlines())
    return parser


def is_1001_url(url: str) -> bool:
    return urllib.parse.urlparse(url).netloc.lower() in ONE_THOUSAND_ONE_HOSTS


def ensure_1001_allowed(url: str) -> float:
    if not is_1001_url(url):
        raise SystemExit(f"Expected a 1001Tracklists URL, got: {url}")
    robots = fetch_robots_parser(url)
    if not robots.can_fetch(USER_AGENT, url):
        raise SystemExit(f"robots.txt does not allow {USER_AGENT} to fetch {url}")
    return robots.crawl_delay(USER_AGENT) or DEFAULT_1001_CRAWL_DELAY


def fetch_1001_tracklist_html(url: str, *, delay: float | None = None) -> str:
    robots_delay = ensure_1001_allowed(url)
    crawl_delay = robots_delay if delay is None else max(delay, robots_delay)
    if crawl_delay > 0:
        time.sleep(crawl_delay)
    html_text = fetch_text(url)
    if is_1001_challenge(html_text):
        raise TracklistsChallengeError(
            "1001Tracklists served a bot-protection challenge instead of tracklist HTML."
        )
    return html_text


def is_1001_challenge(html_text: str) -> bool:
    lower = html_text.lower()
    if 'class="tlpitem"' in lower or "schema.org/musicrecording" in lower:
        return False
    return "turnstile" in lower or "cf-challenge" in lower or "please enable javascript" in lower


def fetch_json(url: str) -> object:
    return json.loads(fetch_text(url))


def raw_mixesdb_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    title = urllib.parse.unquote(parsed.path.removeprefix("/w/"))
    query = urllib.parse.urlencode({"title": title, "action": "raw"})
    return urllib.parse.urlunparse(parsed._replace(path="/w/index.php", query=query))


def plain_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def extract_tracklist_url(text: str) -> str | None:
    for url in URL_RE.findall(text):
        clean = url.rstrip(").,]")
        host = urllib.parse.urlparse(clean).netloc.lower()
        if "1001tracklists.com" in host or host == "1001.tl":
            return clean
    return None


def default_mixesdb_title_match(category_url: str) -> str:
    leaf = urllib.parse.unquote(urllib.parse.urlparse(category_url).path.rsplit("/", 1)[-1])
    return leaf.removeprefix("Category:").replace("_", " ")


def extract_mixesdb_category_pages(category_url: str, html_text: str, title_match: str | None = None) -> list[dict[str, str]]:
    parser = LinkParser(category_url)
    parser.feed(html_text)
    title_match = title_match or default_mixesdb_title_match(category_url)
    seen: set[str] = set()
    pages: list[dict[str, str]] = []
    for link in parser.links:
        parsed = urllib.parse.urlparse(link["url"])
        if parsed.netloc and parsed.netloc != "www.mixesdb.com":
            continue
        if not parsed.path.startswith("/w/20"):
            continue
        if title_match and title_match.lower() not in link["text"].lower():
            continue
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        pages.append(link)
    return pages


def normalize_mixesdb_time(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean or clean == "?":
        return None
    parts = clean.split(":")
    if len(parts) == 1 and parts[0].isdigit():
        return f"{int(parts[0]):02d}:00"
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return f"{int(parts[0])}:{int(parts[1]):02d}:{int(parts[2]):02d}"
    return None


def normalize_1001_time(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip().strip("[]")
    if not clean or clean == "?":
        return None
    match = TIME_RE.search(clean)
    return match.group(0) if match else None


def parse_1001tracklists_html(html_text: str) -> list[dict[str, object]]:
    parser = OneThousandOneTracklistParser()
    parser.feed(html_text)
    parser.close()
    return parser.tracks


def tracks_from_browser_1001(rows: object) -> list[dict[str, object]]:
    if not isinstance(rows, list):
        return []
    tracks: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        cue = normalize_1001_time(str(row.get("cue") or ""))
        position = len(tracks) + 1
        artist = str(row["artist"]).strip() if row.get("artist") else None
        title = remove_artist_prefix(str(row["title"]), artist)
        raw_text = f"{position}. "
        if cue:
            raw_text += f"[{cue}] "
        raw_text += f"{artist} - {title}" if artist else title
        tracks.append(
            {
                "position": position,
                "cue_seconds": seconds_from_time(cue),
                "artist": artist,
                "title": title,
                "raw_text": raw_text,
            }
        )
    return tracks


def parse_mixesdb_raw_tracklist(raw_text: str) -> tuple[str | None, str]:
    soundcloud_url = None
    player_block_match = re.search(r"\{\{Player(?P<body>.*?)\}\}", raw_text, re.DOTALL)
    if player_block_match:
        player_match = MIXESDB_PLAYER_RE.search(player_block_match.group("body"))
        if player_match:
            soundcloud_url = player_match.group(0).strip()

    lines: list[str] = []
    for line in raw_text.splitlines():
        match = MIXESDB_TRACK_RE.match(line.strip())
        if not match:
            continue
        cue = normalize_mixesdb_time(match.group("time"))
        body = match.group("body").strip()
        position = len(lines) + 1
        if cue:
            lines.append(f"{position}. [{cue}] {body}")
        else:
            lines.append(f"{position}. {body}")
    return soundcloud_url, "\n".join(lines)


def extract_soundcloud_tracks(page_url: str, html_text: str) -> list[dict[str, str]]:
    parser = SoundCloudTracksPageParser(page_url)
    parser.feed(html_text)
    parser.close()
    seen: set[str] = set()
    tracks: list[dict[str, str]] = []
    for track in parser.tracks:
        if track["url"] in seen:
            continue
        seen.add(track["url"])
        tracks.append(track)
    return tracks


def extract_soundcloud_user_id(html_text: str) -> str | None:
    match = SOUNDCLOUD_USER_API_RE.search(html_text)
    return match.group(1) if match else None


def discover_soundcloud_client_id(page_url: str, html_text: str) -> str | None:
    for src in SCRIPT_SRC_RE.findall(html_text):
        script_url = urllib.parse.urljoin(page_url, html.unescape(src))
        if "sndcdn.com/assets/" not in script_url:
            continue
        script = fetch_text(script_url)
        match = CLIENT_ID_RE.search(script)
        if match:
            return match.group(1)
    return None


def soundcloud_api_url(user_id: str, client_id: str, limit: int = 50) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "limit": limit,
            "offset": 0,
            "linked_partitioning": 1,
        }
    )
    return f"https://api-v2.soundcloud.com/users/{user_id}/tracks?{query}"


def with_client_id(url: str, client_id: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query["client_id"] = [client_id]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))


def published_month(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"(\d{4})-(\d{2})-", value)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def published_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else None


def fetch_soundcloud_api_tracks(
    page_url: str,
    html_text: str,
    *,
    client_id: str | None = None,
    max_tracks: int | None = None,
    stop_urls: set[str] | None = None,
) -> list[dict[str, str]]:
    user_id = extract_soundcloud_user_id(html_text)
    if not user_id:
        return []
    client_id = client_id or discover_soundcloud_client_id(page_url, html_text)
    if not client_id:
        return []

    url = soundcloud_api_url(user_id, client_id)
    tracks: list[dict[str, str]] = []
    while url and (max_tracks is None or len(tracks) < max_tracks):
        data = fetch_json(url)
        if not isinstance(data, dict):
            break
        collection = data.get("collection", [])
        if not isinstance(collection, list):
            break
        for item in collection:
            if not isinstance(item, dict) or not item.get("permalink_url") or not item.get("title"):
                continue
            if stop_urls and str(item["permalink_url"]) in stop_urls:
                return tracks
            user = item.get("user")
            uploader = user.get("username") if isinstance(user, dict) else None
            track = {
                "title": str(item["title"]),
                "url": str(item["permalink_url"]),
                "description": str(item.get("description") or ""),
            }
            created_month = published_month(item.get("created_at"))
            created_date = published_date(item.get("created_at"))
            if created_month:
                track["published_month"] = created_month
            if created_date:
                track["release_date"] = created_date
            if uploader:
                track["uploader"] = str(uploader)
            tracks.append(track)
            if max_tracks is not None and len(tracks) >= max_tracks:
                break
        next_href = data.get("next_href")
        url = with_client_id(next_href, client_id) if isinstance(next_href, str) else ""
    return tracks


def infer_month(title: str, fallback: str | None = None) -> str | None:
    match = MONTH_RE.search(title)
    if match:
        month_name = match.group(2) or match.group(1)
        return f"{match.group(3)}-{MONTHS[month_name.lower()]:02d}"
    return fallback


def infer_mixesdb_page_month(title: str) -> str | None:
    match = re.match(r"(\d{4})-(\d{2})-\d{2}\s+-\s+", title)
    return f"{match.group(1)}-{match.group(2)}" if match else infer_month(title)


def mixesdb_page_date(title: str) -> str | None:
    match = re.match(r"(\d{4}-\d{2}-\d{2})\s+-\s+", title)
    return match.group(1) if match else None


def title_has_month(title: str) -> bool:
    return MONTH_RE.search(title) is not None


def seconds_from_time(value: str | None) -> int | None:
    if not value:
        return None
    match = TIME_RE.fullmatch(value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def split_artist_title(body: str) -> tuple[str | None, str]:
    for separator in (" - ", " – ", " — "):
        if separator in body:
            artist, title = body.split(separator, 1)
            return artist.strip() or None, title.strip()
    return None, body.strip()


def remove_artist_prefix(title: str, artist: str | None) -> str:
    title = title.strip()
    if not artist:
        return title
    for separator in (" - ", " – ", " — "):
        prefix = f"{artist}{separator}"
        if title.casefold().startswith(prefix.casefold()):
            return title[len(prefix) :].strip()
    return title


def parse_tracklist(text: str) -> list[dict[str, object]]:
    tracks: list[dict[str, object]] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        match = TRACK_RE.match(raw)
        if not match:
            continue
        body = match.group("body").strip()
        if not body or URL_RE.search(body):
            continue
        artist, title = split_artist_title(body)
        tracks.append(
            {
                "position": int(match.group("num")) if match.group("num") else len(tracks) + 1,
                "cue_seconds": seconds_from_time(match.group("time")),
                "artist": artist,
                "title": title,
                "raw_text": raw,
            }
        )
    return tracks


def parse_description_tracklist(description: str | None) -> list[dict[str, object]]:
    if not description:
        return []
    lines = description.splitlines()
    start = 0
    for index, line in enumerate(lines):
        header = line.strip().casefold().rstrip(":")
        if header == "tracklist" or header.endswith(" tracklist"):
            start = index + 1
            break

    track_lines: list[str] = []
    started = False
    for line in lines[start:]:
        raw = line.strip()
        if not raw:
            continue
        if not TRACKLIST_LINE_RE.match(raw):
            if started:
                break
            continue
        track_lines.append(raw)
        started = True

    tracks = parse_tracklist("\n".join(track_lines))
    return tracks if len(tracks) >= 2 else []


def infer_uploader(title: str) -> str | None:
    if " by " in title:
        return title.rsplit(" by ", 1)[1].strip() or None
    return None


def magic_tape_number(title: str) -> int | None:
    match = re.search(r"\bmagic tape\s+0*(\d{1,3})\b", title, re.IGNORECASE)
    return int(match.group(1)) if match else None


def numbered_series_number(title: str, series: str | None) -> int | None:
    if not series:
        return None
    pattern = re.escape(series).replace(r"\ ", r"\s+")
    match = re.search(rf"\b{pattern}\s+#?0*(\d{{1,4}})\b", title, re.IGNORECASE)
    return int(match.group(1)) if match else None


def find_mixtape_for_source(
    conn: sqlite3.Connection,
    *,
    soundcloud_url: str | None,
    page_title: str,
    series: str | None,
) -> sqlite3.Row | None:
    if soundcloud_url:
        mixtape = conn.execute(
            "SELECT id, title FROM mixtapes WHERE soundcloud_url = ?",
            (soundcloud_url,),
        ).fetchone()
        if mixtape:
            page_number = numbered_series_number(page_title, series)
            mixtape_number = numbered_series_number(mixtape["title"], series)
            if page_number is None or mixtape_number is None or page_number == mixtape_number:
                return mixtape

    series_number = numbered_series_number(page_title, series)
    if series and series_number is not None:
        pattern = f"%{series} #{series_number}%"
        spaced_pattern = f"%{series} {series_number}%"
        zero_pattern = f"%{series} #{series_number:03d}%"
        zero_spaced_pattern = f"%{series} {series_number:03d}%"
        return conn.execute(
            """
            SELECT id, title FROM mixtapes
            WHERE series = ? AND (title LIKE ? OR title LIKE ? OR title LIKE ? OR title LIKE ?)
            ORDER BY id
            LIMIT 1
            """,
            (series, pattern, spaced_pattern, zero_pattern, zero_spaced_pattern),
        ).fetchone()

    month = infer_month(page_title)
    if series and month:
        return conn.execute(
            """
            SELECT id, title FROM mixtapes
            WHERE series = ? AND month = ?
            ORDER BY id
            LIMIT 1
            """,
            (series, month),
        ).fetchone()
    return None


def index_mixtape(
    conn: sqlite3.Connection,
    soundcloud_url: str,
    *,
    month: str | None = None,
    release_date: str | None = None,
    series: str | None = None,
    title_override: str | None = None,
    uploader_override: str | None = None,
    offline: bool = False,
    description: str | None = None,
    tracklist_url_override: str | None = None,
    tracklist_file: Path | None = None,
) -> tuple[int, str, str | None, int]:
    metadata = {} if offline else fetch_soundcloud_oembed(soundcloud_url)
    title = plain_text(metadata.get("title")) or soundcloud_url
    description = description if description is not None else plain_text(metadata.get("description"))
    if title_override:
        title = title_override
    tracklist_url = tracklist_url_override or extract_tracklist_url(description)
    month = month or infer_month(title) or dt.date.today().strftime("%Y-%m")
    uploader = uploader_override or infer_uploader(title)

    cur = conn.execute(
        """
        INSERT INTO mixtapes (
            soundcloud_url, title, uploader, month, release_date, series, description,
            tracklist_url, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(soundcloud_url) DO UPDATE SET
            title = excluded.title,
            uploader = COALESCE(excluded.uploader, mixtapes.uploader),
            month = COALESCE(excluded.month, mixtapes.month),
            release_date = COALESCE(excluded.release_date, mixtapes.release_date),
            series = COALESCE(excluded.series, mixtapes.series),
            description = excluded.description,
            tracklist_url = COALESCE(excluded.tracklist_url, mixtapes.tracklist_url)
        RETURNING id
        """,
        (
            soundcloud_url,
            title,
            uploader,
            month,
            release_date,
            series,
            description,
            tracklist_url,
            dt.datetime.now(dt.timezone.utc).isoformat(),
        ),
    )
    mixtape_id = cur.fetchone()["id"]

    imported = 0
    if tracklist_file:
        imported = import_tracks(conn, mixtape_id, tracklist_file)
    elif not conn.execute("SELECT 1 FROM tracks WHERE mixtape_id = ? LIMIT 1", (mixtape_id,)).fetchone():
        imported = insert_tracks(conn, mixtape_id, parse_description_tracklist(description))
    return mixtape_id, title, tracklist_url, imported


def add_mixtape(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    description = None
    if args.description_file:
        description = args.description_file.read_text(encoding="utf-8").strip()
    mixtape_id, title, tracklist_url, imported = index_mixtape(
        conn,
        args.soundcloud_url,
        month=args.month,
        release_date=args.release_date,
        series=args.series,
        title_override=args.title,
        uploader_override=args.uploader,
        offline=args.offline,
        description=description,
        tracklist_url_override=args.tracklist_url,
        tracklist_file=args.tracklist_file,
    )

    conn.commit()
    print(f"Indexed mixtape #{mixtape_id}: {title}")
    if tracklist_url:
        print(f"Tracklist source: {tracklist_url}")
    if imported:
        print(f"Imported {imported} tracks")


def batch_add_soundcloud_page(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    stop_urls = None
    if args.stop_at_existing:
        stop_urls = {
            row["soundcloud_url"]
            for row in conn.execute("SELECT soundcloud_url FROM mixtapes WHERE soundcloud_url IS NOT NULL")
        }
    html_text = fetch_text(args.page_url)
    tracks: list[dict[str, str]] = []
    api_checked = False
    if args.source in {"auto", "api"}:
        api_checked = True
        tracks = fetch_soundcloud_api_tracks(
            args.page_url,
            html_text,
            client_id=args.client_id,
            max_tracks=args.limit,
            stop_urls=stop_urls,
        )
    if args.source == "api" and not tracks:
        raise SystemExit("Could not read tracks from SoundCloud API.")
    if not tracks and not (args.stop_at_existing and api_checked):
        tracks = extract_soundcloud_tracks(args.page_url, html_text)
    if args.match:
        pattern = re.compile(args.match, re.IGNORECASE)
        tracks = [track for track in tracks if pattern.search(track["title"])]
    if args.monthly_only:
        tracks = [track for track in tracks if title_has_month(track["title"])]
    if args.limit:
        tracks = tracks[: args.limit]
    if not tracks:
        print("No SoundCloud tracks found.")
        return

    for track in tracks:
        month = args.month or infer_month(track["title"], track.get("published_month"))
        mixtape_id, title, tracklist_url, imported = index_mixtape(
            conn,
            track["url"],
            month=month,
            release_date=track.get("release_date"),
            series=args.series,
            title_override=track["title"],
            uploader_override=args.uploader or track.get("uploader"),
            offline=args.offline or bool(track.get("description")),
            description=track.get("description"),
        )
        suffix = f" | tracklist: {tracklist_url}" if tracklist_url else ""
        if imported:
            suffix += f" | imported {imported} tracks"
        print(f"Indexed mixtape #{mixtape_id}: {title}{suffix}")
    conn.commit()


def import_tracks(conn: sqlite3.Connection, mixtape_id: int, path: Path) -> int:
    return insert_tracks(conn, mixtape_id, parse_tracklist(path.read_text(encoding="utf-8")))


def insert_tracks(conn: sqlite3.Connection, mixtape_id: int, tracks: list[dict[str, object]]) -> int:
    before = conn.total_changes
    for track in tracks:
        conn.execute(
            """
            INSERT OR IGNORE INTO tracks (
                mixtape_id, position, cue_seconds, artist, title, raw_text
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mixtape_id,
                track["position"],
                track["cue_seconds"],
                track["artist"],
                track["title"],
                track["raw_text"],
            ),
        )
    return conn.total_changes - before


def import_mixesdb_category(args: argparse.Namespace) -> None:
    category_url = args.category_url
    pages = extract_mixesdb_category_pages(category_url, fetch_text(category_url), args.title_match)
    if args.match:
        pattern = re.compile(args.match, re.IGNORECASE)
        pages = [page for page in pages if pattern.search(page["text"])]
    if args.limit:
        pages = pages[: args.limit]
    if not pages:
        print("No MixesDB pages found.")
        return

    conn = connect(args.db)
    if args.new_only:
        known_urls = {
            row["tracklist_url"]
            for row in conn.execute("SELECT tracklist_url FROM mixtapes WHERE tracklist_url IS NOT NULL")
        }
        pages = [page for page in pages if page["url"] not in known_urls]
        if not pages:
            print("No new MixesDB pages found.")
            return
    imported_pages = 0
    imported_tracks = 0
    missing = 0
    for index, page in enumerate(pages, start=1):
        raw = fetch_text(raw_mixesdb_url(page["url"]))
        soundcloud_url, tracklist_text = parse_mixesdb_raw_tracklist(raw)
        tracks = parse_tracklist(tracklist_text)
        if not soundcloud_url:
            print(f"{index:>3}. skipped, no SoundCloud URL: {page['text']}")
            missing += 1
            continue
        mixtape = find_mixtape_for_source(
            conn,
            soundcloud_url=soundcloud_url,
            page_title=page["text"],
            series=args.series,
        )
        if not mixtape:
            if args.add_missing:
                mixtape_id, _, _, _ = index_mixtape(
                    conn,
                    soundcloud_url,
                    month=infer_mixesdb_page_month(page["text"]),
                    release_date=mixesdb_page_date(page["text"]),
                    series=args.series,
                    title_override=page["text"],
                    uploader_override=args.uploader,
                    offline=True,
                    tracklist_url_override=page["url"],
                )
            else:
                print(f"{index:>3}. no matching mixtape: {page['text']}")
                missing += 1
                continue
        else:
            mixtape_id = int(mixtape["id"])
            conn.execute(
                "UPDATE mixtapes SET tracklist_url = ? WHERE id = ?",
                (page["url"], mixtape_id),
            )
        inserted = insert_tracks(conn, mixtape_id, tracks)
        imported_pages += 1
        imported_tracks += inserted
        print(f"{index:>3}. mixtape #{mixtape_id}: imported {inserted}/{len(tracks)} tracks from {page['text']}")
        if args.delay and index < len(pages):
            import time

            time.sleep(args.delay)
    conn.commit()
    print(f"Imported {imported_tracks} new tracks from {imported_pages} MixesDB pages; {missing} missing/skipped.")


def import_tracklist(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    if args.replace:
        conn.execute("DELETE FROM tracks WHERE mixtape_id = ?", (args.mixtape_id,))
    imported = import_tracks(conn, args.mixtape_id, args.tracklist_file)
    if args.tracklist_url:
        conn.execute(
            "UPDATE mixtapes SET tracklist_url = ? WHERE id = ?",
            (args.tracklist_url, args.mixtape_id),
        )
    conn.commit()
    print(f"Imported {imported} tracks into mixtape #{args.mixtape_id}")
    if args.tracklist_url:
        print(f"Tracklist source: {args.tracklist_url}")


def import_1001_tracklist(args: argparse.Namespace) -> None:
    try:
        html_text = fetch_1001_tracklist_html(args.tracklist_url, delay=args.delay)
    except TracklistsChallengeError as exc:
        raise SystemExit(
            f"{exc}\n"
            "Open the page in your browser, copy the visible tracklist text, then use "
            "`import-tracklist --tracklist-url` for this mixtape."
        ) from exc
    tracks = parse_1001tracklists_html(html_text)
    if not tracks:
        raise SystemExit("No track metadata was found in the 1001Tracklists HTML.")

    conn = connect(args.db)
    if args.replace:
        conn.execute("DELETE FROM tracks WHERE mixtape_id = ?", (args.mixtape_id,))
    inserted = insert_tracks(conn, args.mixtape_id, tracks)
    conn.execute(
        "UPDATE mixtapes SET tracklist_url = ? WHERE id = ?",
        (args.tracklist_url, args.mixtape_id),
    )
    conn.commit()
    print(f"Imported {inserted}/{len(tracks)} tracks into mixtape #{args.mixtape_id}")
    print(f"Tracklist source: {args.tracklist_url}")


def import_1001_assisted(args: argparse.Namespace) -> None:
    ensure_1001_allowed(args.tracklist_url)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required for assisted browser imports. Run `python3 -m uv sync`, "
            "then `python3 -m uv run playwright install chromium`."
        ) from exc

    profile_dir = args.profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    print("Opening a visible Chromium window.")
    if args.auto_read:
        print("Will read automatically once tracklist elements are visible.")
    else:
        print("Load the tracklist, complete any normal browser checks, then return here and press Enter.")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.new_page()
            try:
                page.goto(args.tracklist_url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
            except PlaywrightTimeoutError:
                print("Initial page load timed out, but the browser is still open for manual review.")
            if args.auto_read:
                try:
                    page.wait_for_selector(ONE_THOUSAND_ONE_TRACK_SELECTOR, timeout=args.timeout * 1000)
                except PlaywrightTimeoutError:
                    if not sys.stdin.isatty():
                        raise SystemExit(
                            "Tracklist elements were not detected automatically. "
                            "Rerun without --auto-read for manual confirmation or try again later."
                        )
                    print("Tracklist elements were not detected automatically.")
                    input("Press Enter once the 1001Tracklists page shows the tracklist...")
            else:
                input("Press Enter once the 1001Tracklists page shows the tracklist...")
            html_text = page.content()
            browser_tracks = page.evaluate(
                """
                () => {
                  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                  const rows = Array.from(document.querySelectorAll('tr.tlpItem, [itemtype*="MusicRecording"]'));
                  const seen = new Set();
                  return rows.map((node, index) => {
                    const scope = node.closest('tr') || node;
                    const artist =
                      scope.querySelector('meta[itemprop="byArtist"]')?.content ||
                      scope.querySelector('[itemprop="byArtist"]')?.textContent ||
                      '';
                    const title =
                      scope.querySelector('meta[itemprop="name"]')?.content ||
                      scope.querySelector('[itemprop="name"]')?.textContent ||
                      scope.querySelector('.trackValue')?.textContent ||
                      '';
                    const cue =
                      scope.querySelector('.cueValue')?.textContent ||
                      scope.querySelector('[class*="cue"]')?.textContent ||
                      '';
                    const key = `${clean(artist)}\\u0000${clean(title)}\\u0000${clean(cue)}`;
                    if (!clean(title) || seen.has(key)) return null;
                    seen.add(key);
                    return {
                      position: index + 1,
                      artist: clean(artist) || null,
                      title: clean(title),
                      cue: clean(cue)
                    };
                  }).filter(Boolean);
                }
                """
            )
        finally:
            if not args.keep_open:
                context.close()

    if args.debug_html:
        args.debug_html.write_text(html_text, encoding="utf-8")
        print(f"Saved rendered HTML to {args.debug_html}")

    tracks = parse_1001tracklists_html(html_text)
    if not tracks:
        tracks = tracks_from_browser_1001(browser_tracks)
    if not tracks:
        if is_1001_challenge(html_text):
            raise SystemExit("The rendered page still looks like a challenge page; no tracks were imported.")
        raise SystemExit("No track metadata was found in the rendered 1001Tracklists page.")

    conn = connect(args.db)
    if args.replace:
        conn.execute("DELETE FROM tracks WHERE mixtape_id = ?", (args.mixtape_id,))
    inserted = insert_tracks(conn, args.mixtape_id, tracks)
    conn.execute(
        "UPDATE mixtapes SET tracklist_url = ? WHERE id = ?",
        (args.tracklist_url, args.mixtape_id),
    )
    conn.commit()
    print(f"Imported {inserted}/{len(tracks)} tracks into mixtape #{args.mixtape_id}")
    print(f"Tracklist source: {args.tracklist_url}")


def enrich_beatport_assisted(args: argparse.Namespace) -> None:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required for Beatport assisted enrichment. Run `python3 -m uv sync`, "
            "then `python3 -m uv run playwright install chromium`."
        ) from exc

    conn = connect(args.db)
    tracks = beatport_enrichment_candidates(conn, args)
    if not tracks:
        print("No tracks matched the enrichment filters.")
        return

    args.profile_dir.mkdir(parents=True, exist_ok=True)
    print("Opening a visible Chromium window for Beatport lookups.")
    print("For each track, open the exact Beatport track page, then return here and press Enter.")

    saved = 0
    skipped = 0
    with sync_playwright() as playwright:
        browser = None
        if args.cdp_url:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context(viewport={"width": 1440, "height": 1000})
        else:
            context = playwright.chromium.launch_persistent_context(
                str(args.profile_dir),
                headless=False,
                viewport={"width": 1440, "height": 1000},
            )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            if args.manual_start:
                print("Navigate this browser to Beatport manually, finish any checks, then return here.")
                input("Press Enter once Beatport is loaded and ready...")
            elif args.search_mode == "ui":
                try:
                    page.goto("https://www.beatport.com/", wait_until="domcontentloaded", timeout=args.timeout * 1000)
                except PlaywrightTimeoutError:
                    print("Beatport home page load timed out, but the browser is still open.")
            for index, track in enumerate(tracks, start=1):
                label = track_label(track)
                search_query = beatport_search_query(track["artist"], track["title"])
                print("")
                print(f"[{index}/{len(tracks)}] Track #{track['id']}: {label}")
                print(f"Search query: {search_query}")
                if args.search_mode == "url":
                    search_url = beatport_search_url(track["artist"], track["title"])
                    print(f"Search URL: {search_url}")
                    try:
                        page.goto(search_url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                    except PlaywrightTimeoutError:
                        print("Initial search page load timed out, but the browser is still open.")
                else:
                    searched = search_beatport_with_ui(page, search_query, args.timeout)
                    if not searched:
                        if args.assisted_search_focus:
                            print("Could not find Beatport search UI automatically.")
                            input("Click/focus the Beatport search box in the browser, then press Enter here...")
                            submit_search_from_focused_field(page, search_query, args.timeout)
                        else:
                            print("Could not drive Beatport search UI automatically. Please search manually in the browser.")

                if args.auto_first_result:
                    if open_first_beatport_track_result(page, track["title"], args.timeout):
                        print("Opened first Beatport track result.")
                    else:
                        print("Could not open the first track result automatically. Please choose it manually.")

                if args.choose_result:
                    metadata = choose_beatport_search_result(page, track, args.auto_choose_result, args.timeout)
                    if metadata:
                        print_beatport_metadata(metadata)
                        confirm = input("Save this Beatport metadata? [Y/n] ").strip().lower()
                        if confirm in {"", "y", "yes"}:
                            upsert_track_metadata(conn, track["id"], metadata)
                            conn.commit()
                            saved += 1
                            print(f"Saved metadata for track #{track['id']}.")
                        else:
                            skipped += 1
                        continue

                response = input("Press Enter on the exact track page, `s` to skip, or `q` to quit: ").strip().lower()
                if response == "q":
                    break
                if response == "s":
                    skipped += 1
                    continue

                page_data = page.evaluate(
                    """
                    () => ({
                      url: window.location.href,
                      title: document.title || '',
                      bodyText: document.body ? document.body.innerText : '',
                      jsonLd: Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                        .map((node) => node.textContent || '')
                    })
                    """
                )
                metadata = extract_beatport_metadata(page_data)
                print_beatport_metadata(metadata)
                if not is_beatport_track_url(str(metadata.get("source_url") or "")):
                    print("Current page does not look like a Beatport track page; skipping.")
                    skipped += 1
                    continue

                confirm = input("Save this Beatport metadata? [Y/n] ").strip().lower()
                if confirm in {"", "y", "yes"}:
                    upsert_track_metadata(conn, track["id"], metadata)
                    conn.commit()
                    saved += 1
                    print(f"Saved metadata for track #{track['id']}.")
                else:
                    skipped += 1
        finally:
            if args.cdp_url:
                if browser is not None:
                    browser.close()
            elif not args.keep_open:
                context.close()

    print(f"Beatport enrichment complete: saved {saved}, skipped {skipped}.")


def enrich_beatport_manual(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    tracks = beatport_enrichment_candidates(conn, args)
    if not tracks:
        print("No tracks matched the enrichment filters.")
        return

    if args.open_browser:
        open_in_default_browser("https://www.beatport.com/")

    print("Use your normal browser to search Beatport and choose the exact track page.")
    print("Each search query will be copied to your clipboard unless --no-copy is set.")

    saved = 0
    skipped = 0
    for index, track in enumerate(tracks, start=1):
        query = beatport_search_query(track["artist"], track["title"])
        print("")
        print(f"[{index}/{len(tracks)}] Track #{track['id']}: {track_label(track)}")
        print(f"Search query: {query}")
        if args.copy:
            if copy_to_clipboard(query):
                print("Copied search query to clipboard.")
            else:
                print("Could not copy to clipboard; copy the query above manually.")

        response = input("Paste exact Beatport track URL, `s` to skip, or `q` to quit: ").strip()
        lowered = response.lower()
        if lowered == "q":
            break
        if lowered in {"", "s"}:
            skipped += 1
            continue
        if not is_beatport_track_url(response):
            print("That does not look like a Beatport track URL; skipping.")
            skipped += 1
            continue

        metadata = manual_beatport_metadata(response)
        if args.prompt_metadata:
            prompt_manual_metadata(metadata)
        print_beatport_metadata(metadata)
        confirm = input("Save this Beatport metadata? [Y/n] ").strip().lower()
        if confirm in {"", "y", "yes"}:
            upsert_track_metadata(conn, track["id"], metadata)
            conn.commit()
            saved += 1
            print(f"Saved metadata for track #{track['id']}.")
        else:
            skipped += 1

    print(f"Beatport manual enrichment complete: saved {saved}, skipped {skipped}.")


def beatport_enrichment_candidates(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    clauses = []
    params: list[object] = []
    if args.track_id:
        clauses.append("t.id = ?")
        params.append(args.track_id)
    if args.mixtape_id:
        clauses.append("t.mixtape_id = ?")
        params.append(args.mixtape_id)
    if args.year:
        clauses.append("COALESCE(m.release_date, m.month, '') LIKE ?")
        params.append(f"{args.year}%")
    if args.month:
        clauses.append("m.month = ?")
        params.append(args.month)
    if args.series:
        clauses.append("LOWER(COALESCE(m.series, '')) = LOWER(?)")
        params.append(args.series)
    if args.query:
        clauses.append("LOWER(COALESCE(t.artist, '') || ' ' || t.title) LIKE LOWER(?)")
        params.append(f"%{args.query}%")
    if not args.include_enriched:
        clauses.append("bm.id IS NULL")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = "" if args.track_id else "LIMIT ?"
    if not args.track_id:
        params.append(args.limit)
    return conn.execute(
        f"""
        SELECT t.id, t.mixtape_id, t.position, t.artist, t.title, m.month, m.release_date,
               m.series, m.title AS mixtape_title, bm.source_url AS beatport_track_url
        FROM tracks t
        JOIN mixtapes m ON m.id = t.mixtape_id
        LEFT JOIN track_metadata bm ON bm.track_id = t.id AND bm.source = 'beatport'
        {where}
        ORDER BY COALESCE(m.release_date, m.month, '') DESC, m.id DESC, t.position, t.id
        {limit}
        """,
        params,
    ).fetchall()


def track_label(track: sqlite3.Row) -> str:
    artist = f"{track['artist']} - " if track["artist"] else ""
    release = track["release_date"] or track["month"] or "---- --"
    return f"{artist}{track['title']} ({track['series'] or 'Uncategorized'}, {release})"


def copy_to_clipboard(value: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=value, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def open_in_default_browser(url: str) -> None:
    try:
        subprocess.run(["open", url], check=False)
    except OSError:
        pass


def manual_beatport_metadata(url: str) -> dict[str, object]:
    raw_snapshot = {"url": url, "entry": "manual"}
    return {
        "source_url": url,
        "source_track_id": beatport_track_id_from_url(url),
        "bpm": None,
        "musical_key": None,
        "genre": None,
        "label": None,
        "release_title": None,
        "release_date": None,
        "confidence": "manual",
        "raw_json": json.dumps(raw_snapshot, ensure_ascii=False),
    }


def prompt_manual_metadata(metadata: dict[str, object]) -> None:
    print("Optional Beatport fields. Press Enter to leave blank.")
    metadata["bpm"] = normalize_bpm(input("BPM: ").strip() or None, "")
    metadata["musical_key"] = input("Key: ").strip() or None
    metadata["genre"] = input("Genre: ").strip() or None
    metadata["label"] = input("Label: ").strip() or None
    metadata["release_title"] = input("Release title: ").strip() or None
    metadata["release_date"] = normalize_release_date(input("Release date: ").strip() or None)


def search_beatport_with_ui(page: object, query: str, timeout: int) -> bool:
    timeout_ms = timeout * 1000
    if "beatport.com" not in page.url:
        page.goto("https://www.beatport.com/", wait_until="domcontentloaded", timeout=timeout_ms)

    search_buttons = (
        "button[aria-label='Search']",
        "button[aria-label*='Search']",
        "a[aria-label='Search']",
        "a[aria-label*='Search']",
        "[data-testid='search']",
        "[data-testid*='search']",
        "button:has-text('Search')",
    )
    for selector in search_buttons:
        try:
            button = page.locator(selector).first
            if button.count():
                button.click(timeout=1500)
                break
        except Exception:
            pass

    search_inputs = (
        "input[type='search']",
        "input[placeholder='Search']",
        "input[placeholder*='Search']",
        "input[name='q']",
        "input[name='search']",
        "[data-testid='search-input']",
        "[data-testid*='search'] input",
        "form input",
    )
    for selector in search_inputs:
        try:
            field = page.locator(selector).first
            if not field.count():
                continue
            field.click(timeout=2000)
            clear_beatport_search_field(page)
            field.fill(query, timeout=2000)
            field.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def submit_search_from_focused_field(page: object, query: str, timeout: int) -> None:
    clear_beatport_search_field(page)
    page.keyboard.type(query, delay=25)
    page.keyboard.press("Enter")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
    except Exception:
        pass


def choose_beatport_search_result(
    page: object,
    track: sqlite3.Row,
    auto_choose: bool = True,
    timeout: int = 30,
) -> dict[str, object] | None:
    candidates = wait_for_beatport_result_candidates(page, track["artist"], track["title"], timeout)
    return choose_from_beatport_candidates(page, track, candidates, auto_choose, timeout)


def choose_from_beatport_candidates(
    page: object,
    track: sqlite3.Row,
    candidates: list[dict[str, object]],
    auto_choose: bool,
    timeout: int,
) -> dict[str, object] | None:
    if not candidates:
        print("No Beatport track candidates were readable from the search results.")
        return None

    print("Beatport result candidates:")
    for index, candidate in enumerate(candidates[:8], start=1):
        metadata = candidate["metadata"]
        pieces = [
            str(candidate.get("label") or metadata.get("source_url")),
            f"BPM {metadata['bpm']}" if metadata.get("bpm") else None,
            f"Key {metadata['musical_key']}" if metadata.get("musical_key") else None,
            str(metadata.get("label")) if metadata.get("label") else None,
            str(metadata.get("release_date")) if metadata.get("release_date") else None,
        ]
        print(f"  {index}. {' | '.join(piece for piece in pieces if piece)}")

    if auto_choose:
        choice = automatic_beatport_choice(candidates)
        if choice is not None:
            print(f"Auto-selected result {choice + 1}.")
            return candidates[choice]["metadata"]
        print("No clear closest result; choose manually.")

    response = input("Choose result number, `r` to reread, Enter to use page manually, `s` to skip, or `q` to quit: ").strip().lower()
    if response == "q":
        raise SystemExit("Stopped.")
    if response == "r":
        candidates = wait_for_beatport_result_candidates(page, track["artist"], track["title"], timeout)
        return choose_from_beatport_candidates(page, track, candidates, auto_choose, timeout)
    if response in {"", "s"}:
        return None
    if not response.isdigit():
        print("Result choice was not a number; continuing manually.")
        return None
    choice = int(response)
    if choice < 1 or choice > min(len(candidates), 8):
        print("Result choice was out of range; continuing manually.")
        return None
    return candidates[choice - 1]["metadata"]


def wait_for_beatport_result_candidates(
    page: object,
    expected_artist: str | None,
    expected_title: str,
    timeout: int,
) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout
    last_candidates: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        candidates = beatport_search_result_candidates(page, expected_artist, expected_title)
        if candidates:
            last_candidates = candidates
            if int(candidates[0]["score"]) > 0:
                return candidates
        time.sleep(0.5)
    return last_candidates


def automatic_beatport_choice(candidates: list[dict[str, object]]) -> int | None:
    if not candidates:
        return None
    best_score = int(candidates[0]["score"])
    best_title_score = int(candidates[0].get("title_score", 0))
    required_title_score = int(candidates[0].get("required_title_score", 1))
    if best_score <= 0 or best_title_score < required_title_score:
        return None
    if len(candidates) > 1 and int(candidates[1]["score"]) == best_score:
        return None
    return 0


def beatport_search_result_candidates(page: object, expected_artist: str | None, expected_title: str) -> list[dict[str, object]]:
    raw_candidates = page.evaluate(
        """
        () => {
          const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const anchors = Array.from(document.querySelectorAll('a[href*="/track/"]'));
          const seen = new Set();
          return anchors.map((anchor) => {
            const href = anchor.href || anchor.getAttribute('href') || '';
            if (!href || seen.has(href)) return null;
            seen.add(href);
            let row = anchor;
            let node = anchor.parentElement;
            for (let depth = 0; depth < 10 && node; depth += 1) {
              const text = clean(node.innerText || '');
              if ((/\\b\\d{2,3}\\s*BPM\\b/i.test(text) || /\\b\\d{4}-\\d{2}-\\d{2}\\b/.test(text)) && text.length < 500) {
                row = node;
                break;
              }
              node = node.parentElement;
            }
            const rowLinks = Array.from(row.querySelectorAll('a[href]')).map((link) => ({
              href: new URL(link.href || link.getAttribute('href'), window.location.origin).href,
              text: clean(link.innerText || '')
            }));
            return {
              url: new URL(href, window.location.origin).href,
              anchorText: clean(anchor.innerText || ''),
              text: clean(row.innerText || anchor.innerText || ''),
              links: rowLinks
            };
          }).filter(Boolean);
        }
        """
    )
    title_tokens = title_match_tokens(expected_title)
    artist_tokens = title_match_tokens(expected_artist or "")
    candidates = []
    seen_urls = set()
    for raw in raw_candidates:
        url = str(raw.get("url") or "")
        if url in seen_urls or not is_beatport_track_url(url):
            continue
        seen_urls.add(url)
        text = str(raw.get("text") or raw.get("anchorText") or "")
        metadata = extract_beatport_result_metadata(url, str(raw.get("anchorText") or ""), text, raw.get("links") or [])
        title_score = beatport_token_score(f"{url} {text}", title_tokens)
        artist_score = beatport_token_score(f"{url} {text}", artist_tokens)
        score = title_score * 3 + artist_score * 5
        candidates.append(
            {
                "url": url,
                "label": summarize_candidate_text(text, url),
                "score": score,
                "title_score": title_score,
                "artist_score": artist_score,
                "required_title_score": required_title_score(title_tokens),
                "metadata": metadata,
            }
        )
    candidates.sort(key=lambda candidate: (-int(candidate["score"]), str(candidate["label"])))
    return candidates


def extract_beatport_result_metadata(
    url: str,
    title: str,
    row_text: str,
    links: list[dict[str, object]],
) -> dict[str, object]:
    bpm = None
    musical_key = None
    bpm_key_match = re.search(r"\b(\d{2,3})\s*BPM\s*-\s*([A-G](?:b|#)?\s+(?:Major|Minor))\b", row_text, re.IGNORECASE)
    if bpm_key_match:
        bpm = bpm_key_match.group(1)
        musical_key = bpm_key_match.group(2)
    release_date = normalize_release_date(row_text)
    label = first_link_text(links, "/label/")
    genre = first_link_text(links, "/genre/")
    release_title = first_link_text(links, "/release/")
    return {
        "source_url": url,
        "source_track_id": beatport_track_id_from_url(url),
        "bpm": bpm,
        "musical_key": musical_key,
        "genre": genre,
        "label": label,
        "release_title": release_title,
        "release_date": release_date,
        "confidence": "manual-search-result",
        "raw_json": json.dumps({"url": url, "title": title, "rowText": row_text}, ensure_ascii=False),
    }


def first_link_text(links: list[dict[str, object]], path_part: str) -> str | None:
    for link in links:
        href = str(link.get("href") or "")
        text = str(link.get("text") or "").strip()
        if path_part in href and text:
            return text
    return None


def beatport_candidate_score(url: str, text: str, title_tokens: list[str], artist_tokens: list[str]) -> int:
    haystack = f"{url} {text}"
    return beatport_token_score(haystack, title_tokens) * 3 + beatport_token_score(haystack, artist_tokens) * 5


def beatport_token_score(text: str, tokens: list[str]) -> int:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return sum(1 for token in tokens if token in words)


def required_title_score(tokens: list[str]) -> int:
    if not tokens:
        return 1
    return max(1, (len(tokens) + 1) // 2)


def result_text_as_lines(text: str) -> str:
    labels = ("BPM", "Key", "Genre", "Label", "Release Date")
    normalized = text
    for label in labels:
        normalized = re.sub(rf"\b{re.escape(label)}\b", f"\n{label}\n", normalized, flags=re.IGNORECASE)
    return normalized


def summarize_candidate_text(text: str, url: str) -> str:
    if text:
        return text[:180]
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    return " ".join(parts[:2]) if parts else url


def clear_beatport_search_field(page: object) -> None:
    page.evaluate(
        """
        () => {
          const active = document.activeElement;
          if (!active || !('value' in active)) return;
          active.value = '';
          active.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward' }));
          active.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """
    )


def open_first_beatport_track_result(page: object, expected_title: str, timeout: int) -> bool:
    timeout_ms = timeout * 1000
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    expected_tokens = title_match_tokens(expected_title)
    selectors = (
        "a[href^='/track/']",
        "a[href*='/track/']",
    )
    for selector in selectors:
        try:
            links = page.locator(selector)
            count = links.count()
            for index in range(count):
                link = links.nth(index)
                href = link.get_attribute("href", timeout=1000) or ""
                target_url = urllib.parse.urljoin("https://www.beatport.com", href)
                if not beatport_track_id_from_url(target_url):
                    continue
                if expected_tokens and not url_matches_title_tokens(target_url, expected_tokens):
                    continue
                page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                return is_beatport_track_url(page.url)
        except Exception:
            continue
    return False


def title_match_tokens(title: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", title.lower())
    ignored = {
        "a",
        "an",
        "and",
        "are",
        "at",
        "feat",
        "ft",
        "for",
        "get",
        "in",
        "is",
        "it",
        "me",
        "mix",
        "my",
        "of",
        "on",
        "original",
        "remix",
        "the",
        "to",
        "with",
        "your",
        "extended",
        "edit",
    }
    return [token for token in tokens if token not in ignored]


def url_matches_title_tokens(url: str, expected_tokens: list[str]) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return all(token in path for token in expected_tokens)


def upsert_track_metadata(conn: sqlite3.Connection, track_id: int, metadata: dict[str, object]) -> None:
    conn.execute(
        """
        INSERT INTO track_metadata (
            track_id, source, source_url, source_track_id, bpm, musical_key, genre, label,
            release_title, release_date, confidence, raw_json, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id, source) DO UPDATE SET
            source_url = excluded.source_url,
            source_track_id = excluded.source_track_id,
            bpm = excluded.bpm,
            musical_key = excluded.musical_key,
            genre = excluded.genre,
            label = excluded.label,
            release_title = excluded.release_title,
            release_date = excluded.release_date,
            confidence = excluded.confidence,
            raw_json = excluded.raw_json,
            fetched_at = excluded.fetched_at
        """,
        (
            track_id,
            "beatport",
            metadata.get("source_url"),
            metadata.get("source_track_id"),
            metadata.get("bpm"),
            metadata.get("musical_key"),
            metadata.get("genre"),
            metadata.get("label"),
            metadata.get("release_title"),
            metadata.get("release_date"),
            metadata.get("confidence"),
            metadata.get("raw_json"),
            dt.datetime.now(dt.timezone.utc).isoformat(),
        ),
    )


def extract_beatport_metadata(page_data: dict[str, object]) -> dict[str, object]:
    body_text = str(page_data.get("bodyText") or "")
    lines = clean_lines(body_text.splitlines())
    raw_snapshot = {
        "url": page_data.get("url"),
        "title": page_data.get("title"),
        "jsonLd": page_data.get("jsonLd") or [],
    }
    metadata: dict[str, object] = {
        "source_url": str(page_data.get("url") or ""),
        "source_track_id": beatport_track_id_from_url(str(page_data.get("url") or "")),
        "bpm": extract_labeled_value(lines, ("bpm",)),
        "musical_key": extract_labeled_value(lines, ("key",)),
        "genre": extract_labeled_value(lines, ("genre", "genres")),
        "label": extract_labeled_value(lines, ("label",)),
        "release_title": extract_labeled_value(lines, ("release", "release title")),
        "release_date": extract_labeled_value(lines, ("release date", "released")),
        "confidence": "manual",
        "raw_json": json.dumps(raw_snapshot, ensure_ascii=False),
    }

    metadata["bpm"] = normalize_bpm(metadata.get("bpm"), body_text)
    metadata["release_date"] = normalize_release_date(metadata.get("release_date"))
    return metadata


def clean_lines(lines: list[str]) -> list[str]:
    return [" ".join(line.split()) for line in lines if " ".join(line.split())]


def extract_labeled_value(lines: list[str], labels: tuple[str, ...]) -> str | None:
    normalized_labels = {label.lower() for label in labels}
    for index, line in enumerate(lines):
        normalized = line.rstrip(":").lower()
        if normalized in normalized_labels and index + 1 < len(lines):
            return lines[index + 1]
        for label in normalized_labels:
            prefix = f"{label}:"
            if normalized.startswith(prefix):
                return line[len(prefix) :].strip()
    return None


def normalize_bpm(value: object, body_text: str) -> str | None:
    if value:
        match = re.search(r"\b(\d{2,3})\b", str(value))
        if match:
            return match.group(1)
    match = re.search(r"\bBPM\s*:?\s*(\d{2,3})\b|\b(\d{2,3})\s*BPM\b", body_text, re.IGNORECASE)
    if not match:
        return None
    return next(group for group in match.groups() if group)


def normalize_release_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        return iso_match.group(1)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def beatport_track_id_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if "beatport.com" not in parsed.netloc.lower():
        return None
    for part in reversed([part for part in parsed.path.split("/") if part]):
        if part.isdigit():
            return part
    return None


def is_beatport_track_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    return "beatport.com" in parsed.netloc.lower() and "track" in parts and beatport_track_id_from_url(url) is not None


def print_beatport_metadata(metadata: dict[str, object]) -> None:
    print("Extracted:")
    for label, key in (
        ("URL", "source_url"),
        ("Beatport ID", "source_track_id"),
        ("BPM", "bpm"),
        ("Key", "musical_key"),
        ("Genre", "genre"),
        ("Label", "label"),
        ("Release", "release_title"),
        ("Release date", "release_date"),
    ):
        print(f"  {label}: {metadata.get(key) or '-'}")


def list_mixtapes(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    where: list[str] = []
    params: list[object] = []
    if args.year:
        where.append("m.month LIKE ?")
        params.append(f"{args.year}-%")
    if args.series:
        where.append("m.series = ?")
        params.append(args.series)
    if args.with_tracks:
        where.append("EXISTS (SELECT 1 FROM tracks tx WHERE tx.mixtape_id = m.id)")
    if args.without_tracks:
        where.append("NOT EXISTS (SELECT 1 FROM tracks tx WHERE tx.mixtape_id = m.id)")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order = "DESC" if args.desc else "ASC"
    limit_sql = "LIMIT ?" if args.limit else ""
    if args.limit:
        params.append(args.limit)
    rows = conn.execute(
        f"""
        SELECT m.id, m.month, m.series, m.title, COUNT(t.id) AS track_count
        FROM mixtapes m
        LEFT JOIN tracks t ON t.mixtape_id = m.id
        {where_sql}
        GROUP BY m.id
        ORDER BY COALESCE(m.month, '') {order}, m.id {order}
        {limit_sql}
        """,
        params,
    ).fetchall()
    for row in rows:
        series = f" [{row['series']}]" if row["series"] else ""
        print(f"{row['id']:>3} {row['month'] or '---- --'}{series} {row['track_count']:>3} tracks  {row['title']}")


def stats(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    summary = conn.execute(
        """
        SELECT
            COUNT(DISTINCT m.id) AS mixtapes,
            COUNT(DISTINCT CASE WHEN t.id IS NOT NULL THEN m.id END) AS mixtapes_with_tracks,
            COUNT(t.id) AS tracks,
            COUNT(DISTINCT CASE WHEN m.tracklist_url IS NOT NULL THEN m.id END) AS tracklist_sources,
            MIN(m.month) AS min_month,
            MAX(m.month) AS max_month
        FROM mixtapes m
        LEFT JOIN tracks t ON t.mixtape_id = m.id
        """
    ).fetchone()
    print(f"mixtapes:           {summary['mixtapes']}")
    print(f"mixtapes w/tracks:  {summary['mixtapes_with_tracks']}")
    print(f"tracks:             {summary['tracks']}")
    print(f"tracklist sources:  {summary['tracklist_sources']}")
    print(f"month range:         {summary['min_month'] or '---- --'} to {summary['max_month'] or '---- --'}")
    print()
    rows = conn.execute(
        """
        SELECT substr(m.month, 1, 4) AS year,
               COUNT(DISTINCT m.id) AS mixtapes,
               COUNT(DISTINCT CASE WHEN t.id IS NOT NULL THEN m.id END) AS mixtapes_with_tracks,
               COUNT(t.id) AS tracks
        FROM mixtapes m
        LEFT JOIN tracks t ON t.mixtape_id = m.id
        GROUP BY year
        ORDER BY year
        """
    ).fetchall()
    if rows:
        print("year   mixes  w/tracks  tracks")
        for row in rows:
            print(f"{row['year'] or '----':<6} {row['mixtapes']:>5} {row['mixtapes_with_tracks']:>9} {row['tracks']:>7}")


def list_tracks(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    where: list[str] = []
    params: list[object] = []
    if args.mixtape_id:
        where.append("m.id = ?")
        params.append(args.mixtape_id)
    if args.year:
        where.append("m.month LIKE ?")
        params.append(f"{args.year}-%")
    if args.month:
        where.append("m.month = ?")
        params.append(args.month)
    if args.query:
        where.append("(t.artist LIKE ? OR t.title LIKE ? OR t.raw_text LIKE ?)")
        needle = f"%{args.query}%"
        params.extend([needle, needle, needle])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit_sql = "LIMIT ?" if args.limit else ""
    if args.limit:
        params.append(args.limit)
    rows = conn.execute(
        f"""
        SELECT t.position, t.cue_seconds, t.artist, t.title,
               m.id AS mixtape_id, m.month, m.title AS mixtape_title
        FROM tracks t
        JOIN mixtapes m ON m.id = t.mixtape_id
        {where_sql}
        ORDER BY m.month, m.id, t.position, t.id
        {limit_sql}
        """,
        params,
    ).fetchall()
    for row in rows:
        cue = format_seconds(row["cue_seconds"])
        artist = f"{row['artist']} - " if row["artist"] else ""
        print(
            f"{row['month'] or '---- --'} mix #{row['mixtape_id']:>3} "
            f"{row['position']:>3}. {cue} {artist}{row['title']} "
            f"({row['mixtape_title']})"
        )


def show_mixtape(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    mixtape = conn.execute("SELECT * FROM mixtapes WHERE id = ?", (args.mixtape_id,)).fetchone()
    if not mixtape:
        raise SystemExit(f"No mixtape found with id {args.mixtape_id}")
    print(f"#{mixtape['id']} {mixtape['title']}")
    print(f"SoundCloud: {mixtape['soundcloud_url']}")
    if mixtape["tracklist_url"]:
        print(f"Tracklist:  {mixtape['tracklist_url']}")
    print()
    rows = conn.execute(
        """
        SELECT position, cue_seconds, artist, title
        FROM tracks
        WHERE mixtape_id = ?
        ORDER BY position, id
        """,
        (args.mixtape_id,),
    ).fetchall()
    for row in rows:
        cue = format_seconds(row["cue_seconds"])
        artist = f"{row['artist']} - " if row["artist"] else ""
        print(f"{row['position']:>3}. {cue} {artist}{row['title']}".rstrip())


def format_seconds(value: int | None) -> str:
    if value is None:
        return "        "
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"[{hours}:{minutes:02}:{seconds:02}]"
    return f"[{minutes:02}:{seconds:02}]"


def search(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    needle = f"%{args.query}%"
    found = False
    if args.type in {"all", "mixes"}:
        rows = conn.execute(
            """
            SELECT m.id, m.month, m.series, m.title, m.soundcloud_url, m.tracklist_url,
                   COUNT(t.id) AS track_count
            FROM mixtapes m
            LEFT JOIN tracks t ON t.mixtape_id = m.id
            WHERE m.title LIKE ? OR m.uploader LIKE ? OR m.series LIKE ? OR m.soundcloud_url LIKE ?
                  OR m.tracklist_url LIKE ?
            GROUP BY m.id
            ORDER BY m.month, m.id
            LIMIT ?
            """,
            (needle, needle, needle, needle, needle, args.limit),
        ).fetchall()
        if rows:
            found = True
            print("Mixes")
            for row in rows:
                series = f" [{row['series']}]" if row["series"] else ""
                print(f"  #{row['id']:>3} {row['month'] or '---- --'}{series} {row['track_count']:>3} tracks  {row['title']}")
    if args.type in {"all", "tracks"}:
        rows = conn.execute(
            """
            SELECT t.position, t.cue_seconds, t.artist, t.title,
                   m.id AS mixtape_id, m.month, m.title AS mixtape_title
            FROM tracks t
            JOIN mixtapes m ON m.id = t.mixtape_id
            WHERE t.artist LIKE ? OR t.title LIKE ? OR t.raw_text LIKE ?
            ORDER BY m.month, m.id, t.position
            LIMIT ?
            """,
            (needle, needle, needle, args.limit),
        ).fetchall()
        if rows:
            found = True
            if args.type == "all":
                print("Tracks")
            for row in rows:
                cue = format_seconds(row["cue_seconds"])
                artist = f"{row['artist']} - " if row["artist"] else ""
                print(
                    f"  {row['month'] or '---- --'} mix #{row['mixtape_id']:>3} "
                    f"{row['position']:>3}. {cue} {artist}{row['title']} "
                    f"({row['mixtape_title']})"
                )
    if not found:
        print("No matches.")


def export_index(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    backfill_release_dates(conn)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    mixtapes = conn.execute(
        """
        SELECT m.id, m.month, m.release_date, m.series, m.title, m.uploader, m.soundcloud_url,
               m.tracklist_url, COUNT(t.id) AS track_count
        FROM mixtapes m
        LEFT JOIN tracks t ON t.mixtape_id = m.id
        GROUP BY m.id
        ORDER BY COALESCE(m.series, ''), COALESCE(m.month, ''), m.id
        """
    ).fetchall()
    tracks = conn.execute(
        """
        SELECT t.id, t.mixtape_id, m.month, m.series, m.title AS mixtape_title,
               m.release_date, m.soundcloud_url, m.tracklist_url, t.position, t.cue_seconds,
               t.artist, t.title, t.raw_text
        FROM tracks t
        JOIN mixtapes m ON m.id = t.mixtape_id
        ORDER BY COALESCE(m.series, ''), COALESCE(m.month, ''), m.id, t.position, t.id
        """
    ).fetchall()
    track_metadata = conn.execute(
        """
        SELECT id, track_id, source, source_url, source_track_id, bpm, musical_key,
               genre, label, release_title, release_date, confidence, raw_json, fetched_at
        FROM track_metadata
        ORDER BY source, track_id
        """
    ).fetchall()

    write_csv(
        output / "mixtapes.csv",
        [
            "id",
            "month",
            "release_date",
            "series",
            "title",
            "uploader",
            "soundcloud_url",
            "tracklist_url",
            "track_count",
        ],
        mixtapes,
    )
    write_csv(
        output / "tracks.csv",
        [
            "id",
            "mixtape_id",
            "month",
            "release_date",
            "series",
            "mixtape_title",
            "soundcloud_url",
            "tracklist_url",
            "position",
            "cue_seconds",
            "cue",
            "artist",
            "title",
            "raw_text",
            "beatport_url",
        ],
        [track_export_row(row) for row in tracks],
    )
    write_json(output / "mixtapes.json", [dict(row) for row in mixtapes])
    write_json(output / "tracks.json", [track_export_row(row) for row in tracks])
    write_csv(
        output / "track_metadata.csv",
        [
            "id",
            "track_id",
            "source",
            "source_url",
            "source_track_id",
            "bpm",
            "musical_key",
            "genre",
            "label",
            "release_title",
            "release_date",
            "confidence",
            "raw_json",
            "fetched_at",
        ],
        track_metadata,
    )
    write_json(output / "track_metadata.json", [dict(row) for row in track_metadata])
    write_markdown_index(output / "index.md", conn)
    write_latest_mixtapes_report(output / "latest-mixtapes.md", conn)
    print(f"Exported {len(mixtapes)} mixtapes, {len(tracks)} tracks, and {len(track_metadata)} metadata rows to {output}")


def backfill_release_dates(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, tracklist_url FROM mixtapes WHERE release_date IS NULL AND tracklist_url IS NOT NULL"
    ).fetchall()
    for row in rows:
        release_date = date_from_url(row["tracklist_url"])
        if release_date:
            conn.execute("UPDATE mixtapes SET release_date = ? WHERE id = ?", (release_date, row["id"]))
    conn.commit()


def date_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"/(\d{4}-\d{2}-\d{2})[_-]", urllib.parse.unquote(url))
    return match.group(1) if match else None


def load_export(args: argparse.Namespace) -> None:
    source = args.input
    mixtapes_path = source / "mixtapes.csv"
    tracks_path = source / "tracks.csv"
    track_metadata_path = source / "track_metadata.csv"
    if not mixtapes_path.exists() or not tracks_path.exists():
        raise SystemExit(f"Expected {mixtapes_path} and {tracks_path}")

    conn = connect(args.db)
    conn.execute("DELETE FROM track_metadata")
    conn.execute("DELETE FROM tracks")
    conn.execute("DELETE FROM mixtapes")

    mixtapes = read_csv(mixtapes_path)
    tracks = read_csv(tracks_path)
    track_metadata = read_csv(track_metadata_path) if track_metadata_path.exists() else []
    loaded_at = dt.datetime.now(dt.timezone.utc).isoformat()
    for row in mixtapes:
        conn.execute(
            """
            INSERT INTO mixtapes (
                id, soundcloud_url, title, uploader, month, release_date, series,
                description, tracklist_url, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]),
                row["soundcloud_url"],
                row["title"],
                row["uploader"] or None,
                row["month"] or None,
                row.get("release_date") or None,
                row["series"] or None,
                "",
                row["tracklist_url"] or None,
                loaded_at,
            ),
        )
    for row in tracks:
        conn.execute(
            """
            INSERT INTO tracks (
                id, mixtape_id, position, cue_seconds, artist, title, raw_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]),
                int(row["mixtape_id"]),
                int(row["position"]) if row["position"] else None,
                int(row["cue_seconds"]) if row["cue_seconds"] else None,
                row["artist"] or None,
                row["title"],
                row["raw_text"],
            ),
        )
    for row in track_metadata:
        conn.execute(
            """
            INSERT INTO track_metadata (
                id, track_id, source, source_url, source_track_id, bpm, musical_key, genre,
                label, release_title, release_date, confidence, raw_json, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]),
                int(row["track_id"]),
                row["source"],
                row["source_url"] or None,
                row["source_track_id"] or None,
                row["bpm"] or None,
                row["musical_key"] or None,
                row["genre"] or None,
                row["label"] or None,
                row["release_title"] or None,
                row["release_date"] or None,
                row["confidence"] or None,
                row["raw_json"] or None,
                row["fetched_at"] or loaded_at,
            ),
        )
    conn.commit()
    print(f"Loaded {len(mixtapes)} mixtapes, {len(tracks)} tracks, and {len(track_metadata)} metadata rows from {source}")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: list[sqlite3.Row | dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            data = dict(row)
            writer.writerow({field: data.get(field) for field in fieldnames})


def write_json(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def track_export_row(row: sqlite3.Row) -> dict[str, object]:
    data = dict(row)
    data["cue"] = format_seconds(data["cue_seconds"]).strip()
    data["beatport_url"] = beatport_search_url(data.get("artist"), data["title"])
    return data


def write_markdown_index(path: Path, conn: sqlite3.Connection) -> None:
    summary = conn.execute(
        """
        SELECT COUNT(DISTINCT m.id) AS mixtapes,
               COUNT(DISTINCT CASE WHEN t.id IS NOT NULL THEN m.id END) AS mixtapes_with_tracks,
               COUNT(t.id) AS tracks,
               MIN(m.month) AS min_month,
               MAX(m.month) AS max_month
        FROM mixtapes m
        LEFT JOIN tracks t ON t.mixtape_id = m.id
        """
    ).fetchone()
    by_series = conn.execute(
        """
        SELECT m.series,
               COUNT(DISTINCT m.id) AS mixtapes,
               COUNT(DISTINCT CASE WHEN t.id IS NOT NULL THEN m.id END) AS mixtapes_with_tracks,
               COUNT(t.id) AS tracks,
               MIN(m.month) AS min_month,
               MAX(m.month) AS max_month
        FROM mixtapes m
        LEFT JOIN tracks t ON t.mixtape_id = m.id
        GROUP BY m.series
        ORDER BY m.series
        """
    ).fetchall()
    recent = conn.execute(
        """
        SELECT m.month, m.series, m.title, COUNT(t.id) AS track_count, m.soundcloud_url, m.tracklist_url
        FROM mixtapes m
        LEFT JOIN tracks t ON t.mixtape_id = m.id
        GROUP BY m.id
        ORDER BY COALESCE(m.month, '') DESC, m.id DESC
        LIMIT 20
        """
    ).fetchall()

    lines = [
        "# Crate Digger Index",
        "",
        "Generated from the local SQLite index. The CSV/JSON files next to this document are the canonical tracked data exports.",
        "",
        "## Summary",
        "",
        f"- Mixtapes: {summary['mixtapes']}",
        f"- Mixtapes with tracks: {summary['mixtapes_with_tracks']}",
        f"- Tracks: {summary['tracks']}",
        f"- Month range: {summary['min_month'] or '---- --'} to {summary['max_month'] or '---- --'}",
        "",
        "## Series",
        "",
        "| Series | Mixes | With Tracks | Tracks | Range |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in by_series:
        series = row["series"] or ""
        lines.append(
            f"| {markdown_escape(series)} | {row['mixtapes']} | {row['mixtapes_with_tracks']} | "
            f"{row['tracks']} | {row['min_month'] or '---- --'} to {row['max_month'] or '---- --'} |"
        )
    lines.extend(
        [
            "",
            "## Latest Mixes",
            "",
            "| Month | Series | Tracks | Mix |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for row in recent:
        title = markdown_link(row["title"], row["soundcloud_url"])
        if row["tracklist_url"]:
            title += f" ([tracklist]({row['tracklist_url']}))"
        lines.append(
            f"| {row['month'] or '---- --'} | {markdown_escape(row['series'] or '')} | "
            f"{row['track_count']} | {title} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_latest_mixtapes_report(path: Path, conn: sqlite3.Connection) -> None:
    latest_by_series = conn.execute(
        """
        WITH ranked AS (
            SELECT m.id, m.month, m.release_date, m.series, m.title, m.soundcloud_url,
                   m.tracklist_url,
                   COUNT(t.id) AS track_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.series
                       ORDER BY COALESCE(m.release_date, m.month, '') DESC, m.id DESC
                   ) AS series_rank
            FROM mixtapes m
            LEFT JOIN tracks t ON t.mixtape_id = m.id
            GROUP BY m.id
        )
        SELECT * FROM ranked
        WHERE series_rank = 1
        ORDER BY COALESCE(release_date, month, '') DESC, series
        """
    ).fetchall()
    recent = conn.execute(
        """
        SELECT m.id, m.month, m.release_date, m.series, m.title, m.soundcloud_url,
               m.tracklist_url,
               COUNT(t.id) AS track_count
        FROM mixtapes m
        LEFT JOIN tracks t ON t.mixtape_id = m.id
        GROUP BY m.id
        ORDER BY COALESCE(m.release_date, m.month, '') DESC, m.id DESC
        LIMIT 20
        """
    ).fetchall()

    lines = [
        "# Latest Mixtapes",
        "",
        "Generated from tracked Crate Digger exports. `Release date` uses the exact published date when available and falls back to the indexed month.",
        "",
        "## Latest By Series",
        "",
    ]
    append_latest_report_section(lines, latest_by_series, conn)
    lines.extend(
        [
            "",
            "## Recent Releases",
            "",
        ]
    )
    append_latest_report_section(lines, recent, conn)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_latest_report_section(lines: list[str], rows: list[sqlite3.Row], conn: sqlite3.Connection) -> None:
    lines.extend(
        [
            "| Release Date | Series | Tracks | Mixtape |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for row in rows:
        lines.append(latest_report_row(row))

    lines.extend(["", "### Tracklists", ""])
    for row in rows:
        lines.extend(latest_tracklist_details(row, conn))
        lines.append("")


def latest_report_row(row: sqlite3.Row) -> str:
    release = row["release_date"] or row["month"] or "---- --"
    return (
        f"| {release} | {markdown_escape(row['series'] or '')} | {row['track_count']} | "
        f"{markdown_link(row['title'], row['soundcloud_url'])} |"
    )


def latest_tracklist_details(row: sqlite3.Row, conn: sqlite3.Connection) -> list[str]:
    release = row["release_date"] or row["month"] or "---- --"
    summary = f"{release} - {row['series'] or 'Uncategorized'} - {row['title']} ({row['track_count']} tracks)"
    tracks = conn.execute(
        """
        SELECT t.position, t.cue_seconds, t.artist, t.title, bm.source_url AS beatport_track_url,
               bm.bpm AS beatport_bpm, bm.musical_key AS beatport_key,
               bm.genre AS beatport_genre, bm.label AS beatport_label
        FROM tracks t
        LEFT JOIN track_metadata bm ON bm.track_id = t.id AND bm.source = 'beatport'
        WHERE t.mixtape_id = ?
        ORDER BY t.position, t.id
        """,
        (row["id"],),
    ).fetchall()

    lines = [
        "<details>",
        f"<summary>{html.escape(summary, quote=False)}</summary>",
        "",
    ]
    if tracks:
        mix_sentence = mixtape_metadata_sentence(tracks)
        if mix_sentence:
            lines.extend([mix_sentence, ""])
        for index, track in enumerate(tracks, start=1):
            position = track["position"] or index
            cue = format_seconds(track["cue_seconds"]).strip()
            artist = f"{track['artist']} - " if track["artist"] else ""
            cue_prefix = f"{cue} " if cue else ""
            track_text = markdown_text(cue_prefix + artist + track["title"])
            beatport_url = track["beatport_track_url"] or beatport_search_url(track["artist"], track["title"])
            metadata = track_metadata_summary(track)
            metadata_text = f" - {metadata}" if metadata else ""
            lines.append(f"{position}. {track_text} ([Beatport]({beatport_url})){metadata_text}")
    else:
        lines.append("_No tracks indexed yet._")

    if row["tracklist_url"]:
        lines.extend(["", f"Source: [tracklist]({row['tracklist_url']})"])
    lines.append("</details>")
    return lines


def track_metadata_summary(track: sqlite3.Row) -> str:
    parts = []
    if track["beatport_bpm"]:
        parts.append(f"{track['beatport_bpm']} BPM")
    if track["beatport_key"]:
        parts.append(str(track["beatport_key"]))
    if track["beatport_genre"]:
        parts.append(str(track["beatport_genre"]))
    if track["beatport_label"]:
        parts.append(str(track["beatport_label"]))
    return f"`{'; '.join(parts)}`" if parts else ""


def mixtape_metadata_sentence(tracks: list[sqlite3.Row]) -> str:
    enriched = [track for track in tracks if track["beatport_bpm"] or track["beatport_key"] or track["beatport_genre"]]
    if not enriched:
        return ""

    bpms = [int(track["beatport_bpm"]) for track in enriched if str(track["beatport_bpm"] or "").isdigit()]
    keys = [str(track["beatport_key"]) for track in enriched if track["beatport_key"]]
    genres = [str(track["beatport_genre"]) for track in enriched if track["beatport_genre"]]

    phrases = []
    if bpms:
        low = min(bpms)
        high = max(bpms)
        phrases.append(f"{tempo_description(bpms)} {low}-{high} BPM")
    if genres:
        phrases.append(f"leaning toward {dominant_value(genres)}")
    if keys:
        phrases.append(f"often in {dominant_value(keys)}")
    if not phrases:
        return ""
    return f"_A {', '.join(phrases)} mix._"


def tempo_description(bpms: list[int]) -> str:
    average = sum(bpms) / len(bpms)
    if average < 118:
        return "downtempo"
    if average < 124:
        return "mid-tempo"
    if average < 132:
        return "driving"
    return "fast"


def dominant_value(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


def markdown_text(value: str) -> str:
    return value.replace("<", "&lt;").replace(">", "&gt;")


def beatport_search_url(artist: object, title: object) -> str:
    return f"https://www.beatport.com/search?q={urllib.parse.quote_plus(beatport_search_query(artist, title))}"


def beatport_search_query(artist: object, title: object) -> str:
    return " ".join(str(part).strip() for part in (artist, title) if part)


def markdown_link(label: str, url: str | None) -> str:
    escaped = markdown_escape(label)
    return f"[{escaped}]({url})" if url else escaped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="add or update a SoundCloud mixtape")
    add.add_argument("soundcloud_url")
    add.add_argument("--month", help="month label, for example 2026-05")
    add.add_argument("--release-date", help="release date, for example 2026-05-09")
    add.add_argument("--series", help="series name, for example Monthly Mixtape")
    add.add_argument("--title", help="manual title, useful with --offline")
    add.add_argument("--uploader")
    add.add_argument("--offline", action="store_true", help="skip SoundCloud metadata lookup")
    add.add_argument("--description-file", type=Path, help="manual SoundCloud description text")
    add.add_argument("--tracklist-url", help="override the detected 1001Tracklists URL")
    add.add_argument("--tracklist-file", type=Path, help="text file copied from 1001Tracklists")
    add.set_defaults(func=add_mixtape)

    batch = subparsers.add_parser(
        "batch-add-soundcloud-page",
        help="add tracks listed on a SoundCloud profile page",
    )
    batch.add_argument("page_url", help="SoundCloud listing URL, for example a /tracks page")
    batch.add_argument("--series", help="series name to apply to every mix")
    batch.add_argument("--uploader")
    batch.add_argument("--month", help="month label override to apply to every mix")
    batch.add_argument("--limit", type=int, help="maximum number of listing items to add")
    batch.add_argument("--match", help="case-insensitive title regex filter")
    batch.add_argument("--monthly-only", action="store_true", help="only add titles that contain a month name")
    batch.add_argument("--stop-at-existing", action="store_true", help="stop paginated API reads at first known URL")
    batch.add_argument("--client-id", help="SoundCloud client id override")
    batch.add_argument("--source", choices=("auto", "api", "html"), default="auto")
    batch.add_argument("--offline", action="store_true", help="skip per-track SoundCloud metadata lookup")
    batch.set_defaults(func=batch_add_soundcloud_page)

    imp = subparsers.add_parser("import-tracklist", help="import pasted tracklist text")
    imp.add_argument("mixtape_id", type=int)
    imp.add_argument("tracklist_file", type=Path)
    imp.add_argument("--tracklist-url", help="source URL for the pasted tracklist")
    imp.add_argument("--replace", action="store_true", help="replace existing tracks for this mixtape")
    imp.set_defaults(func=import_tracklist)

    one_thousand_one = subparsers.add_parser(
        "import-1001-tracklist",
        help="import a 1001Tracklists page when normal HTML is available",
    )
    one_thousand_one.add_argument("mixtape_id", type=int)
    one_thousand_one.add_argument("tracklist_url", help="1001Tracklists tracklist URL")
    one_thousand_one.add_argument("--replace", action="store_true", help="replace existing tracks for this mixtape")
    one_thousand_one.add_argument(
        "--delay",
        type=float,
        help="seconds to wait before fetching; never lower than robots.txt crawl-delay",
    )
    one_thousand_one.set_defaults(func=import_1001_tracklist)

    assisted = subparsers.add_parser(
        "import-1001-assisted",
        help="open a visible browser and import 1001Tracklists after manual review",
    )
    assisted.add_argument("mixtape_id", type=int)
    assisted.add_argument("tracklist_url", help="1001Tracklists tracklist URL")
    assisted.add_argument("--replace", action="store_true", help="replace existing tracks for this mixtape")
    assisted.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(".browser-profile/1001tracklists"),
        help="local browser profile directory for session state",
    )
    assisted.add_argument("--timeout", type=int, default=60, help="initial page load timeout in seconds")
    assisted.add_argument(
        "--auto-read",
        action="store_true",
        help="read automatically once visible tracklist elements are detected",
    )
    assisted.add_argument("--keep-open", action="store_true", help="leave the browser open after reading the page")
    assisted.add_argument("--debug-html", type=Path, help="save rendered page HTML for parser debugging")
    assisted.set_defaults(func=import_1001_assisted)

    beatport = subparsers.add_parser(
        "enrich-beatport-assisted",
        help="open a visible browser and save manually confirmed Beatport metadata",
    )
    beatport.add_argument("--track-id", type=int, help="enrich one track id")
    beatport.add_argument("--mixtape-id", type=int, help="filter by mixtape id")
    beatport.add_argument("--year", help="filter by release year, for example 2026")
    beatport.add_argument("--month", help="filter by month, for example 2026-05")
    beatport.add_argument("--series", help="filter by series name")
    beatport.add_argument("--query", help="filter track artist/title text")
    beatport.add_argument("--limit", type=int, default=10, help="maximum tracks to review")
    beatport.add_argument("--include-enriched", action="store_true", help="also show tracks with Beatport metadata")
    beatport.add_argument(
        "--search-mode",
        choices=("ui", "url"),
        default="ui",
        help="use Beatport's search UI by default; url jumps directly to /search?q=",
    )
    beatport.add_argument(
        "--no-assisted-search-focus",
        action="store_false",
        dest="assisted_search_focus",
        help="do not prompt for manual search-field focus when automatic UI search fails",
    )
    beatport.add_argument(
        "--manual-start",
        action="store_true",
        help="open a blank browser and wait while you navigate to Beatport manually",
    )
    beatport.add_argument(
        "--cdp-url",
        help="attach to an already-running Chrome debugging endpoint, for example http://127.0.0.1:9222",
    )
    beatport.add_argument(
        "--auto-first-result",
        action="store_true",
        help="after searching, open the first Beatport track result automatically",
    )
    beatport.add_argument(
        "--choose-result",
        action="store_true",
        help="after searching, print readable track results and save the selected row metadata",
    )
    beatport.add_argument(
        "--manual-result-choice",
        action="store_false",
        dest="auto_choose_result",
        help="when using --choose-result, ask for a result number instead of auto-selecting the closest row",
    )
    beatport.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(".browser-profile/beatport"),
        help="local browser profile directory for Beatport session state",
    )
    beatport.add_argument("--timeout", type=int, default=60, help="initial page load timeout in seconds")
    beatport.add_argument("--keep-open", action="store_true", help="leave the browser open after enrichment")
    beatport.set_defaults(func=enrich_beatport_assisted, auto_choose_result=True)

    beatport_manual = subparsers.add_parser(
        "enrich-beatport-manual",
        help="copy Beatport search queries and save manually confirmed track URLs",
    )
    beatport_manual.add_argument("--track-id", type=int, help="enrich one track id")
    beatport_manual.add_argument("--mixtape-id", type=int, help="filter by mixtape id")
    beatport_manual.add_argument("--year", help="filter by release year, for example 2026")
    beatport_manual.add_argument("--month", help="filter by month, for example 2026-05")
    beatport_manual.add_argument("--series", help="filter by series name")
    beatport_manual.add_argument("--query", help="filter track artist/title text")
    beatport_manual.add_argument("--limit", type=int, default=10, help="maximum tracks to review")
    beatport_manual.add_argument("--include-enriched", action="store_true", help="also show tracks with Beatport metadata")
    beatport_manual.add_argument("--open-browser", action="store_true", help="open Beatport in your default browser first")
    beatport_manual.add_argument(
        "--no-copy",
        action="store_false",
        dest="copy",
        help="do not copy each Beatport search query to the clipboard",
    )
    beatport_manual.add_argument(
        "--url-only",
        action="store_false",
        dest="prompt_metadata",
        help="save only the confirmed Beatport URL and track id",
    )
    beatport_manual.set_defaults(func=enrich_beatport_manual, copy=True, prompt_metadata=True)

    mixesdb = subparsers.add_parser(
        "import-mixesdb-category",
        help="import tracklists from MixesDB category pages",
    )
    mixesdb.add_argument(
        "category_url",
        nargs="?",
        default="https://www.mixesdb.com/w/Category:Only_100s",
    )
    mixesdb.add_argument("--match", help="case-insensitive page title regex filter")
    mixesdb.add_argument("--title-match", help="plain text required in category page titles")
    mixesdb.add_argument("--limit", type=int, help="maximum number of pages to import")
    mixesdb.add_argument("--delay", type=float, default=1.0, help="seconds to wait between page fetches")
    mixesdb.add_argument("--add-missing", action="store_true", help="add MixesDB pages not already in mixtapes")
    mixesdb.add_argument("--new-only", action="store_true", help="only fetch pages whose tracklist URL is not indexed")
    mixesdb.add_argument("--series", default="Only 100s", help="series used with --add-missing")
    mixesdb.add_argument("--uploader", default="Only 100s", help="uploader used with --add-missing")
    mixesdb.set_defaults(func=import_mixesdb_category)

    stat_cmd = subparsers.add_parser("stats", help="summarize the indexed database")
    stat_cmd.set_defaults(func=stats)

    list_cmd = subparsers.add_parser("list", help="list indexed mixtapes")
    list_cmd.add_argument("--year", help="filter by year, for example 2021")
    list_cmd.add_argument("--series", help="filter by series name")
    list_cmd.add_argument("--with-tracks", action="store_true", help="only show mixtapes with imported tracks")
    list_cmd.add_argument("--without-tracks", action="store_true", help="only show mixtapes without imported tracks")
    list_cmd.add_argument("--limit", type=int, help="maximum rows to print")
    list_cmd.add_argument("--desc", action="store_true", help="show newest rows first")
    list_cmd.set_defaults(func=list_mixtapes)

    tracks = subparsers.add_parser("tracks", help="list indexed tracks")
    tracks.add_argument("--mixtape-id", type=int, help="filter by mixtape id")
    tracks.add_argument("--year", help="filter by year, for example 2021")
    tracks.add_argument("--month", help="filter by month, for example 2021-04")
    tracks.add_argument("--query", help="filter track artist/title text")
    tracks.add_argument("--limit", type=int, default=50, help="maximum rows to print")
    tracks.set_defaults(func=list_tracks)

    show = subparsers.add_parser("show", help="show one mixtape and its tracks")
    show.add_argument("mixtape_id", type=int)
    show.set_defaults(func=show_mixtape)

    find = subparsers.add_parser("search", help="search indexed mixes and tracks")
    find.add_argument("query")
    find.add_argument("--type", choices=("all", "mixes", "tracks"), default="all")
    find.add_argument("--limit", type=int, default=25, help="maximum mixes and tracks to print")
    find.set_defaults(func=search)

    export = subparsers.add_parser("export", help="export indexed data to tracked files")
    export.add_argument("--output", type=Path, default=Path("data"), help="output directory")
    export.set_defaults(func=export_index)

    load = subparsers.add_parser("load-export", help="load tracked exported data into SQLite")
    load.add_argument("--input", type=Path, default=Path("data"), help="export directory")
    load.set_defaults(func=load_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
