# Agent: Browser Setup

## Purpose

Ensure a developer-enabled Chrome session is available for browser-assisted
imports and Beatport metadata enrichment.

## Inputs

| Name | Required | Default |
| --- | --- | --- |
| `cdp_url` | No | `http://127.0.0.1:9222` |
| `needs_beatport` | No | `true` |

## Commands

Check whether Chrome DevTools Protocol is reachable:

```sh
curl http://127.0.0.1:9222/json/version
```

If it is not reachable, ask the human to open developer-enabled Chrome or open
it with:

```sh
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.crate-digger-chrome-profile"
```

If Beatport is needed, ask the human to confirm Beatport is loaded only once.

## Rules

- Do not run tracklist imports.
- Do not run Beatport enrichment.
- Do not commit or push.
- Return quickly once the browser is ready.

## Success Criteria

- `cdp_url` responds, or the human has been asked for the one browser setup
  action needed.
- The next agent can attach to the browser.

## Output Contract

```json
{
  "agent": "browser-setup",
  "status": "complete",
  "cdp_url": "http://127.0.0.1:9222",
  "browser_ready": true,
  "human_request_used": false,
  "notes": []
}
```

## Handoff

Return to the main invoker. The next likely agent is
`find-tracklist-source`, `import-tracklist`, or `enrich-beatport`.

