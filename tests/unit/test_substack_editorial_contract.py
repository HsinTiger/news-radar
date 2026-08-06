import plistlib
from pathlib import Path

from substack_radar import compose, composer, promise_cover
from src import llm_brain, notify


REPO = Path(__file__).resolve().parents[2]


def test_editorial_profile_routes_daily_weekly_and_deep_bundles() -> None:
    assert composer.resolve_editorial_profile("morning").name == "daily"
    assert composer.resolve_editorial_profile("evening").name == "daily"
    assert composer.resolve_editorial_profile("podcast").name == "weekly"
    assert composer.resolve_editorial_profile("company").name == "weekly"
    assert composer.resolve_editorial_profile("morning", has_deep_bundle=True).name == "weekly"
    assert composer.resolve_editorial_profile("company", override="daily").name == "daily"


def test_daily_prompt_is_compact_human_and_writing_only() -> None:
    profile = composer.resolve_editorial_profile("morning")
    system = composer._build_system_instruction(profile)
    prompt = composer._build_user_prompt(
        raw_title="測試來源",
        raw_content="一份含日期、人物與數字的測試素材。",
        mode="morning",
        topic_category="ai_application",
        editorial_note="",
        profile=profile,
    )
    combined = system + prompt

    assert "先說人話" in combined
    assert "先建立共同背景" in combined
    assert "像人，不演人" in combined
    assert "虛構第一手經驗" in combined
    assert "證據" in combined and "推論" in combined and "未知" in combined
    assert "1400–2200" in combined
    assert "具體回信問題" in combined
    assert "🖼 視覺位置" not in combined
    assert "Path B" not in combined
    assert "Path C" not in combined
    assert "chart_prompt" not in combined
    assert "Manny" not in combined and "曼報" not in combined
    assert "Aporia" not in combined
    output_example = prompt.split("=== 輸出格式", 1)[1]
    assert "//" not in output_example
    assert '"cover_character": "robot"' in output_example
    assert set(composer.SubstackDraft.model_json_schema()["properties"]) == {
        "title",
        "subtitle",
        "body_markdown",
        "cover_character",
        "cover_image_prompt",
    }


def test_antigravity_schema_prompt_has_only_current_writer_fields() -> None:
    compact, required = llm_brain._compact_schema_for_prompt(composer.SubstackDraft)
    combined = compact + "\n" + ", ".join(required)

    assert required == (
        "title",
        "subtitle",
        "body_markdown",
    )
    assert "cover_character" in compact
    assert "cover_image_prompt" in compact
    assert "generated_by" not in combined
    assert "hook_type" not in combined
    assert "metaphor_domain_used" not in combined
    assert "open_ending_form" not in combined
    assert "reading_time_minutes" not in combined


def test_weekly_prompt_requires_synthesis_countercase_and_watch_signal() -> None:
    profile = composer.resolve_editorial_profile("company")
    system = composer._build_system_instruction(profile)
    prompt = composer._build_user_prompt(
        raw_title="Example Corp",
        raw_content="財報事實與管理層說法。",
        mode="company",
        topic_category="us_stocks",
        editorial_note="",
        profile=profile,
    )
    combined = system + prompt

    assert "2800–4200" in combined
    assert "最強反方" in combined
    assert "後續訊號" in combined
    assert "財報事實" in combined
    assert "資料未揭露" in combined


def test_podcast_prompt_uses_the_dialogue_as_a_launchpad_not_a_summary() -> None:
    profile = composer.resolve_editorial_profile("podcast")
    prompt = composer._build_user_prompt(
        raw_title="A long interview",
        raw_content="主持人追問，來賓回答。",
        mode="podcast",
        topic_category="ai_model",
        editorial_note="",
        profile=profile,
    )

    assert "Podcast 是起點，不是文章主題" in prompt
    assert "延伸問題" in prompt
    assert "來賓的主張" in prompt and "作者的推論" in prompt
    assert "不要摘要整集" in prompt


def test_profile_specific_audit_and_obsolete_marker_warning() -> None:
    daily = composer.resolve_editorial_profile("morning")
    body = "具體段落。" * 80 + "\n\n> 🖼 視覺位置 · 舊標記\n\n你會先觀察哪一個訊號？"
    draft = composer.SubstackDraft(
        title="這件事值得重看",
        subtitle="一個具體反差，讓舊答案開始失效",
        body_markdown=body,
    )
    warnings = composer.audit_substack_draft(draft, profile=daily)

    assert any("字數低於下限" in warning for warning in warnings)
    assert any("舊內文視覺標記" in warning for warning in warnings)


def test_editorial_schedule_is_one_noon_batch_plus_one_combined_weekly_job() -> None:
    installer = (REPO / "scripts" / "install_substack_daily_agents.sh").read_text(
        encoding="utf-8"
    )
    worker = (REPO / "scripts" / "substack_editorial_worker.sh").read_text(
        encoding="utf-8"
    )
    legacy_setup = (REPO / "substack_radar" / "setup_launchd.sh").read_text(
        encoding="utf-8"
    )

    noon = plistlib.loads(
        (REPO / "scripts" / "com.hsin.news-radar.substack-podcast-noon.plist").read_bytes()
    )
    company = plistlib.loads(
        (REPO / "scripts" / "com.hsin.news-radar.company-compose.plist").read_bytes()
    )

    assert noon["StartCalendarInterval"] == {"Hour": 12, "Minute": 0}
    assert noon["ProgramArguments"][-1] == "podcast-batch"
    assert company["StartCalendarInterval"] == {"Weekday": 0, "Hour": 9, "Minute": 0}
    assert company["ProgramArguments"][-1] == "weekly"
    assert not (REPO / "scripts" / "com.hsin.news-radar.substack-daily.plist").exists()
    assert not (REPO / "scripts" / "com.hsin.news-radar.substack-podcast-noon-1.plist").exists()
    assert not (REPO / "scripts" / "com.hsin.news-radar.substack-podcast-noon-2.plist").exists()
    assert not (REPO / "scripts" / "com.hsin.news-radar.company-pick.plist").exists()

    active_agents = installer.split("AGENTS=(", 1)[1].split(")", 1)[0]
    legacy_agents = installer.split("LEGACY_AGENTS=(", 1)[1].split(")", 1)[0]
    assert "substack-podcast-noon" in active_agents
    assert "company-compose" in active_agents
    assert "company-pick" not in active_agents
    assert "substack-podcast-noon-1" in legacy_agents
    assert "substack-podcast-noon-2" in legacy_agents
    assert "company-pick" in legacy_agents
    assert "compose.py podcast" not in installer
    assert "compose.py evening" not in installer
    for legacy_label in (
        "com.hsin.news-radar.substack-morning",
        "com.hsin.news-radar.substack-podcast1",
        "com.hsin.news-radar.substack-podcast2",
        "com.hsin.news-radar.substack-podcast3",
        "com.hsin.news-radar.substack-evening",
        "com.newsradar.substack_morning",
        "com.newsradar.substack_evening",
        "com.newsradar.substack_podcast",
        "com.newsradar.substack_podcast2",
        "com.newsradar.substack_podcast3",
        "com.newsradar.company_pick",
        "com.newsradar.substack_company",
    ):
        assert legacy_label in installer
    assert "state_store.py lock" in worker
    assert "state_store.py pull" in worker
    assert "state_store.py push" in worker
    assert "--require-substack-draft" in worker
    assert "podcast-batch" in worker
    assert worker.count("substack_radar/compose.py podcast") == 2
    assert "substack_radar/compose.py podcast --harvest --editorial-profile weekly" in worker
    assert "substack_radar/compose.py podcast --editorial-profile weekly" in worker
    assert "COMPOSE_ARGS=(morning" not in worker
    pick_index = worker.index("scripts/pick_company_candidate.py")
    company_index = worker.index("substack_radar/compose.py company")
    assert pick_index < company_index
    assert "install_substack_daily_agents.sh" in legacy_setup
    assert "compose.py podcast" not in legacy_setup
    assert "compose.py evening" not in legacy_setup


def test_public_cadence_copy_matches_two_daily_podcast_extensions() -> None:
    expected = "每天兩篇對談延伸 · 每週一篇公司深拆"

    assert expected in compose.build_footer_block()
    assert promise_cover.SLOGAN == expected


def test_success_notification_uses_editorial_profile_not_obsolete_metadata() -> None:
    html, plain = notify._success_body(
        mode="morning",
        title="測試標題",
        subtitle="這是一個足夠長的測試副標",
        url=None,
        body_md="測試內文",
        metadata={
            "chinese_chars": 1800,
            "word_floor": 1400,
            "word_cap": 2200,
            "editorial_profile": "daily",
        },
        warnings=[],
        onedrive_path=None,
    )
    combined = html + plain

    assert "Profile" in combined and "daily" in combined
    assert "Metaphor" not in combined
    assert "Hook type" not in combined
    assert "Open ending" not in combined


def test_legacy_runtime_prompt_fragments_are_removed() -> None:
    for relative in (
        "substack_radar/config/substack_soul.md",
        "substack_radar/config/substack_voice_anchor.md",
        "substack_radar/config/company_analysis_soul.md",
        "substack_radar/config/manny_skills",
        "scripts/launchd/com.newsradar.substack_company.plist",
    ):
        assert not (REPO / relative).exists(), relative
