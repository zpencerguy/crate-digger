# Agent: Export And Sync

## Purpose

Export the local SQLite state to tracked `data/` files and prepare a final
summary for human approval.

## Inputs

| Name | Required | Description |
| --- | --- | --- |
| `mixtape_id` | Yes | Target mixtape id. |
| `commit_message` | No | Suggested commit message. |
| `auto_push` | No | Defaults to false. |

## Commands

```sh
python3 -m uv run crate-digger export --output data

python3 -m uv run crate-digger show <mixtape_id>
git status --short
git diff --stat
```

After human approval, the main invoker can run:

```sh
git add data
git commit -m "Enrich <mixtape name>"
git push origin main
```

## Rules

- This agent may export tracked files.
- This agent should not commit or push unless the workflow explicitly grants
  that action and the human has already approved it.
- Summarize changed files and data counts.
- Call out any skipped tracks or residual uncertainty.

## Success Criteria

- `data/` reflects the current SQLite database.
- Final approval summary is ready.
- No hidden untracked work is mixed into the proposed sync.

## Output Contract

```json
{
  "agent": "export-and-sync",
  "status": "ready_for_approval",
  "mixtape_id": 639,
  "files_changed": [
    "data/latest-mixtapes.md",
    "data/tracks.json",
    "data/track_metadata.json"
  ],
  "diff_stat": "8 files changed",
  "suggested_commit_message": "Enrich Only 100s May 2026",
  "notes": []
}
```

## Handoff

Return to the main invoker for final human approval, commit, and push.

