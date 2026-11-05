from typing import Any

from pydantic import BaseModel, Field


def approx_tokens(text: str) -> int:
    return -(-len(text) // 4)  # ~4 characters per token


class Chunk(BaseModel):
    text: str
    heading_path: str = ""  # "Title > Section > Subsection"
    kind: str = "text"  # text | code | thread | diff | summary
    flagged: bool = False

    @property
    def tokens(self) -> int:
        return approx_tokens(self.text)

    @property
    def embed_text(self) -> str:
        """What gets embedded: the breadcrumb plus the text, so a chunk keeps its context."""
        return f"{self.heading_path}\n{self.text}" if self.heading_path else self.text


class DocIn(BaseModel):
    kind: str  # page | pr | doc | qa
    external_id: str
    title: str
    url: str = ""
    version: str = ""
    team: str | None = None
    repo: str | None = None
    path_hint: list[str] = Field(default_factory=list)  # files a PR touched; used to boost PRs near the task's files
    meta: dict[str, Any] = Field(default_factory=dict)
    chunks: list[Chunk] = Field(default_factory=list)

    @property
    def content_hash(self) -> str:
        import hashlib

        h = hashlib.sha256(self.version.encode())
        for c in self.chunks:
            h.update(c.heading_path.encode() + b"\0" + c.text.encode() + b"\0")
        return h.hexdigest()


class Hit(BaseModel):
    chunk_id: int
    doc_id: int
    kind: str
    title: str
    url: str
    heading_path: str
    text: str
    flagged: bool = False
    score: float = 0.0
    sim: float = 0.0  # cosine similarity (0 when the chunk was found by keywords only)
    path_hint: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
