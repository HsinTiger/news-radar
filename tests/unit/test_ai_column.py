"""2026-10-18：週二、四的 智取未來（podcast 模式＋--column ai）。"""
from datetime import date

from substack_radar import composer, daily_publish, fb_follow
from substack_radar.compose import AI_TOPIC_BOOST, COLUMN_INTRO, COLUMN_PREFIX, apply_column_prefix


def test_ai_column_name_is_consistent_everywhere():
    name = COLUMN_PREFIX["ai"]
    assert name == daily_publish.AI_COLUMN and name in fb_follow.COLUMN_DESC and name in COLUMN_INTRO
    assert COLUMN_INTRO[name] == fb_follow.COLUMN_DESC[name]
    assert apply_column_prefix("工程師怎麼導入 Agent", "ai").endswith(f"｜{name}")
    assert fb_follow.split_column(f"x｜{name}")[0] == name


def test_ai_profile_only_when_column_is_ai(monkeypatch):
    monkeypatch.delenv("SUBSTACK_COLUMN", raising=False)
    assert composer.resolve_editorial_profile("podcast").brief_path.name == "editorial_podcast.md"
    monkeypatch.setenv("SUBSTACK_COLUMN", "ai")
    p = composer.resolve_editorial_profile("podcast")
    assert p.brief_path.name == "editorial_ai.md" and p.article_kind == "podcast"


def test_tuesday_thursday_publish_ai_first_then_podcast():
    tue, thu, wed = date(2026, 10, 20), date(2026, 10, 22), date(2026, 10, 21)
    assert [c for _, c in daily_publish.plan_for(tue)][:2] == ["智取未來", "吹牛免稅"]
    assert daily_publish.plan_for(thu)[0][1] == "智取未來"
    assert daily_publish.plan_for(wed)[0][1] == "吹牛免稅"


def test_topic_boost_prefers_deployment_and_chips():
    assert AI_TOPIC_BOOST.search("What Forward Deployed Engineers learned in production")
    assert AI_TOPIC_BOOST.search("AI Inference Is a Data Movement Problem")
    assert not AI_TOPIC_BOOST.search("My morning routine")


def test_ai_sources_do_not_overlap_with_podcast_sources():
    import yaml
    from pathlib import Path
    cfg = Path(__file__).resolve().parents[2] / "substack_radar" / "config"
    ai = {s["url"] for s in yaml.safe_load((cfg / "substack_ai_sources.yaml").read_text())["sources"]}
    pod = {s["url"] for s in yaml.safe_load((cfg / "substack_podcast_sources.yaml").read_text())["sources"]}
    assert not ai & pod and len(ai) >= 20
