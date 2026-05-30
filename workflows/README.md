# Workflow Runbooks

This directory defines repeatable, markdown-driven workflows for Crate Digger.
The first workflow covers the local data-driven update that happens after
GitHub Actions indexes new SoundCloud uploads.

These files are written for a main invoker agent that can delegate bounded work
to sub-agents. Each sub-agent file describes its purpose, inputs, commands,
success criteria, output contract, and handoff rules.

## Available Workflows

| Workflow | Purpose |
| --- | --- |
| [enrich-mixtape.md](enrich-mixtape.md) | Pull new indexed data, import a tracklist if needed, enrich with Beatport metadata, export reviewable data, and prepare a final approval summary. |

## Agent Instructions

| Agent | Purpose |
| --- | --- |
| [agents/browser-setup.md](agents/browser-setup.md) | Ensure a developer-enabled Chrome session is available through CDP. |
| [agents/find-tracklist-source.md](agents/find-tracklist-source.md) | Find the best source URL for a mixtape tracklist. |
| [agents/import-tracklist.md](agents/import-tracklist.md) | Import tracks from MixesDB, 1001Tracklists, or copied text. |
| [agents/enrich-beatport.md](agents/enrich-beatport.md) | Enrich tracks with Beatport metadata through a human-observed browser flow. |
| [agents/review-ambiguous.md](agents/review-ambiguous.md) | Collect uncertain matches into a compact human review. |
| [agents/export-and-sync.md](agents/export-and-sync.md) | Export tracked data files and prepare commit/push instructions for final approval. |

## Operating Principles

- Keep the main invoker responsible for sequencing, final validation, and user
  communication.
- Give each sub-agent one bounded job and one clear output contract.
- Prefer batching uncertainty into one review step instead of interrupting the
  human for every track.
- Do not let sub-agents commit, push, or make broad unrelated edits.
- Use the repo's existing CLI commands and storage model.
- Respect source site rules, robots.txt, crawl delays, and visible human review
  flows.

