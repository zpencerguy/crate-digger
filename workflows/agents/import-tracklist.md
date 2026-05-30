# Agent: Import Tracklist

## Purpose

Import track rows into SQLite for a target mixtape.

## Inputs

| Name | Required | Description |
| --- | --- | --- |
| `mixtape_id` | Yes | Existing mixtape id. |
| `tracklist_url` | No | 1001Tracklists, MixesDB, or other source URL. |
| `tracklist_file` | No | Local copied tracklist text file. |
| `replace` | No | Defaults to true for an empty or intentionally refreshed mixtape. |

## Commands

For 1001Tracklists static HTML:

```sh
python3 -m uv run crate-digger import-1001-tracklist <mixtape_id> "<tracklist_url>" --replace
```

For 1001Tracklists rendered pages:

```sh
python3 -m uv run crate-digger import-1001-assisted <mixtape_id> "<tracklist_url>" \
  --replace \
  --auto-read
```

For copied text:

```sh
python3 -m uv run crate-digger import-tracklist <mixtape_id> tracklists/<file>.txt \
  --replace \
  --tracklist-url "<tracklist_url>"
```

Verify:

```sh
python3 -m uv run crate-digger show <mixtape_id>
```

## Rules

- Try the least interactive import first.
- If static 1001 HTML contains no rows, use assisted rendered import.
- Do not run Beatport enrichment.
- Do not export, commit, or push.
- If import fails because the visible page requires human action, return a
  blocked status with the exact action needed.

## Success Criteria

- `crate-digger show <mixtape_id>` lists one or more tracks.
- The mixtape has a recorded tracklist source when available.

## Output Contract

```json
{
  "agent": "import-tracklist",
  "status": "complete",
  "mixtape_id": 639,
  "tracklist_url": "https://www.1001tracklists.com/tracklist/example.html",
  "tracks_imported": 14,
  "tracks_total": 14,
  "notes": []
}
```

## Handoff

Return to the main invoker. The next agent is usually `enrich-beatport`.

