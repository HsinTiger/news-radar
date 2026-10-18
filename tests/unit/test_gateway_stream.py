from src.gateway_stream import collect_sse


def test_collect_sse_joins_content_and_skips_reasoning():
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"想很久"}}]}',
        'data: {"choices":[{"delta":{"content":"嬌生"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"三年"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":9}}',
        "data: [DONE]",
    ]
    text, usage = collect_sse(lines)
    assert text == "嬌生三年" and usage["completion_tokens"] == 9


def test_writing_log_redacts_secrets():
    from src.run_log import redact
    out = redact("401 invalid sk-abcdefgh12345 nvapi-ZZZZZZZZZZ AIzaSyA1234567890abcdefghijk Bearer xyz")
    assert "sk-abc" not in out and "nvapi-" not in out and "AIza" not in out and "xyz" not in out
