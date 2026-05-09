import unittest

from crate_digger.cli import (
    discover_soundcloud_client_id,
    default_mixesdb_title_match,
    extract_mixesdb_category_pages,
    extract_tracklist_url,
    extract_soundcloud_tracks,
    extract_soundcloud_user_id,
    infer_mixesdb_page_month,
    infer_month,
    magic_tape_number,
    parse_mixesdb_raw_tracklist,
    parse_tracklist,
    seconds_from_time,
    split_artist_title,
)


class TracklistParserTest(unittest.TestCase):
    def test_seconds_from_time_accepts_mm_ss_and_h_mm_ss(self):
        self.assertEqual(seconds_from_time("04:35"), 275)
        self.assertEqual(seconds_from_time("1:02:03"), 3723)

    def test_split_artist_title_accepts_common_separators(self):
        self.assertEqual(split_artist_title("Artist One - First Track"), ("Artist One", "First Track"))
        self.assertEqual(split_artist_title("Unknown ID"), (None, "Unknown ID"))

    def test_parse_tracklist_accepts_numbered_timestamps(self):
        tracks = parse_tracklist(
            """
            # copied from a tracklist page
            01. [00:00] Artist One - First Track
            02. [04:35] Artist Two - Second Track
            03. Unknown ID
            https://1001.tl/example
            """
        )

        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[0]["position"], 1)
        self.assertEqual(tracks[0]["cue_seconds"], 0)
        self.assertEqual(tracks[0]["artist"], "Artist One")
        self.assertEqual(tracks[0]["title"], "First Track")
        self.assertEqual(tracks[2]["artist"], None)
        self.assertEqual(tracks[2]["title"], "Unknown ID")

    def test_extract_tracklist_url_finds_1001_links(self):
        self.assertEqual(
            extract_tracklist_url("full list: https://www.1001tracklists.com/tracklist/example.html"),
            "https://www.1001tracklists.com/tracklist/example.html",
        )
        self.assertEqual(
            extract_tracklist_url("short link https://1001.tl/abc."),
            "https://1001.tl/abc",
        )

    def test_infer_month_uses_second_month_for_combined_titles(self):
        self.assertEqual(infer_month("November 2025 - Only 100s"), "2025-11")
        self.assertEqual(infer_month("June + July 2025 - Only 100s"), "2025-07")
        self.assertEqual(infer_month("No month here", "2025-02"), "2025-02")

    def test_infer_mixesdb_page_month_uses_leading_date(self):
        self.assertEqual(infer_mixesdb_page_month("2013-03-19 - The Magician - Magic Tape 31"), "2013-03")
        self.assertEqual(infer_mixesdb_page_month("The Aston Shuffle - Only 100s April 2021"), "2021-04")

    def test_extract_soundcloud_tracks_from_fallback_html(self):
        tracks = extract_soundcloud_tracks(
            "https://soundcloud.com/itsonly100s/tracks",
            """
            <h2><a href="/itsonly100s/november-2025-only-100s">November 2025 - Only 100s</a></h2>
            published on 2025-11-24T05:53:55Z
            <h2><a href="/itsonly100s">Only 100s</a></h2>
            <h2><a href="/itsonly100s/tracks">Only 100s's tracks</a></h2>
            <h2><a href="/itsonly100s/october-2025-only-100s">October 2025 - Only 100s</a></h2>
            published on 2025-10-28T18:01:05Z
            """,
        )

        self.assertEqual(
            tracks,
            [
                {
                    "title": "November 2025 - Only 100s",
                    "url": "https://soundcloud.com/itsonly100s/november-2025-only-100s",
                    "published_month": "2025-11",
                },
                {
                    "title": "October 2025 - Only 100s",
                    "url": "https://soundcloud.com/itsonly100s/october-2025-only-100s",
                    "published_month": "2025-10",
                },
            ],
        )

    def test_extract_soundcloud_api_bits_from_page_html(self):
        html = """
        <script src="https://a-v2.sndcdn.com/assets/app.js"></script>
        <a href="https://api.soundcloud.com/users/soundcloud%3Ausers%3A268947013">api</a>
        """
        self.assertEqual(extract_soundcloud_user_id(html), "268947013")

    def test_discover_client_id_returns_none_without_asset_match(self):
        html = '<script src="https://example.com/app.js"></script>'
        self.assertIsNone(discover_soundcloud_client_id("https://soundcloud.com/itsonly100s/tracks", html))

    def test_extract_mixesdb_category_pages_filters_only100s_pages(self):
        pages = extract_mixesdb_category_pages(
            "https://www.mixesdb.com/w/Category:Only_100s",
            """
            <a href="/w/2021-04-29_-_The_Aston_Shuffle_-_Only_100s_April_2021">
              2021-04-29 - The Aston Shuffle - Only 100s April 2021
            </a>
            <a href="/w/File:2021-04-29_-_The_Aston_Shuffle_-_Only_100s_April_2021.jpg">
              File:2021-04-29 - The Aston Shuffle - Only 100s April 2021.jpg
            </a>
            <a href="/w/2021-03-06_-_Milk_%26_Sugar_-_Club_FG">Milk & Sugar</a>
            """,
        )

        self.assertEqual(
            pages,
            [
                {
                    "text": "2021-04-29 - The Aston Shuffle - Only 100s April 2021",
                    "url": "https://www.mixesdb.com/w/2021-04-29_-_The_Aston_Shuffle_-_Only_100s_April_2021",
                }
            ],
        )

    def test_extract_mixesdb_category_pages_defaults_to_category_name(self):
        pages = extract_mixesdb_category_pages(
            "https://www.mixesdb.com/w/Category:Magic_Tape",
            """
            <a href="/w/2026-01-29_-_The_Magician_-_Magic_Tape_131">
              2026-01-29 - The Magician - Magic Tape 131
            </a>
            <a href="/w/2026-02-01_-_Someone_Else">Someone Else</a>
            """,
        )

        self.assertEqual(default_mixesdb_title_match("https://www.mixesdb.com/w/Category:Magic_Tape"), "Magic Tape")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["text"], "2026-01-29 - The Magician - Magic Tape 131")

    def test_magic_tape_number_extracts_zero_padded_values(self):
        self.assertEqual(magic_tape_number("Magic Tape 09"), 9)
        self.assertEqual(magic_tape_number("The Magician - Magic Tape 131"), 131)
        self.assertIsNone(magic_tape_number("Only 100s January 2024"))

    def test_parse_mixesdb_raw_tracklist_converts_wiki_lines(self):
        soundcloud_url, text = parse_mixesdb_raw_tracklist(
            """
            {{Player|https://soundcloud.com/itsonly100s/example}}
            # [00] Jayda G - All I Need
            # [06] Pleasure State - Take My Time [Repopulate Mars]
            # [?] Unknown ID
            """
        )
        tracks = parse_tracklist(text)

        self.assertEqual(soundcloud_url, "https://soundcloud.com/itsonly100s/example")
        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[0]["cue_seconds"], 0)
        self.assertEqual(tracks[1]["cue_seconds"], 360)
        self.assertEqual(tracks[2]["title"], "Unknown ID")


if __name__ == "__main__":
    unittest.main()
