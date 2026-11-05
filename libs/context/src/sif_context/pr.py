"""Pull requests as searchable documents: a summary, review threads, discussion, and the most relevant diffs."""

import re
from collections import defaultdict

from pydantic import BaseModel, Field

from sif_context.models import Chunk, DocIn, approx_tokens

JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_NOISE = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Cargo\.lock|go\.sum)$|\.min\.(js|css)$|(^|/)(dist|build|vendor|node_modules)/")
_TRIVIAL = re.compile(r"^\W*(lgtm|looks good( to me)?|approved?|thanks?|thank you|\+1|ship it|:shipit:|nice)\W*$", re.IGNORECASE)
DIFF_LINES, MAX_DIFFS, MAX_SUMMARY_FILES = 40, 5, 30


class FileChange(BaseModel):
    path: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    patch: str = ""


class ReviewComment(BaseModel):
    id: int
    author: str
    body: str
    path: str = ""
    diff_hunk: str = ""
    in_reply_to: int | None = None
    association: str = ""  # GitHub's author_association: OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, NONE...


class Comment(BaseModel):
    author: str
    body: str
    state: str = ""  # for reviews: APPROVED, CHANGES_REQUESTED, COMMENTED
    association: str = ""


class PRData(BaseModel):
    repo: str
    number: int
    title: str
    body: str = ""
    url: str = ""
    author: str = ""
    merged_at: str = ""
    labels: list[str] = Field(default_factory=list)
    head_ref: str = ""
    files: list[FileChange] = Field(default_factory=list)
    review_comments: list[ReviewComment] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)  # issue comments and review summaries


def jira_keys(*texts: str) -> list[str]:
    return sorted({k for t in texts for k in JIRA_KEY.findall(t or "")})


def _threads(comments: list[ReviewComment]) -> list[list[ReviewComment]]:
    by_id = {c.id: c for c in comments}
    groups: dict[int, list[ReviewComment]] = defaultdict(list)
    for c in comments:
        root = c
        while root.in_reply_to in by_id:
            root = by_id[root.in_reply_to]
        groups[root.id].append(c)
    return list(groups.values())


def pr_to_doc(pr: PRData, team: str | None = None) -> DocIn:
    keys = jira_keys(pr.title, pr.body, pr.head_ref)
    paths = [f.path for f in pr.files]
    listing = "\n".join(f"- {f.path} (+{f.additions} -{f.deletions})" for f in pr.files[:MAX_SUMMARY_FILES])
    more = f"\n- ... and {len(pr.files) - MAX_SUMMARY_FILES} more files" if len(pr.files) > MAX_SUMMARY_FILES else ""
    summary = (f"PR #{pr.number}: {pr.title}\nAuthor: {pr.author}. Merged: {pr.merged_at[:10]}."
               f"{' Jira: ' + ', '.join(keys) + '.' if keys else ''}{' Labels: ' + ', '.join(pr.labels) + '.' if pr.labels else ''}\n"
               f"Files changed:\n{listing}{more}")
    if pr.body.strip():
        summary += f"\n\nDescription:\n{pr.body.strip()[:1500]}"
    head = f"PR #{pr.number} {pr.title}"
    chunks = [Chunk(text=summary, heading_path=head, kind="summary")]

    changes_requested = False
    for thread in _threads(pr.review_comments):
        first = thread[0]
        lines = [f"Review comment on {first.path or 'the PR'}:"]
        if first.diff_hunk:
            lines.append("Code under review:\n" + "\n".join(first.diff_hunk.splitlines()[-6:]))
        lines += [f"- {c.author}: {c.body.strip()}" for c in thread if not _TRIVIAL.match(c.body)]
        if len(lines) > 1 + (1 if first.diff_hunk else 0):
            chunks.append(Chunk(text="\n".join(lines), heading_path=f"{head} > review thread", kind="thread"))
    talk = []
    for c in pr.comments:
        if c.state == "CHANGES_REQUESTED":
            changes_requested = True
        if len(c.body.strip()) >= 40 and not _TRIVIAL.match(c.body):
            talk.append(f"- {c.author}{' (' + c.state.lower().replace('_', ' ') + ')' if c.state else ''}: {c.body.strip()}")
    if talk:
        chunks.append(Chunk(text="Discussion:\n" + "\n".join(talk)[:3500], heading_path=f"{head} > discussion", kind="thread"))

    useful = sorted((f for f in pr.files if f.patch and not _NOISE.search(f.path)), key=lambda f: f.additions + f.deletions, reverse=True)
    for f in useful[:MAX_DIFFS]:
        patch = "\n".join(f.patch.splitlines()[:DIFF_LINES])
        chunks.append(Chunk(text=f"{f.path} ({f.status}, +{f.additions} -{f.deletions})\n```diff\n{patch}\n```",
                            heading_path=f"{head} > {f.path}", kind="diff"))

    return DocIn(kind="pr", external_id=str(pr.number), title=pr.title, url=pr.url, version=pr.merged_at, repo=pr.repo, team=team,
                 path_hint=paths[:200], chunks=[c for c in chunks if approx_tokens(c.text) > 3],
                 meta={"number": pr.number, "author": pr.author, "merged_at": pr.merged_at, "jira_keys": keys,
                       "labels": pr.labels, "changes_requested": changes_requested})
