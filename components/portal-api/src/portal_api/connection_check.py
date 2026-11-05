"""Asks Jira or GitHub whether a team's credentials work, and whether they reach what the team needs. Returns (status, detail); never raises."""

import httpx

from sif_contracts.connections import GithubCreds, JiraCreds, redact

client_factory = httpx.AsyncClient  # tests swap in a client with a mock transport


def _client(**kw) -> httpx.AsyncClient:
    return client_factory(timeout=10, follow_redirects=False, **kw)  # a redirect could carry the credentials to another host


async def check_jira(creds: JiraCreds, projects: list[str]) -> tuple[str, str]:
    try:
        async with _client(base_url=creds.base_url, auth=(creds.email, creds.token), headers={"Accept": "application/json"}) as c:
            me = await c.get("/rest/api/3/myself")
            if me.status_code in (401, 403):
                return "failed", "Jira rejected the email or API token."
            if me.status_code != 200:
                return "failed", f"Jira answered {me.status_code} at {creds.base_url}. Is this the right site URL?"
            who = me.json().get("displayName") or creds.email
            missing = [p for p in projects if (await c.get(f"/rest/api/3/project/{p}")).status_code != 200]
    except (httpx.HTTPError, ValueError) as e:
        return "failed", redact(f"Could not reach {creds.base_url}: {type(e).__name__}", creds.token)
    if missing:
        return "failed", f"Signed in as {who}, but cannot see the project(s): {', '.join(missing)}."
    return "connected", f"Signed in as {who}." + (f" Can see {', '.join(projects)}." if projects else " The team has no Jira projects listed yet.")


async def check_github(creds: GithubCreds, repos: list[str]) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {creds.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        async with _client(base_url=creds.api_url, headers=headers) as c:
            me = await c.get("/user")
            if me.status_code == 401:
                return "failed", "GitHub rejected the token."
            if me.status_code == 200:
                who = f"Signed in as {me.json().get('login', 'unknown')}."
            else:  # app and some fine-grained tokens cannot read /user; /rate_limit works for any valid token
                rate = await c.get("/rate_limit")
                if rate.status_code != 200:
                    return "failed", f"GitHub answered {me.status_code} at {creds.api_url}. Is the API URL right?"
                who = "Token accepted."
            missing = [r for r in repos if (await c.get(f"/repos/{r}")).status_code != 200]
    except (httpx.HTTPError, ValueError) as e:
        return "failed", redact(f"Could not reach {creds.api_url}: {type(e).__name__}", creds.token)
    if missing:
        return "failed", f"{who} But the token cannot read: {', '.join(missing)}."
    return "connected", who + (f" Can read {', '.join(repos)}." if repos else " The team has no repositories listed yet.")
