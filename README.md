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
- [x] Milestone 2: Redpanda + Connect SSE ingestion & filtering
- [x] Milestone 3: Connect enrichment branch (diff fetch)
- [x] Milestone 4: Postgres schema + migration
- [x] Milestone 5: Reasoning service skeleton (consumer)
- [x] Milestone 6: Extraction + mismatch check LLM call
- [x] Milestone 7: Classification call
- [x] Milestone 8: Confidence-gated escalation
- [x] Milestone 9: Postgres write path + review routing
- [x] Milestone 10: Test on parse/retry/branching logic
- [x] Milestone 11: Serve layer
- [x] Milestone 12: Full docker-compose wiring end to end
- [x] Milestone 13: Finish run instructions

## Setup & running

### Prerequisites
- Docker Desktop Running

### First-time setup
```
bash
cp .env.example .env
```
`.env.example` already has a working `WIKI_USER_AGENT` in it. Since Wikipedia blocks requests that don't seem like they're coming from a real person, the header is needed to prevent 403 errors. You can change the email if you want it to be yours.

### Run it
```
bash
docker compose up -d --build
```
Starts 6 containers: `redpanda`, `ollama`, `connect`, `postgres`, `reasoning_service`, `serve_layer`.

### Verify it's working
```
bash
docker compose logs reasoning_service -f
```
Look for `extraction_ok=...`, `classification_ok=... label=... confidence=...`, and `upserted status=...` lines.

```
bash
docker compose exec postgres psql -U wiki -d wiki_edits -c \
  "SELECT revision_id, article_title, label, confidence, status FROM edit_classifications ORDER BY updated_at DESC LIMIT 10;"
```

Web page: `http://localhost:8080/` 
JSON: `curl http://localhost:8080/api/classifications?status=review`

### Clean restart
```
bash
docker compose down -v   # wipes Postgres data + Ollama model cache too
docker compose up -d --build
```

## Tradeoffs
**Local 1B model as the default, no API key needed.** Running `docker compose up` works with a small free model. That being said, it's not great at following instructions. I notcied this especially during testing when it sometimes copied the placeholder text from the JSON schema instead of writing real content. It almost always sputs out the same 0.90 confidence score no matter what its looking at. I will say that the code does not crash when this happens, it fauls safely. Swapping in a hosted model like Anthropic would probably have better results, but that requires an API key.

**Nothing is running in parallel - only one worker** There's a single process which is handling records one at a time. Each record needs 1 to 3 calls to the LLM. Wiki produces edits faster than this process can handle them. This means the backlog just grows. For example, when testing, it was about 200 messages behind in the first 15 minutes. This is obviously fine for a demo, but if it were to scale we would need multiple workers running asynchronously. 

**The 0.6 confidence cutoff** The rule that decides when to escalate and edit for a closer look has nevver been checked against real examples.

**No connection pooling for the database** Every request opens a brand new Postgres connection from scratch. This is again, fine for the demo, but needs to be optimized for a real load of requests. 

**Every edit that survives filtering gets its diff pulled, no exceptions** Aside from filtering out bots, non-article pages, and heartbeat messages, everything else triggers a Wikipedia API call to fetch the diff. This step becomes a bottleneck at full Wiki volume

**No way to see when things are failing** If extraction or classification keeps failing on a record, it just gets labeled as `unclear` and the raw model output gets logged somewhere. 

## What surprised you

Pulling a model with Ollama's `/api/pull` doesn't mean it's actually loaded and ready. Getting the weights onto disk is a separate step from loading them into the runtime. Also, the slowest part is whichever request happens to go first. My first version didn't account for this and crashed on the very first real record beacuse of that cold start. 

Kafka's consumer liveness setting matter for a reason I did not expect. `max.poll.interval.ms` defaults to 5 minutes. This is normally more than enough. But a single record could mean extraction, classification, escalation, with its own retry, and 120 timeout, that limit does not suffice. 

Local CPU inference is way less predictable than I expected. The same mode and same prompt took anywhere from about 13 seconds to over 10 minuytes depending on what else was running on the machine at that time. 

## Why this matters

Wikipedia stands in for any product with a stream of user-generated changes that no small team can read one by one: internal wikis, documentation platforms, community-edited knowledge bases. A trust and safety lead or content ops manager uses this to know which edits actually need a human look, not just which ones changed the most text. Get the classification wrong and one of two costs shows up: vandalism sits live because it got waved through as trivial, or a reviewer burns their day on harmless typo fixes that never needed a human at all. Either way the cost isn't abstract. It's bad information staying public longer than it should, or a team's limited attention spent on the wrong edits out of a thousand.