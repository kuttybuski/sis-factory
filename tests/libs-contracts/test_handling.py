import pytest

from sif_contracts import handling as H
from sif_contracts.settings import Settings


def test_builtin_story_and_task_keep_todays_behaviour():
    for kind in ("story", "task"):
        p, src = H.resolve(kind)
        assert (p.intent, p.design, p.implement, p.split_into) == (False, False, True, "none")
        assert set(src.values()) == {"builtin"}


def test_builtin_epic_and_feature_split_with_approval_and_do_not_implement():
    for kind in ("epic", "feature"):
        p, _ = H.resolve(kind)
        assert (p.split_into, p.split_approval, p.implement) == ("story", True, False)
        assert H.check(kind, H.as_dict(p)) == []


def test_team_overrides_factory_overrides_builtin_field_by_field():
    p, src = H.resolve("story", {"intent": True, "intent_approval": True, "instructions": "factory text"}, {"intent_approval": False, "design": True})
    assert (p.intent, p.intent_approval, p.design, p.instructions) == (True, False, True, "factory text")
    assert (src["intent"], src["intent_approval"], src["design"], src["instructions"], src["implement"]) == ("factory", "team", "team", "factory", "builtin")


def test_unknown_stored_keys_are_ignored_so_old_rows_still_load():
    p, _ = H.resolve("task", {"from_the_future": 1, "intent": True})
    assert p.intent is True
    assert H.from_dict({"nonsense": 1}).implement is True


@pytest.mark.parametrize("kind,fields,fragment", [
    ("story", {"split_into": "story", "implement": False}, "cannot be split"),
    ("feature", {"split_into": "feature", "implement": False}, "only be split into stories"),
    ("epic", {"split_into": "story", "implement": True}, "turn off intent, design and implement"),
    ("story", {"implement": False}, "nothing for the factory to do"),
    ("story", {"split_approval": True}, "split_approval needs split_into"),
    ("story", {"intent_approval": True}, "intent_approval needs intent"),
    ("task", {"design_approval": True}, "design_approval needs design"),
    ("bug", {}, "kind must be one of"),
    ("story", {"intent": "maybe"}, "intent"),
])
def test_incoherent_policies_are_rejected(kind, fields, fragment):
    assert any(fragment in e for e in H.check(kind, fields))


def test_instructions_have_a_size_limit():
    assert H.check("story", {"instructions": "x" * 11}, max_chars=10)
    assert H.check("story", {"instructions": "x" * 10}, max_chars=10) == []


def test_full_pipeline_story_is_valid():
    assert H.check("story", {"intent": True, "intent_approval": True, "design": True, "design_approval": True}) == []


def test_guidance_includes_the_role_and_the_instructions():
    g = H.guidance_for("architect", "  Prefer boring technology.  ")
    assert "architect" in g and "design.md" in g and g.endswith("Prefer boring technology.")
    assert "Prefer" not in H.guidance_for("intent")


def test_parse_split_accepts_a_list_and_explains_problems():
    ok = H.parse_split('[{"title": "A", "description": "do a. Acceptance: a works"}]')
    assert ok == [{"title": "A", "description": "do a. Acceptance: a works"}]
    assert "not valid JSON" in H.parse_split("nope")
    assert "non-empty" in H.parse_split("[]")
    assert "child 1" in H.parse_split('[{"title": "A"}]')
    assert "limit" in H.parse_split("[" + ",".join(['{"title": "a", "description": "b"}'] * 3) + "]", limit=2)


def test_kind_of_maps_jira_types_and_defaults_to_task():
    m = Settings().jira_type_map
    assert [H.kind_of(t, m) for t in ("Epic", "Story", "Bug", "Sub-task", "New Feature", "Spike")] == ["epic", "story", "task", "task", "feature", "task"]
    assert H.kind_of("Story", "story:nonsense") == "task"
