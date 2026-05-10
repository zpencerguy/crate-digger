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
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


DEFAULT_DB = Path("crate-digger.sqlite3")
SOUNDCLOUD_OEMBED = "https://soundcloud.com/oembed"
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
    (?:(?P<num>\d{1,3})[\).:-]\s*)?
    (?:\[?(?P<time>(?:(?:\d{1,2}:)?\d{1,2}:\d{2}))\]?\s*)?
    (?P<body>.+?)
    \s*$
    """,
    re.VERBOSE,
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

CREATE INDEX IF NOT EXISTS tracks_artist_idx ON tracks(artist);
CREATE INDEX IF NOT EXISTS tracks_title_idx ON tracks(title);
CREATE INDEX IF NOT EXISTS mixtapes_month_idx ON mixtapes(month);
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
        headers={"User-Agent": "crate-digger/0.1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "crate-digger/0.1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> object:
    return json.loads(fetch_text(url))


def raw_mixesdb_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    title = parsed.path.removeprefix("/w/")
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


def infer_uploader(title: str) -> str | None:
    if " by " in title:
        return title.rsplit(" by ", 1)[1].strip() or None
    return None


def magic_tape_number(title: str) -> int | None:
    match = re.search(r"\bmagic tape\s+0*(\d{1,3})\b", title, re.IGNORECASE)
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
            return mixtape

    tape_number = magic_tape_number(page_title)
    if series == "Magic Tape" and tape_number is not None:
        pattern = f"%Magic Tape {tape_number}%"
        zero_pattern = f"%Magic Tape {tape_number:02d}%"
        return conn.execute(
            """
            SELECT id, title FROM mixtapes
            WHERE series = ? AND (title LIKE ? OR title LIKE ?)
            ORDER BY id
            LIMIT 1
            """,
            (series, pattern, zero_pattern),
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
                "UPDATE mixtapes SET tracklist_url = COALESCE(tracklist_url, ?) WHERE id = ?",
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
    imported = import_tracks(conn, args.mixtape_id, args.tracklist_file)
    conn.commit()
    print(f"Imported {imported} tracks into mixtape #{args.mixtape_id}")


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
        ],
        [track_export_row(row) for row in tracks],
    )
    write_json(output / "mixtapes.json", [dict(row) for row in mixtapes])
    write_json(output / "tracks.json", [track_export_row(row) for row in tracks])
    write_markdown_index(output / "index.md", conn)
    write_latest_mixtapes_report(output / "latest-mixtapes.md", conn)
    print(f"Exported {len(mixtapes)} mixtapes and {len(tracks)} tracks to {output}")


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
    if not mixtapes_path.exists() or not tracks_path.exists():
        raise SystemExit(f"Expected {mixtapes_path} and {tracks_path}")

    conn = connect(args.db)
    conn.execute("DELETE FROM tracks")
    conn.execute("DELETE FROM mixtapes")

    mixtapes = read_csv(mixtapes_path)
    tracks = read_csv(tracks_path)
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
    conn.commit()
    print(f"Loaded {len(mixtapes)} mixtapes and {len(tracks)} tracks from {source}")


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
            SELECT m.month, m.release_date, m.series, m.title, m.soundcloud_url,
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
        SELECT m.month, m.release_date, m.series, m.title, m.soundcloud_url,
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
        "| Release Date | Series | Tracks | Mixtape |",
        "| --- | --- | ---: | --- |",
    ]
    for row in latest_by_series:
        lines.append(latest_report_row(row))
    lines.extend(
        [
            "",
            "## Recent Releases",
            "",
            "| Release Date | Series | Tracks | Mixtape |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for row in recent:
        lines.append(latest_report_row(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def latest_report_row(row: sqlite3.Row) -> str:
    release = row["release_date"] or row["month"] or "---- --"
    return (
        f"| {release} | {markdown_escape(row['series'] or '')} | {row['track_count']} | "
        f"{markdown_link(row['title'], row['soundcloud_url'])} |"
    )


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


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
    imp.set_defaults(func=import_tracklist)

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
