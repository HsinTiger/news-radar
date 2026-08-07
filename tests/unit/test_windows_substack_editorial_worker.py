from scripts import windows_substack_editorial_worker as worker


def test_windows_writer_uses_repo_contract_and_write_only_commands() -> None:
    commands = worker.compose_commands("podcast-batch", python_executable="python")

    assert commands == [
        [
            "python",
            "-u",
            "substack_radar/compose.py",
            "podcast",
            "--harvest",
            "--editorial-profile",
            "weekly",
            "--no-draft",
        ],
        [
            "python",
            "-u",
            "substack_radar/compose.py",
            "podcast",
            "--editorial-profile",
            "weekly",
            "--no-draft",
        ],
    ]


def test_windows_writer_model_contract_is_gpt_then_claude(monkeypatch) -> None:
    monkeypatch.delenv("SUBSTACK_COMPOSER_BACKEND", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)

    env = worker.writer_environment()

    assert env["SUBSTACK_COMPOSER_BACKEND"] == "codex_cli,claude_cli"
    assert env["CODEX_MODEL"] == "gpt-latest"
    assert env["CLAUDE_MODEL"] == "claude-latest"
    assert env["SUBSTACK_AUTO_DRAFT"] == "0"


def test_windows_weekly_selects_company_then_writes() -> None:
    assert worker.compose_commands("weekly", python_executable="py") == [
        ["py", "scripts/pick_company_candidate.py"],
        [
            "py",
            "-u",
            "substack_radar/compose.py",
            "company",
            "--editorial-profile",
            "weekly",
            "--no-draft",
        ],
    ]
