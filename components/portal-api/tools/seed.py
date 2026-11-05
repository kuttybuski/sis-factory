"""Seeds clearly-marked demo data so the dashboard has something to show. Remove with: python tools/seed.py --clear.

Demo rows have demo=true and are hidden from the dashboard unless it is opened with ?demo=1.
"""

import asyncio
import os
import random
import sys
import time

from sis_contracts.events import EventType as T
from sis_contracts.events import FactoryEvent

from portal_api import db

TEAMS = [("Payments", ["PAY"]), ("Platform", ["PLT"]), ("Mobile", ["MOB"])]
TITLES = ["Fix login redirect", "Add retry to billing job", "Upgrade logging lib", "Write API docs", "Handle null address", "Speed up search"]
REPOS = {"PAY": "acme/payments-api", "PLT": "acme/platform", "MOB": "acme/mobile-app"}
DAY = 86_400_000


def make_events(rng: random.Random, n: int) -> list[FactoryEvent]:
    now = int(time.time() * 1000)
    events: list[FactoryEvent] = []
    for i in range(n):
        proj = rng.choice(["PAY", "PLT", "MOB"])
        key = f"{proj}-{100 + i}"
        t = now - rng.randint(0, 29 * DAY) - rng.randint(60_000, 3_600_000)
        def e(kind: T, dt: int, **kw):
            events.append(FactoryEvent(type=kind, task_id=key, ts=t + dt, **kw))
        e(T.TASK_DISCOVERED, 0, jira_key=key, title=rng.choice(TITLES), repo=REPOS[proj], budget_usd=2.0)
        e(T.CLASSIFY_STARTED, 5_000)
        if rng.random() < 0.1:
            e(T.JIRA_BOUNCED, 20_000, text="Needs acceptance criteria.")
            continue
        e(T.CLASSIFY_RESULT, 15_000, classifier="routing", result="cheap-best")
        e(T.REPO_CHECKED_OUT, 30_000)
        e(T.SANDBOX_CREATED, 45_000)
        e(T.AGENT_RUNNING, 60_000)
        cost = round(rng.uniform(0.05, 2.0), 2)
        e(T.COST_UPDATED, 600_000, cost_usd=cost)
        r = rng.random()
        if r < 0.72:
            e(T.PR_OPENED, 900_000, pr_url=f"https://github.com/{REPOS[proj]}/pull/{rng.randint(1, 900)}")
        elif r < 0.84:
            e(T.TASK_FAILED, 900_000)
        elif r < 0.94:
            e(T.AGENT_ASKED_QUESTION, 700_000, reason="question", text="Which module is canonical?")
            e(T.WAIT_TIMED_OUT, 700_000 + 7_200_000)
        else:
            e(T.TASK_KILLED, 800_000)
    return sorted(events, key=lambda ev: ev.ts)


async def main() -> None:
    pool = await db.open_pool(os.environ["DATABASE_URL"])
    if "--clear" in sys.argv:
        print("removed demo tasks:", await db.clear_demo(pool))
    else:
        for name, projects in TEAMS:
            await db.upsert_team(pool, name=name, jira_projects=projects, confluence_spaces=[])
        applied = [await db.ingest(pool, ev, demo=True) for ev in make_events(random.Random(7), 60)]
        print("demo events applied:", sum(applied))
    await pool.close()


asyncio.run(main())
