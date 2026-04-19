"""Unit tests for megalos_panel.types dataclass contract.

Verifies field names and types via dataclasses.fields so drift against the
mechanical gate (contract commitment A) fails loudly rather than silently.
"""

from dataclasses import fields

from megalos_panel.types import PanelRequest, PanelResult


def test_panel_request_required_fields_construct():
    req = PanelRequest(prompt="hello", model="claude-opus-4-7")
    assert req.prompt == "hello"
    assert req.model == "claude-opus-4-7"
    assert isinstance(req.request_id, str)
    assert len(req.request_id) > 0


def test_panel_request_default_request_id_unique_per_instance():
    a = PanelRequest(prompt="p", model="m")
    b = PanelRequest(prompt="p", model="m")
    assert a.request_id != b.request_id


def test_panel_request_explicit_request_id_preserved():
    req = PanelRequest(prompt="p", model="m", request_id="fixed-id-123")
    assert req.request_id == "fixed-id-123"


def test_panel_request_field_shape():
    names = {f.name: f.type for f in fields(PanelRequest)}
    assert set(names.keys()) == {"prompt", "model", "request_id"}
    assert names["prompt"] is str
    assert names["model"] is str
    assert names["request_id"] is str


def test_panel_result_success_shape():
    res = PanelResult(selection="A", raw_response="raw text", error=None)
    assert res.selection == "A"
    assert res.raw_response == "raw text"
    assert res.error is None


def test_panel_result_error_shape():
    res = PanelResult(selection="", raw_response="", error="provider exhausted")
    assert res.error == "provider exhausted"


def test_panel_result_field_shape():
    names = {f.name: f.type for f in fields(PanelResult)}
    assert set(names.keys()) == {"selection", "raw_response", "error"}
    assert names["selection"] is str
    assert names["raw_response"] is str
    # error is Optional[str] / str | None — expressed as a union type object.
    assert names["error"] == (str | None)
