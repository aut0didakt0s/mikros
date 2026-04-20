"""Tests for megalos_server.mcp_registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from megalos_server.mcp_registry import (
    AuthConfig,
    Registry,
    RegistryLoadError,
    ServerConfig,
    UnknownServer,
)


FIXTURES = Path(__file__).parent / "fixtures" / "mcp_registry"


# --- Happy path ------------------------------------------------------------


def test_valid_registry_parses_multiple_servers() -> None:
    reg = Registry.from_yaml(FIXTURES / "valid.yaml")
    assert reg.names() == ["search", "weather"]
    weather = reg.get("weather")
    assert isinstance(weather, ServerConfig)
    assert weather.name == "weather"
    assert weather.url == "https://weather.example.com/mcp"
    assert weather.transport == "http"
    assert isinstance(weather.auth, AuthConfig)
    assert weather.auth.type == "bearer"
    assert weather.auth.token_env == "WEATHER_TOKEN"
    assert weather.timeout_default == 30.0

    search = reg.get("search")
    assert search.timeout_default is None
    assert search.auth.token_env == "SEARCH_TOKEN"


def test_empty_file_parses_as_empty_registry(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("")
    reg = Registry.from_yaml(path)
    assert reg.names() == []


def test_empty_servers_list_parses_as_empty_registry(tmp_path: Path) -> None:
    path = tmp_path / "empty_list.yaml"
    path.write_text("servers: []\n")
    reg = Registry.from_yaml(path)
    assert reg.names() == []


# --- Malformed inputs ------------------------------------------------------


def test_malformed_yaml_raises_actionable() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "malformed_yaml.yaml")
    msg = str(excinfo.value)
    assert "malformed_yaml.yaml" in msg
    assert "YAML parse error" in msg


def test_missing_file_raises() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "does_not_exist.yaml")
    assert "cannot read file" in str(excinfo.value)


def test_top_level_not_mapping_raises(tmp_path: Path) -> None:
    path = tmp_path / "list_top.yaml"
    path.write_text("- a\n- b\n")
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "top-level must be a mapping" in str(excinfo.value)


def test_unknown_top_level_field_rejected() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "unknown_top_level.yaml")
    msg = str(excinfo.value)
    assert "unknown top-level field" in msg
    assert "'version'" in msg


def test_servers_not_a_list_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_servers.yaml"
    path.write_text("servers: {name: x}\n")
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "'servers' must be a list" in str(excinfo.value)


def test_server_entry_not_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad_entry.yaml"
    path.write_text("servers:\n  - just-a-string\n")
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    msg = str(excinfo.value)
    assert "entry 1" in msg
    assert "must be a mapping" in msg


def test_unknown_per_server_field_rejected() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "unknown_server_field.yaml")
    msg = str(excinfo.value)
    assert "entry 1" in msg
    assert 'name="weather"' in msg
    assert "'retries'" in msg


def test_duplicate_server_name_rejected_with_both_indices() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "duplicate_name.yaml")
    msg = str(excinfo.value)
    assert "duplicate server name" in msg
    assert "'weather'" in msg
    assert "entries 1 and 2" in msg


def test_missing_name_rejected() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "missing_name.yaml")
    msg = str(excinfo.value)
    assert "entry 1" in msg
    assert "'name'" in msg


def test_missing_url_rejected() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "missing_url.yaml")
    msg = str(excinfo.value)
    assert "entry 1" in msg
    assert "'url'" in msg


def test_empty_name_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty_name.yaml"
    path.write_text(
        "servers:\n"
        "  - name: ''\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: X\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "'name' must be a non-empty string" in str(excinfo.value)


def test_non_string_url_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_url.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: 42\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: X\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "'url' must be a non-empty string" in str(excinfo.value)


def test_unsupported_transport_stdio() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "bad_transport_stdio.yaml")
    msg = str(excinfo.value)
    assert "unsupported transport" in msg
    assert "'stdio'" in msg


def test_unsupported_transport_grpc() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "bad_transport_grpc.yaml")
    assert "'grpc'" in str(excinfo.value)


def test_unsupported_transport_arbitrary(tmp_path: Path) -> None:
    path = tmp_path / "bad_transport.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: pigeon\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: X\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "unsupported transport" in str(excinfo.value)


# --- Auth validation -------------------------------------------------------


def test_auth_not_a_mapping_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_auth.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth: not-a-mapping\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "'auth' must be a mapping" in str(excinfo.value)


def test_auth_unknown_field_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_auth_field.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: X\n"
        "      realm: foo\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "unknown auth field" in str(excinfo.value)


def test_auth_missing_field_rejected(tmp_path: Path) -> None:
    path = tmp_path / "missing_auth_field.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "missing required field" in str(excinfo.value)
    assert "token_env" in str(excinfo.value)


def test_auth_unsupported_type_rejected() -> None:
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(FIXTURES / "bad_auth.yaml")
    assert "unsupported auth.type" in str(excinfo.value)


def test_auth_empty_token_env_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty_token_env.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: ''\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "token_env must be a non-empty string" in str(excinfo.value)


# --- timeout_default validation --------------------------------------------


def test_timeout_default_non_number_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_timeout.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: X\n"
        "    timeout_default: soon\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "'timeout_default' must be a number" in str(excinfo.value)


def test_timeout_default_bool_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bool_timeout.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: X\n"
        "    timeout_default: true\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "'timeout_default' must be a number" in str(excinfo.value)


def test_timeout_default_non_positive_rejected(tmp_path: Path) -> None:
    path = tmp_path / "zero_timeout.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: X\n"
        "    timeout_default: 0\n"
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        Registry.from_yaml(path)
    assert "'timeout_default' must be positive" in str(excinfo.value)


def test_timeout_default_integer_coerced_to_float(tmp_path: Path) -> None:
    path = tmp_path / "int_timeout.yaml"
    path.write_text(
        "servers:\n"
        "  - name: weather\n"
        "    url: https://x.example.com\n"
        "    transport: http\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: X\n"
        "    timeout_default: 15\n"
    )
    reg = Registry.from_yaml(path)
    cfg = reg.get("weather")
    assert cfg.timeout_default == 15.0
    assert isinstance(cfg.timeout_default, float)


# --- Registry.get ----------------------------------------------------------


def test_get_missing_raises_unknown_server_with_available_names() -> None:
    reg = Registry.from_yaml(FIXTURES / "valid.yaml")
    with pytest.raises(UnknownServer) as excinfo:
        reg.get("missing")
    err = excinfo.value
    assert err.name == "missing"
    # available_names is sorted and complete
    assert err.available_names == ["search", "weather"]
    assert "missing" in str(err)
    assert "search" in str(err)
    assert "weather" in str(err)


def test_get_on_empty_registry_lists_no_names(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("servers: []\n")
    reg = Registry.from_yaml(path)
    with pytest.raises(UnknownServer) as excinfo:
        reg.get("anything")
    assert excinfo.value.available_names == []
