# Crate Digger

Crate Digger indexes monthly SoundCloud mixtapes and the tracks inside them.

The first version is intentionally local and boring: it stores everything in
SQLite, uses SoundCloud's public oEmbed endpoint for mix metadata, extracts
1001Tracklists links from SoundCloud descriptions when present, and imports
tracklists from pasted text.

## 1001Tracklists

1001Tracklists is great for manual lookup. Crate Digger treats it as an
optional source that must be used politely:

- Read `robots.txt` before fetching.
- Use the project user-agent instead of Python's default user-agent.
- Respect the site's crawl delay.
- Stop when the site serves a JavaScript or bot-protection challenge.

If normal page HTML is available, import it directly:

```sh
python3 -m uv run crate-digger import-1001-tracklist 1 \
  "https://www.1001tracklists.com/tracklist/15vbkbst/the-aston-shuffle-only-100s-april-2026-2026-04-28.html"
```

If the page is challenged, use the dependable manual workflow:

1. Add the SoundCloud mix URL.
2. Let Crate Digger capture the title, description, and 1001Tracklists link.
3. Copy the visible tracklist text from 1001Tracklists into a `.txt` file.
4. Import that file into the indexed mixtape with the source URL attached.

```sh
python3 -m uv run crate-digger import-tracklist 1 tracklists/only100s-2026-04.txt \
  --tracklist-url "https://www.1001tracklists.com/tracklist/15vbkbst/the-aston-shuffle-only-100s-april-2026-2026-04-28.html"
```

That gives you fast local search and keeps the index from depending on bypassing
bot protection.

For a human-supervised local import, use the assisted browser flow. It opens a
visible browser, waits for you to confirm that the tracklist is visible, then
reads the rendered page:

```sh
python3 -m uv run playwright install chromium
python3 -m uv run crate-digger import-1001-assisted 1 \
  "https://www.1001tracklists.com/tracklist/15vbkbst/the-aston-shuffle-only-100s-april-2026-2026-04-28.html" \
  --replace
```

Use `--auto-read` to skip the terminal confirmation when the visible browser
loads the tracklist normally:

```sh
python3 -m uv run crate-digger import-1001-assisted 1 \
  "https://www.1001tracklists.com/tracklist/15vbkbst/the-aston-shuffle-only-100s-april-2026-2026-04-28.html" \
  --replace \
  --auto-read
```

This command is intended for local use, not GitHub Actions.

## Development Setup

This repo uses [uv](https://docs.astral.sh/uv/) for its Python environment.

Install dependencies and create `.venv`:

```sh
python3 -m uv sync
```

Run the CLI from the managed environment:

```sh
python3 -m uv run crate-digger --help
```

If you want `uv` available as a normal shell command, add your user Python bin
directory to your shell profile:

```sh
echo 'export PATH="$HOME/Library/Python/3.9/bin:$PATH"' >> ~/.zshrc
```

Then open a new terminal and use `uv run ...` directly.

Run tests:

```sh
python3 -m uv run python -m unittest discover -s tests
```

## Web UI

Crate Digger includes a small Django app for browsing the indexed data and
editing it through Django admin. The Django models point at the existing
`crate-digger.sqlite3` tables, so the CLI and web UI share the same local
working database.

Create the Django admin/auth tables in your local SQLite database:

```sh
python3 -m uv run python manage.py migrate
```

Start the site:

```sh
python3 -m uv run python manage.py runserver
```

Then open <http://127.0.0.1:8000/>.

To use `/admin/`, create a local admin user:

```sh
python3 -m uv run python manage.py createsuperuser
```

## Usage

## Agent Workflows

Repeatable, markdown-defined workflows live in
[`workflows/`](workflows/README.md). The first workflow,
[`workflows/enrich-mixtape.md`](workflows/enrich-mixtape.md), describes the
post-GitHub-Action enrichment path: pull the latest indexed data, import a
tracklist if needed, enrich tracks with Beatport metadata, export `data/`, and
prepare a final approval summary.

The workflow is designed for a main invoker agent that delegates bounded tasks
to simple markdown-defined sub-agents in [`workflows/agents/`](workflows/agents/).
The practical goal is to keep the human loop to two or three useful checkpoints:
browser/source readiness, ambiguous match review, and final commit/push
approval.

## Storage Model

Crate Digger uses SQLite as a local working cache and exports reviewable files
for GitHub:

- `crate-digger.sqlite3` is ignored because it is a generated binary database.
- `data/mixtapes.csv` and `data/tracks.csv` are stable, diffable exports.
- `data/track_metadata.csv` stores optional confirmed external metadata.
- `data/mixtapes.json`, `data/tracks.json`, and `data/track_metadata.json`
  are convenient for scripts.
- `data/index.md` is a quick GitHub-friendly summary.
- `data/latest-mixtapes.md` is a GitHub-friendly latest releases report.

Export the current SQLite index:

```sh
python3 -m uv run crate-digger export --output data
```

Load the tracked exports back into SQLite:

```sh
python3 -m uv run crate-digger load-export --input data
```

The GitHub Actions workflow in `.github/workflows/index.yml` rebuilds the
SQLite database from the tracked `data/` exports, checks only for newer
SoundCloud uploads and new MixesDB pages, exports `data/`, and commits changes
when newly indexed data appears.

For incremental checks, use `--stop-at-existing` with SoundCloud imports and
`--new-only` with MixesDB imports. This keeps scheduled runs focused on new
uploads instead of walking the entire archive every time.

```sh
python3 -m uv run crate-digger add \
  "https://soundcloud.com/example/monthly-mix-may-2026" \
  --month 2026-05 \
  --series "Monthly Mixtape"
```

If you already have a copied tracklist:

```sh
python3 -m uv run crate-digger add \
  "https://soundcloud.com/example/monthly-mix-may-2026" \
  --month 2026-05 \
  --series "Monthly Mixtape" \
  --tracklist-file tracklists/2026-05.txt
```

If you want to skip SoundCloud lookup and enter metadata yourself:

```sh
python3 -m uv run crate-digger add \
  "https://soundcloud.com/example/monthly-mix-may-2026" \
  --offline \
  --title "May 2026 Monthly Mix" \
  --month 2026-05 \
  --series "Monthly Mixtape" \
  --tracklist-url "https://1001.tl/example" \
  --tracklist-file tracklists/2026-05.txt
```

Or import tracks later:

```sh
python3 -m uv run crate-digger import-tracklist 1 tracklists/2026-05.txt
```

Replace existing tracks and record the source URL:

```sh
python3 -m uv run crate-digger import-tracklist 1 tracklists/2026-05.txt \
  --replace \
  --tracklist-url "https://1001.tl/example"
```

Summarize the local database:

```sh
python3 -m uv run crate-digger stats
```

Search across indexed mixes and tracks:

```sh
python3 -m uv run crate-digger search "Four Tet"
```

Search only tracks:

```sh
python3 -m uv run crate-digger search "Jayda G" --type tracks
```

List indexed tracks:

```sh
python3 -m uv run crate-digger tracks --year 2021 --limit 20
```

Show one mix:

```sh
python3 -m uv run crate-digger show 1
```

List mixes:

```sh
python3 -m uv run crate-digger list
```

Filter mix listings:

```sh
python3 -m uv run crate-digger list --year 2023 --with-tracks
python3 -m uv run crate-digger list --without-tracks --desc --limit 10
```

Batch import a SoundCloud profile's monthly mixes:

```sh
python3 -m uv run crate-digger batch-add-soundcloud-page \
  "https://soundcloud.com/itsonly100s/tracks" \
  --series "Only 100s" \
  --uploader "Only 100s" \
  --monthly-only
```

The same workflow can index The Magician's Magic Tape archive:

```sh
python3 -m uv run crate-digger batch-add-soundcloud-page \
  "https://soundcloud.com/themagician/tracks" \
  --series "Magic Tape" \
  --uploader "The Magician" \
  --match "^Magic Tape [0-9]+"
```

Mau P's weekly `XXX Radio` archive uses the same pattern:

```sh
python3 -m uv run crate-digger batch-add-soundcloud-page \
  "https://soundcloud.com/realmaup/tracks" \
  --series "XXX Radio" \
  --uploader "Mau P" \
  --match "^XXX Radio #[0-9]+"
```

Rebūke's weekly `ERA` archive can be indexed from SoundCloud too:

```sh
python3 -m uv run crate-digger batch-add-soundcloud-page \
  "https://soundcloud.com/rebukemusic/tracks" \
  --series "ERA" \
  --uploader "Rebūke" \
  --match "^ERA [0-9]+"
```

The SoundCloud batch importer tries SoundCloud's public JSON endpoint first and
falls back to the static page HTML when needed. `--monthly-only` keeps yearly
recaps and other non-monthly uploads out of the local index.

Import available Only 100s tracklists from MixesDB:

```sh
python3 -m uv run crate-digger import-mixesdb-category
```

Import available Magic Tape tracklists from MixesDB:

```sh
python3 -m uv run crate-digger import-mixesdb-category \
  "https://www.mixesdb.com/w/Category:Magic_Tape" \
  --series "Magic Tape" \
  --uploader "The Magician"
```

Import available `XXX Radio` tracklists from MixesDB:

```sh
python3 -m uv run crate-digger import-mixesdb-category \
  "https://www.mixesdb.com/w/Category:XXX_Radio" \
  --series "XXX Radio" \
  --uploader "Mau P" \
  --title-match "XXX Radio"
```

Import available Rebūke `ERA` source pages from MixesDB:

```sh
python3 -m uv run crate-digger import-mixesdb-category \
  "https://www.mixesdb.com/w/Category%3AReb%C5%ABke" \
  --series "ERA" \
  --uploader "Rebūke" \
  --title-match "ERA"
```

MixesDB is currently the best primary source for these archives' per-track data:
its raw wiki pages include SoundCloud player URLs and structured numbered
tracklists, which lets Crate Digger match pages back to indexed SoundCloud
mixes. The importer is safe to rerun; existing tracks are ignored. Use
`--add-missing` when a MixesDB page exists but the upload did not appear in the
SoundCloud archive response.

Enrich tracks with confirmed Beatport pages in a visible browser:

```sh
python3 -m uv run crate-digger enrich-beatport-assisted --limit 10
```

The command opens Beatport searches one track at a time. Navigate to the exact
track page, return to the terminal, and press Enter to save BPM, key, genre,
label, release metadata, and the confirmed Beatport URL when the page exposes
those fields.

By default, it uses Beatport's visible search UI instead of jumping straight to
query-param URLs. That keeps the flow closer to a normal manual session. If the
search control cannot be found automatically, the command asks you to click into
Beatport's search box so it can type and submit the query through the focused
field.

For an even more manual start, let the browser open blank and navigate to
Beatport yourself before the script touches anything:

```sh
python3 -m uv run crate-digger enrich-beatport-assisted --mixtape-id 637 --manual-start
```

You can also attach to a real Chrome instance that you launch with a debugging
port:

```sh
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.crate-digger-chrome-profile"

python3 -m uv run crate-digger enrich-beatport-assisted \
  --mixtape-id 637 \
  --manual-start \
  --cdp-url http://127.0.0.1:9222 \
  --auto-first-result
```

If the right match is visible in Beatport search results, use numbered result
selection instead of opening a track page. The closest row is selected
automatically when title and artist matching produce one clear winner; add
`--manual-result-choice` to pick the row yourself.

```sh
python3 -m uv run crate-digger enrich-beatport-assisted \
  --track-id 5100 \
  --manual-start \
  --cdp-url http://127.0.0.1:9222 \
  --choose-result
```

Useful filters:

```sh
python3 -m uv run crate-digger enrich-beatport-assisted --series "XXX Radio" --limit 5
python3 -m uv run crate-digger enrich-beatport-assisted --mixtape-id 12
python3 -m uv run crate-digger enrich-beatport-assisted --track-id 345
```

If Beatport challenges the automated browser, use the normal-browser manual
flow. It copies each search query to your clipboard, then asks you to paste the
confirmed Beatport track URL:

```sh
python3 -m uv run crate-digger enrich-beatport-manual --mixtape-id 637 --limit 3
```

Use `--url-only` when you only want to save confirmed Beatport links without
typing BPM, key, genre, label, or release fields.

## Tracklist Text Format

The parser accepts common tracklist lines:

```text
01. [00:00] Artist One - First Track
02. [04:35] Artist Two - Second Track
03. Unknown ID
```

Lines beginning with `#` are ignored. URLs are skipped.

## Next Ideas

- Add a tiny web UI for browsing by month, artist, and track.
- Add CSV export for Rekordbox, Serato notes, or personal archives.
- Add optional MusicBrainz/Discogs lookup for normalized artist/title metadata.
- Add a review queue for unknown IDs and duplicate track detection.
