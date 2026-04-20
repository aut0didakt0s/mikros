"""MCP server registry loader.

Parses and validates an ``mcp_servers.yaml`` registry describing the external
MCP servers megalos may call into. Strict: rejects unknown fields, duplicate
names, malformed auth, unsupported transports.

Environment-variable resolution for ``auth.token_env`` is deliberately NOT
performed here — the registry is parsed once at server startup and kept
immutable. Missing env vars surface at call time (where the error is
actionable against a specific call, not daemon restart).

No FastMCP import, no network, no logging: the loader either returns a
``Registry`` or raises ``RegistryLoadError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


# --- Exceptions ------------------------------------------------------------


class RegistryLoadError(Exception):
    """Raised when ``mcp_servers.yaml`` is malformed or fails validation.

    Messages always name the file path and, where applicable, the 1-based
    server entry index and the specific field, e.g.::

        mcp_servers.yaml: entry 2 (name="weather"): unknown field 'retries'
    """


class UnknownServer(Exception):
    """Raised by ``Registry.get`` when no server matches the requested name.

    Carries ``available_names`` so callers can surface a useful error without
    another registry lookup.
    """

    def __init__(self, name: str, available_names: list[str]) -> None:
        self.name = name
        self.available_names = list(available_names)
        super().__init__(
            f"unknown MCP server {name!r}; available: {self.available_names}"
        )


# --- Typed config objects --------------------------------------------------

# Transport is kept as a Literal for v1. Widen the union (and the validator)
# when stdio/grpc support lands — no speculative extension today.
Transport = Literal["http"]
_VALID_TRANSPORTS: tuple[str, ...] = ("http",)


@dataclass(frozen=True)
class AuthConfig:
    """Authentication block. v1 supports bearer tokens via env-var lookup.

    ``token_env`` names an environment variable to read at call time; it is
    NOT resolved here.
    """

    type: Literal["bearer"]
    token_env: str


@dataclass(frozen=True)
class ServerConfig:
    """One MCP server entry from the registry."""

    name: str
    url: str
    transport: Transport
    auth: AuthConfig
    timeout_default: float | None = None


# --- Validation ------------------------------------------------------------

_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"servers"})
_SERVER_KEYS: frozenset[str] = frozenset(
    {"name", "url", "transport", "auth", "timeout_default"}
)
_SERVER_REQUIRED: frozenset[str] = frozenset({"name", "url", "transport", "auth"})
_AUTH_KEYS: frozenset[str] = frozenset({"type", "token_env"})
_AUTH_REQUIRED: frozenset[str] = frozenset({"type", "token_env"})


def _entry_prefix(path: Path, index: int, name: object) -> str:
    """Build the ``file: entry N (name="x"):`` prefix used in error messages."""
    if isinstance(name, str) and name:
        return f'{path.name}: entry {index} (name="{name}")'
    return f"{path.name}: entry {index}"


def _parse_auth(raw: object, path: Path, index: int, name: object) -> AuthConfig:
    prefix = _entry_prefix(path, index, name)
    if not isinstance(raw, dict):
        raise RegistryLoadError(f"{prefix}: 'auth' must be a mapping")
    unknown = set(raw.keys()) - _AUTH_KEYS
    if unknown:
        raise RegistryLoadError(
            f"{prefix}: unknown auth field(s) {sorted(unknown)!r}"
        )
    missing = _AUTH_REQUIRED - raw.keys()
    if missing:
        raise RegistryLoadError(
            f"{prefix}: 'auth' missing required field(s) {sorted(missing)!r}"
        )
    auth_type = raw["type"]
    if auth_type != "bearer":
        raise RegistryLoadError(
            f"{prefix}: unsupported auth.type {auth_type!r}; supported: ['bearer']"
        )
    token_env = raw["token_env"]
    if not isinstance(token_env, str) or not token_env:
        raise RegistryLoadError(
            f"{prefix}: auth.token_env must be a non-empty string"
        )
    return AuthConfig(type="bearer", token_env=token_env)


def _parse_server(raw: object, path: Path, index: int) -> ServerConfig:
    if not isinstance(raw, dict):
        raise RegistryLoadError(
            f"{path.name}: entry {index}: server entry must be a mapping"
        )
    name = raw.get("name")
    prefix = _entry_prefix(path, index, name)
    unknown = set(raw.keys()) - _SERVER_KEYS
    if unknown:
        raise RegistryLoadError(
            f"{prefix}: unknown field(s) {sorted(unknown)!r}"
        )
    missing = _SERVER_REQUIRED - raw.keys()
    if missing:
        raise RegistryLoadError(
            f"{prefix}: missing required field(s) {sorted(missing)!r}"
        )
    if not isinstance(name, str) or not name:
        raise RegistryLoadError(f"{prefix}: 'name' must be a non-empty string")
    url = raw["url"]
    if not isinstance(url, str) or not url:
        raise RegistryLoadError(f"{prefix}: 'url' must be a non-empty string")
    transport = raw["transport"]
    if transport not in _VALID_TRANSPORTS:
        raise RegistryLoadError(
            f"{prefix}: unsupported transport {transport!r}; "
            f"supported: {list(_VALID_TRANSPORTS)}"
        )
    auth = _parse_auth(raw["auth"], path, index, name)
    timeout_default: float | None = None
    if "timeout_default" in raw:
        td = raw["timeout_default"]
        # bool is a subclass of int; reject it explicitly.
        if isinstance(td, bool) or not isinstance(td, (int, float)):
            raise RegistryLoadError(
                f"{prefix}: 'timeout_default' must be a number"
            )
        if td <= 0:
            raise RegistryLoadError(
                f"{prefix}: 'timeout_default' must be positive"
            )
        timeout_default = float(td)
    return ServerConfig(
        name=name,
        url=url,
        transport="http",
        auth=auth,
        timeout_default=timeout_default,
    )


# --- Registry --------------------------------------------------------------


@dataclass(frozen=True)
class Registry:
    """Immutable collection of ``ServerConfig`` keyed by name."""

    servers: dict[str, ServerConfig]

    @classmethod
    def from_yaml(cls, path: Path) -> Registry:
        """Parse and validate an ``mcp_servers.yaml`` file.

        Raises ``RegistryLoadError`` on any malformed input. Messages always
        identify the file and, where meaningful, the 1-based entry index and
        offending field.
        """
        try:
            raw_text = path.read_text()
        except OSError as e:
            raise RegistryLoadError(f"{path}: cannot read file: {e}") from e
        try:
            doc = yaml.safe_load(raw_text)
        except yaml.YAMLError as e:
            raise RegistryLoadError(f"{path.name}: YAML parse error: {e}") from e
        if doc is None:
            # An empty file is a zero-server registry; treat as valid.
            return cls(servers={})
        if not isinstance(doc, dict):
            raise RegistryLoadError(
                f"{path.name}: top-level must be a mapping, "
                f"got {type(doc).__name__}"
            )
        unknown = set(doc.keys()) - _TOP_LEVEL_KEYS
        if unknown:
            raise RegistryLoadError(
                f"{path.name}: unknown top-level field(s) {sorted(unknown)!r}"
            )
        raw_servers = doc.get("servers", [])
        if not isinstance(raw_servers, list):
            raise RegistryLoadError(
                f"{path.name}: 'servers' must be a list, "
                f"got {type(raw_servers).__name__}"
            )
        servers: dict[str, ServerConfig] = {}
        seen: dict[str, int] = {}
        for i, raw in enumerate(raw_servers, start=1):
            cfg = _parse_server(raw, path, i)
            if cfg.name in seen:
                raise RegistryLoadError(
                    f"{path.name}: duplicate server name {cfg.name!r} "
                    f"at entries {seen[cfg.name]} and {i}"
                )
            seen[cfg.name] = i
            servers[cfg.name] = cfg
        return cls(servers=servers)

    def get(self, name: str) -> ServerConfig:
        """Return the config for ``name`` or raise ``UnknownServer``."""
        try:
            return self.servers[name]
        except KeyError:
            raise UnknownServer(name, sorted(self.servers.keys())) from None

    def names(self) -> list[str]:
        """Sorted list of registered server names."""
        return sorted(self.servers.keys())
