from sis_context.pr import Comment, FileChange, PRData, ReviewComment, jira_keys, pr_to_doc


def pr(**kw):
    base = dict(repo="acme/api", number=42, title="PAY-141 Add retry to billing job", body="Adds backoff. Closes PAY-141 and PAY-150.",
                url="https://github.com/acme/api/pull/42", author="ann", merged_at="2026-05-01T10:00:00Z", labels=["billing"], head_ref="feature/PAY-141-retry",
                files=[FileChange(path="src/billing/retry.py", additions=30, deletions=2, patch="@@ -1,2 +1,3 @@\n+def retry():\n+    pass"),
                       FileChange(path="package-lock.json", additions=900, deletions=800, patch="@@\n+junk"),
                       FileChange(path="tests/test_retry.py", additions=10, deletions=0, patch="@@\n+def test_x(): pass")],
                review_comments=[ReviewComment(id=1, author="bob", body="Please use exponential backoff instead of a fixed delay.", path="src/billing/retry.py", diff_hunk="@@\n+sleep(5)"),
                                 ReviewComment(id=2, author="ann", body="Done, switched to backoff.", path="src/billing/retry.py", in_reply_to=1),
                                 ReviewComment(id=3, author="bob", body="LGTM", path="tests/test_retry.py")],
                comments=[Comment(author="bob", body="Looks good overall but please add a test for the max-attempts case.", state="CHANGES_REQUESTED"),
                          Comment(author="ci-bot", body="LGTM")])
    return PRData(**{**base, **kw})


def test_jira_keys_come_from_title_body_and_branch():
    assert jira_keys("PAY-141 fix", "closes PAY-150", "feature/PLT-7-x") == ["PAY-141", "PAY-150", "PLT-7"]
    assert jira_keys("no keys here, but V8-ish text") == []


def test_summary_lists_files_and_jira_keys():
    d = pr_to_doc(pr(), team="Payments")
    s = d.chunks[0]
    assert s.kind == "summary" and "PR #42" in s.text and "PAY-141" in s.text and "src/billing/retry.py" in s.text
    assert (d.kind, d.external_id, d.repo, d.team) == ("pr", "42", "acme/api", "Payments")
    assert d.meta["jira_keys"] == ["PAY-141", "PAY-150"] and d.meta["changes_requested"] is True


def test_review_threads_group_replies_and_skip_trivial_remarks():
    threads = [c for c in pr_to_doc(pr()).chunks if c.kind == "thread" and "Review comment" in c.text]
    assert len(threads) == 1  # the LGTM thread is dropped
    assert "exponential backoff instead of a fixed delay" in threads[0].text and "Done, switched" in threads[0].text


def test_discussion_keeps_substantive_comments_only():
    talk = next(c for c in pr_to_doc(pr()).chunks if c.text.startswith("Discussion"))
    assert "max-attempts" in talk.text and "ci-bot" not in talk.text


def test_diffs_skip_lockfiles_and_keep_the_biggest_real_changes():
    diffs = [c for c in pr_to_doc(pr()).chunks if c.kind == "diff"]
    assert [c.heading_path.split(" > ")[-1] for c in diffs] == ["src/billing/retry.py", "tests/test_retry.py"]


def test_path_hint_lists_every_changed_file():
    assert set(pr_to_doc(pr()).path_hint) == {"src/billing/retry.py", "package-lock.json", "tests/test_retry.py"}
