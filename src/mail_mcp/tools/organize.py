"""Organising tools: copy, move, flag, delete, folders.

``copy_email`` / ``move_email`` / ``mark_emails`` are registered when
``MAIL_MCP_WRITE_ENABLED=true``; ``delete_emails`` and the folder tools
additionally need ``MAIL_MCP_DESTRUCTIVE_ENABLED=true``.

Nothing here can destroy mail:

* ``delete_emails`` only ever moves messages to the trash. There is no
  permanent delete, and messages already in the trash cannot be deleted
  again, so this server cannot empty the trash. A trashed message can be
  moved back with ``move_email``.
* ``delete_folder`` only deletes a folder with no messages and no
  subfolders, and never a system folder.
* ``rename_folder`` refuses system folders.

None of these guards has an override argument or environment variable.
"""

from __future__ import annotations

from .. import imap_client
from ..config import Config
from ..credentials import resolve_auth
from .schemas import (
    CopyEmailInput,
    CreateFolderInput,
    DeleteEmailInput,
    DeleteFolderInput,
    MarkFlagsInput,
    MoveEmailInput,
    RenameFolderInput,
)


def _auth(cfg: Config, alias: str | None):
    acct = cfg.account(alias)
    return acct, resolve_auth(acct)


def create_folder(cfg: Config, params: CreateFolderInput) -> dict:
    acct, creds = _auth(cfg, params.account)
    with imap_client.connect(acct, creds) as c:
        created = imap_client.create_folder(c, mailbox=params.mailbox)
    # Report what actually happened: the operation is idempotent, so report
    # "already_exists" rather than always claiming a fresh creation.
    return {
        "account": acct.alias,
        "mailbox": params.mailbox,
        "status": "created" if created else "already_exists",
    }


def _protected_names(acct) -> tuple[str, ...]:
    """The account's own drafts / trash names, on top of the built-in list."""
    return tuple(n for n in (acct.drafts_mailbox, acct.trash_mailbox) if n)


def rename_folder(cfg: Config, params: RenameFolderInput) -> dict:
    acct, creds = _auth(cfg, params.account)
    with imap_client.connect(acct, creds) as c:
        imap_client.rename_folder(
            c,
            old_name=params.old_name,
            new_name=params.new_name,
            protected=_protected_names(acct),
        )
    return {
        "account": acct.alias,
        "old_name": params.old_name,
        "new_name": params.new_name,
        "status": "renamed",
    }


def delete_folder(cfg: Config, params: DeleteFolderInput) -> dict:
    acct, creds = _auth(cfg, params.account)
    with imap_client.connect(acct, creds) as c:
        imap_client.delete_folder(
            c, mailbox=params.mailbox, protected=_protected_names(acct),
        )
    return {
        "account": acct.alias,
        "mailbox": params.mailbox,
        "status": "deleted",
        # Always zero: a folder holding any message is refused, not deleted.
        "messages_lost": 0,
    }


def copy_email(cfg: Config, params: CopyEmailInput) -> dict:
    acct, creds = _auth(cfg, params.account)
    with imap_client.connect(acct, creds) as c:
        copied = imap_client.copy_uids(
            c, source=params.source, destination=params.destination, uids=params.uids,
        )
    return {
        "account": acct.alias,
        "copied": copied,
        "source": params.source,
        "destination": params.destination,
    }


def move_email(cfg: Config, params: MoveEmailInput) -> dict:
    acct, creds = _auth(cfg, params.account)
    with imap_client.connect(acct, creds) as c:
        moved = imap_client.move_uids(
            c, source=params.source, destination=params.destination, uids=params.uids
        )
    return {
        "account": acct.alias,
        "moved": moved,
        "source": params.source,
        "destination": params.destination,
    }


def mark(cfg: Config, params: MarkFlagsInput) -> dict:
    acct, creds = _auth(cfg, params.account)
    add: list[str] = []
    remove: list[str] = []
    if params.mark_read is True:
        add.append("\\Seen")
    elif params.mark_read is False:
        remove.append("\\Seen")
    if params.mark_flagged is True:
        add.append("\\Flagged")
    elif params.mark_flagged is False:
        remove.append("\\Flagged")
    with imap_client.connect(acct, creds) as c:
        affected = imap_client.set_flags(
            c,
            mailbox=params.mailbox,
            uids=params.uids,
            add=add or None,
            remove=remove or None,
        )
    return {"account": acct.alias, "affected": affected}


def delete_email(cfg: Config, params: DeleteEmailInput) -> dict:
    """Move messages to the trash. Never a permanent delete."""
    acct, creds = _auth(cfg, params.account)
    with imap_client.connect(acct, creds) as c:
        # Resolve the actual trash mailbox at call time.
        # ``acct.trash_mailbox`` is just a hint; servers in Spanish / French /
        # German use ``Papelera`` / ``Corbeille`` / ``Papierkorb`` (or Outlook
        # 365's ``Elementos eliminados`` / ``Éléments supprimés`` /
        # ``Gelöschte Elemente``), iCloud uses ``Deleted Messages``, and a
        # stale literal ``"Trash"`` would either fail or land messages in a
        # folder the user's mail client does not treat as their trash.
        trash = imap_client.resolve_trash_mailbox(c, acct)
        affected = imap_client.delete_uids(
            c,
            mailbox=params.mailbox,
            uids=params.uids,
            trash_mailbox=trash,
            permanent=False,
        )
    return {
        "account": acct.alias,
        "affected": affected,
        "mode": "trash",
        "trash_mailbox": trash,
        "restore": (
            f"The messages are in {trash!r} and can be moved back with "
            f"move_email(source={trash!r}, destination={params.mailbox!r}). "
            "Their UIDs changed with the move: search the trash for them."
        ),
    }
