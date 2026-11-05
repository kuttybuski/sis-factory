from injection_guard import check_outbound, redact, with_canary

# Secret-shaped values are assembled at run time so this file does not itself look like a leaked secret.
GH = "ghp_" + "a1B2" * 9
AWS = "AKIA" + "ABCD1234EFGH5678"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
ANTHROPIC = "sk-ant-" + "x" * 30
ALLOWED = {"github.com", "acme.atlassian.net"}


def rules(r):
    return {f.rule_id for f in r.findings}


def test_clean_text_with_an_allowed_link_passes():
    r = check_outbound("Fixes login. See https://github.com/acme/app/pull/12", allowed_hosts=ALLOWED)
    assert r.ok and not r.findings


def test_secret_shaped_values_block_and_are_not_echoed():
    for text, rule in [(f"token {GH}", "github-token"), (AWS, "aws-access-key"), (PEM, "private-key"), (ANTHROPIC, "anthropic-key")]:
        r = check_outbound(text)
        assert not r.ok and rule in rules(r)
        assert all(text not in f.excerpt for f in r.findings)


def test_exact_secret_values_held_by_the_caller_are_caught_even_if_not_secret_shaped():
    r = check_outbound("the password is hunter2hunter2", literals=["hunter2hunter2"])
    assert not r.ok and "secret-literal" in rules(r)
    assert check_outbound("short", literals=["abc"]).ok  # too short to be a reliable match


def test_markdown_image_to_an_unknown_host_is_a_beacon():
    r = check_outbound("![x](https://evil.example/p.png)", allowed_hosts=ALLOWED)
    assert not r.ok and "image-beacon" in rules(r)


def test_long_query_values_to_unknown_hosts_look_like_exfiltration():
    r = check_outbound("see https://evil.example/c?d=" + "A" * 40, allowed_hosts=ALLOWED)
    assert not r.ok and "url-with-payload" in rules(r)


def test_plain_link_to_an_unknown_host_is_noted_but_not_blocked():
    r = check_outbound("docs: https://docs.python.org/3/", allowed_hosts=ALLOWED)
    assert r.ok and rules(r) == {"external-url"}


def test_allowed_host_with_query_is_fine():
    assert check_outbound("https://github.com/a/b/pull/1?diff=split&w=" + "1" * 30, allowed_hosts=ALLOWED).ok


def test_canary_in_output_is_critical():
    _, canary = with_canary("p")
    r = check_outbound(f"debug: {canary}", canaries=[canary])
    assert not r.ok and "canary-leak" in rules(r)


def test_redact_masks_secret_shaped_values():
    out = redact(f"a {GH} b {AWS}")
    assert GH not in out and AWS not in out and "[redacted:github-token]" in out
