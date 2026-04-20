"""Tests for megalos_server.mcp_executor and the runtime fork path in tools.py.

Covers:

- ``resolve_args`` walker: nested dict/list recursion, ref substitution,
  literal pass-through, ``_REF_ABSENT`` propagation, ``_SkippedPredecessor``
  propagation.
- ``execute_mcp_tool_call_step``: envelope mapping for each ``CallOutcome``
  variant, unresolved-ref short-circuit (no call made), timeout precedence,
  registry-None invariant error.
- Runtime fork in ``register_tools`` / ``submit_step`` / ``start_workflow``:
  auto-executes ``mcp_tool_call`` steps, writes envelopes to step_data,
  advances to the next non-mcp step, surfaces cascade errors.
- Integration path using the S01 stub FastMCP server (`mcp_stub_server`
  fixture) exercises real ``mcp_client.call`` against ``echo`` / ``fail`` /
  ref-path-in-args.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest  # type: ignore[import-not-found]
import yaml

from megalos_server import create_app, mcp_client, state
from megalos_server.mcp_client import (
    Ok,
    ProtocolError,
    SchemaValidationError,
    TimeoutError as McpTimeoutError,
    ToolExecutionError,
    TransportError,
)
from megalos_server.mcp_executor import (
    _effective_timeout,
    execute_mcp_tool_call_step,
    find_absent_ref_path,
    resolve_args,
)
from megalos_server.mcp_registry import AuthConfig, Registry, ServerConfig
from megalos_server.tools import _REF_ABSENT, _SkippedPredecessor
from tests.fixtures.mcp_stub import mcp_stub_server  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_registry(url: str = "http://127.0.0.1:1/mcp/", timeout_default: float | None = None) -> Registry:
    return Registry(
        servers={
            "stub": ServerConfig(
                name="stub",
                url=url,
                transport="http",
                auth=AuthConfig(type="bearer", token_env="STUB_TOKEN"),
                timeout_default=timeout_default,
            )
        }
    )


def _write_workflow(tmp_path: Path, wf: dict) -> Path:
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir(exist_ok=True)
    path = wf_dir / f"{wf['name']}.yaml"
    path.write_text(yaml.safe_dump(wf))
    return path


def _write_registry(tmp_path: Path, url: str) -> Path:
    doc = {
        "servers": [
            {
                "name": "stub",
                "url": url,
                "transport": "http",
                "auth": {"type": "bearer", "token_env": "STUB_TOKEN"},
            }
        ]
    }
    path = tmp_path / "mcp_servers.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def _call_tool_on(app: Any, tool_name: str, args: dict) -> dict:
    result = asyncio.run(app.call_tool(tool_name, args))
    return result.structured_content


# ---------------------------------------------------------------------------
# resolve_args
# ---------------------------------------------------------------------------


def test_resolve_args_walks_nested_structures():
    step_data = {"alpha": json.dumps({"user": "Alice", "count": 3})}
    raw = {
        "greeting": "${step_data.alpha.user}",
        "static": "hello",
        "nested": {"count": "${step_data.alpha.count}"},
        "items": ["${step_data.alpha.user}", "literal"],
    }
    out = resolve_args(raw, step_data, skipped_set=set(), referencing_step_id="t")
    assert out == {
        "greeting": "Alice",
        "static": "hello",
        "nested": {"count": 3},
        "items": ["Alice", "literal"],
    }


def test_resolve_args_literal_pass_through():
    out = resolve_args(
        {"k": "plain", "n": 5, "b": True, "f": 1.5, "none": None},
        step_data={},
        skipped_set=set(),
        referencing_step_id="t",
    )
    assert out == {"k": "plain", "n": 5, "b": True, "f": 1.5, "none": None}


def test_resolve_args_missing_ref_returns_absent_sentinel():
    out = resolve_args(
        {"v": "${step_data.missing}"},
        step_data={},
        skipped_set=set(),
        referencing_step_id="t",
    )
    assert out["v"] is _REF_ABSENT


def test_resolve_args_propagates_skipped_predecessor():
    with pytest.raises(_SkippedPredecessor):
        resolve_args(
            {"v": "${step_data.alpha}"},
            step_data={},
            skipped_set={"alpha"},
            referencing_step_id="consumer",
        )


def test_find_absent_ref_path_locates_leaf():
    tree = {"a": {"b": ["x", _REF_ABSENT]}, "c": "y"}
    assert find_absent_ref_path(tree) == "a.b[1]"


def test_find_absent_ref_path_none_when_fully_resolved():
    assert find_absent_ref_path({"a": 1, "b": ["x", "y"]}) is None


# ---------------------------------------------------------------------------
# execute_mcp_tool_call_step — envelope mapping via patched mcp_client.call
# ---------------------------------------------------------------------------


def _make_step(timeout: float | None = None) -> dict:
    step = {
        "id": "s1",
        "title": "call stub",
        "action": "mcp_tool_call",
        "server": "stub",
        "tool": "echo",
        "args": {"value": "hi"},
    }
    if timeout is not None:
        step["timeout"] = timeout
    return step


def test_envelope_ok(monkeypatch):
    monkeypatch.setattr(mcp_client, "call", lambda **kw: Ok(value="yo", duration_ms=1.0))
    env = execute_mcp_tool_call_step(
        _make_step(), step_data={}, skipped_set=set(),
        registry=_stub_registry(), workflow_name="wf",
    )
    assert env == {"ok": True, "value": "yo"}


def test_envelope_tool_execution_error(monkeypatch):
    monkeypatch.setattr(
        mcp_client, "call",
        lambda **kw: ToolExecutionError(message="boom", duration_ms=1.0),
    )
    env = execute_mcp_tool_call_step(
        _make_step(), step_data={}, skipped_set=set(),
        registry=_stub_registry(), workflow_name="wf",
    )
    assert env == {"ok": False, "error": {"message": "boom"}}


def test_envelope_transport_error(monkeypatch):
    monkeypatch.setattr(
        mcp_client, "call",
        lambda **kw: TransportError(detail="no dns", duration_ms=1.0),
    )
    env = execute_mcp_tool_call_step(
        _make_step(), step_data={}, skipped_set=set(),
        registry=_stub_registry(), workflow_name="wf",
    )
    assert env == {"ok": False, "error": {"message": "transport error: no dns"}}


def test_envelope_protocol_error(monkeypatch):
    monkeypatch.setattr(
        mcp_client, "call",
        lambda **kw: ProtocolError(detail="bad envelope", duration_ms=1.0),
    )
    env = execute_mcp_tool_call_step(
        _make_step(), step_data={}, skipped_set=set(),
        registry=_stub_registry(), workflow_name="wf",
    )
    assert env == {"ok": False, "error": {"message": "protocol error: bad envelope"}}


def test_envelope_schema_error(monkeypatch):
    monkeypatch.setattr(
        mcp_client, "call",
        lambda **kw: SchemaValidationError(detail="wrong type", duration_ms=1.0),
    )
    env = execute_mcp_tool_call_step(
        _make_step(), step_data={}, skipped_set=set(),
        registry=_stub_registry(), workflow_name="wf",
    )
    assert env == {"ok": False, "error": {"message": "schema error: wrong type"}}


def test_envelope_timeout(monkeypatch):
    monkeypatch.setattr(
        mcp_client, "call",
        lambda **kw: McpTimeoutError(duration_ms=1.0),
    )
    env = execute_mcp_tool_call_step(
        _make_step(), step_data={}, skipped_set=set(),
        registry=_stub_registry(), workflow_name="wf",
    )
    assert env == {"ok": False, "error": {"message": "timeout"}}


def test_unresolved_ref_short_circuits_call(monkeypatch):
    called = {"n": 0}

    def _spy(**kw):  # pragma: no cover - should not run
        called["n"] += 1
        return Ok(value="", duration_ms=0.0)

    monkeypatch.setattr(mcp_client, "call", _spy)
    step = {
        "id": "s1", "title": "x", "action": "mcp_tool_call",
        "server": "stub", "tool": "echo",
        "args": {"value": "${step_data.missing.field}"},
    }
    env = execute_mcp_tool_call_step(
        step, step_data={}, skipped_set=set(),
        registry=_stub_registry(), workflow_name="wf",
    )
    assert env["ok"] is False
    assert "unresolved arg ref" in env["error"]["message"]
    assert "step_data.value" in env["error"]["message"] or "step_data." in env["error"]["message"]
    assert called["n"] == 0


def test_registry_none_raises_invariant_error():
    with pytest.raises(RuntimeError, match="registry required"):
        execute_mcp_tool_call_step(
            _make_step(), step_data={}, skipped_set=set(),
            registry=None, workflow_name="wf",
        )


# ---------------------------------------------------------------------------
# Timeout precedence
# ---------------------------------------------------------------------------


def test_timeout_step_wins_over_registry_default():
    reg = _stub_registry(timeout_default=10.0)
    step = _make_step(timeout=2.0)
    assert _effective_timeout(step, reg) == 2.0


def test_timeout_registry_default_used_when_step_omits():
    reg = _stub_registry(timeout_default=7.5)
    step = _make_step()
    assert _effective_timeout(step, reg) == 7.5


def test_timeout_falls_back_to_module_default():
    reg = _stub_registry(timeout_default=None)
    step = _make_step()
    assert _effective_timeout(step, reg) == 30.0


def test_execute_passes_effective_timeout_to_call(monkeypatch):
    reg = _stub_registry(timeout_default=9.0)
    captured: dict = {}

    def _spy(**kw):
        captured.update(kw)
        return Ok(value="x", duration_ms=0.0)

    monkeypatch.setattr(mcp_client, "call", _spy)
    execute_mcp_tool_call_step(
        _make_step(timeout=3.0), step_data={}, skipped_set=set(),
        registry=reg, workflow_name="wf",
    )
    assert captured["timeout"] == 3.0
    assert captured["server_name"] == "stub"
    assert captured["tool"] == "echo"
    assert captured["args"] == {"value": "hi"}


# ---------------------------------------------------------------------------
# Runtime fork — end-to-end via start_workflow / submit_step
# ---------------------------------------------------------------------------


def _build_app_with_mcp_workflow(tmp_path: Path, wf: dict, url: str) -> Any:
    _write_workflow(tmp_path, wf)
    _write_registry(tmp_path, url)
    # Monkeypatching env so registry's token_env is present even for patched
    # call paths (real call path is patched in the patched-client tests).
    os.environ.setdefault("STUB_TOKEN", "dummy")
    return create_app(
        workflow_dir=tmp_path / "workflows",
        registry_path=tmp_path / "mcp_servers.yaml",
    )


def test_fork_executes_mcp_step_at_start(tmp_path, monkeypatch):
    wf = {
        "name": "mcp_first",
        "description": "d",
        "category": "test",
        "output_format": "structured_code",
        "steps": [
            {
                "id": "ping",
                "title": "Ping",
                "action": "mcp_tool_call",
                "server": "stub",
                "tool": "echo",
                "args": {"value": "hello"},
            },
            {
                "id": "finish",
                "title": "Finish",
                "directive_template": "Summarize. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
        ],
    }
    monkeypatch.setattr(
        mcp_client, "call",
        lambda **kw: Ok(value="hello-back", duration_ms=1.0),
    )
    app = _build_app_with_mcp_workflow(tmp_path, wf, "http://127.0.0.1:1/mcp/")

    out = _call_tool_on(app, "start_workflow", {"workflow_type": "mcp_first", "context": ""})
    assert out["current_step"]["id"] == "finish"  # executor skipped past ping
    sid = out["session_id"]

    sess = state.get_session(sid)
    assert "ping" in sess["step_data"]
    assert json.loads(sess["step_data"]["ping"]) == {"ok": True, "value": "hello-back"}


def test_fork_executes_mcp_step_mid_workflow(tmp_path, monkeypatch):
    wf = {
        "name": "mcp_mid",
        "description": "d",
        "category": "test",
        "output_format": "structured_code",
        "steps": [
            {
                "id": "alpha",
                "title": "Alpha",
                "directive_template": "Say something. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
            {
                "id": "ping",
                "title": "Ping",
                "action": "mcp_tool_call",
                "server": "stub",
                "tool": "echo",
                "args": {"value": "${step_data.alpha}"},
            },
            {
                "id": "bravo",
                "title": "Bravo",
                "directive_template": "Summarize. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
        ],
    }
    captured_args: dict = {}

    def _spy(**kw):
        captured_args.update(kw)
        return Ok(value="echoed", duration_ms=1.0)

    monkeypatch.setattr(mcp_client, "call", _spy)
    app = _build_app_with_mcp_workflow(tmp_path, wf, "http://127.0.0.1:1/mcp/")

    out = _call_tool_on(app, "start_workflow", {"workflow_type": "mcp_mid", "context": ""})
    sid = out["session_id"]
    sub = _call_tool_on(app, "submit_step", {
        "session_id": sid, "step_id": "alpha", "content": "Alice",
    })
    assert sub["next_step"]["id"] == "bravo"  # executor ran ping in-band
    sess = state.get_session(sid)
    assert json.loads(sess["step_data"]["ping"]) == {"ok": True, "value": "echoed"}
    assert captured_args["args"] == {"value": "Alice"}  # ref resolved


def test_fork_envelope_on_tool_error(tmp_path, monkeypatch):
    wf = {
        "name": "mcp_err",
        "description": "d",
        "category": "test",
        "output_format": "structured_code",
        "steps": [
            {
                "id": "ping",
                "title": "Ping",
                "action": "mcp_tool_call",
                "server": "stub",
                "tool": "fail",
                "args": {"message": "nope"},
            },
            {
                "id": "finish",
                "title": "Finish",
                "directive_template": "Summarize. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
        ],
    }
    monkeypatch.setattr(
        mcp_client, "call",
        lambda **kw: ToolExecutionError(message="nope", duration_ms=1.0),
    )
    app = _build_app_with_mcp_workflow(tmp_path, wf, "http://127.0.0.1:1/mcp/")
    out = _call_tool_on(app, "start_workflow", {"workflow_type": "mcp_err", "context": ""})
    sid = out["session_id"]
    sess = state.get_session(sid)
    assert json.loads(sess["step_data"]["ping"]) == {
        "ok": False, "error": {"message": "nope"},
    }


def test_fork_cascade_error_when_predecessor_skipped(tmp_path, monkeypatch):
    wf = {
        "name": "mcp_cascade",
        "description": "d",
        "category": "test",
        "output_format": "structured_code",
        "steps": [
            {
                "id": "gate",
                "title": "Gate",
                "directive_template": "Give yes/no. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
            {
                "id": "maybe",
                "title": "Maybe",
                "directive_template": "Only if gate==yes. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
                "precondition": {"when_equals": {"ref": "step_data.gate", "value": "yes"}},
            },
            {
                "id": "ping",
                "title": "Ping",
                "action": "mcp_tool_call",
                "server": "stub",
                "tool": "echo",
                "args": {"value": "${step_data.maybe}"},
            },
            {
                "id": "finish",
                "title": "Finish",
                "directive_template": "Done. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
        ],
    }
    called = {"n": 0}

    def _spy(**kw):  # pragma: no cover — should not run
        called["n"] += 1
        return Ok(value="x", duration_ms=0.0)

    monkeypatch.setattr(mcp_client, "call", _spy)
    app = _build_app_with_mcp_workflow(tmp_path, wf, "http://127.0.0.1:1/mcp/")

    out = _call_tool_on(app, "start_workflow", {"workflow_type": "mcp_cascade", "context": ""})
    sid = out["session_id"]
    sub = _call_tool_on(app, "submit_step", {
        "session_id": sid, "step_id": "gate", "content": "no",
    })
    # ping depends on maybe (which was skipped); executor raises cascade.
    assert sub["status"] == "error"
    assert sub["code"] == "skipped_predecessor_reference"
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# Integration — real FastMCP stub server
# ---------------------------------------------------------------------------


def _integration_app(tmp_path: Path, wf: dict, url: str) -> Any:
    _write_workflow(tmp_path, wf)
    _write_registry(tmp_path, url)
    os.environ.setdefault("STUB_TOKEN", "dummy")
    return create_app(
        workflow_dir=tmp_path / "workflows",
        registry_path=tmp_path / "mcp_servers.yaml",
    )


def test_integration_echo_success(tmp_path, mcp_stub_server):  # noqa: F811
    wf = {
        "name": "int_echo",
        "description": "d",
        "category": "test",
        "output_format": "structured_code",
        "steps": [
            {
                "id": "ping",
                "title": "Ping",
                "action": "mcp_tool_call",
                "server": "stub",
                "tool": "echo",
                "args": {"value": "hello-there"},
            },
            {
                "id": "finish",
                "title": "Finish",
                "directive_template": "Summarize. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
        ],
    }
    app = _integration_app(tmp_path, wf, mcp_stub_server.url)
    out = _call_tool_on(app, "start_workflow", {"workflow_type": "int_echo", "context": ""})
    sid = out["session_id"]
    assert out["current_step"]["id"] == "finish"
    sess = state.get_session(sid)
    env = json.loads(sess["step_data"]["ping"])
    assert env["ok"] is True
    assert env["value"] == "hello-there"


def test_integration_fail_tool_error_envelope(tmp_path, mcp_stub_server):  # noqa: F811
    wf = {
        "name": "int_fail",
        "description": "d",
        "category": "test",
        "output_format": "structured_code",
        "steps": [
            {
                "id": "ping",
                "title": "Ping",
                "action": "mcp_tool_call",
                "server": "stub",
                "tool": "fail",
                "args": {"message": "boom-please"},
            },
            {
                "id": "finish",
                "title": "Finish",
                "directive_template": "Summarize. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
        ],
    }
    app = _integration_app(tmp_path, wf, mcp_stub_server.url)
    out = _call_tool_on(app, "start_workflow", {"workflow_type": "int_fail", "context": ""})
    sid = out["session_id"]
    sess = state.get_session(sid)
    env = json.loads(sess["step_data"]["ping"])
    assert env["ok"] is False
    assert "boom-please" in env["error"]["message"]


def test_integration_ref_path_resolves_from_prior_step(tmp_path, mcp_stub_server):  # noqa: F811
    wf = {
        "name": "int_ref",
        "description": "d",
        "category": "test",
        "output_format": "structured_code",
        "steps": [
            {
                "id": "alpha",
                "title": "Alpha",
                "directive_template": "Say something. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
            {
                "id": "ping",
                "title": "Ping",
                "action": "mcp_tool_call",
                "server": "stub",
                "tool": "echo",
                "args": {"value": "${step_data.alpha}"},
            },
            {
                "id": "bravo",
                "title": "Bravo",
                "directive_template": "Summarize. (stub)",
                "gates": ["done"],
                "anti_patterns": ["skip"],
            },
        ],
    }
    app = _integration_app(tmp_path, wf, mcp_stub_server.url)
    out = _call_tool_on(app, "start_workflow", {"workflow_type": "int_ref", "context": ""})
    sid = out["session_id"]
    sub = _call_tool_on(app, "submit_step", {
        "session_id": sid, "step_id": "alpha", "content": "payload-carried",
    })
    assert sub["next_step"]["id"] == "bravo"
    sess = state.get_session(sid)
    env = json.loads(sess["step_data"]["ping"])
    assert env == {"ok": True, "value": "payload-carried"}
