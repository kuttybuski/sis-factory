# context-api

Hybrid retrieval and `CONTEXT.md` packs for agent sandboxes. See `docs/context-and-learning.md`.

`POST /v1/pack`, `POST /v1/search`, `POST /v1/similar-questions`, `GET /v1/sources|quarantine|gaps|usage/{task}`.
Reads the index in Postgres; fetches approved lessons from `portal-api` (and degrades to no rules if it is down).
