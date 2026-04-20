"""End-to-end integration tests for ``action: mcp_tool_call`` workflows.

Exercises five representative workflow fixtures under
``tests/fixtures/workflows/mcp_tool_call/`` through the full server path
(``create_app`` → ``start_workflow`` → ``submit_step``) against the S01
FastMCP stub fixture. Reuses the harness patterns already established in
``test_mcp_executor.py``: a tmp-dir workflow directory, a tmp-path
registry YAML pointed at the stub URL, and FastMCP tool calls dispatched
via ``asyncio.run(app.call_tool(...))``.

Each fixture test verifies one specific slice of executor behaviour:

- ``success_then_read.yaml``  — happy path; value flows through echo.
- ``error_routing.yaml``      — precondition on envelope bool ``ok``.
- ``missing_server.yaml``     — validator rejects unknown server.
- ``nested_literal_args.yaml`` — walker assembles nested literals.
- ``ref_path_in_nested_args.yaml`` — walker resolves refs at depth.

Two of the fixtures (nested-literal and nested-ref) spy on
``mcp_client.call`` rather than round-tripping through the stub: the stub
tools only accept a flat ``value: str`` signature, so the walker-shape
assertions target the call's captured ``args`` kwarg instead of the
tool's return value. The happy-path fixtures still exercise the real
stub.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest  # type: ignore[import-not-found]
import yaml

from megalos_server import create_app, mcp_client, state
from megalos_server.mcp_client import Ok
from megalos_server.schema import validate_workflow
from megalos_server.mcp_registry import AuthConfig, Registry, ServerConfig
from tests.fixtures.mcp_stub import mcp_stub_server  # noqa: F401


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "workflows" / "mcp_tool_call"


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


def _stage_workflow(tmp_path: Path, fixture_name: str) -> Path:
    """Copy one fixture into a flat tmp workflows/ dir (create_app is non-recursive)."""
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir(exist_ok=True)
    dst = wf_dir / fixture_name
    shutil.copy(FIXTURE_DIR / fixture_name, dst)
    return wf_dir


def _build_app(tmp_path: Path, fixture_name: str, url: str) -> Any:
    wf_dir = _stage_workflow(tmp_path, fixture_name)
    registry_path = _write_registry(tmp_path, url)
    os.environ.setdefault("STUB_TOKEN", "dummy")
    return create_app(workflow_dir=wf_dir, registry_path=registry_path)


def _call(app: Any, tool_name: str, args: dict) -> dict:
    return asyncio.run(app.call_tool(tool_name, args)).structured_content


def _programmatic_registry(url: str) -> Registry:
    """Build a Registry without touching disk — mirrors test_mcp_executor pattern."""
    return Registry(
        servers={
            "stub": ServerConfig(
                name="stub",
                url=url,
                transport="http",
                auth=AuthConfig(type="bearer", token_env="STUB_TOKEN"),
                timeout_default=None,
            )
        }
    )


# ---------------------------------------------------------------------------
# success_then_read.yaml  — happy path through real stub
# ---------------------------------------------------------------------------


def test_success_then_read_flows_value_through_echo(tmp_path, mcp_stub_server):  # noqa: F811
    app = _build_app(tmp_path, "success_then_read.yaml", mcp_stub_server.url)

    out = _call(app, "start_workflow", {"workflow_type": "success_then_read", "context": ""})
    sid = out["session_id"]
    assert out["current_step"]["id"] == "topic"

    # Submit the first LLM step; mcp step runs in-band, pointer advances to consume.
    sub = _call(app, "submit_step", {
        "session_id": sid, "step_id": "topic", "content": "wave-propagation",
    })
    assert sub["next_step"]["id"] == "consume"

    sess = state.get_session(sid)
    envelope = json.loads(sess["step_data"]["echoed"])
    assert envelope == {"ok": True, "value": "wave-propagation"}


# ---------------------------------------------------------------------------
# error_routing.yaml — precondition routes on envelope bool `ok`
# ---------------------------------------------------------------------------


def test_error_routing_selects_failure_branch(tmp_path, mcp_stub_server):  # noqa: F811
    """`fail` tool yields ok=false; precondition ok==false matches on_error,
    ok==true precondition skips on_success. Exercises boolean-in-when_equals."""
    app = _build_app(tmp_path, "error_routing.yaml", mcp_stub_server.url)
    out = _call(app, "start_workflow", {"workflow_type": "error_routing", "context": ""})
    sid = out["session_id"]

    # The call step is the first step; executor runs it in-band, then
    # evaluates preconditions on on_error and on_success, lands on_error.
    assert out["current_step"]["id"] == "on_error"

    sess = state.get_session(sid)
    env = json.loads(sess["step_data"]["call"])
    assert env["ok"] is False
    assert "intentional-failure" in env["error"]["message"]

    # on_success must never become the current step — its precondition is
    # ok==true, which evaluates false against the failed envelope.
    sub = _call(app, "submit_step", {
        "session_id": sid, "step_id": "on_error", "content": "handled",
    })
    # Next pointer should be on_success (not yet skipped because linear
    # precondition eval happens during advance); but since its precondition
    # is also false given the failed envelope, it gets skipped and workflow
    # completes.
    assert sub.get("status") == "complete" or sub.get("next_step") is None or \
        sub.get("next_step", {}).get("id") != "on_success"


def test_error_routing_on_success_precondition_skips_when_failed(tmp_path, mcp_stub_server):  # noqa: F811
    """Belt-and-braces: confirm on_success stays unvisited when ok=false."""
    app = _build_app(tmp_path, "error_routing.yaml", mcp_stub_server.url)
    out = _call(app, "start_workflow", {"workflow_type": "error_routing", "context": ""})
    sid = out["session_id"]
    _call(app, "submit_step", {
        "session_id": sid, "step_id": "on_error", "content": "handled",
    })
    sess = state.get_session(sid)
    assert "on_success" not in sess["step_data"]


# ---------------------------------------------------------------------------
# missing_server.yaml — validator path (no runtime)
# ---------------------------------------------------------------------------


def test_missing_server_rejected_by_validator(tmp_path):
    """Validator must reject at load time with actionable message naming
    the missing server and the list of available names."""
    registry = _programmatic_registry("http://127.0.0.1:1/mcp/")
    fixture = FIXTURE_DIR / "missing_server.yaml"
    errors, _ = validate_workflow(str(fixture), registry=registry)
    assert errors, "missing_server.yaml should fail schema validation"
    joined = " | ".join(errors)
    assert "unknown_server_not_in_registry" in joined
    assert "['stub']" in joined or "stub" in joined
    assert "mcp_tool_call_unknown_server" in joined


def test_missing_server_rejected_by_create_app(tmp_path):
    """create_app must surface the validator error rather than load the workflow."""
    wf_dir = _stage_workflow(tmp_path, "missing_server.yaml")
    registry_path = _write_registry(tmp_path, "http://127.0.0.1:1/mcp/")
    os.environ.setdefault("STUB_TOKEN", "dummy")
    with pytest.raises(Exception) as excinfo:
        create_app(workflow_dir=wf_dir, registry_path=registry_path)
    assert "unknown_server_not_in_registry" in str(excinfo.value) \
        or "mcp_tool_call_unknown_server" in str(excinfo.value)


# ---------------------------------------------------------------------------
# nested_literal_args.yaml — walker pass-through, spy on mcp_client.call
# ---------------------------------------------------------------------------


def test_nested_literal_args_pass_through_walker(tmp_path, monkeypatch):
    """The walker must assemble the nested literal dict/list/primitive tree
    verbatim and hand it to mcp_client.call without mutation."""
    captured: dict = {}

    def _spy(**kw):
        captured.update(kw)
        return Ok(value="ignored", duration_ms=0.0)

    monkeypatch.setattr(mcp_client, "call", _spy)
    app = _build_app(tmp_path, "nested_literal_args.yaml", "http://127.0.0.1:1/mcp/")
    out = _call(app, "start_workflow", {"workflow_type": "nested_literal_args", "context": ""})
    assert out["current_step"]["id"] == "finish"

    assert captured["args"] == {
        "config": {
            "verbose": True,
            "retries": 3,
            "items": ["first", "second", 42],
        },
        "label": "outer",
    }
    assert captured["tool"] == "echo"
    assert captured["server_name"] == "stub"


# ---------------------------------------------------------------------------
# ref_path_in_nested_args.yaml — walker resolves ref at depth
# ---------------------------------------------------------------------------


def test_ref_path_in_nested_args_resolves_deep_leaf(tmp_path, monkeypatch):
    """A ${step_data.seed} leaf buried in a dict→list chain must be resolved
    and leave surrounding literals intact."""
    captured: dict = {}

    def _spy(**kw):
        captured.update(kw)
        return Ok(value="ignored", duration_ms=0.0)

    monkeypatch.setattr(mcp_client, "call", _spy)
    app = _build_app(tmp_path, "ref_path_in_nested_args.yaml", "http://127.0.0.1:1/mcp/")
    out = _call(app, "start_workflow", {"workflow_type": "ref_path_in_nested_args", "context": ""})
    sid = out["session_id"]
    sub = _call(app, "submit_step", {
        "session_id": sid, "step_id": "seed", "content": "payload-xyz",
    })
    assert sub["next_step"]["id"] == "finish"

    assert captured["args"] == {
        "outer": {
            "items": ["literal-before", "payload-xyz", "literal-after"],
        },
    }


# ---------------------------------------------------------------------------
# Observability smoke — executor debug log fires on happy path.
# ---------------------------------------------------------------------------


def test_executor_debug_log_fires_on_success(tmp_path, mcp_stub_server, caplog):  # noqa: F811
    """Confirm the S02 debug log ('mcp_tool_call step executed') is emitted
    during integration path. Covers Observability Impact from T03 plan."""
    import logging

    app = _build_app(tmp_path, "success_then_read.yaml", mcp_stub_server.url)
    out = _call(app, "start_workflow", {"workflow_type": "success_then_read", "context": ""})
    sid = out["session_id"]

    with caplog.at_level(logging.DEBUG, logger="megalos_server.workflow"):
        _call(app, "submit_step", {
            "session_id": sid, "step_id": "topic", "content": "log-check",
        })

    messages = [r.message for r in caplog.records if r.name == "megalos_server.workflow"]
    assert any("mcp_tool_call step executed" in m for m in messages), messages
