from sif_context.models import approx_tokens
from sif_context.text import html_to_blocks, html_to_chunks, md_to_chunks

PAGE = """
<h1>Payments service</h1>
<p>Overview of how the payments service handles charges.</p>
<h2>Retries</h2>
<p>Failed charges are retried with exponential backoff, at most five times.</p>
<ul><li>First retry after 1 minute<ul><li>nested detail kept once</li></ul></li><li>Then 5 minutes</li></ul>
<h2>Runbook</h2>
<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[kubectl rollout restart deploy/payments
kubectl logs deploy/payments]]></ac:plain-text-body></ac:structured-macro>
<table><tr><th>Code</th><th>Meaning</th></tr><tr><td>402</td><td>Card declined</td></tr></table>
"""


def test_blocks_keep_structure_and_do_not_duplicate_nested_text():
    blocks = html_to_blocks(PAGE)
    kinds = [b.kind for b in blocks]
    assert kinds[0] == "heading" and "code" in kinds and "row" in kinds
    assert sum(b.text == "nested detail kept once" for b in blocks) == 1
    assert next(b for b in blocks if b.kind == "code").text.startswith("kubectl rollout restart")
    assert any(b.text == "402 | Card declined" for b in blocks)


def test_chunks_carry_a_heading_breadcrumb():
    chunks = html_to_chunks(PAGE, "Payments", "Engineering > Backend")
    paths = {c.heading_path for c in chunks}
    assert any(p.startswith("Engineering > Backend > Payments") for p in paths)
    assert any("Runbook" in p for p in paths)
    retry = next(c for c in chunks if "exponential backoff" in c.text)
    assert "Retries" in retry.heading_path and "Retries" in retry.embed_text


def test_code_blocks_stay_whole():
    chunks = html_to_chunks(PAGE, "Payments")
    code = next(c for c in chunks if "kubectl rollout" in c.text)
    assert "kubectl logs deploy/payments" in code.text and code.text.startswith("```")


def test_long_sections_split_within_the_token_limit():
    big = "<h1>Big</h1>" + "".join(f"<p>Sentence number {i} explains one more detail about the system. " * 3 + "</p>" for i in range(300))
    chunks = html_to_chunks(big, "Big")
    assert len(chunks) > 3
    assert all(approx_tokens(c.text) <= 700 for c in chunks)


def test_markdown_headings_and_fences():
    md = "# Title\n\nIntro paragraph here that is long enough to be kept in the index.\n\n## Setup\n\nRun make install.\n\n```bash\nmake test\n```\n"
    chunks = md_to_chunks(md, "README", "repo")
    assert chunks and chunks[0].heading_path.startswith("repo > README")
    assert any("make test" in c.text for c in chunks)


def test_empty_and_garbage_input():
    assert html_to_chunks("", "Empty") == []
    assert html_to_chunks("<<<not html>>>", "Junk") is not None
