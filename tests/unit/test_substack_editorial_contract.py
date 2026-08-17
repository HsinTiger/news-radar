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
    assert "1800–2800" in combined
    assert "具體回信問題" in combined
    assert "🖼 視覺位置" not in combined
    assert "Path B" not in combined
    assert "Path C" not in combined
    assert "chart_prompt" not in combined
    assert "Manny" not in combined and "曼報" not in combined
    assert "Aporia" not in combined
    output_example = prompt.split("=== 輸出格式", 1)[1]
    assert "//" not in output_example
    assert "cover_character" not in output_example
    assert "cover_image_prompt" not in output_example
    assert set(composer.SubstackDraft.model_json_schema()["properties"]) == {
        "title",
        "subtitle",
        # 2026-08-12：Substack 草稿本來就有 search_engine_title/description 兩欄，
        # 我們一直留空，等於把 15 字的鉤子標題交給搜尋引擎當線索。
        "seo_title",
        "seo_description",
        # 2026-08-16：tag 是站內導覽與 SEO/AEO 的入口，由模型產生、由
        # normalise_tags() 併回既有詞彙，避免 263 個 tag 繼續分裂。
        "tags",
        "body_markdown",
    }


def test_antigravity_schema_prompt_has_only_current_writer_fields() -> None:
    compact, required = llm_brain._compact_schema_for_prompt(composer.SubstackDraft)
    combined = compact + "\n" + ", ".join(required)

    assert required == (
        "title",
        "subtitle",
        "seo_title",
        "seo_description",
        "tags",
        "body_markdown",
    )
    assert "cover_character" not in compact
    assert "cover_image_prompt" not in compact
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

    assert "3800–6000" in combined
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


def test_reader_ready_article_removes_every_production_instruction(tmp_path) -> None:
    draft = composer.SubstackDraft(
        title="把製程留在後台",
        subtitle="讀者只需要文章，不需要知道文章怎麼生產",
        body_markdown=(
            "🧠 產文路線：legacy · 模型 old（發布前刪此行）\n\n"
            "第一段是讀者需要的內容。\n\n"
            "🖼 視覺位置 · 舊內文插圖\n\n"
            "場景描述：這是製程資料。\n\n"
            "🔍 Path B · Google 搜：不該出現在文章裡\n\n"
            "🎨 Path C · 生圖 prompt：internal image instruction\n\n"
            "第二段仍然是讀者需要的內容。"
        ),
    )
    draft.generated_by = "Codex CLI · 模型 gpt-latest"

    article = compose.write_article_substack_md(
        tmp_path,
        draft,
        sources_block=(
            "> 📚 **本文取材**（公開來源、可點擊查證）\n"
            "> 主來源 — [測試訪談](https://example.com/interview)\n\n"
        ),
    )
    compose.append_footer_block(article_md_path=article)
    text = article.read_text(encoding="utf-8")

    assert "第一段是讀者需要的內容" in text
    assert "第二段仍然是讀者需要的內容" in text
    assert "> 🧠 **產文路線**：Codex CLI · 模型 gpt-latest" in text
    assert text.count("產文路線") == 1
    assert "發布前刪此行" not in text
    assert "本文取材" in text and "https://example.com/interview" in text
    assert text.index("第二段仍然是讀者需要的內容") < text.index("本文取材")
    # 2026-08-16：footer 收尾從「覺得我哪個判斷站不住，直接回信」改成留言邀請——
    # owner 覺得原句太銳利，像在下戰帖。
    assert "有想法？留言區聊聊" in text
    for forbidden in (
        "視覺位置",
        "Path B",
        "Path C",
        "生圖 prompt",
        "封面圖 Prompt",
        "substack-editor",
        "發布前刪",
    ):
        assert forbidden not in text


def test_windows_writer_backend_is_codex_then_claude(monkeypatch) -> None:
    monkeypatch.setattr(composer, "SUBSTACK_BACKEND", "codex_cli,claude_cli")

    assert composer._resolve_backends() == ("codex_cli", "claude_cli")


def test_single_claude_override_never_reintroduces_gemini(monkeypatch) -> None:
    monkeypatch.setattr(composer, "SUBSTACK_BACKEND", "claude_cli")

    assert composer._resolve_backends() == ("claude_cli",)


def test_reader_artifacts_keep_character_cover_without_cover_prompt(
    monkeypatch, tmp_path
) -> None:
    captured = {}

    def fake_character_cover(**kwargs):
        captured.update(kwargs)
        cover = kwargs["output_dir"] / "cover.png"
        cover.write_bytes(b"\x89PNG\r\n")
        return cover

    monkeypatch.setattr(
        "substack_radar.character_cover.render_character_cover",
        fake_character_cover,
    )

    cover = compose.render_substack_cover(
        title="對談留下的真正問題",
        subtitle="從一場訪談延伸出可獨立成立的判斷",
        topic_category="ai_model",
        output_dir=tmp_path,
        mode="podcast",
    )

    assert cover == tmp_path / "cover.png"
    assert cover.is_file()
    assert captured["mode"] == "podcast"
    assert captured["character"] is None  # mode selects 達達; writer does not prompt it


def test_editorial_schedule_is_one_noon_batch_plus_one_combined_weekly_job() -> None:
    installer = (REPO / "scripts" / "install_substack_daily_agents.sh").read_text(
        encoding="utf-8"
    )
    worker = (REPO / "scripts" / "substack_editorial_worker.sh").read_text(
        encoding="utf-8"
    )
    fast_worker = (REPO / "scripts" / "drain_substack_fast.sh").read_text(
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
    assert "git fetch --quiet origin main" in fast_worker
    assert "git merge --ff-only origin/main" in fast_worker
    assert fast_worker.index("git fetch --quiet origin main") < fast_worker.index(
        "scripts/drain_substack.py"
    )


def test_public_cadence_copy_matches_the_reader_facing_promise() -> None:
    """對外承諾是「每天一篇、每週日一篇」。每天產兩篇 podcast 草稿是我們自己
    的排程（緩衝），不是給讀者看的數字——2026-08-17 owner 明確區分了這兩件事。"""
    expected = "每天一篇思想延伸 · 每週日一篇公司拆解"

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
