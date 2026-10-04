# What this fork changes

This is a fork of [`mario-hernandez/mail-mcp`](https://github.com/mario-hernandez/mail-mcp),
taken at upstream commit `ba728df` (version 0.7.0). It exists for one reason:
**the server must not be able to send mail.**

An assistant that reads untrusted mail, holds private data and can also
transmit is exploitable by anyone who can put a message in the inbox.
Upstream handles that by gating its send tools behind environment switches.
This fork removes the capability, so there is nothing to switch on.

## The changes

### 1. Sending is removed

| Upstream | This fork |
|---|---|
| `send_email` and `send_draft` tools, always visible to the model, refusing to run until two switches are set | No such tools |
| `smtp_client.send`, SMTP login and authentication | Removed. `smtp_client.py` keeps only the message builders the draft tools use, and does not import `smtplib` |
| Server instructions that tell the model how to get sending enabled | Replaced with "this server cannot send; save a draft" |
| `MAIL_MCP_SEND_ENABLED`, `MAIL_MCP_SEND_HOURLY_LIMIT` | Ignored |
| Microsoft OAuth requests `IMAP.AccessAsUser.All` and `SMTP.Send` | Requests `IMAP.AccessAsUser.All` only |
| `init` and `doctor --connect` test an SMTP login | Test IMAP only |
| `get_account_info` returns SMTP host, port and user | Does not |

Drafts are unchanged: `save_draft`, `reply_draft`, `forward_draft` and
`update_draft` write to the Drafts mailbox over IMAP, and you send from your
own mail client.

The config file still has `smtp_host`, `smtp_port`, `smtp_starttls` and
`smtp_username`, and `add-account` still takes `--smtp-host`. They are inert.
They stay so that a config written by upstream loads here and the other way
round.

### 2. The write switch is split

| Switches set | Tools added |
|---|---|
| none | read tools and the four draft tools |
| `MAIL_MCP_WRITE_ENABLED=true` | `copy_email`, `move_email`, `mark_emails` |
| `MAIL_MCP_WRITE_ENABLED=true` and `MAIL_MCP_DESTRUCTIVE_ENABLED=true` | `delete_emails`, `create_folder`, `rename_folder`, `delete_folder` |

Upstream registers all seven with the write switch alone.
`MAIL_MCP_DESTRUCTIVE_ENABLED` on its own does nothing.

### 3. Draft attachments come from one folder

A draft can attach files from `~/Documents/mail-mcp-outbox`, plus the folder
named in `MAIL_MCP_ATTACHMENT_DIR` if you set one. Upstream also allowed
`~/Downloads` and the temp directory, which let a prompt-injected model copy
anything you had downloaded into a draft that then syncs to your mail
provider.

`forward_draft` is not affected: it attaches the original message itself,
not a file from disk.

### 4. A guard test

`tests/test_no_send.py` fails if:

- any module under `src/mail_mcp/` imports `smtplib`, `aiosmtplib`, `smtpd` or
  `aiosmtpd`, in any form (`import`, `from … import`, `__import__`,
  `importlib.import_module`);
- a tool with "send" in its name is registered under any combination of
  switches, including the upstream ones;
- `mail_mcp.tools.send`, `drafts.send_draft` or `smtp_client.send` exist;
- the server instructions mention the send tools or `SEND_ENABLED`;
- the OAuth scopes include SMTP;
- the default attachment allowlist is anything other than the outbox folder.

`tests/test_server_gating.py` pins the exact tool set for every combination
of switches, so adding a tool or moving one to a weaker gate has to be a
deliberate edit to that file.

### 5. The destructive switch cannot destroy mail

The destructive tools are limited so the switch is safe to leave on. None of
these limits has an override argument or environment variable.

| Upstream | This fork |
|---|---|
| `delete_emails(permanent=true)` expunges, behind `MAIL_MCP_ALLOW_PERMANENT_DELETE` and `confirm=true` | No permanent delete. `delete_emails` only moves to the trash; `permanent` is a validation error and the switch is ignored |
| Messages in the trash can be deleted like any others | Refused: that would be emptying the trash |
| `delete_folder(confirm=true)` deletes a folder with messages in it | Only a folder with no messages and no subfolders is deleted; `confirm` is a validation error |
| A missing message count is treated as zero | The delete is refused |
| Any folder can be deleted or renamed | The inbox, trash, drafts, sent, junk and archive folders cannot, whether or not the server flags them |

A trashed message can be moved back with `move_email`. Your mail provider may
still erase old trash on its own schedule; that is a mail-account setting.

`update_draft` still removes the superseded copy of a draft it has just
re-saved. That is the one remaining permanent removal, and it can only
happen inside the Drafts mailbox.

`tests/test_destructive_guards.py` pins all of this, including that no other
code path requests a permanent delete and that a bare `EXPUNGE` is never
issued.

### 6. Moving mail works without IMAP MOVE

iCloud does not advertise the MOVE extension, so upstream's `move_email` and
move-to-trash fail there. This fork falls back to COPY, flag `\Deleted`,
`UID EXPUNGE` of exactly those UIDs, in that order, and removes nothing until
the copy has succeeded.

## What is not changed

- Drafts are on by default, as upstream. There is no pure read-only mode.
- `download_attachment` and `get_email_raw` still write files under
  `~/Downloads/mail-mcp/<alias>/`.
- Credentials, TLS, search, the untrusted-content envelope and the IMAP client
  are upstream's code, untouched apart from docstrings.
- The module is still called `smtp_client` and the package `mail-mcp`, to keep
  merges from upstream small.

## Installing

Install from this repository, not from PyPI or upstream:

```bash
pip install "mail-mcp[cli] @ git+https://github.com/dennisfoconnor/mail-mcp.git@main"
```

To confirm you are running the fork, start it and read the first log line.
It ends with `send=unavailable`.

## Merging from upstream

Upstream is active, and its changes will often touch the files this fork
edits. After any merge:

1. Run `pytest`. `tests/test_no_send.py`, `tests/test_server_gating.py` and
   `tests/test_destructive_guards.py` must pass. If they fail, the merge
   brought back sending, a new tool, or a way to destroy mail.
2. Do not resolve a conflict by taking upstream's side in
   `src/mail_mcp/server.py`, `src/mail_mcp/smtp_client.py`,
   `src/mail_mcp/tools/drafts.py`, `src/mail_mcp/tools/organize.py`,
   `src/mail_mcp/tools/schemas.py`, `src/mail_mcp/imap_client.py` or
   `src/mail_mcp/safety/attachments.py` without reading it.
3. If upstream adds a new tool, decide which of the three levels it belongs
   to and add it to the expected sets in `tests/test_server_gating.py`.
4. `src/mail_mcp/tools/send.py` must stay deleted.

## How this was verified

The unit suite passes with these changes (515 passed, 3 skipped). It was run
with small local stand-ins for `imapclient` and `keyring` and against a newer
`mcp` SDK than the project pins, because the packages could not be installed
where the work was done. The three skips are two that upstream already skips
and the one test that needs the pinned `mcp` SDK. The GreenMail integration
tests under `tests/integration/` were edited to match but not run. Run the
full suite once in a normal environment (`pip install -e ".[dev]" && pytest`)
before relying on the fork.
