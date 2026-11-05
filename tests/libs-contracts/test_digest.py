import subprocess
from datetime import date

from sis_contracts.digest import append_lessons, block_ids, file_patch, pr_body, title

L1 = {"id": 1, "text": "Use Decimal for money amounts.", "source": "mined"}
L2 = {"id": 2, "text": "Run unit-tests before finishing.", "source": "mined", "eval_n": 12, "eval_baseline": 5, "eval_candidate": 9, "source_url": "https://x/pull/3"}


def test_a_new_block_is_added_after_what_the_file_already_says():
    md = append_lessons("# Project\n\nBuild with make.\n", [L1, L2])
    assert md.startswith("# Project\n\nBuild with make.\n\n<!-- sis:lessons:start -->")
    assert "- Use Decimal for money amounts. _(sis L1)_\n- Run unit-tests before finishing. _(sis L2)_\n<!-- sis:lessons:end -->\n" in md
    assert block_ids(md) == {1, 2}
    assert append_lessons("", [L1]).startswith("<!-- sis:lessons:start -->")


def test_existing_lines_and_human_edits_in_the_block_are_kept_and_nothing_is_added_twice():
    md = append_lessons("# P\n", [L1]).replace("Use Decimal for money amounts.", "Use Decimal (never float) for money.")
    md2 = append_lessons(md, [L1, L2])
    assert "Use Decimal (never float) for money. _(sis L1)_" in md2 and md2.count("(sis L1)") == 1 and block_ids(md2) == {1, 2}
    assert md2.endswith("<!-- sis:lessons:end -->\n")


def test_block_ids_only_count_tags_inside_the_block():
    assert block_ids(None) == set() and block_ids("see (sis L9)") == set()
    assert block_ids("x (sis L9)\n<!-- sis:lessons:start -->\n- a _(sis L3)_\n<!-- sis:lessons:end -->") == {3}


def apply(tmp_path, path, old, patch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    if old is not None:
        (tmp_path / path).write_text(old)
    r = subprocess.run(["git", "apply", "-"], cwd=tmp_path, input=patch.encode(), capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
    return (tmp_path / path).read_text()


def test_the_patch_applies_to_a_new_file_an_existing_one_and_one_without_a_final_newline(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    new = append_lessons("", [L1])
    assert apply(tmp_path / "a", "CLAUDE.md", None, file_patch("CLAUDE.md", None, new)) == new
    old = "# P\nno newline"
    assert apply(tmp_path / "b", "CLAUDE.md", old, file_patch("CLAUDE.md", old, append_lessons(old, [L1]))) == append_lessons(old, [L1])


def test_the_pr_says_why_each_lesson_is_proposed_and_how_to_decide():
    body = pr_body([L1, L2], "CLAUDE.md")
    assert "**L1** Use Decimal" in body and "solved 9/12 past tickets with it, 5/12 without" in body and "[evidence](https://x/pull/3)" in body
    assert "Delete a line before merging" in body
    assert title("acme/api", 2, date(2026, 10, 31)) == "sis: 2 lessons learned in acme/api (2026-10-31)"
