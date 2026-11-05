import httpx

from jira_connector.adf import adf_to_text, clean, text_to_adf


class JiraClient:
    def __init__(self, base_url: str = "", email: str = "", token: str = "", client: httpx.AsyncClient | None = None) -> None:
        self.http = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), auth=(email, token), timeout=30, headers={"Accept": "application/json"})

    async def myself(self) -> dict:
        r = await self.http.get("/rest/api/3/myself")
        r.raise_for_status()
        return r.json()

    async def search(self, jql: str, fields: list[str], limit: int = 100) -> list[dict]:
        """Uses the enhanced search endpoint (the old /search was removed by Atlassian) and follows its page token."""
        out, token = [], None
        while len(out) < limit:
            body = {"jql": jql, "fields": fields, "maxResults": min(50, limit - len(out)), **({"nextPageToken": token} if token else {})}
            r = await self.http.post("/rest/api/3/search/jql", json=body)
            r.raise_for_status()
            data = r.json()
            out.extend(data.get("issues", []))
            token = data.get("nextPageToken")
            if not token or data.get("isLast"):
                break
        return out

    async def comments(self, key: str) -> list[dict]:
        """Comments oldest first, bodies converted to text."""
        out, start = [], 0
        while True:
            r = await self.http.get(f"/rest/api/3/issue/{key}/comment", params={"orderBy": "created", "startAt": start, "maxResults": 100})
            r.raise_for_status()
            data = r.json()
            for c in data.get("comments", []):
                out.append({"id": str(c["id"]), "author": c.get("author", {}), "text": clean(adf_to_text(c.get("body"))), "created": c.get("created", "")})
            start += len(data.get("comments", []))
            if not data.get("comments") or start >= data.get("total", 0):
                return out

    async def add_comment(self, key: str, text: str) -> str:
        """Returns the new comment's id."""
        r = await self.http.post(f"/rest/api/3/issue/{key}/comment", json={"body": text_to_adf(text)})
        r.raise_for_status()
        return str(r.json().get("id", ""))

    async def update_labels(self, key: str, add: list[str], remove: list[str]) -> None:
        ops = [{"remove": x} for x in remove] + [{"add": x} for x in add]
        if ops:
            r = await self.http.put(f"/rest/api/3/issue/{key}", json={"update": {"labels": ops}})
            r.raise_for_status()

    async def create_issue(self, project: str, summary: str, description: str, issue_type: str, parent_key: str | None = None, labels: list[str] | None = None) -> str:
        """Creates an issue and returns its key. Not retried by the caller: a lost response would otherwise leave a duplicate."""
        fields = {"project": {"key": project}, "summary": summary[:250], "description": text_to_adf(description), "issuetype": {"name": issue_type}, "labels": labels or []}
        if parent_key:
            fields["parent"] = {"key": parent_key}
        r = await self.http.post("/rest/api/3/issue", json={"fields": fields})
        r.raise_for_status()
        return r.json()["key"]


class DryRunJira(JiraClient):
    """Reads are real; writes are only logged. For the first run against a real project."""

    def __init__(self, inner: JiraClient, log) -> None:
        self.http, self.inner, self.log = inner.http, inner, log

    async def add_comment(self, key: str, text: str) -> str:
        self.log("DRY RUN: would comment on %s: %s", key, text[:200])
        return ""

    async def update_labels(self, key: str, add: list[str], remove: list[str]) -> None:
        self.log("DRY RUN: would change labels on %s: add %s, remove %s", key, add, remove)

    async def create_issue(self, project: str, summary: str, description: str, issue_type: str, parent_key: str | None = None, labels: list[str] | None = None) -> str:
        self.log("DRY RUN: would create a %s in %s under %s: %s", issue_type, project, parent_key, summary[:120])
        return f"{project}-DRY"
