"""Cold-start latency benchmark for `megalos_server.mcp_client.call`.

Migrates the piggyback JSONL recorder that previously rode on top of
`tests/test_mcp_client.py` onto the pytest-benchmark harness. The
recorder's p50/p95/max summary is not preserved — pytest-benchmark emits
its own min/max/mean/stddev/median/IQR table. The historical JSONL
output at `runs/m006_s01_t03_latency.jsonl` is not reproduced; prior
samples are not load-bearing.

The benchmark exercises the same code path the recorder did:
`mcp_client.call("stub", "echo", {"value": "hello"}, reg)` against the
existing `tests/fixtures/mcp_stub.mcp_stub_server` fixture. It imports
that fixture through `tests.fixtures.mcp_stub`, which resolves because
`benchmarks/pytest.ini` declares `pythonpath = ..`.
"""

from __future__ import annotations

from megalos_server import mcp_client
from megalos_server.mcp_client import Ok
from megalos_server.mcp_registry import AuthConfig, Registry, ServerConfig
from tests.fixtures.mcp_stub import mcp_stub_server  # noqa: F401


def _registry_for_stub(stub_url: str) -> Registry:
    return Registry(
        servers={
            "stub": ServerConfig(
                name="stub",
                url=stub_url,
                transport="http",
                auth=AuthConfig(type="bearer", token_env="STUB_TOKEN"),
                timeout_default=None,
            )
        }
    )


def bench_mcp_client_cold_start(  # type: ignore[no-untyped-def]
    benchmark, mcp_stub_server, monkeypatch  # noqa: F811
) -> None:
    """Measure `mcp_client.call` round-trip against the in-process stub.

    Each pytest-benchmark iteration clears the validator cache so every
    call re-runs `tools/list` — this matches the "cold-start" framing
    the original recorder used. Without the clear, the cache hit on
    iteration 2+ would collapse the measurement to a pure `tools/call`
    round-trip, which is a different signal (worth benchmarking in S02,
    not here).
    """
    monkeypatch.setenv("STUB_TOKEN", "test-token")
    reg = _registry_for_stub(mcp_stub_server.url)

    def _call_cold() -> None:
        mcp_client._validator_cache.clear()
        outcome = mcp_client.call("stub", "echo", {"value": "hello"}, reg)
        assert isinstance(outcome, Ok), outcome

    benchmark(_call_cold)
