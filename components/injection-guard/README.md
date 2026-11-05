# injection-guard

Defences against prompt injection in text that reaches, or leaves, the coding agent. A Python library with a small HTTP
service (`/v1/scan`, `/v1/check-output`, `/v1/guidance`) for callers that are not Python.

**It is one layer, not a guarantee.** Pattern matching catches known phrasing and common obfuscation. A determined attacker
can word an attack in a way no rule has seen. The real protection is that a sandbox holds no credentials, has deny-all
egress, and cannot merge: assume some injections get through and limit what they can do. This component makes attacks
louder, harder and easier to review.

## What is in it

| Module | Purpose |
|---|---|
| `normalize.py` | Removes hidden characters (zero-width, bidi, Unicode tag block), folds look-alike letters, and builds extra views of the text: URL/HTML-decoded, base64, hex, ROT13, de-spaced letters |
| `rules.py` | About 25 weighted rules in categories: override, impersonation, exfiltration, stealth, jailbreak, targeting, delimiter, dangerous-command, scope-escalation |
| `scanner.py` | `scan(text, source)` returns `allow`, `flag` or `block` with the score and findings. Thresholds depend on how much the source is trusted |
| `spotlight.py` | `wrap_untrusted()` wraps data in per-call random-nonce markers and defangs markup, plus the `SYSTEM_GUIDANCE` text for the agent's system prompt |
| `canary.py` | A secret marker in the system prompt; if it shows up in output, the prompt leaked |
| `outbound.py` | `check_outbound()` checks PR text, commit messages and Jira comments for secrets, exfiltration URLs and image beacons, canaries and exact secret values |
| `classifier.py` | Optional hook for an ML classifier (not bundled) |
| `service.py` | FastAPI front. It never logs scanned text, and accepts no real secret values |

## Verdicts

- `allow`: pass to the agent, still wrapped as untrusted.
- `flag`: pass wrapped, raise the task's risk tier, show it in the dashboard.
- `block`: do not pass to the agent; quarantine, comment in Jira, show it in the dashboard.

Thresholds (flag, block): untrusted sources (Jira, PR text, commit messages, tool output, web) 25 and 60; semi-trusted
(Confluence, repo files) 35 and 70; unknown sources get the strict profile. Override per profile with
`INJECTION_UNTRUSTED_FLAG`, `INJECTION_UNTRUSTED_BLOCK`, `INJECTION_SEMI_TRUSTED_FLAG` and `INJECTION_SEMI_TRUSTED_BLOCK`.

Known false positives, by design flagged rather than blocked: text that quotes an attack phrase (release notes, security
tickets) and install instructions such as `curl ... | sh`.

## Where it plugs in

| Caller | Does |
|---|---|
| `orchestrator-worker` | `scan` (with the team's blocklist) the ticket, the answer to a clarifying question, every reply to an agent's question, review feedback on documents, and PR review comments, before an agent sees them. Block: the text is held back and Jira says why; a held-back reply leaves the wait open for a plain answer |
| `context-ingest` | `scan` Confluence pages and PR text before indexing; drop or quarantine blocked chunks |
| `context-api` | `wrap_untrusted` every retrieved chunk before it goes into `CONTEXT.md` |
| `agent-runner` | add `SYSTEM_GUIDANCE` and a `with_canary` marker to the system prompt |
| `sandbox-gateway` | `check_outbound` (in-process, with the real token values as `literals`) on every PR body, commit message, Jira comment and tool argument |

## Incidents and the security PR

Every flag or block in orchestrator-worker is an incident. portal-api keeps one row per attempt per team (`injection_incidents`:
source, verdict, score, rules, who wrote it, how often it was seen), identified by `fingerprint()`: a hash of the normalized text, so the
text itself is never stored. The worker logs each one (`make logs`) and, every `SIS_DIGEST_POLL_SECONDS`, opens one security PR per team
repository for the attempts not yet recorded there. It adds them to `.sis/security/injection-log.md` and their fingerprints to
`.sis/security/blocklist.txt`. Merging is the approval: fingerprints still in the blocklist are blocked for that team from then on, even
where the rules alone would let the text through. A line deleted before merging, or a closed PR, marks the attempt harmless. While a
security PR is open on a repository, new attempts wait for the next one. The PR never contains the attack text, so git-broker's own
screen lets it through; the Jira key leads to the original.

Incidents with no repository are kept but get no PR. Factory-wide rule changes (`rules.py`, the corpus) are not proposed automatically yet.

## Not covered yet

- No ML classifier is bundled. Plug one into `Classifier` after checking its license and measuring it on your own traffic.
- Rules are English-centric.
- Image, PDF and attachment text is not extracted.
- The test corpus is small and hand-written (`tests/injection-guard/corpus.py`). It guards against regressions; it is not a benchmark.
