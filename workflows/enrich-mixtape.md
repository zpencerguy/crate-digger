# Workflow: Enrich Mixtape

## Goal

Given a mixtape indexed by GitHub Actions, import missing track data, enrich
tracks with Beatport metadata, export GitHub-friendly data files, and present a
small final summary for human approval.

## Inputs

| Name | Required | Description |
| --- | --- | --- |
| `mixtape_id` | Usually | Existing SQLite mixtape id. Optional only when `discover_latest` is true. |
| `series` | No | Optional series filter such as `Only 100s`, `XXX Radio`, `ERA`, or `Magic Tape`. |
| `tracklist_url` | No | Known 1001Tracklists, MixesDB, or other source URL. |
| `discover_latest` | No | If true, inspect recent indexed mixtapes and pick the newest enrichment candidate. |
| `cdp_url` | No | Defaults to `http://127.0.0.1:9222`. |
| `auto_push` | No | Defaults to false. Main invoker should ask before pushing unless explicitly enabled. |

## Human Request Budget

Aim for two or three human requests.

| Request | When |
| --- | --- |
| Browser/source readiness | Only if Chrome CDP is not ready, Beatport needs manual readiness, or no tracklist source is found. |
| Ambiguous match review | Only if Beatport candidates are unclear. |
| Final approval | Before committing or pushing generated data. |

## Main Invoker Responsibilities

1. Pull latest `main`.
2. Identify the target mixtape.
3. Inspect current track and metadata counts.
4. Delegate only bounded tasks to sub-agents.
5. Merge sub-agent results into one state summary.
6. Ask the human only when blocked, when review is meaningful, or before final sync.
7. Commit and push only after final approval.

## Sub-Agent Order

| Step | Agent | Skip Rule |
| ---: | --- | --- |
| 1 | Main invoker | Never skipped. Pull and identify target. |
| 2 | [browser-setup](agents/browser-setup.md) | Skip if no browser-based import/enrichment is needed. |
| 3 | [find-tracklist-source](agents/find-tracklist-source.md) | Skip if target already has tracks or a trusted source URL is provided. |
| 4 | [import-tracklist](agents/import-tracklist.md) | Skip if target already has tracks. |
| 5 | [enrich-beatport](agents/enrich-beatport.md) | Skip if all tracks already have Beatport metadata. |
| 6 | [review-ambiguous](agents/review-ambiguous.md) | Skip if no ambiguous or skipped Beatport matches. |
| 7 | [export-and-sync](agents/export-and-sync.md) | Never skipped after data changes. |

## Decision Tree

| Question | Yes | No |
| --- | --- | --- |
| Does `crate-digger show <mixtape_id>` list tracks? | Go to Beatport enrichment. | Find/import a tracklist first. |
| Is a tracklist URL already known? | Import it. | Delegate source discovery. |
| Does static 1001 import find rows? | Continue to enrichment. | Use assisted rendered import. |
| Are Beatport matches high-confidence? | Save automatically. | Queue for review. |
| Did exported files change? | Summarize and ask for final approval. | Report no sync needed. |

## Canonical Commands

```sh
git pull origin main

python3 -m uv run crate-digger list --desc --limit 20
python3 -m uv run crate-digger show <mixtape_id>

python3 -m uv run crate-digger import-1001-assisted <mixtape_id> "<tracklist_url>" \
  --replace \
  --auto-read

python3 -m uv run crate-digger enrich-beatport-assisted \
  --mixtape-id <mixtape_id> \
  --limit 50 \
  --manual-start \
  --cdp-url http://127.0.0.1:9222 \
  --choose-result

python3 -m uv run crate-digger export --output data

git status --short
git diff --stat
```

## State Summary Contract

The main invoker should keep this summary current as agents complete:

```json
{
  "mixtape_id": null,
  "title": null,
  "series": null,
  "soundcloud_url": null,
  "tracklist_url": null,
  "tracks_before": 0,
  "tracks_after": 0,
  "beatport_before": 0,
  "beatport_after": 0,
  "ambiguous_matches": [],
  "skipped_tracks": [],
  "files_changed": [],
  "ready_for_approval": false
}
```

## Done Criteria

- Target mixtape is identified.
- Tracklist is imported or confirmed already present.
- Beatport metadata is saved for every high-confidence match.
- Ambiguous or skipped tracks are summarized in one place.
- `data/` exports are refreshed when data changed.
- Human has a concise final summary before commit/push.

