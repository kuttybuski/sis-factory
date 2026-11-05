from sif_contracts import Settings
from sif_contracts import policy

S = Settings(_env_file=None)


def test_defaults_match_the_agreed_behaviour():
    assert (S.label_trigger, S.label_picked, S.mention) == ("sif", "sif-picked", "@sif")
    assert S.wait_timeout_minutes == 120
    assert S.max_budget_usd == 2.0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("SIF_LABEL_TRIGGER", "robot")
    monkeypatch.setenv("SIF_MENTION", "@bot")
    monkeypatch.setenv("SIF_WAIT_TIMEOUT_MINUTES", "30")
    monkeypatch.setenv("SIF_MAX_BUDGET_USD", "7.5")
    s = Settings(_env_file=None)
    assert (s.label_trigger, s.mention, s.wait_timeout_minutes, s.max_budget_usd) == ("robot", "@bot", 30, 7.5)


def test_pickup_requires_trigger_and_not_already_picked():
    assert policy.should_pick(["sif"], S)
    assert not policy.should_pick(["backend"], S)
    assert not policy.should_pick(["sif", "sif-picked"], S)


def test_only_comments_mentioning_the_agent_count():
    assert policy.is_for_agent("@sif use backoff", S)
    assert policy.is_for_agent("Thanks, @SIF approve", S)
    assert not policy.is_for_agent("please look at this @siffy", S)
    assert not policy.is_for_agent("email me at x@sif.com", S)
    assert not policy.is_for_agent("no mention here", S)


def test_real_jira_mention_of_the_bot_account_matches():
    s = Settings(_env_file=None, bot_account_id="abc123")
    assert policy.is_for_agent("[~accountid:abc123] go ahead", s)
    assert not policy.is_for_agent("[~accountid:other] go ahead", s)
    assert policy.message_for_agent("[~accountid:abc123] go ahead", s) == "go ahead"


def test_message_strips_the_mention():
    assert policy.message_for_agent("@sif  use   backoff", S) == "use backoff"


def test_budget_reply_classification():
    assert policy.classify_reply("approve") is policy.Decision.APPROVE
    assert policy.classify_reply("Stop please") is policy.Decision.STOP
    assert policy.classify_reply("use the other module") is policy.Decision.ANSWER
    assert policy.classify_reply("") is policy.Decision.ANSWER


def test_the_model_defaults_to_haiku_and_is_configurable(monkeypatch):
    assert S.model_implementation == "claude-haiku-5-5"
    monkeypatch.setenv("MODEL_IMPLEMENTATION", "my-bigger-model")
    assert Settings(_env_file=None).model_implementation == "my-bigger-model"
