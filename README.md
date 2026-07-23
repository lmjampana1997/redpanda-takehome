# Wikipedia Recent-Changes Edit Classifier

A local pipeline that ingests the Wikipedia recent-changes SSE stream, enriches
each edit with its real diff via Redpanda Connect, runs a multi-step LLM
reasoning loop to classify each edit (vandalism / substantive / trivia /
unclear), stores results in Postgres, and serves them for review.

## Architecture

```
Wikipedia SSE stream
      |
      v
Redpanda Connect (filter: bots/namespaces/heartbeats; enrich: diff fetch)
      |
      v
Redpanda topic: wiki.edits.enriched
      |
      v
Reasoning service (Python, consumer)
  1. extraction + comment/diff mismatch check (LLM)
  2. classification (LLM)
  3. confidence-gated escalation (LLM, deeper context)
  4. output parsing / retry / fallback
  5. UPSERT into Postgres, route flagged items to review
      |
      v
Postgres  --->  Serve layer (web/JSON)
```

## Status

Built incrementally, milestone by milestone. See commit history for progress.

- [x] Milestone 1: Repo scaffold
- [ ] Milestone 2: Redpanda + Connect SSE ingestion & filtering
- [ ] Milestone 3: Connect enrichment branch (diff fetch)
- [ ] Milestone 4: Postgres schema + migration
- [ ] Milestone 5: Reasoning service skeleton (consumer)
- [ ] Milestone 6: Extraction + mismatch check LLM call
- [ ] Milestone 7: Classification call
- [ ] Milestone 8: Confidence-gated escalation
- [ ] Milestone 9: Postgres write path + review routing
- [ ] Milestone 10: Test on parse/retry/branching logic
- [ ] Milestone 11: Serve layer
- [ ] Milestone 12: Full docker-compose wiring end to end
- [ ] Milestone 13: Finish run instructions

## Setup & running

<!-- Filled in as milestones land; finalized in milestone 13. -->

TODO (milestone 13)

## Configuration

Copy `.env.example` to `.env`. Defaults use local Ollama (no API keys needed).
To use a hosted LLM instead, set `LLM_PROVIDER=anthropic` or
`LLM_PROVIDER=openai` and the corresponding API key — see `.env.example`.

## Tradeoffs

TODO — written by hand, not by the assistant.

## What surprised you

TODO — written by hand, not by the assistant.

## Production failure modes

TODO — written by hand, not by the assistant.

## Why this matters

TODO — written by hand, not by the assistant.
