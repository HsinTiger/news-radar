import argparse
import json

from src.content_quality_guard import QualityIssue
from src.schema import CarouselCards, MultiPlatformDraft, PlatformVariant
from scripts import publish_now


def _args(*, platforms="threads", submission_id="submission-test-001"):
    return argparse.Namespace(
        url="",
        title="Owner view",
        text="這是一段足夠長的 owner 原始觀點，用來驗證立即發文會先留下 canonical evidence，"
        "再逐平台發布；內容刻意超過八十個字，避免輸入長度 gate 影響測試。"
        "第二段補充可操作判斷與讀者價值，確保 compose 路徑可被完整執行。",
        file="",
        platforms=platforms,
        note="",
        exact_copy_json="",
        submission_id=submission_id,
        result_json="",
        setup_only=False,
        evidence_dir="",
    )


def _variant(label: str) -> PlatformVariant:
    return PlatformVariant(
        title=f"{label} 有用的判斷",
        body="先說結論。\n\n這是有來源脈絡的分析，讀者能直接帶走下一步。",
        hashtags=["#AI"],
        primary_topic_tag="#AI",
        char_count=45,
    )


def _bundle(platforms) -> MultiPlatformDraft:
    values = {platform: _variant(platform) for platform in platforms}
    return MultiPlatformDraft(
        fb=values.get("fb"),
        ig=values.get("ig"),
        threads=values.get("threads"),
        carousel=CarouselCards(
            insight_statement="真正差異在可驗證的執行",
            insight_support="每個平台都要有獨立成功證據",
            stat_number="3",
            stat_caption="三個平台分別追蹤",
            takeaways=["失敗平台單獨重試", "成功平台不重複發文"],
        ),
    )


def _temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(publish_now.dbmod, "DB_PATH", tmp_path / "news_radar.db")


def _allow_fake_quality(monkeypatch):
    monkeypatch.setattr(
        publish_now,
        "_quality_issues",
        lambda _platform, item, **_kwargs: (item["full_text"], []),
    )


def test_threads_only_persists_lineage_and_retry_is_idempotent(
    monkeypatch, tmp_path
):
    _temp_db(monkeypatch, tmp_path)
    _allow_fake_quality(monkeypatch)
    compose_calls = []
    publish_calls = []

    async def fake_compose(*_args, platforms, **_kwargs):
        compose_calls.append(tuple(platforms))
        return _bundle(platforms)

    async def fake_publish(platform, *_args, **_kwargs):
        publish_calls.append(platform)
        return True, "", "threads-post-1"

    monkeypatch.setattr(publish_now, "compose_multi_platform", fake_compose)
    monkeypatch.setattr(publish_now, "_publish_platform", fake_publish)

    exit_code, result = publish_now.asyncio.run(publish_now.run(_args()))
    assert exit_code == 0
    assert result["status"] == "published"
    assert compose_calls == [("threads",)]
    assert publish_calls == ["threads"]

    conn = publish_now.dbmod.get_conn()
    try:
        assert [
            row[0]
            for row in conn.execute("SELECT platform FROM platform_drafts").fetchall()
        ] == ["threads"]
        tags = json.loads(conn.execute("SELECT tags FROM news_items").fetchone()[0])
        assert "control_submission:submission-test-001" in tags
        assert conn.execute("SELECT COUNT(*) FROM publish_log WHERE success=1").fetchone()[0] == 1
    finally:
        conn.close()

    # Same submission reuses the canonical draft and does not call Meta again.
    exit_code, result = publish_now.asyncio.run(publish_now.run(_args()))
    assert exit_code == 0
    assert result["status"] == "published"
    assert compose_calls == [("threads",)]
    assert publish_calls == ["threads"]


def test_partial_retry_calls_only_missing_platforms(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    _allow_fake_quality(monkeypatch)
    attempts = {"facebook": 0, "instagram": 0, "threads": 0}

    async def fake_compose(*_args, platforms, **_kwargs):
        return _bundle(platforms)

    async def first_publish(platform, *_args, **_kwargs):
        db_platform = publish_now._DB_PLATFORM[platform]
        attempts[db_platform] += 1
        return db_platform == "facebook", "temporary" if db_platform != "facebook" else "", f"{db_platform}-id"

    monkeypatch.setattr(publish_now, "compose_multi_platform", fake_compose)
    monkeypatch.setattr(publish_now, "_publish_platform", first_publish)
    args = _args(platforms="fb,ig,threads", submission_id="submission-partial-001")

    exit_code, result = publish_now.asyncio.run(publish_now.run(args))
    assert exit_code == 1
    assert result["status"] == "partial"
    assert attempts == {"facebook": 1, "instagram": 1, "threads": 1}

    second_calls = []

    async def retry_publish(platform, *_args, **_kwargs):
        db_platform = publish_now._DB_PLATFORM[platform]
        second_calls.append(db_platform)
        return True, "", f"{db_platform}-retry-id"

    monkeypatch.setattr(publish_now, "_publish_platform", retry_publish)
    exit_code, result = publish_now.asyncio.run(publish_now.run(args))
    assert exit_code == 0
    assert result["status"] == "published"
    assert set(second_calls) == {"instagram", "threads"}
    assert "facebook" not in second_calls


def test_unresolved_rewrite_is_held_without_external_publish(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    compose_calls = []
    publish_calls = []

    async def fake_compose(*_args, platforms, **_kwargs):
        compose_calls.append(tuple(platforms))
        return _bundle(platforms)

    async def must_not_publish(*args, **kwargs):
        publish_calls.append((args, kwargs))
        raise AssertionError("quality-held content must not reach Meta")

    monkeypatch.setattr(publish_now, "compose_multi_platform", fake_compose)
    monkeypatch.setattr(
        publish_now,
        "check_quality",
        lambda *_args, **_kwargs: [
            QualityIssue(
                code="test_rewrite",
                severity="rewrite",
                message="needs rewrite",
                evidence="test evidence",
            )
        ],
    )
    monkeypatch.setattr(publish_now, "_publish_platform", must_not_publish)

    exit_code, result = publish_now.asyncio.run(
        publish_now.run(_args(submission_id="submission-quality-001"))
    )
    assert exit_code == 4
    assert result["status"] == "quality_held"
    assert len(compose_calls) == 2
    assert publish_calls == []

    conn = publish_now.dbmod.get_conn()
    try:
        draft = conn.execute("SELECT status,queue_status FROM drafts").fetchone()
        assert tuple(draft) == ("pending_review", None)
        assert conn.execute("SELECT COUNT(*) FROM content_quality_evaluations").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM publish_log").fetchone()[0] == 0
    finally:
        conn.close()


def test_setup_only_renders_evidence_without_db_upload_or_meta(monkeypatch, tmp_path):
    canonical_db = tmp_path / "canonical.db"
    monkeypatch.setattr(publish_now.dbmod, "DB_PATH", canonical_db)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("setup-only must not touch canonical or external publish I/O")

    async def fake_compose(*_args, platforms, **_kwargs):
        return _bundle(platforms)

    monkeypatch.setattr(publish_now.dbmod, "init_db", forbidden)
    monkeypatch.setattr(publish_now.dbmod, "get_conn", forbidden)
    monkeypatch.setattr(publish_now, "upload_cards", forbidden)
    monkeypatch.setattr(publish_now, "_publish_platform", forbidden)
    monkeypatch.setattr(publish_now, "compose_multi_platform", fake_compose)
    monkeypatch.setattr(publish_now, "check_quality", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(publish_now, "check_platform_style", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(publish_now, "check_platform_format", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(publish_now, "build_cards", lambda **_kwargs: [object(), object()])
    monkeypatch.setattr(
        publish_now,
        "render_cards",
        lambda *, output_dir, **_kwargs: [
            output_dir / "card-1.png",
            output_dir / "card-2.png",
        ],
    )
    args = _args(platforms="fb,ig,threads", submission_id="")
    args.setup_only = True
    args.evidence_dir = str(tmp_path / "evidence")

    exit_code, result = publish_now.asyncio.run(publish_now.run_setup_only(args))

    assert exit_code == 0
    assert result["status"] == "setup_ready"
    assert result["publish_invoked"] is False
    assert result["canonical_state_mutated"] is False
    assert set(result["card_files"]) == {"fb", "ig", "threads"}
    assert not canonical_db.exists()
    evidence = json.loads(
        (tmp_path / "evidence" / "setup_only_evidence.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["status"] == "setup_ready"


def test_exact_copy_uses_no_composer_and_cannot_broaden_platform_scope(
    monkeypatch, tmp_path
):
    def forbidden_compose(*_args, **_kwargs):
        raise AssertionError("exact copy must not invoke a model")

    monkeypatch.setattr(publish_now, "compose_multi_platform", forbidden_compose)
    monkeypatch.setattr(publish_now, "check_quality", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(publish_now, "check_platform_style", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(publish_now, "check_platform_format", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(publish_now, "build_cards", lambda **_kwargs: [object(), object()])
    monkeypatch.setattr(
        publish_now,
        "render_cards",
        lambda *, output_dir, **_kwargs: [output_dir / "one.png", output_dir / "two.png"],
    )
    args = _args(platforms="threads", submission_id="")
    args.setup_only = True
    args.evidence_dir = str(tmp_path / "evidence")
    args.exact_copy_json = _bundle(["threads"]).model_dump_json()

    exit_code, result = publish_now.asyncio.run(publish_now.run_setup_only(args))

    assert exit_code == 0
    assert result["status"] == "setup_ready"

    broadened = _bundle(["fb", "threads"]).model_dump_json()
    try:
        publish_now._load_exact_bundle(broadened, ["threads"])
    except ValueError as exc:
        assert "unrequested platforms: fb" in str(exc)
    else:
        raise AssertionError("unrequested exact-copy platform must fail closed")


def test_source_bounded_food_safety_override_passes_all_platform_gates() -> None:
    source = (
        "食藥署27日公布中聯油脂案第三方獨立調查結果，苯(a)駢芘超標並非單一因素，"
        "而是高風險原料管理、製程管控與檢驗監測等缺失交互影響。"
        "行政院7月23日通過食品安全衛生管理法修正草案並送立法院審議，"
        "聚焦源頭、製程、異常通報、品質管理與數位治理。"
    )
    platforms = ["fb", "ig", "threads"]
    bundle = publish_now._apply_source_bounded_overrides(
        _bundle(platforms),
        title="食藥署公布中聯油脂案第三方獨立調查結果",
        source_text=source,
        platforms=platforms,
    )
    finalized, structural = publish_now._finalize_bundle(bundle, platforms)

    assert structural == []
    for platform, item in finalized.items():
        _visible, issues = publish_now._quality_issues(
            platform,
            item,
            title="食藥署公布中聯油脂案第三方獨立調查結果",
            source_text=source,
            carousel=bundle.carousel,
        )
        assert not [issue for issue in issues if issue.severity == "rewrite"]
        assert "問題油品流入市面" not in item["full_text"]
        assert "未來可追蹤" not in item["full_text"]
    assert len(publish_now.build_cards(
        title=finalized["ig"]["title"], subtitle="", carousel=bundle.carousel
    )) == 5
