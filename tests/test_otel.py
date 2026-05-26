from src.observability.otel import setup_otel


def test_setup_otel_returns_none_when_creds_missing(monkeypatch):
    monkeypatch.delenv("ARIZE_API_KEY", raising=False)
    monkeypatch.delenv("ARIZE_SPACE_ID", raising=False)
    # Reset singleton between tests
    import src.observability.otel as mod
    mod._PROVIDER = None
    assert setup_otel("agent-temporal-dev") is None


def test_setup_otel_is_idempotent(monkeypatch):
    # Force a sentinel provider so we don't actually contact Arize.
    import src.observability.otel as mod

    sentinel = object()
    mod._PROVIDER = sentinel

    monkeypatch.setenv("ARIZE_API_KEY", "x")
    monkeypatch.setenv("ARIZE_SPACE_ID", "s")
    a = setup_otel("agent-temporal-dev")
    b = setup_otel("agent-temporal-dev")
    assert a is b is sentinel  # singleton respected
    # Cleanup
    mod._PROVIDER = None
