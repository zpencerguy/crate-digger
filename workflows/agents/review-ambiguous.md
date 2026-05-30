# Agent: Review Ambiguous

## Purpose

Compress uncertain Beatport or tracklist decisions into one human review step.

## Inputs

| Name | Required | Description |
| --- | --- | --- |
| `mixtape_id` | Yes | Target mixtape id. |
| `ambiguous` | Yes | List from previous agents. |
| `skipped` | No | Tracks skipped because no safe match was found. |

## Rules

- Present a compact table.
- Ask for one batch decision when possible.
- Do not continue asking per track unless the human explicitly wants that.
- Do not export, commit, or push.

## Review Table Format

| Track | Suggested Match | Reason | Recommended Action |
| --- | --- | --- | --- |
| `Artist - Title` | Beatport URL or candidate number | Multiple close versions | Accept / choose other / skip |

## Output Contract

```json
{
  "agent": "review-ambiguous",
  "status": "complete",
  "accepted": [],
  "rejected": [],
  "still_skipped": [],
  "notes": []
}
```

## Handoff

Return to the main invoker. The next agent is usually `export-and-sync`.

