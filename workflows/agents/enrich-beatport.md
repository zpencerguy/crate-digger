# Agent: Enrich Beatport

## Purpose

Use a human-observed browser flow to save Beatport metadata for tracks in a
target mixtape.

## Inputs

| Name | Required | Default |
| --- | --- | --- |
| `mixtape_id` | Yes | none |
| `cdp_url` | No | `http://127.0.0.1:9222` |
| `limit` | No | `50` |
| `auto_choose` | No | `true` |
| `review_ambiguous_at_end` | No | `true` |

## Command

```sh
python3 -m uv run crate-digger enrich-beatport-assisted \
  --mixtape-id <mixtape_id> \
  --limit 50 \
  --manual-start \
  --cdp-url http://127.0.0.1:9222 \
  --choose-result
```

## Rules

- Save high-confidence search result matches.
- For ambiguous matches, prefer queueing them for review over repeatedly
  interrupting the human.
- If the current CLI prompts for a choice, select only when the result is
  clearly correct from artist, title, remix/version, label, and release date.
- Skip weak matches instead of saving bad metadata.
- Do not export, commit, or push.

## Match Confidence Guidance

| Confidence | Criteria | Action |
| --- | --- | --- |
| High | Artist and title match, remix/version is compatible, release date is plausible. | Save. |
| Medium | Track is likely correct but multiple versions exist, such as edit vs extended. | Queue or choose with note. |
| Low | Artist/title mismatch, stale search results, or unrelated catalog results. | Reread once, then skip/queue. |

## Success Criteria

- All high-confidence tracks have Beatport metadata.
- Ambiguous or skipped tracks are listed with reasons.

## Output Contract

```json
{
  "agent": "enrich-beatport",
  "status": "complete",
  "mixtape_id": 639,
  "saved": 14,
  "skipped": 0,
  "ambiguous": [],
  "notes": []
}
```

If ambiguity remains:

```json
{
  "agent": "enrich-beatport",
  "status": "needs_review",
  "mixtape_id": 639,
  "saved": 12,
  "skipped": 0,
  "ambiguous": [
    {
      "track": "Artist - Title",
      "suggested_match": "https://www.beatport.com/track/example/123",
      "reason": "Multiple close remix versions."
    }
  ],
  "notes": []
}
```

## Handoff

Return to the main invoker. If ambiguity exists, the next agent is
`review-ambiguous`; otherwise continue to `export-and-sync`.

