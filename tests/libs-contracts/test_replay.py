from sis_contracts.replay import changed_lines, files, score

REF = """diff --git a/src/app.py b/src/app.py
index 1..2 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return b + a
diff --git a/tests/test_app.py b/tests/test_app.py
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -0,0 +1 @@
+assert add(1, 2) == 3
"""


def test_files_and_changed_lines_ignore_headers_and_context():
    assert files(REF) == {"src/app.py", "tests/test_app.py"}
    assert changed_lines(REF) == {"-return a + b", "+return b + a", "+assert add(1, 2) == 3"}


def test_the_merged_change_itself_scores_one_and_nothing_scores_zero():
    assert score(REF, REF) == 1.0
    assert score(REF, "") == 0.0 and score("", REF) == 0.0


def test_partial_work_scores_in_between_and_formatting_does_not_count():
    only_fix = REF.split("diff --git a/tests")[0]
    partial = score(REF, only_fix)
    assert 0.4 < partial < 1.0
    assert score(REF, REF.replace("return b + a", "return   b +  a")) == 1.0
    wrong_file = REF.replace("src/app.py", "src/other.py")
    assert score(REF, wrong_file) < score(REF, REF)
