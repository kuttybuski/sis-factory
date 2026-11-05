"""The checked-out repository inside the sandbox, and turning the agent's edits into a patch."""

import asyncio
import io
import os
import tarfile
from pathlib import Path


async def _git(cwd: Path, *args: str, input: bytes | None = None) -> bytes:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd.parent), "GIT_AUTHOR_NAME": "sis", "GIT_AUTHOR_EMAIL": "sis@localhost",
           "GIT_COMMITTER_NAME": "sis", "GIT_COMMITTER_EMAIL": "sis@localhost", "GIT_CONFIG_NOSYSTEM": "1"}
    p = await asyncio.create_subprocess_exec("git", *args, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE if input else None,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate(input)
    if p.returncode:
        raise RuntimeError(f"git {args[0]} failed: {err.decode(errors='replace')[:300]}")
    return out


def safe_extract(data: bytes, dest: Path) -> None:
    """Extracts a tarball without trusting its paths or link targets."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if not str(target).startswith(str(dest.resolve())) or m.issym() or m.islnk() or m.isdev():
                continue
            tar.extract(m, dest, filter="data")


async def prepare(data: bytes, root: Path) -> Path:
    """Unpacks the repository and records a baseline commit, so the final change is exactly `git diff` from it."""
    repo = root / "repo"
    safe_extract(data, repo)
    await _git(repo, "init", "-q", "-b", "baseline")
    await _git(repo, "add", "-A")
    await _git(repo, "commit", "-q", "--allow-empty", "-m", "baseline")
    return repo


async def make_patch(repo: Path) -> str:
    await _git(repo, "add", "-A")
    return (await _git(repo, "diff", "--cached", "--binary", "--no-color", "baseline")).decode(errors="replace")


def place_documents(repo: Path, rel_dir: str, documents: dict[str, str]) -> None:
    """Writes the documents from earlier stages into the repository. File names come from the orchestrator, never from the agent."""
    for name, text in documents.items():
        if "/" in name or name.startswith(".") or not rel_dir:
            continue
        target = repo / rel_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def read_document(repo: Path, name: str, rel_dir: str) -> str:
    """The document an agent wrote: in the repository root, or in the stage directory."""
    for candidate in (repo / name, repo / rel_dir / name):
        f = candidate.resolve()
        if str(f).startswith(str(repo.resolve())) and f.is_file():
            return f.read_text(errors="replace")
    return ""
