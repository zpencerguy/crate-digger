# Agent: Find Tracklist Source

## Purpose

Find the best usable tracklist source for a target mixtape.

## Inputs

| Name | Required | Description |
| --- | --- | --- |
| `mixtape_id` | Yes | Existing mixtape id. |
| `title` | Yes | Mixtape title from SQLite. |
| `series` | No | Series name. |
| `soundcloud_url` | Yes | Indexed SoundCloud URL. |
| `tracklist_url` | No | Existing candidate URL, if already known. |

## Source Priority

1. Existing `tracklist_url` in SQLite.
2. SoundCloud description URL captured by `crate-digger add`.
3. 1001Tracklists normal public search result.
4. Human-provided URL.

MixesDB is not part of the default source discovery path. Use it only when the
human explicitly asks for an ad hoc fallback or provides a specific MixesDB URL.

## Commands

Inspect the current mixtape:

```sh
python3 -m uv run crate-digger show <mixtape_id>
```

Use 1001Tracklists normal site search and respect robots.txt and crawl delay.
Return the URL only; import happens in the next agent.

## Rules

- Respect robots.txt and crawl delays.
- Do not bypass challenges.
- Do not import tracks.
- Do not enrich Beatport metadata.
- Do not commit or push.
- Prefer high-confidence exact title/month/year matches.

## Success Criteria

- A likely source URL is found, or source discovery returns a clear `not_found`
  status with the searches attempted.

## Output Contract

```json
{
  "agent": "find-tracklist-source",
  "status": "complete",
  "source": "1001tracklists",
  "tracklist_url": "https://www.1001tracklists.com/tracklist/example.html",
  "confidence": "high",
  "human_request_needed": false,
  "notes": []
}
```

If no source is found:

```json
{
  "agent": "find-tracklist-source",
  "status": "not_found",
  "source": null,
  "tracklist_url": null,
  "confidence": "none",
  "human_request_needed": true,
  "notes": ["Ask human for a tracklist URL or copied tracklist text."]
}
```

## Handoff

Return to the main invoker. If a URL was found, the next agent is
`import-tracklist`.
