from src.observability.otel import setup_otel


def test_setup_otel_returns_none_when_no_backend(monkeypatch):
    monkeypatch.delenv("ARIZE_API_KEY", raising=False)
    monkeypatch.delenv("ARIZE_SPACE_ID", raising=False)
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    import src.observability.otel as mod
    mod._PROVIDER = None
    assert setup_otel("agent-temporal-dev") is None


def test_setup_otel_prefers_phoenix_when_set(monkeypatch):
    """When both Phoenix and Arize creds are present, Phoenix wins —
    it's the local/dev backend and zero-credential default."""
    import src.observability.otel as mod
    mod._PROVIDER = None

    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006/v1/traces")
    monkeypatch.setenv("ARIZE_API_KEY", "ignored")
    monkeypatch.setenv("ARIZE_SPACE_ID", "ignored")

    called = {}

    def fake_build_phoenix(name, endpoint):
        called["phoenix"] = (name, endpoint)
        return object()

    def fake_build_arize(name):
        called["arize"] = name
        return object()

    monkeypatch.setattr(mod, "_build_phoenix_provider", fake_build_phoenix)
    monkeypatch.setattr(mod, "_build_arize_provider", fake_build_arize)
    # Skip the instrumentor side-effect — tests don't need it active.
    monkeypatch.setattr(
        mod,
        "setup_otel",
        mod.setup_otel,  # untouched
    )

    # Patch the deferred imports to be no-ops.
    import sys
    import types
    fake_inst_mod = types.ModuleType("openinference.instrumentation.claude_agent_sdk")
    class _NoopInstrumentor:
        def instrument(self, **kw):  # noqa: D401
            return None
    fake_inst_mod.ClaudeAgentSDKInstrumentor = _NoopInstrumentor
    sys.modules["openinference.instrumentation.claude_agent_sdk"] = fake_inst_mod

    setup_otel("agent-temporal-dev")
    assert "phoenix" in called and "arize" not in called
    assert called["phoenix"] == (
        "agent-temporal-dev", "http://phoenix:6006/v1/traces"
    )

    mod._PROVIDER = None


def test_setup_otel_is_idempotent(monkeypatch):
    import src.observability.otel as mod
    sentinel = object()
    mod._PROVIDER = sentinel

    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006/v1/traces")
    a = setup_otel("agent-temporal-dev")
    b = setup_otel("agent-temporal-dev")
    assert a is b is sentinel

    mod._PROVIDER = None
