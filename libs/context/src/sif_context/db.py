"""Storage and retrieval for the context index (Postgres + pgvector), and the pool and migration helpers every Postgres service uses."""

import asyncio
from collections.abc import AsyncIterator
from importlib import resources
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from sif_context.embed import Embedder, vector_literal
from sif_context.models import DocIn, Hit
from sif_context.rank import boost, dedupe_per_doc, keyword_query, rrf
from sif_context.safety import screen


def make_pool(url: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(url, open=False, kwargs={"row_factory": dict_row}, min_size=1, max_size=10)


async def migrate(pool: AsyncConnectionPool, package: str = "sif_context.migrations") -> None:
    """Applies the package's *.sql files that have not run yet, in name order, each in its own transaction."""
    files = sorted(f for f in resources.files(package).iterdir() if f.name.endswith(".sql"))
    async with pool.connection() as conn:
        await conn.execute("create table if not exists schema_migrations (name text primary key)")
        done = {r["name"] for r in await (await conn.execute("select name from schema_migrations")).fetchall()}
        for f in files:
            if f.name not in done:
                async with conn.transaction():
                    await conn.execute(f.read_text())
                    await conn.execute("insert into schema_migrations (name) values (%s)", (f.name,))


async def open_pool(url: str, package: str = "sif_context.migrations") -> AsyncConnectionPool:
    """An open, migrated pool. Waits up to 30 seconds for Postgres, which may still be starting with the stack."""
    pool = make_pool(url)
    await pool.open()
    for attempt in range(30):
        try:
            await migrate(pool, package)
            return pool
        except Exception:
            if attempt == 29:
                raise
            await asyncio.sleep(1)
    return pool


# ------------------------------------------------------------------------------------- sources
async def upsert_source(pool: AsyncConnectionPool, *, kind: str, key: str, team: str | None = None, repo: str | None = None,
                        config: dict[str, Any] | None = None) -> dict[str, Any]:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "insert into context_sources (kind, key, team, repo, config) values (%s,%s,%s,%s,%s) "
            "on conflict (kind, key) do update set team = excluded.team, repo = excluded.repo, config = excluded.config returning *",
            (kind, key, team, repo, Jsonb(config or {})))).fetchone()


async def list_sources(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "select s.*, (select count(*) from context_docs d where d.source_id = s.id)::int as docs, "
            "(select count(*) from context_chunks c join context_docs d on d.id = c.doc_id where d.source_id = s.id)::int as chunks, "
            "(select count(*) from context_quarantine q where q.source_id = s.id)::int as quarantined "
            "from context_sources s order by s.kind, s.key")).fetchall()


async def get_source(pool: AsyncConnectionPool, source_id: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from context_sources where id = %s", (source_id,))).fetchone()


# ------------------------------------------------------------------------------------- sync
class SyncStats(BaseModel):
    docs: int = 0
    new_or_changed: int = 0
    unchanged: int = 0
    deleted: int = 0
    chunks: int = 0
    quarantined: int = 0
    redactions: int = 0
    proposed_lessons: int = 0


async def sync_docs(pool: AsyncConnectionPool, source_id: int, docs: AsyncIterator[DocIn], embedder: Embedder, *, complete: bool = True) -> SyncStats:
    """Indexes docs for a source. When `complete` is true, documents no longer returned are removed (deleted pages).

    Embedding happens before any write for a doc, so an embeddings outage leaves the previous version of the doc in place."""
    stats, seen = SyncStats(), set()
    await embedder.embed(["warm up"])  # learns the model name actually in use, so unchanged-doc checks compare the right tag
    async for doc in docs:
        seen.add(doc.external_id)
        stats.docs += 1
        async with pool.connection() as conn:
            row = await (await conn.execute(
                "select d.id, d.content_hash, bool_and(c.embedding_model is not distinct from %s) as same_model from context_docs d "
                "left join context_chunks c on c.doc_id = d.id where d.source_id = %s and d.external_id = %s group by d.id",
                (embedder.model, source_id, doc.external_id))).fetchone()
        if row and row["content_hash"] == doc.content_hash and row["same_model"] is not False:
            stats.unchanged += 1
            continue

        screened = screen(doc.chunks, doc.kind)
        stats.quarantined += len(screened.quarantined)
        stats.redactions += screened.redactions
        vectors = await embedder.embed([c.embed_text for c in screened.kept]) if screened.kept else []

        async with pool.connection() as conn, conn.transaction():
            d = await (await conn.execute(
                "insert into context_docs (source_id, external_id, kind, title, url, version, content_hash, team, repo, path_hint, meta, indexed_at) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) on conflict (source_id, external_id) do update set kind = excluded.kind, "
                "title = excluded.title, url = excluded.url, version = excluded.version, content_hash = excluded.content_hash, team = excluded.team, "
                "repo = excluded.repo, path_hint = excluded.path_hint, meta = excluded.meta, indexed_at = now() returning id",
                (source_id, doc.external_id, doc.kind, doc.title, doc.url, doc.version, doc.content_hash, doc.team, doc.repo, doc.path_hint,
                 Jsonb(doc.meta)))).fetchone()
            await conn.execute("delete from context_chunks where doc_id = %s", (d["id"],))
            await conn.execute("delete from context_quarantine where source_id = %s and external_id = %s", (source_id, doc.external_id))
            for i, (chunk, vec) in enumerate(zip(screened.kept, vectors)):
                await conn.execute(
                    "insert into context_chunks (doc_id, ord, heading_path, text, kind, tokens, flagged, embedding, embedding_model) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)",
                    (d["id"], i, chunk.heading_path, chunk.text, chunk.kind, chunk.tokens, chunk.flagged, vector_literal(vec), embedder.model))
            for chunk, rules in screened.quarantined:
                await conn.execute("insert into context_quarantine (source_id, external_id, title, rules, excerpt) values (%s,%s,%s,%s,%s)",
                                   (source_id, doc.external_id, doc.title, rules, chunk.text[:120]))
        stats.new_or_changed += 1
        stats.chunks += len(screened.kept)

    if complete:
        async with pool.connection() as conn:
            cur = await conn.execute("delete from context_docs where source_id = %s and not (external_id = any(%s))", (source_id, list(seen)))
            stats.deleted = cur.rowcount
    return stats


async def record_sync(pool: AsyncConnectionPool, source_id: int, *, ok: bool, error: str | None, stats: SyncStats | None) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update context_sources set last_sync_at = now(), last_status = %s, last_error = %s, stats = case when %s then %s else stats end where id = %s",
            ("ok" if ok else "error", error, stats is not None, Jsonb(stats.model_dump() if stats else {}), source_id))


# ------------------------------------------------------------------------------------- search
class SearchCtx(BaseModel):
    team: str | None = None
    repo: str | None = None
    paths: list[str] = []
    kinds: list[str] = []  # doc kinds: page, pr, doc (repo docs), note (pushed), qa; empty = all


_ACCESS = "(d.team = %(team)s or d.repo = %(repo)s or (d.team is null and d.repo is null)) and exists (select 1 from context_sources s where s.id = d.source_id and s.enabled)"
_HIT_COLS = ("c.id as chunk_id, c.doc_id, d.kind, d.title, d.url, c.heading_path, c.text, c.flagged, d.path_hint, d.meta")


async def search(pool: AsyncConnectionPool, embedder: Embedder, query: str, ctx: SearchCtx, *, k: int = 8, pool_size: int = 40) -> list[Hit]:
    """Hybrid search: vector similarity plus keyword match, fused by rank, boosted for PRs near the task's files.

    Only documents the task's team or repo may see (or documents with no owner) are searched."""
    qvec = vector_literal((await embedder.embed([query]))[0])
    kinds = ctx.kinds or ["page", "pr", "doc", "note", "qa"]
    params = {"team": ctx.team, "repo": ctx.repo, "kinds": kinds, "model": embedder.model, "qvec": qvec, "n": pool_size}
    async with pool.connection() as conn:
        vec = await (await conn.execute(
            f"select c.id, 1 - (c.embedding <=> %(qvec)s::vector) as sim from context_chunks c join context_docs d on d.id = c.doc_id "
            f"where {_ACCESS} and d.kind = any(%(kinds)s) and c.embedding_model = %(model)s "
            f"order by c.embedding <=> %(qvec)s::vector limit %(n)s", params)).fetchall()
        kw_text = keyword_query(query)
        kw = await (await conn.execute(
            f"select c.id, ts_rank_cd(c.tsv, q) as rank from context_chunks c join context_docs d on d.id = c.doc_id, to_tsquery('english', %(kw)s) q "
            f"where {_ACCESS} and d.kind = any(%(kinds)s) and c.tsv @@ q order by rank desc limit %(n)s", {**params, "kw": kw_text})).fetchall() if kw_text else []
        fused = rrf([[r["id"] for r in vec], [r["id"] for r in kw]])
        if not fused:
            return []
        rows = await (await conn.execute(f"select {_HIT_COLS} from context_chunks c join context_docs d on d.id = c.doc_id where c.id = any(%s)", (list(fused),))).fetchall()
    sims = {r["id"]: r["sim"] for r in vec}
    hits = [Hit(**r, score=fused[r["chunk_id"]], sim=sims.get(r["chunk_id"], 0.0)) for r in rows]
    for h in hits:
        h.score = boost(h, ctx.paths)
    hits.sort(key=lambda h: h.score, reverse=True)
    return dedupe_per_doc(hits)[:k]


async def record_usage(pool: AsyncConnectionPool, task_id: str, section: str, hits: list[Hit]) -> None:
    async with pool.connection() as conn:
        for rank, h in enumerate(hits):
            await conn.execute(
                "insert into context_usage (task_id, chunk_id, doc_id, title, url, heading_path, section, rank, score) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (task_id, h.chunk_id, h.doc_id, h.title, h.url, h.heading_path, section, rank, h.score))


async def task_usage(pool: AsyncConnectionPool, task_id: str) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from context_usage where task_id = %s order by section, rank", (task_id,))).fetchall()


async def record_gap(pool: AsyncConnectionPool, *, task_id: str | None, query: str, best_score: float | None, repo: str | None, team: str | None) -> None:
    async with pool.connection() as conn:
        await conn.execute("insert into context_gaps (task_id, query, best_score, repo, team) values (%s,%s,%s,%s,%s)", (task_id, query[:500], best_score, repo, team))


async def list_gaps(pool: AsyncConnectionPool, limit: int = 50) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from context_gaps order by created_at desc limit %s", (limit,))).fetchall()


async def list_quarantine(pool: AsyncConnectionPool, limit: int = 50) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "select q.*, s.kind as source_kind, s.key as source_key from context_quarantine q join context_sources s on s.id = q.source_id "
            "order by q.created_at desc limit %s", (limit,))).fetchall()


async def set_source_enabled(pool: AsyncConnectionPool, source_id: int, enabled: bool) -> None:
    async with pool.connection() as conn:
        await conn.execute("update context_sources set enabled = %s where id = %s", (enabled, source_id))


async def delete_source(pool: AsyncConnectionPool, source_id: int) -> bool:
    async with pool.connection() as conn:
        return (await conn.execute("delete from context_sources where id = %s", (source_id,))).rowcount > 0
