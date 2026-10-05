"""``move_uids`` on servers with and without the IMAP MOVE extension.

iCloud does not advertise RFC 6851 MOVE, so ``IMAPClient.move`` raises
``CapabilityError`` there and ``move_email`` (and move-to-Trash) could not
work at all. The fallback is the sequence RFC 6851 itself gives as the
equivalent: COPY, flag the originals ``\\Deleted``, then ``UID EXPUNGE``
exactly those UIDs.

What these tests pin:

* MOVE is used when the server has it;
* without MOVE the fallback runs in copy → flag → expunge order, and the
  expunge is UID-scoped (never a bare ``EXPUNGE``, which would also remove
  whatever another client had already flagged ``\\Deleted``);
* nothing is flagged or expunged unless the copy succeeded;
* with neither MOVE nor UIDPLUS the call fails before mutating anything.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mail_mcp import imap_client

ICLOUD_LIKE = (b"IMAP4REV1", b"UIDPLUS", b"IDLE", b"NAMESPACE")
WITH_MOVE = (b"IMAP4REV1", b"UIDPLUS", b"MOVE")
LEGACY = (b"IMAP4REV1",)


def _client(capabilities: tuple[bytes, ...]) -> tuple[MagicMock, list[str]]:
    """A fake IMAP client that records the order of mutating calls."""
    calls: list[str] = []
    client = MagicMock()
    client.capabilities.return_value = capabilities
    client.select_folder.side_effect = lambda *a, **k: calls.append("select")
    client.move.side_effect = lambda *a, **k: calls.append("move")
    client.copy.side_effect = lambda *a, **k: calls.append("copy")
    client.add_flags.side_effect = lambda *a, **k: calls.append("add_flags")
    client.uid_expunge.side_effect = lambda *a, **k: calls.append("uid_expunge")
    client.expunge.side_effect = lambda *a, **k: calls.append("expunge")
    return client, calls


def test_uses_move_when_the_server_supports_it():
    client, calls = _client(WITH_MOVE)

    moved = imap_client.move_uids(client, source="INBOX", destination="Archive", uids=[7, 8])

    assert moved == 2
    assert calls == ["select", "move"]
    client.move.assert_called_once_with([7, 8], "Archive")
    client.select_folder.assert_called_once_with("INBOX", readonly=False)


def test_falls_back_to_copy_flag_uid_expunge_without_move():
    client, calls = _client(ICLOUD_LIKE)

    moved = imap_client.move_uids(client, source="INBOX", destination="Tickets", uids=[6064])

    assert moved == 1
    assert calls == ["select", "copy", "add_flags", "uid_expunge"]
    client.copy.assert_called_once_with([6064], "Tickets")
    client.add_flags.assert_called_once_with([6064], [b"\\Deleted"], silent=True)
    client.uid_expunge.assert_called_once_with([6064])
    client.move.assert_not_called()


def test_fallback_never_issues_a_bare_expunge():
    """A bare EXPUNGE would remove other clients' ``\\Deleted`` messages too."""
    client, calls = _client(ICLOUD_LIKE)

    imap_client.move_uids(client, source="INBOX", destination="Archive", uids=[1, 2, 3])

    assert "expunge" not in calls
    client.expunge.assert_not_called()
    client.uid_expunge.assert_called_once_with([1, 2, 3])


def test_nothing_is_removed_when_the_copy_fails():
    client, calls = _client(ICLOUD_LIKE)

    def failing_copy(*_a, **_k):
        calls.append("copy")
        raise RuntimeError("[TRYCREATE] destination does not exist")

    client.copy.side_effect = failing_copy

    with pytest.raises(RuntimeError, match="TRYCREATE"):
        imap_client.move_uids(client, source="INBOX", destination="Nope", uids=[5])

    assert calls == ["select", "copy"]
    client.add_flags.assert_not_called()
    client.uid_expunge.assert_not_called()


def test_refuses_without_mutation_when_neither_move_nor_uidplus():
    client, calls = _client(LEGACY)

    with pytest.raises(imap_client.UIDPlusRequired, match="No messages were mutated"):
        imap_client.move_uids(client, source="INBOX", destination="Archive", uids=[5])

    assert calls == ["select"]
    client.copy.assert_not_called()
    client.add_flags.assert_not_called()


def test_capability_probe_failure_is_treated_as_unsupported():
    client, calls = _client(ICLOUD_LIKE)
    client.capabilities.side_effect = OSError("connection dropped")

    with pytest.raises(imap_client.UIDPlusRequired):
        imap_client.move_uids(client, source="INBOX", destination="Archive", uids=[5])

    assert calls == ["select"]


def test_move_to_trash_uses_the_same_fallback():
    """``delete_uids(permanent=False)`` is a move, so it needs the fallback too."""
    client, calls = _client(ICLOUD_LIKE)

    affected = imap_client.delete_uids(
        client, mailbox="INBOX", uids=[9], trash_mailbox="Deleted Messages", permanent=False,
    )

    assert affected == 1
    assert calls == ["select", "copy", "add_flags", "uid_expunge"]
    client.copy.assert_called_once_with([9], "Deleted Messages")


def test_empty_uid_list_touches_nothing():
    client, calls = _client(ICLOUD_LIKE)

    assert imap_client.move_uids(client, source="INBOX", destination="Archive", uids=[]) == 0
    assert calls == []


def test_move_email_tool_reports_the_move(monkeypatch):
    """End to end through the tool handler, on an iCloud-like server."""
    from contextlib import contextmanager
    from pathlib import Path

    from mail_mcp.config import AccountModel, Config, ConfigModel
    from mail_mcp.credentials import AuthCredential
    from mail_mcp.tools import organize
    from mail_mcp.tools.schemas import MoveEmailInput

    client, calls = _client(ICLOUD_LIKE)

    @contextmanager
    def fake_connect(_account, _creds):
        yield client

    acct = AccountModel(
        alias="icloud", email="x@example.com",
        imap_host="imap.example.com", smtp_host="smtp.example.com",
    )
    cfg = Config(path=Path("/tmp/x"), model=ConfigModel(accounts=[acct]))
    monkeypatch.setattr(imap_client, "connect", fake_connect)
    monkeypatch.setattr(
        organize, "_auth",
        lambda cfg, alias: (cfg.account(alias),
                            AuthCredential(kind="password", username="x@example.com", secret="x")),
    )

    out = organize.move_email(
        cfg, MoveEmailInput(account="icloud", source="INBOX", destination="Tickets", uids=[6064]),
    )

    assert out == {"account": "icloud", "moved": 1, "source": "INBOX", "destination": "Tickets"}
    assert calls == ["select", "copy", "add_flags", "uid_expunge"]
