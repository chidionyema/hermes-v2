"""``otto.boot.config`` — every value named by an environment variable,
and a missing token is a loud, structured refusal, never a silent
default (LAW 46, LAW 50).
"""

from __future__ import annotations

import pytest

from otto.boot.config import (
    API_BASE_ENV,
    BootConfig,
    CONFIG_PATH_ENV,
    PORT_ENV,
    TOKEN_ENV,
    read_api_base,
    read_chat_allowlist,
    read_port,
    read_token,
)
from otto.boot.errors import BootRefused


def test_missing_token_refuses_loudly_naming_the_env_var() -> None:
    with pytest.raises(BootRefused) as excinfo:
        read_token({})
    assert TOKEN_ENV in excinfo.value.reason
    # The refusal is structured (safe to log and to print), never a bare
    # string, and never contains any token value (there was none to leak).
    as_dict = excinfo.value.as_dict()
    assert as_dict["error"] == "otto.boot.refused"
    assert TOKEN_ENV in as_dict["reason"]


def test_blank_token_is_treated_as_missing() -> None:
    with pytest.raises(BootRefused):
        read_token({TOKEN_ENV: "   "})


def test_token_is_read_verbatim_when_present() -> None:
    assert read_token({TOKEN_ENV: "a-token-value"}) == "a-token-value"


def test_missing_config_path_refuses_loudly() -> None:
    with pytest.raises(BootRefused) as excinfo:
        read_chat_allowlist({})
    assert CONFIG_PATH_ENV in excinfo.value.reason


def test_config_path_naming_an_unreadable_file_refuses(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(BootRefused):
        read_chat_allowlist({CONFIG_PATH_ENV: str(missing)})


def test_valid_allowlist_file_parses_chat_ids_to_int(tmp_path) -> None:
    config_file = tmp_path / "boot.yaml"
    config_file.write_text("chat_allowlist:\n  111: founder\n  222: ops\n")
    allowlist = read_chat_allowlist({CONFIG_PATH_ENV: str(config_file)})
    assert allowlist == {111: "founder", 222: "ops"}


def test_allowlist_file_with_non_integer_key_refuses(tmp_path) -> None:
    config_file = tmp_path / "boot.yaml"
    config_file.write_text("chat_allowlist:\n  not-a-number: founder\n")
    with pytest.raises(BootRefused):
        read_chat_allowlist({CONFIG_PATH_ENV: str(config_file)})


def test_allowlist_file_with_blank_principal_refuses(tmp_path) -> None:
    config_file = tmp_path / "boot.yaml"
    config_file.write_text("chat_allowlist:\n  111: ''\n")
    with pytest.raises(BootRefused):
        read_chat_allowlist({CONFIG_PATH_ENV: str(config_file)})


def test_port_defaults_when_unset() -> None:
    assert read_port({}) == 8080


def test_port_reads_the_env_var() -> None:
    assert read_port({PORT_ENV: "9090"}) == 9090


def test_port_out_of_range_refuses() -> None:
    with pytest.raises(BootRefused):
        read_port({PORT_ENV: "70000"})


def test_port_non_integer_refuses() -> None:
    with pytest.raises(BootRefused):
        read_port({PORT_ENV: "not-a-port"})


def test_api_base_defaults_to_the_real_telegram_api() -> None:
    assert read_api_base({}) == "https://api.telegram.org"


def test_api_base_reads_the_env_var_and_strips_trailing_slash() -> None:
    assert (
        read_api_base({API_BASE_ENV: "https://example.test/api/"})
        == "https://example.test/api"
    )


def test_boot_config_from_env_refuses_when_token_missing(tmp_path) -> None:
    config_file = tmp_path / "boot.yaml"
    config_file.write_text("chat_allowlist:\n  111: founder\n")
    with pytest.raises(BootRefused) as excinfo:
        BootConfig.from_env({CONFIG_PATH_ENV: str(config_file)})
    assert TOKEN_ENV in excinfo.value.reason


def test_boot_config_from_env_assembles_every_field(tmp_path) -> None:
    config_file = tmp_path / "boot.yaml"
    config_file.write_text("chat_allowlist:\n  111: founder\n")
    config = BootConfig.from_env(
        {
            TOKEN_ENV: "a-token-value",
            CONFIG_PATH_ENV: str(config_file),
            PORT_ENV: "9999",
        }
    )
    assert config.token == "a-token-value"  # noqa: S105 - synthetic test placeholder
    assert config.chat_allowlist == {111: "founder"}
    assert config.port == 9999
    assert config.telegram_api_base == "https://api.telegram.org"
