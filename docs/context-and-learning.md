# Context and learning

How the agent gets the right context, and how the system gets better. See `components/injection-guard/README.md` for the safety layer that sits under all of it.

## The context pipeline

```
Confluence spaces ─┐                              ┌─ POST /v1/pack  ──► CONTEXT.md (rules + cited, wrapped data)
GitHub merged PRs ─┤                              ├─ POST /v1/search
Repo docs ─────────┼─► context-ingest ─► Postgres ┤
Answered questions ┤   (screen, chunk, embed)  +   └─ POST /v1/similar-questions ──► reuse an earlier answer
Pushed documents ──┘                      pgvector
                          embeddings (local model, or any OpenAI-compatible provider)
```

| Deployable | Role |
|---|---|
| `context-ingest` | Pulls from each source on a schedule (`SIS_CONTEXT_SYNC_MINUTES`) or on demand; only component holding Confluence/GitHub credentials (read-only is enough). Also accepts pushed documents (`POST /v1/ingest/docs`) |
| `embeddings` | OpenAI-compatible `/v1/embeddings`; local ONNX model by default |
| `context-api` | Hybrid search (vector + keyword, fused by rank), packs, similar questions, usage and gap records |
| `libs/context` | Shared code: chunking, screening, ranking, connectors, storage |

Set up a team (spaces and repos), then sync:

```bash
curl -X PUT localhost:8000/api/teams -H "Authorization: Bearer $SIS_INGEST_TOKEN" -H 'content-type: application/json' \
  -d '{"name":"Payments","jira_projects":["PAY"],"confluence_spaces":["PAY"],"repos":["acme/payments-api"]}'
```

Open the dashboard's **Context** page and press *Sync everything now* (or wait for the schedule).

### What gets indexed

- **Confluence**: only the spaces you list per team. Pages are split by heading; every chunk keeps its breadcrumb. Deleted pages are removed on the next complete sync (a failed or partial sync never deletes anything).
- **GitHub**: the last `SIS_PR_HISTORY_COUNT` (100) merged PRs per repo: summary, review threads, discussion, and the biggest real diffs (lockfiles and build output skipped). PRs that touched the same files as a new task rank higher. Plus README, `AGENTS.md`, `CLAUDE.md`, CONTRIBUTING, ADRs, CODEOWNERS and `docs/**/*.md`.
- **Answered questions**: every question an agent asked and the human answered, so the next task can reuse the answer.
- **Anything else**: push it with `POST /v1/ingest/docs` (runbooks, incident reports, exported tickets).

### Safety and access

- Every chunk is scanned by `injection-guard` before indexing. Secrets are redacted; text that looks like an instruction to the agent is **quarantined** (shown on the Context page, never indexed). Wiki pages and pushed documents get the strict profile; repo docs the semi-trusted one.
- Retrieval is scoped: a task only sees documents owned by its team or repo, or documents with no owner. Disabled sources disappear from search.
- In a pack, rules from approved lessons are the only trusted text. Everything retrieved is wrapped in `<untrusted>` blocks with a per-call random marker and a citation label.
- Flagged chunks are left out of packs unless `include_flagged` is set.

### Embeddings without internet

The first start downloads the model from huggingface.co. On networks that block Hugging Face's file hosts (it happened while building this), choose one:

1. **Pre-load the weights**: download the model on an unrestricted machine, mount the directory, set `EMBEDDINGS_MODEL_PATH`.
2. **Hosted provider**: point `EMBEDDINGS_URL`, `EMBEDDINGS_MODEL` and `EMBEDDINGS_API_KEY` at any OpenAI-compatible embeddings API (note: ticket and wiki text then leaves the cluster).
3. **Offline fallback**: `EMBEDDINGS_FALLBACK=hash` keeps everything working but only matches shared words, not meaning. `/healthz` reports `degraded: true`.

Each chunk records the model that produced it; changing the model re-indexes on the next sync.

## Learning

| Mechanism | How it works | Status |
|---|---|---|
| `@sis remember <rule>` / `@sis forget L12` | Lessons with scope (path, repo, team, global), expiry and role-based approval; delivered as a trusted "Team rules" block | Built end to end: `jira-connector` parses the command, applies the author's role and answers in Jira |
| Answered-question reuse | Indexed from task history; `POST /v1/similar-questions` returns an earlier answer above `SIS_QA_REUSE_SIM` | Built: `sandbox-gateway` checks for a similar earlier answer before any question goes to Jira |
| Review-feedback mining | The same reviewer remark on `SIS_REVIEW_MIN_COUNT` different PRs becomes a candidate lesson (always reviewed by a human) | Built (runs during PR sync) |
| Weekly retro | Counts outcomes, groups common agent errors, gives advice | Built (Learning page) |
| Budget suggestions | p90 cost of tasks that opened a PR, plus headroom, per team | Built (suggestions only) |
| Documentation gaps | Tasks whose topic found nothing relevant | Built (Context page) |
| Eval gate for global lessons | `SIS_LESSON_GLOBAL_REQUIRES_EVAL=true` blocks approval until a passing replay report is recorded | Gate built; the replay harness is not |

Not built: the replay/eval harness itself, cross-task playbooks, reranking with a cross-encoder, query rewriting with an LLM, and a login for the dashboard (approve/reject/sync buttons are open on localhost).
