"""Unit tests for megalos_panel.adapters.

Cover the three layers of behaviour the adapter modules own:

1. The registry ``ADAPTERS`` + ``dispatch()`` resolve model-name prefixes to
   adapter classes (``claude-`` -> Claude, ``gpt-`` -> OpenAI), and unknown
   prefixes raise ``ValueError``.
2. Each adapter translates a PanelRequest into exactly one SDK call and
   returns the assistant text. SDK client constructors are monkeypatched to
   return fake clients so no network is touched.
3. Provider SDK exceptions are classified into the internal retry taxonomy:
   rate-limit -> ``RateLimitError``, connection/timeout/5xx-status ->
   ``TransientError``. Non-5xx ``APIStatusError`` instances propagate
   unchanged.

Missing API-key handling is exercised by constructing adapters with neither
``api_key`` nor the relevant environment variable set.
"""

from __future__ import annotations

import httpx
import pytest

from megalos_panel.adapters import ADAPTERS, Adapter, dispatch
from megalos_panel.errors import RateLimitError, TransientError
from megalos_panel.types import PanelRequest


# --- Registry / dispatch ----------------------------------------------------


def test_adapters_registry_has_expected_prefixes():
    assert "claude-" in ADAPTERS
    assert "gpt-" in ADAPTERS


def test_dispatch_claude_prefix():
    from megalos_panel.adapters.claude import ClaudeAdapter

    assert dispatch("claude-opus-4-7") is ClaudeAdapter
    assert dispatch("claude-sonnet-4-5") is ClaudeAdapter


def test_dispatch_gpt_prefix():
    from megalos_panel.adapters.openai import OpenAIAdapter

    assert dispatch("gpt-4o") is OpenAIAdapter
    assert dispatch("gpt-5") is OpenAIAdapter


def test_dispatch_unknown_raises():
    with pytest.raises(ValueError, match="no panel adapter"):
        dispatch("llama-3")


def test_adapter_protocol_is_checkable():
    # Sanity: Adapter is a Protocol; both concrete adapters satisfy it
    # structurally. We can't runtime-check without @runtime_checkable, but we
    # can confirm the Protocol attribute surface.
    assert hasattr(Adapter, "invoke")


# --- Fake clients + fixtures ------------------------------------------------


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClaudeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]


class _FakeClaudeMessages:
    def __init__(self, behavior):  # type: ignore[no-untyped-def]
        self._behavior = behavior
        self.calls: list[dict] = []

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self._behavior(kwargs)


class _FakeClaudeClient:
    def __init__(self, behavior, **_ignored):  # type: ignore[no-untyped-def]
        self.messages = _FakeClaudeMessages(behavior)


class _FakeOpenAIMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeOpenAIChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeOpenAIMessage(content)


class _FakeOpenAIResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeOpenAIChoice(content)]


class _FakeOpenAICompletions:
    def __init__(self, behavior):  # type: ignore[no-untyped-def]
        self._behavior = behavior
        self.calls: list[dict] = []

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self._behavior(kwargs)


class _FakeOpenAIChat:
    def __init__(self, behavior):  # type: ignore[no-untyped-def]
        self.completions = _FakeOpenAICompletions(behavior)


class _FakeOpenAIClient:
    def __init__(self, behavior, **_ignored):  # type: ignore[no-untyped-def]
        self.chat = _FakeOpenAIChat(behavior)


@pytest.fixture
def patch_claude(monkeypatch):  # type: ignore[no-untyped-def]
    """Monkeypatch anthropic.Anthropic to a fake whose behavior is controlled."""
    import anthropic

    import megalos_panel.adapters.claude as claude_mod

    def _install(behavior):  # type: ignore[no-untyped-def]
        fake_client = _FakeClaudeClient(behavior)

        def _factory(**_ignored):  # type: ignore[no-untyped-def]
            return fake_client

        monkeypatch.setattr(claude_mod.anthropic, "Anthropic", _factory)
        return fake_client

    # Expose the installer so tests pick their own behavior.
    _install.anthropic = anthropic  # type: ignore[attr-defined]
    return _install


@pytest.fixture
def patch_openai(monkeypatch):  # type: ignore[no-untyped-def]
    """Monkeypatch openai.OpenAI to a fake whose behavior is controlled."""
    import openai

    import megalos_panel.adapters.openai as openai_mod

    def _install(behavior):  # type: ignore[no-untyped-def]
        fake_client = _FakeOpenAIClient(behavior)

        def _factory(**_ignored):  # type: ignore[no-untyped-def]
            return fake_client

        monkeypatch.setattr(openai_mod.openai, "OpenAI", _factory)
        return fake_client

    _install.openai = openai  # type: ignore[attr-defined]
    return _install


def _httpx_request() -> httpx.Request:
    return httpx.Request("POST", "https://example.invalid/v1/messages")


def _httpx_response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_httpx_request())


# --- ClaudeAdapter ----------------------------------------------------------


def test_claude_adapter_returns_assistant_text(patch_claude, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_client = patch_claude(lambda kwargs: _FakeClaudeResponse("selected answer"))

    from megalos_panel.adapters.claude import ClaudeAdapter

    adapter = ClaudeAdapter()
    result = adapter.invoke(PanelRequest(prompt="pick one", model="claude-opus-4-7"))
    assert result == "selected answer"
    # Exactly one call with the prompt routed as a single user message.
    assert len(fake_client.messages.calls) == 1
    call = fake_client.messages.calls[0]
    assert call["model"] == "claude-opus-4-7"
    assert call["messages"] == [{"role": "user", "content": "pick one"}]


def test_claude_adapter_classifies_rate_limit(patch_claude, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    anthropic = patch_claude.anthropic  # type: ignore[attr-defined]  # noqa: F841

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        import anthropic as a
        raise a.RateLimitError("rate limited", response=_httpx_response(429), body=None)

    patch_claude(_boom)

    from megalos_panel.adapters.claude import ClaudeAdapter

    adapter = ClaudeAdapter()
    with pytest.raises(RateLimitError):
        adapter.invoke(PanelRequest(prompt="hi", model="claude-opus-4-7"))


def test_claude_adapter_classifies_timeout(patch_claude, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        import anthropic as a
        raise a.APITimeoutError(_httpx_request())

    patch_claude(_boom)

    from megalos_panel.adapters.claude import ClaudeAdapter

    with pytest.raises(TransientError):
        ClaudeAdapter().invoke(PanelRequest(prompt="hi", model="claude-opus-4-7"))


def test_claude_adapter_classifies_connection_error(patch_claude, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        import anthropic as a
        raise a.APIConnectionError(request=_httpx_request())

    patch_claude(_boom)

    from megalos_panel.adapters.claude import ClaudeAdapter

    with pytest.raises(TransientError):
        ClaudeAdapter().invoke(PanelRequest(prompt="hi", model="claude-opus-4-7"))


def test_claude_adapter_classifies_5xx_status(patch_claude, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        import anthropic as a
        raise a.APIStatusError("boom", response=_httpx_response(503), body=None)

    patch_claude(_boom)

    from megalos_panel.adapters.claude import ClaudeAdapter

    with pytest.raises(TransientError):
        ClaudeAdapter().invoke(PanelRequest(prompt="hi", model="claude-opus-4-7"))


def test_claude_adapter_propagates_4xx_status(patch_claude, monkeypatch):  # type: ignore[no-untyped-def]
    """Non-5xx APIStatusError (e.g. 400 bad request) is terminal: re-raise as-is."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import anthropic as a

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        raise a.APIStatusError("bad request", response=_httpx_response(400), body=None)

    patch_claude(_boom)

    from megalos_panel.adapters.claude import ClaudeAdapter

    with pytest.raises(a.APIStatusError):
        ClaudeAdapter().invoke(PanelRequest(prompt="hi", model="claude-opus-4-7"))


def test_claude_adapter_missing_api_key_raises(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from megalos_panel.adapters.claude import ClaudeAdapter

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ClaudeAdapter()


# --- OpenAIAdapter ----------------------------------------------------------


def test_openai_adapter_returns_assistant_text(patch_openai, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_client = patch_openai(lambda kwargs: _FakeOpenAIResponse("gpt answer"))

    from megalos_panel.adapters.openai import OpenAIAdapter

    adapter = OpenAIAdapter()
    result = adapter.invoke(PanelRequest(prompt="choose", model="gpt-4o"))
    assert result == "gpt answer"
    assert len(fake_client.chat.completions.calls) == 1
    call = fake_client.chat.completions.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["messages"] == [{"role": "user", "content": "choose"}]


def test_openai_adapter_classifies_rate_limit(patch_openai, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        import openai as o
        raise o.RateLimitError("rate limited", response=_httpx_response(429), body=None)

    patch_openai(_boom)

    from megalos_panel.adapters.openai import OpenAIAdapter

    with pytest.raises(RateLimitError):
        OpenAIAdapter().invoke(PanelRequest(prompt="hi", model="gpt-4o"))


def test_openai_adapter_classifies_timeout(patch_openai, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        import openai as o
        raise o.APITimeoutError(_httpx_request())

    patch_openai(_boom)

    from megalos_panel.adapters.openai import OpenAIAdapter

    with pytest.raises(TransientError):
        OpenAIAdapter().invoke(PanelRequest(prompt="hi", model="gpt-4o"))


def test_openai_adapter_classifies_connection_error(patch_openai, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        import openai as o
        raise o.APIConnectionError(request=_httpx_request())

    patch_openai(_boom)

    from megalos_panel.adapters.openai import OpenAIAdapter

    with pytest.raises(TransientError):
        OpenAIAdapter().invoke(PanelRequest(prompt="hi", model="gpt-4o"))


def test_openai_adapter_classifies_5xx_status(patch_openai, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        import openai as o
        raise o.APIStatusError("boom", response=_httpx_response(502), body=None)

    patch_openai(_boom)

    from megalos_panel.adapters.openai import OpenAIAdapter

    with pytest.raises(TransientError):
        OpenAIAdapter().invoke(PanelRequest(prompt="hi", model="gpt-4o"))


def test_openai_adapter_propagates_4xx_status(patch_openai, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    import openai as o

    def _boom(_kwargs):  # type: ignore[no-untyped-def]
        raise o.APIStatusError("bad request", response=_httpx_response(400), body=None)

    patch_openai(_boom)

    from megalos_panel.adapters.openai import OpenAIAdapter

    with pytest.raises(o.APIStatusError):
        OpenAIAdapter().invoke(PanelRequest(prompt="hi", model="gpt-4o"))


def test_openai_adapter_missing_api_key_raises(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from megalos_panel.adapters.openai import OpenAIAdapter

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIAdapter()


# --- Error taxonomy exports -------------------------------------------------


def test_error_taxonomy_classes_are_exceptions():
    assert issubclass(RateLimitError, Exception)
    assert issubclass(TransientError, Exception)
