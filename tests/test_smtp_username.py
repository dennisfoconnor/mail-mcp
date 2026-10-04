"""Tests for the optional ``AccountModel.smtp_username`` config field.

This fork cannot send, so the field is inert: nothing logs in to SMTP. It is
still validated and round-tripped so a config written by upstream mail-mcp
loads unchanged. The upstream tests for SMTP authentication and the SMTP
envelope sender were removed together with that code.
"""

from __future__ import annotations

import argparse
import json

import pydantic
import pytest

from mail_mcp import config as config_mod
from mail_mcp.config import AccountModel

UPN = "admin@tenant.onmicrosoft.com"


def _acct(**kw) -> AccountModel:
    base = dict(alias="work", email="m@company.es", imap_host="outlook.office365.com",
                smtp_host="smtp.office365.com")
    base.update(kw)
    return AccountModel(**base)


# --- validation and config round-trip ------------------------------------------

@pytest.mark.parametrize("bad", ["", "no-at-sign", "a@b.com\r\nX-Injected: 1", "a b@c.com", "a@b.com\x01x"])
def test_invalid_values_are_rejected(bad: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        _acct(smtp_username=bad)


def test_old_config_without_the_field_still_loads(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"default_alias": "work", "accounts": [
        {"alias": "work", "email": "m@company.es", "imap_host": "i.example.com", "smtp_host": "s.example.com"}]}))
    cfg = config_mod.load(path)
    assert cfg.account("work").smtp_username is None


def test_field_survives_save_and_load(tmp_path) -> None:
    path = tmp_path / "config.json"
    cfg = config_mod.load(path)
    cfg.model = config_mod.ConfigModel(default_alias="work", accounts=[_acct(smtp_username=UPN)])
    config_mod.save(cfg)
    assert config_mod.load(path).account("work").smtp_username == UPN


# --- CLI and tools ---------------------------------------------------------------

def _add_account_args(**kw) -> argparse.Namespace:
    base = dict(alias="work", email="m@company.es", imap_host="outlook.office365.com", imap_port=993,
                smtp_host="smtp.office365.com", smtp_port=587, smtp_starttls=True,
                drafts_mailbox="Drafts", trash_mailbox="Trash", smtp_username=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def isolated_cli(tmp_path, monkeypatch):
    """Run ``add-account`` against a temp config and a fake keyring."""
    from mail_mcp import __main__ as cli

    path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "default_config_path", lambda: path)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "secret")
    store: dict = {}
    monkeypatch.setattr(cli, "set_password", lambda a, e, p: store.__setitem__((a, e), p))

    def _get(a, e):
        if (a, e) not in store:
            raise RuntimeError("missing")
        return store[(a, e)]

    monkeypatch.setattr(cli, "get_password", _get)
    return cli, path


def test_add_account_keeps_existing_override_when_flag_omitted(isolated_cli) -> None:
    cli, path = isolated_cli
    cfg = config_mod.load(path)
    cfg.model = config_mod.ConfigModel(default_alias="work", accounts=[_acct(smtp_username=UPN)])
    config_mod.save(cfg)

    assert cli._cmd_add_account(_add_account_args(smtp_port=25)) == 0
    acct = config_mod.load(path).account("work")
    assert acct.smtp_port == 25
    assert acct.smtp_username == UPN


def test_add_account_flag_sets_the_override(isolated_cli) -> None:
    cli, path = isolated_cli
    assert cli._cmd_add_account(_add_account_args(smtp_username=UPN)) == 0
    assert config_mod.load(path).account("work").smtp_username == UPN


def test_get_account_info_does_not_report_smtp_settings() -> None:
    """The model is never shown SMTP settings — there is nothing it could do with them."""
    from mail_mcp.tools.read import get_account_info
    from mail_mcp.tools.schemas import AccountInfoInput

    cfg = config_mod.Config(path=None, model=config_mod.ConfigModel(
        default_alias="work", accounts=[_acct(smtp_username=UPN)]))
    info = get_account_info(cfg, AccountInfoInput(account="work"))
    assert "smtp" not in info
    assert info["email"] == "m@company.es"
