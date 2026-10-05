"""Guard rail: turning on the destructive switch must not be able to destroy mail.

With ``MAIL_MCP_WRITE_ENABLED`` and ``MAIL_MCP_DESTRUCTIVE_ENABLED`` both set
the model gets ``delete_emails`` and the folder tools. These tests pin what
those tools can never do, whatever arguments are passed and whatever
environment variables are set:

* ``delete_emails`` only moves messages to the trash. There is no permanent
  delete, and messages already in the trash cannot be deleted — the server
  cannot empty the trash. A trashed message can be moved back.
* ``delete_folder`` only deletes a folder with no messages and no
  subfolders. The upstream ``confirm=true`` override is gone.
* System folders (inbox, trash, drafts, sent, junk, archive) cannot be
  deleted or renamed.

The IMAP client is faked; what is under test is the decision logic.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pydantic
import pytest

import mail_mcp
from mail_mcp import imap_client
from mail_mcp import server as server_module
from mail_mcp.config import AccountModel, Config, ConfigModel
from mail_mcp.credentials import AuthCredential
from mail_mcp.tools import organize
from mail_mcp.tools.schemas import (
    DeleteEmailInput,
    DeleteFolderInput,
    MoveEmailInput,
    RenameFolderInput,
)

PACKAGE_DIR = Path(mail_mcp.__file__).resolve().parent

# What iCloud actually reports: SPECIAL-USE on Trash and Sent only. Drafts,
# Junk and Archive carry no flag, so they must be protected by name.
ICLOUD_FOLDERS = [
    ([b"\\Noinferiors"], "/", "INBOX"),
    ([], "/", "Archive"),
    ([b"\\Trash"], "/", "Deleted Messages"),
    ([], "/", "Drafts"),
    ([], "/", "Junk"),
    ([b"\\Sent"], "/", "Sent Messages"),
    ([], "/", "House Stuff"),
    ([], "/", "Tickets"),
    ([], "/", "Travel"),
    ([], "/", "Travel/2026"),
]
ICLOUD_CAPS = (b"IMAP4REV1", b"UIDPLUS", b"IDLE")


def _client(*, counts: dict[str, int] | None = None, folders=None, caps=ICLOUD_CAPS):
    """A fake IMAP client over an iCloud-like folder list, recording mutations."""
    counts = counts or {}
    calls: list[tuple] = []
    client = MagicMock()
    client.capabilities.return_value = caps
    listing = ICLOUD_FOLDERS if folders is None else folders
    client.list_folders.return_value = listing
    names = {name for _f, _d, name in listing}
    client.folder_exists.side_effect = lambda name: name in names
    client.folder_status.side_effect = lambda name, what=None: {b"MESSAGES": counts.get(name, 0)}
    for method in (
        "delete_folder", "rename_folder", "move", "copy", "add_flags", "uid_expunge", "expunge",
    ):
        getattr(client, method).side_effect = (
            lambda *a, _m=method, **k: calls.append((_m, *a))
        )
    return client, calls


def _mutations(calls: list[tuple]) -> list[str]:
    return [c[0] for c in calls]


def _cfg() -> Config:
    acct = AccountModel(
        alias="icloud", email="x@example.com",
        imap_host="imap.example.com", smtp_host="smtp.example.com",
        # The literal defaults — wrong for iCloud, as in a real config.
        drafts_mailbox="Drafts", trash_mailbox="Trash",
    )
    return Config(path=Path("/tmp/x"), model=ConfigModel(accounts=[acct]))


@pytest.fixture
def wired(monkeypatch):
    """Route the tool handlers to a fake client; returns a setter for it."""
    holder: dict = {}

    @contextmanager
    def fake_connect(_account, _creds):
        yield holder["client"]

    monkeypatch.setattr(imap_client, "connect", fake_connect)
    monkeypatch.setattr(
        organize, "_auth",
        lambda cfg, alias: (
            cfg.account(alias),
            AuthCredential(kind="password", username="x@example.com", secret="x"),
        ),
    )
    # Every switch on, including the upstream one that no longer exists.
    monkeypatch.setenv("MAIL_MCP_WRITE_ENABLED", "true")
    monkeypatch.setenv("MAIL_MCP_DESTRUCTIVE_ENABLED", "true")
    monkeypatch.setenv("MAIL_MCP_ALLOW_PERMANENT_DELETE", "true")

    def use(client):
        holder["client"] = client

    return use


# --------------------------------------------------------------------------
# delete_folder: empty, childless, ordinary folders only
# --------------------------------------------------------------------------

def test_delete_folder_deletes_an_empty_ordinary_folder():
    client, calls = _client(counts={"Tickets": 0})

    imap_client.delete_folder(client, mailbox="Tickets")

    assert calls == [("delete_folder", "Tickets")]


@pytest.mark.parametrize("count", [1, 42, 5000])
def test_delete_folder_refuses_a_folder_with_messages(count):
    client, calls = _client(counts={"Tickets": count})

    with pytest.raises(imap_client.FolderNotEmpty, match="not empty"):
        imap_client.delete_folder(client, mailbox="Tickets")

    assert calls == []


def test_delete_folder_has_no_override_parameter():
    """Upstream's ``allow_non_empty`` / ``confirm`` escape hatch must not come back."""
    import inspect

    params = set(inspect.signature(imap_client.delete_folder).parameters)
    assert params == {"client", "mailbox", "protected"}
    assert set(DeleteFolderInput.model_fields) == {"account", "mailbox"}
    with pytest.raises(pydantic.ValidationError):
        DeleteFolderInput(mailbox="Tickets", confirm=True)


def test_delete_folder_tool_refuses_non_empty_even_when_asked_to_confirm(wired):
    client, calls = _client(counts={"House Stuff": 12})
    wired(client)

    with pytest.raises(imap_client.FolderNotEmpty):
        organize.delete_folder(_cfg(), DeleteFolderInput(mailbox="House Stuff"))

    assert calls == []


def test_delete_folder_refuses_a_folder_with_subfolders():
    """An empty parent can still hold mail in its children."""
    client, calls = _client(counts={"Travel": 0, "Travel/2026": 7})

    with pytest.raises(imap_client.FolderNotEmpty, match="subfolders"):
        imap_client.delete_folder(client, mailbox="Travel")

    assert calls == []


def test_delete_folder_refuses_when_server_flags_children():
    folders = [([b"\\HasChildren"], "/", "Projects")]
    client, calls = _client(folders=folders)

    with pytest.raises(imap_client.FolderNotEmpty, match="subfolders"):
        imap_client.delete_folder(client, mailbox="Projects")

    assert calls == []


def test_delete_folder_refuses_when_the_count_is_unreadable():
    """Upstream treated a missing MESSAGES count as zero and deleted."""
    client, calls = _client()
    client.folder_status.side_effect = lambda name, what=None: {}

    with pytest.raises(imap_client.FolderNotEmpty, match="could not read"):
        imap_client.delete_folder(client, mailbox="Tickets")

    assert calls == []


def test_delete_folder_propagates_a_failed_status_call():
    client, calls = _client()
    client.folder_status.side_effect = OSError("connection dropped")

    with pytest.raises(OSError):
        imap_client.delete_folder(client, mailbox="Tickets")

    assert calls == []


def test_delete_folder_refuses_a_missing_folder():
    client, calls = _client()

    with pytest.raises(RuntimeError, match="does not exist"):
        imap_client.delete_folder(client, mailbox="Ghost")

    assert calls == []


# --------------------------------------------------------------------------
# System folders cannot be deleted or renamed
# --------------------------------------------------------------------------

SYSTEM_FOLDERS = ["INBOX", "Deleted Messages", "Sent Messages", "Drafts", "Junk", "Archive"]


@pytest.mark.parametrize("name", SYSTEM_FOLDERS + ["inbox", "DRAFTS", "deleted messages"])
def test_system_folders_cannot_be_deleted_even_when_empty(name):
    client, calls = _client(counts={})

    with pytest.raises(imap_client.ProtectedFolder, match="cannot be deleted"):
        imap_client.delete_folder(client, mailbox=name)

    assert calls == []


@pytest.mark.parametrize("name", SYSTEM_FOLDERS)
def test_system_folders_cannot_be_renamed(name):
    client, calls = _client()

    with pytest.raises(imap_client.ProtectedFolder, match="cannot be renamed"):
        imap_client.rename_folder(client, old_name=name, new_name="Something Else")

    assert calls == []


def test_a_server_flagged_special_folder_is_protected_whatever_its_name():
    folders = [([b"\\Trash"], "/", "Bin"), ([b"\\Archive"], "/", "Old Stuff")]
    client, calls = _client(folders=folders)

    for name in ("Bin", "Old Stuff"):
        with pytest.raises(imap_client.ProtectedFolder):
            imap_client.delete_folder(client, mailbox=name)
        with pytest.raises(imap_client.ProtectedFolder):
            imap_client.rename_folder(client, old_name=name, new_name="X")

    assert calls == []


def test_the_accounts_configured_drafts_and_trash_are_protected():
    folders = [([], "/", "MyDrafts"), ([], "/", "MyBin")]
    client, calls = _client(folders=folders)

    for name in ("MyDrafts", "MyBin"):
        with pytest.raises(imap_client.ProtectedFolder):
            imap_client.delete_folder(client, mailbox=name, protected=("MyDrafts", "MyBin"))

    assert calls == []


def test_ordinary_folders_can_still_be_renamed():
    client, calls = _client()

    imap_client.rename_folder(client, old_name="Tickets", new_name="Events")

    assert calls == [("rename_folder", "Tickets", "Events")]


def test_trash_cannot_be_deleted_or_renamed_through_the_tools(wired):
    client, calls = _client(counts={})
    wired(client)

    with pytest.raises(imap_client.ProtectedFolder):
        organize.delete_folder(_cfg(), DeleteFolderInput(mailbox="Deleted Messages"))
    with pytest.raises(imap_client.ProtectedFolder):
        organize.rename_folder(
            _cfg(), RenameFolderInput(old_name="Deleted Messages", new_name="Old Trash"),
        )

    assert calls == []


# --------------------------------------------------------------------------
# delete_emails: to the trash only, never out of it
# --------------------------------------------------------------------------

def test_delete_emails_moves_to_the_real_trash_and_keeps_a_copy_first(wired):
    client, calls = _client()
    wired(client)

    out = organize.delete_email(_cfg(), DeleteEmailInput(mailbox="INBOX", uids=[11, 12]))

    assert out["mode"] == "trash"
    assert out["trash_mailbox"] == "Deleted Messages"
    assert out["affected"] == 2
    assert "move_email" in out["restore"]
    # Copy lands in the trash before anything is removed from the inbox.
    assert _mutations(calls) == ["copy", "add_flags", "uid_expunge"]
    assert calls[0] == ("copy", [11, 12], "Deleted Messages")
    assert calls[2] == ("uid_expunge", [11, 12])


def test_delete_emails_has_no_permanent_mode():
    assert set(DeleteEmailInput.model_fields) == {"account", "mailbox", "uids"}
    with pytest.raises(pydantic.ValidationError):
        DeleteEmailInput(mailbox="INBOX", uids=[1], permanent=True)
    with pytest.raises(pydantic.ValidationError):
        DeleteEmailInput(mailbox="INBOX", uids=[1], confirm=True)


@pytest.mark.parametrize("mailbox", ["Deleted Messages", "deleted messages"])
def test_delete_emails_refuses_messages_already_in_the_trash(wired, mailbox):
    """Deleting from the trash is emptying the trash."""
    client, calls = _client()
    wired(client)

    with pytest.raises(imap_client.ProtectedFolder, match="already in the trash"):
        organize.delete_email(_cfg(), DeleteEmailInput(mailbox=mailbox, uids=[3, 4, 5]))

    assert calls == []


def test_delete_uids_trash_mode_refuses_the_trash_itself():
    client, calls = _client()

    with pytest.raises(imap_client.ProtectedFolder):
        imap_client.delete_uids(
            client, mailbox="Trash", uids=[1], trash_mailbox="Trash", permanent=False,
        )

    assert calls == []


def test_a_trashed_message_can_be_moved_back(wired):
    client, calls = _client()
    wired(client)

    out = organize.move_email(
        _cfg(),
        MoveEmailInput(source="Deleted Messages", destination="INBOX", uids=[99]),
    )

    assert out["moved"] == 1
    assert calls[0] == ("copy", [99], "INBOX")
    assert _mutations(calls) == ["copy", "add_flags", "uid_expunge"]


def test_moving_a_message_onto_its_own_mailbox_is_rejected():
    from mail_mcp.safety.validation import ValidationError

    client, calls = _client()

    with pytest.raises(ValidationError, match="same mailbox"):
        imap_client.move_uids(
            client, source="Deleted Messages", destination="deleted messages", uids=[1],
        )

    assert calls == []


# --------------------------------------------------------------------------
# Structural: no route to a permanent delete or a bare expunge
# --------------------------------------------------------------------------

def _sources() -> list[Path]:
    return sorted(PACKAGE_DIR.rglob("*.py"))


def test_only_update_draft_may_request_a_permanent_delete():
    """``delete_uids(permanent=True)`` survives for one internal caller only."""
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "permanent":
                    continue
                is_false = isinstance(kw.value, ast.Constant) and kw.value.value is False
                if not is_false:
                    offenders.append(str(path.relative_to(PACKAGE_DIR)))
    assert offenders == ["tools/drafts.py"], offenders


def test_no_module_issues_a_bare_expunge():
    """A bare EXPUNGE removes every ``\\Deleted`` message in the mailbox."""
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "expunge"
            ):
                offenders.append(f"{path.relative_to(PACKAGE_DIR)}:{node.lineno}")
    assert offenders == [], offenders


def test_the_permanent_delete_switch_is_gone():
    for path in _sources():
        assert "MAIL_MCP_ALLOW_PERMANENT_DELETE" not in path.read_text(encoding="utf-8").replace(
            "``MAIL_MCP_ALLOW_PERMANENT_DELETE``", ""
        ), path.name


def test_mark_emails_cannot_flag_a_message_deleted():
    """``\\Deleted`` plus a later expunge by any client would be a delete."""
    from mail_mcp.tools.schemas import MarkFlagsInput

    assert set(MarkFlagsInput.model_fields) == {
        "account", "mailbox", "uids", "mark_read", "mark_flagged",
    }


def test_refusals_carry_stable_codes_and_no_retry_advice():
    protected = server_module._classify(imap_client.ProtectedFolder("x"))
    not_empty = server_module._classify(imap_client.FolderNotEmpty("x"))

    assert protected["code"] == "PROTECTED_FOLDER"
    assert not_empty["code"] == "FOLDER_NOT_EMPTY"
    for out in (protected, not_empty):
        assert out["retryable"] is False
        assert "no override" in out["hint"].lower()
