import unittest

from crate_digger.cli import (
    beatport_track_id_from_url,
    beatport_search_query,
    beatport_search_url,
    beatport_candidate_score,
    connect,
    discover_soundcloud_client_id,
    default_mixesdb_title_match,
    date_from_url,
    extract_beatport_metadata,
    extract_beatport_result_metadata,
    extract_mixesdb_category_pages,
    extract_tracklist_url,
    extract_soundcloud_tracks,
    extract_soundcloud_user_id,
    infer_mixesdb_page_month,
    infer_month,
    is_beatport_track_url,
    magic_tape_number,
    manual_beatport_metadata,
    mixtape_metadata_sentence,
    find_mixtape_for_source,
    numbered_series_number,
    parse_mixesdb_raw_tracklist,
    parse_1001tracklists_html,
    parse_description_tracklist,
    parse_tracklist,
    raw_mixesdb_url,
    required_title_score,
    seconds_from_time,
    split_artist_title,
    tempo_description,
    title_match_tokens,
    track_metadata_summary,
    tracks_from_browser_1001,
    url_matches_title_tokens,
)


class TracklistParserTest(unittest.TestCase):
    def test_beatport_search_url_uses_artist_and_title(self):
        self.assertEqual(beatport_search_query("Jayda G", "All I Need"), "Jayda G All I Need")
        self.assertEqual(
            beatport_search_url("Jayda G", "All I Need"),
            "https://www.beatport.com/search?q=Jayda+G+All+I+Need",
        )
        self.assertEqual(
            beatport_search_url(None, "Unknown ID"),
            "https://www.beatport.com/search?q=Unknown+ID",
        )

    def test_beatport_track_id_from_url_reads_numeric_path_segment(self):
        self.assertEqual(
            beatport_track_id_from_url("https://www.beatport.com/track/all-i-need/12345678"),
            "12345678",
        )
        self.assertIsNone(beatport_track_id_from_url("https://example.com/track/all-i-need/12345678"))

    def test_is_beatport_track_url_requires_track_path_and_id(self):
        self.assertTrue(is_beatport_track_url("https://www.beatport.com/track/all-i-need/12345678"))
        self.assertFalse(is_beatport_track_url("https://www.beatport.com/search?q=All+I+Need"))

    def test_extract_beatport_metadata_reads_labeled_page_text(self):
        metadata = extract_beatport_metadata(
            {
                "url": "https://www.beatport.com/track/all-i-need/12345678",
                "title": "All I Need",
                "bodyText": """
                All I Need
                BPM
                126
                Key
                A Minor
                Genre
                House
                Label
                Ninja Tune
                Release
                All I Need
                Release Date
                May 8, 2026
                """,
                "jsonLd": [],
            }
        )

        self.assertEqual(metadata["source_track_id"], "12345678")
        self.assertEqual(metadata["bpm"], "126")
        self.assertEqual(metadata["musical_key"], "A Minor")
        self.assertEqual(metadata["genre"], "House")
        self.assertEqual(metadata["label"], "Ninja Tune")
        self.assertEqual(metadata["release_title"], "All I Need")
        self.assertEqual(metadata["release_date"], "2026-05-08")
        self.assertEqual(metadata["confidence"], "manual")

    def test_manual_beatport_metadata_stores_confirmed_url(self):
        metadata = manual_beatport_metadata("https://www.beatport.com/track/all-i-need/12345678")

        self.assertEqual(metadata["source_url"], "https://www.beatport.com/track/all-i-need/12345678")
        self.assertEqual(metadata["source_track_id"], "12345678")
        self.assertEqual(metadata["confidence"], "manual")
        self.assertIsNone(metadata["bpm"])

    def test_beatport_title_tokens_match_track_url(self):
        self.assertEqual(title_match_tokens("Feel That (Extended Mix)"), ["feel", "that"])
        self.assertEqual(title_match_tokens("Groove In (Original Mix)"), ["groove"])
        self.assertTrue(url_matches_title_tokens("https://www.beatport.com/track/feel-that/28821753", ["feel", "that"]))
        self.assertFalse(
            url_matches_title_tokens("https://www.beatport.com/track/evolution-nacho-scoppa-remix/14695069", ["tango"])
        )

    def test_extract_beatport_result_metadata_reads_search_row(self):
        metadata = extract_beatport_result_metadata(
            "https://www.beatport.com/track/what-i-want-/28702238",
            "What I Want Original Mix",
            "What I Want Original Mix VITO (UK) Cocoa Minimal / Deep Tech | Deep Tech 128 BPM - F Minor 2026-05-15 $1.69",
            [
                {"href": "https://www.beatport.com/release/what-i-want/6864621", "text": "What I Want"},
                {"href": "https://www.beatport.com/label/cocoa/75684", "text": "Cocoa"},
                {"href": "https://www.beatport.com/genre/minimal-deep-tech/14", "text": "Minimal / Deep Tech"},
            ],
        )

        self.assertEqual(metadata["source_track_id"], "28702238")
        self.assertEqual(metadata["bpm"], "128")
        self.assertEqual(metadata["musical_key"], "F Minor")
        self.assertEqual(metadata["genre"], "Minimal / Deep Tech")
        self.assertEqual(metadata["label"], "Cocoa")
        self.assertEqual(metadata["release_title"], "What I Want")
        self.assertEqual(metadata["release_date"], "2026-05-15")

    def test_beatport_candidate_score_weights_artist_match(self):
        correct = beatport_candidate_score(
            "https://www.beatport.com/track/what-i-want-/28702238",
            "What I Want Original Mix VITO (UK) Cocoa",
            ["what", "want"],
            ["vito"],
        )
        title_only = beatport_candidate_score(
            "https://www.beatport.com/track/what-i-want/4665875",
            "What I Want Original Mix Hannah Wants, Chris Lorenzo",
            ["what", "want"],
            ["vito"],
        )

        self.assertGreater(correct, title_only)

    def test_beatport_candidate_score_uses_whole_word_tokens(self):
        self.assertEqual(
            beatport_candidate_score(
                "https://www.beatport.com/track/tucci/26041642",
                "Tucci Original Mix Obre Obre, Obreidy Random Sounds",
                ["tuba"],
                ["alexey", "union", "ira", "ange", "kinky", "sound"],
            ),
            0,
        )

    def test_required_title_score_requires_half_of_tokens(self):
        self.assertEqual(required_title_score(["pink", "limo", "fezzo", "remix"]), 2)
        self.assertEqual(required_title_score(["tuba"]), 1)

    def test_track_metadata_summary_formats_available_fields(self):
        self.assertEqual(
            track_metadata_summary(
                {
                    "beatport_bpm": "128",
                    "beatport_key": "A Minor",
                    "beatport_genre": "House",
                    "beatport_label": "NO ART",
                }
            ),
            "`128 BPM; A Minor; House; NO ART`",
        )

    def test_mixtape_metadata_sentence_summarizes_enriched_tracks(self):
        self.assertEqual(tempo_description([124, 128, 130]), "driving")
        self.assertEqual(
            mixtape_metadata_sentence(
                [
                    {"beatport_bpm": "124", "beatport_key": "A Minor", "beatport_genre": "House"},
                    {"beatport_bpm": "128", "beatport_key": "A Minor", "beatport_genre": "House"},
                    {"beatport_bpm": "130", "beatport_key": "G Minor", "beatport_genre": "Tech House"},
                ]
            ),
            "_A driving 124-130 BPM, leaning toward House, often in A Minor mix._",
        )

    def test_raw_mixesdb_url_decodes_page_title_before_encoding_query(self):
        self.assertEqual(
            raw_mixesdb_url("https://www.mixesdb.com/w/2024-04-26_-_Reb%C5%ABke_@_Klein_Phoenix_(ERA_109)"),
            "https://www.mixesdb.com/w/index.php?title=2024-04-26_-_Reb%C5%ABke_%40_Klein_Phoenix_%28ERA_109%29&action=raw",
        )

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

    def test_parse_description_tracklist_reads_tracklist_section(self):
        tracks = parse_description_tracklist(
            """
            Listen back here.

            Tracklist

            01. Denzel Jo Armani - Man I Just Woke Up (Extended Mix)
            02. Detlef - Step Over (Original Mix)
            """
        )

        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["artist"], "Denzel Jo Armani")
        self.assertEqual(tracks[0]["title"], "Man I Just Woke Up (Extended Mix)")

    def test_parse_description_tracklist_accepts_colon_and_stops_at_boilerplate(self):
        tracks = parse_description_tracklist(
            """
            Tracklist:

            01. Sirus Hood - Trapped In (Original Mix)
            02. Disfreq - Psychedelic Girls (Extended Mix)

            Be part of the show by leaving a voice message.
            """
        )

        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[1]["artist"], "Disfreq")

    def test_parse_description_tracklist_accepts_inline_header_and_timestamp_rows(self):
        tracks = parse_description_tracklist(
            """
            ERA 010 - Rebuke Studio Mix Tracklist

            00:00 Harvard Bass - After Hour Sweets (Truncate Remix)
            05:10 Cassettes For Kids - Heard This Calling
            """
        )

        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["cue_seconds"], 0)
        self.assertEqual(tracks[0]["artist"], "Harvard Bass")

    def test_parse_description_tracklist_accepts_number_space_rows(self):
        tracks = parse_description_tracklist(
            """
            Tracklist

            01 Rome - Rebuke
            02 Different Man - Bastian Bux
            """
        )

        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["position"], 1)
        self.assertEqual(tracks[0]["artist"], "Rome")

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

    def test_date_from_url_extracts_mixesdb_dates(self):
        self.assertEqual(
            date_from_url("https://www.mixesdb.com/w/2026-03-12_-_The_Magician_-_Magic_Tape_132"),
            "2026-03-12",
        )
        self.assertIsNone(date_from_url("https://soundcloud.com/themagician/magic-tape-132"))

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

    def test_numbered_series_number_extracts_hash_and_plain_values(self):
        self.assertEqual(numbered_series_number("XXX Radio #099", "XXX Radio"), 99)
        self.assertEqual(numbered_series_number("2025-06-20 - Mau P - XXX Radio 141", "XXX Radio"), 141)
        self.assertEqual(numbered_series_number("Magic Tape 09", "Magic Tape"), 9)
        self.assertIsNone(numbered_series_number("Only 100s January 2024", "XXX Radio"))

    def test_find_mixtape_for_source_skips_mismatched_numbered_soundcloud_url(self):
        conn = connect(":memory:")
        conn.execute(
            """
            INSERT INTO mixtapes (
                id, soundcloud_url, title, uploader, month, release_date, series,
                description, tracklist_url, created_at
            )
            VALUES
                (1, 'https://soundcloud.com/realmaup/xxx-radio-18', 'XXX Radio #018', 'Mau P', '2023-02', NULL, 'XXX Radio', '', NULL, 'now'),
                (2, 'https://soundcloud.com/realmaup/xxx-radio-108', 'XXX Radio #108', 'Mau P', '2024-11', NULL, 'XXX Radio', '', NULL, 'now')
            """
        )

        mixtape = find_mixtape_for_source(
            conn,
            soundcloud_url="https://soundcloud.com/realmaup/xxx-radio-18",
            page_title="2024-11-01 - Mau P - XXX Radio 108",
            series="XXX Radio",
        )

        self.assertEqual(mixtape["id"], 2)

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

    def test_parse_1001tracklists_html_reads_microdata_tracks(self):
        tracks = parse_1001tracklists_html(
            """
            <table class="detail">
              <tr class="tlpItem">
                <td><span class="cueValue">00:00</span></td>
                <td>
                  <div itemprop="tracks" itemscope itemtype="http://schema.org/MusicRecording">
                    <meta itemprop="byArtist" content="Jayda G">
                    <meta itemprop="name" content="All I Need">
                  </div>
                </td>
              </tr>
              <tr class="tlpItem">
                <td><span class="cueValue">06:00</span></td>
                <td>
                  <div itemprop="tracks" itemscope itemtype="http://schema.org/MusicRecording">
                    <meta itemprop="byArtist" content="Pleasure State">
                    <meta itemprop="name" content="Take My Time">
                  </div>
                </td>
              </tr>
            </table>
            """
        )

        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["position"], 1)
        self.assertEqual(tracks[0]["cue_seconds"], 0)
        self.assertEqual(tracks[0]["artist"], "Jayda G")
        self.assertEqual(tracks[0]["title"], "All I Need")
        self.assertEqual(tracks[1]["cue_seconds"], 360)

    def test_tracks_from_browser_1001_converts_rendered_rows(self):
        tracks = tracks_from_browser_1001(
            [
                {"artist": "Jayda G", "title": "Jayda G - All I Need", "cue": "00:00"},
                {"artist": "Pleasure State", "title": "Pleasure State - Take My Time", "cue": "06:00"},
            ]
        )

        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["raw_text"], "1. [00:00] Jayda G - All I Need")
        self.assertEqual(tracks[0]["title"], "All I Need")
        self.assertEqual(tracks[1]["cue_seconds"], 360)


if __name__ == "__main__":
    unittest.main()
