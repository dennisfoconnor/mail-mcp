<div align="center">

<img src="docs/images/hero.png" alt="mail-mcp — privacy-first IMAP/SMTP for your AI assistant" />

# mail-mcp

**Privacy-first IMAP MCP server for Claude Desktop, Claude Code, and Codex CLI — a fork that cannot send.**

Give your AI assistant real access to your mailbox — read, search, draft, organise — without sending a single credential to anyone's server.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-compatible-22d3ee.svg)](https://modelcontextprotocol.io/)
[![Security](https://img.shields.io/badge/Security-audited--from--9--peers-f59e0b.svg)](#security)

</div>

> [!IMPORTANT]
> **This is a no-send fork of [`mario-hernandez/mail-mcp`](https://github.com/mario-hernandez/mail-mcp).**
> It reads, searches, organises and drafts mail. It **cannot send**: the send
> tools and the SMTP client are removed, not switched off, and no setting
> brings them back. A draft lands in your Drafts folder and you send it from
> your own mail client. See [`FORK.md`](FORK.md) for every difference from
> upstream and how to merge upstream changes safely.

## Why this exists

I wanted my Claude Code (and Codex) sessions to understand my inbox: find the last email from a client, pull the PDF they sent, draft a reply, archive the newsletter bulk. After auditing nine existing email MCP implementations I kept finding the same patterns — credentials written to disk in plain text, TLS verification bypassed, OAuth secrets shipped in the binary. `mail-mcp` is what I wish one of them had been. The audit notes live in [`SYNTHESIS.md`](SYNTHESIS.md) for anyone who wants to check the reasoning.

Since v0.3 the codebase has gone through repeated adversarial review rounds — every release ships with the findings closed and the regressions pinned to tests. 181 unit tests + a GreenMail integration suite cover the safety boundaries, not the happy path.

## Highlights

- 🔐 **Your password never touches a file.** It lives in the OS keyring — macOS Keychain, Linux Secret Service, Windows Credential Manager — via [`keyring`](https://pypi.org/project/keyring/). The config file only stores host/port/user/alias.
- 🛡️ **TLS is mandatory.** IMAP uses implicit TLS (port 993). There is no knob to disable certificate verification.
- 🧱 **Prompt-injection hardened.** Email bodies are wrapped in an `<untrusted_email_content>` envelope with an explicit warning; closing-tag breakouts and zero-width injection characters are neutralised before the model sees them.
- 🚪 **Write tools are gated in two steps.** Copy, move and flag are *not even registered* unless `MAIL_MCP_WRITE_ENABLED=true`. Delete and folder changes additionally need `MAIL_MCP_DESTRUCTIVE_ENABLED=true`. A tool that is not registered cannot be listed or called by the model.
- 📭 **No sending, by construction.** There is no send tool and no SMTP code. An agent that reads untrusted mail and can also transmit is exploitable by anyone who can put a message in your inbox; this fork removes the channel instead of gating it.
- 🌍 **Localised mailboxes work out of the box.** `save_draft`, `list_drafts`, and `delete_emails` resolve the actual server folder at call time via RFC 6154 SPECIAL-USE: `Borradores`, `Brouillons`, `Entwürfe`, `Bozze`, `Papelera`, `Elementos eliminados`, `[Gmail]/Drafts`, … all handled. No more `[TRYCREATE] folder does not exist` on Outlook ES/FR/DE accounts.
- 🎨 **HTML email done right.** Pass `body_html` to `save_draft` / `reply_draft` / `update_draft` and the message is built as `multipart/alternative` with `body` as the plain-text fallback — the structure spam filters and text-mode clients expect. Reply quotes land in **both** alternatives (HTML-escaped — From/Date are attacker-controlled). And if you paste HTML into `body` by mistake, the response carries an explicit `html_warning` instead of silently delivering raw markup.
- ✒️ **Your signature, where Outlook puts it — when you say so.** Drop `firma.html` / `firma.txt` into `~/.config/mail-mcp/signatures/<alias>/` and drafts, replies and forwards from that account can carry it — after the text, **before the reply quote**, HTML inserted verbatim, never twice. By default the agent must **ask you** for each message (`SIGNATURE_CHOICE_REQUIRED` until it passes `include_signature`); set `signature_mode: "auto"` to always sign. Paths are confined to that directory and size-capped; the LLM only gets an on/off switch.
- 🧾 **Forensic attachment mode for chain-of-custody.** `raw_passthrough=true` on an `AttachmentSpec` sends the file's bytes byte-for-byte. SHA-256 is preserved end-to-end through `save_draft` → IMAP → `download_attachment`. Useful for evidence preservation, eIDAS sealing, BEC incident response.
- 🪶 **Small, auditable, six direct dependencies.** `mcp`, `imapclient`, `keyring`, `pydantic`, `certifi`, `defusedxml`. No web UI, no telemetry, no update checks, no relays, no phone-home.
- 🧰 **Clean tool surface** — structured IMAP search (no concatenation), bounded outputs, path-traversal-safe attachment saves, RFC 4315 UID-scoped EXPUNGE that refuses to silently delete other clients' messages.
- 🤖 **LLM-aware error semantics.** Errors return `{type, message, code, hint, retryable}`. Stable codes (`UIDPLUS_REQUIRED_FOR_SAFE_EXPUNGE`, `SIGNATURE_CHOICE_REQUIRED`, `PERMISSION_DENIED`, `AUTH_FAILED`, `TLS_ERROR`, `NOT_FOUND`, …) let agents branch programmatically without sniffing free-text.

## Architecture

<div align="center">
<img src="docs/images/architecture.png" alt="mail-mcp architecture" />
</div>

Three layers: your AI client talks MCP JSON-RPC over stdio, `mail-mcp` enforces the safety rules, the world only ever sees TLS-wrapped IMAP to the host you configured. Passwords flow one way only: from the OS keyring into a short-lived IMAP session. (The architecture image is upstream's and still shows SMTP; this fork has none.)

## Tools

| Tool | Type | Mode | What it does |
|------|:----:|------|--------------|
| `list_accounts` | ✅ | default | Enumerate configured accounts and the default |
| `get_account_info` | ✅ | default | Connection config + resolved mailboxes for one account |
| `list_folders` | ✅ | default | List mailboxes on the account |
| `get_special_folders` | ✅ | default | Resolve Drafts/Trash/Sent/Junk/Archive via RFC 6154 |
| `get_quota` | ✅ | default | Storage used / limit, or nulls if unavailable |
| `search_emails` | ✅ | default | Structured IMAP search (subject/from/to/since/flags/…) |
| `get_email` | ✅ | default | Fetch a message, body wrapped in XPIA envelope |
| `get_thread` | ✅ | default | Conversation reconstruction via `THREAD=REFERENCES` |
| `list_attachments` | ✅ | default | Attachment metadata for a message |
| `download_attachment` | ✅ | default | Save an attachment to `~/Downloads/mail-mcp/<alias>/`. Forwarded `message/rfc822` parts download as `.eml`. |
| `get_email_raw` | ✅ | default | Escape hatch: full RFC822 source of one message (also saved to disk as `.eml`). Use when `get_email` body is empty or `list_attachments` is missing parts visible in the user's mail client. |
| `list_drafts` | ✅ | default | List the account's Drafts mailbox without guessing its name |
| `save_draft` | ✍️ | default | Build a MIME draft (disk-path attachments; HTML via `body_html` → `multipart/alternative`; account signature on request — see `signature_mode`) |
| `reply_draft` | ✍️ | default | Draft a reply with proper `In-Reply-To` / `References` / `Re: …` subject; `body_html` keeps HTML threads formatted |
| `forward_draft` | ✍️ | default | Draft a forward; original attached as `message/rfc822`; optional `comment_html` |
| `update_draft` | ✍️ | default | Edit a draft in place (APPEND-then-DELETE, preserves Message-ID and both body alternatives) |
| `copy_email` | ⚠️ | `MAIL_MCP_WRITE_ENABLED=true` | Copy without moving (file in two folders) |
| `move_email` | ⚠️ | `MAIL_MCP_WRITE_ENABLED=true` | Move messages between mailboxes |
| `mark_emails` | ⚠️ | `MAIL_MCP_WRITE_ENABLED=true` | Set/clear Seen and Flagged |
| `delete_emails` | 🗑️ | write + `MAIL_MCP_DESTRUCTIVE_ENABLED=true` | Move to Trash by default; permanent delete needs a third switch |
| `create_folder` | ⚠️ | write + `MAIL_MCP_DESTRUCTIVE_ENABLED=true` | Create an IMAP folder (idempotent) |
| `rename_folder` | ⚠️ | write + `MAIL_MCP_DESTRUCTIVE_ENABLED=true` | Rename a folder, refuses collisions |
| `delete_folder` | 🗑️ | write + `MAIL_MCP_DESTRUCTIVE_ENABLED=true` | Delete a folder; non-empty requires `confirm=true` |

There is no `send_email` or `send_draft`. Three levels, each a superset of the one before:

- **Default** — read tools and the four draft tools.
- **`MAIL_MCP_WRITE_ENABLED=true`** — adds `copy_email`, `move_email`, `mark_emails`.
- **`MAIL_MCP_WRITE_ENABLED=true` + `MAIL_MCP_DESTRUCTIVE_ENABLED=true`** — adds `delete_emails` and the folder tools. The destructive switch on its own does nothing.

Tools outside the active level are *not registered*: the model cannot enumerate them, let alone call them.

### Recipes the LLM will get right

The server publishes a concise `instructions` block at handshake time, so the assistant knows what mail-mcp exposes without guessing. The recipes below are the spots where naive integrations stumble — every one of them is what mail-mcp *automates* so the LLM doesn't have to reason about it.

**Pull a PDF the user just received.**
`search_emails` (filter by sender / subject / date) → `get_email` (note the attachment `index`) → `download_attachment(index=…, filename="…")`. The tool writes `~/Downloads/mail-mcp/<alias>/<filename>` and returns the absolute path; the assistant then reads from there with its own file tool. No Microsoft Graph, no `az login`, no OWA links, no browser detour.

**Read a forwarded message.**
Outlook / Exchange "Forward as attachment" wraps the original in a `message/rfc822` part — naive parsers return an empty body. `get_email` unfolds the inner body into the outer one under a `--- Forwarded message ---` divider, and exposes the rfc822 as a virtual attachment that `download_attachment` writes as `.eml`. "Forward inline" with HTML-only payload (common in Outlook 365) used to return empty body too — now `get_email` renders the HTML to a plain-text approximation so the field is never silently empty.

**Targeting one account out of several.**
Every tool takes `account="<alias>"`. Omit it to use the default from `~/.config/mail-mcp/config.json`.

**Search across non-English mail.**
IMAP SEARCH is plain-ASCII per RFC 3501. Use `"nomina"`, not `"nómina"`. Folder names in your language (`Borradores`, `Papelera`, `Elementos eliminados`, `Brouillons`, `Entwürfe`, …) are detected at setup and resolved at every call; the LLM never has to know the localised string.

**Draft a formatted (HTML) email.**
Put the HTML in `body_html` and a real plain-text version of the same content in `body` — the message ships as `multipart/alternative` (plain + html), which is what spam filters and text-mode clients expect. HTML pasted into `body` alone is delivered as raw source; the response flags it with `html_warning` instead of failing. Works on `save_draft`, `reply_draft` (quote appended to both parts) and `update_draft` (partial updates preserve both parts). Link images by URL — inline `cid:` embedding is not supported.

**Sign emails like Outlook does.**
Put the account's signature in `~/.config/mail-mcp/signatures/<alias>/firma.html` and/or `firma.txt` (or point `signature_html_path` / `signature_text_path` in the config at files inside that directory). The write tools append it after the agent's text and before any reply quote: the text one after a `-- ` line in `body`, the HTML one verbatim in `body_html` — so pass `body_html` when the rich signature matters; a plain-text message is never turned into HTML. The signature always ends the agent's own text: before a quote generated by a mail client (Outlook, Gmail, Apple Mail, Thunderbird), at the very end otherwise — `>` quotes and bottom-posted replies are treated as the agent's text, and prose is never mistaken for a quote. A body that already contains the signature is left alone — one that only quotes your earlier signature is still signed — and `include_signature=false` skips it for one call. `get_account_info` tells the agent whether the account has one and its `mode`: with `"ask"` (the default) the agent must ask you and pass `include_signature=true|false` — omitting it returns `SIGNATURE_CHOICE_REQUIRED` and nothing is saved or sent — the decision is never guessed from the body, so editing a draft with `update_draft` asks too (the agent repeats your choice for that draft). `"auto"` signs every message unless told not to. With Microsoft 365 the signature lives in the mailbox and cannot be read over IMAP: save it once from the HTML of a message you sent from Outlook.

**Attach evidence with hash integrity (forensic).**
Pass `raw_passthrough: true` in an `AttachmentSpec`. The bytes go on the wire byte-for-byte, base64-encoded but never re-canonicalised. The recipient verifies `SHA-256(received) == SHA-256(source-on-disk)`. Trade-off: the file arrives as `application/octet-stream` regardless of extension, so the recipient saves and renames if they want their mail client to auto-render it as `.eml`.

**Escape hatch when MIME is exotic.**
`get_email_raw(uid=N, max_bytes=…)` returns the full RFC822 source, capped, wrapped in `<untrusted_email_content>`, and also written to `~/Downloads/mail-mcp/<alias>/raw-uid-<N>.eml`. Reach for it when `get_email` returns an empty body or `list_attachments` is missing parts you can see in the user's mail client.

**Permanent delete that can't take other people's mail with it.**
`delete_emails(permanent=true)` uses RFC 4315 `UID EXPUNGE` (UIDPLUS) under the hood, scoped to the UIDs you asked to remove. On a server without UIDPLUS the call refuses with `error.code = "UIDPLUS_REQUIRED_FOR_SAFE_EXPUNGE"` and *no mutation* — the alternative (bare `EXPUNGE`) would delete every message any client had flagged `\Deleted` in that mailbox, which mail-mcp will not do.

## Install

```bash
# Install this fork straight from its repo (do NOT `pip install mail-mcp` — that is upstream):
pip install "git+https://github.com/dennisfoconnor/mail-mcp.git@main"
```

Requires Python ≥ 3.11. On Linux make sure `libsecret` is installed (most desktops have it); on Windows and macOS the keyring backend ships with the OS.

A full step-by-step integration guide (including Claude Desktop / Claude Code / Codex CLI config snippets and provider hosts) lives at [`docs/INTEGRATION.md`](docs/INTEGRATION.md).

## Setup

### The quick path — interactive wizard

```bash
pip install "mail-mcp[cli] @ git+https://github.com/dennisfoconnor/mail-mcp.git@main"
mail-mcp init
```

`mail-mcp init` asks for your email address, auto-detects the IMAP and SMTP endpoints for your provider (Gmail, iCloud, Outlook.com, Fastmail, Yahoo, IONOS, GMX, Zoho, mailbox.org, Yandex, custom domains hosted on Google Workspace / Microsoft 365, and others), prompts for your password, tests the IMAP login live, and saves the account to the OS keyring. The SMTP endpoint is still recorded so the config file stays compatible with upstream, but this fork never connects to it. No flags to remember.

### The scripted path

```bash
mail-mcp add-account personal m@example.com \
  --imap-host imap.example.com --imap-port 993 \
  --smtp-host smtp.example.com --smtp-port 587

mail-mcp check --alias personal
mail-mcp serve
```

Optional per-account fields in `~/.config/mail-mcp/config.json` (all default to "off", so existing configs keep working):

| Field | What it does |
|---|---|
| `smtp_username` | Inert in this fork (kept so upstream configs load). Upstream: SMTP login identity when it differs from `email` — e.g. the Microsoft 365 UPN (see [`docs/OAUTH_MICROSOFT.md`](docs/OAUTH_MICROSOFT.md)). `add-account --smtp-username`. |
| `signature_mode` | `"ask"` (default): the agent must decide per message whether to add the signature (`include_signature`), else `SIGNATURE_CHOICE_REQUIRED`. `"auto"`: sign unless told not to. |
| `signature_html_path` / `signature_text_path` | Signature files. Default: `signatures/<alias>/firma.html` / `firma.txt` next to the config, if present. `""` disables that part; relative paths are relative to the config directory. Must stay inside `~/.config/mail-mcp/signatures/`, ≤ 64 KiB, UTF-8. |

Re-running `init` or `add-account` on an existing alias keeps these fields.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mail-mcp": {
      "command": "mail-mcp",
      "args": ["serve"]
    }
  }
}
```

To enable copy / move / flag, add an `env` block. Add `"MAIL_MCP_DESTRUCTIVE_ENABLED": "true"` next to it only if you also want delete and folder changes:

```json
{
  "mcpServers": {
    "mail-mcp": {
      "command": "mail-mcp",
      "args": ["serve"],
      "env": {
        "MAIL_MCP_WRITE_ENABLED": "true"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add mail-mcp mail-mcp serve
```

To enable writes:

```bash
claude mcp add --env MAIL_MCP_WRITE_ENABLED=true mail-mcp mail-mcp serve
```

### Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.mail-mcp]
command = "mail-mcp"
args    = ["serve"]

[mcp_servers.mail-mcp.env]
MAIL_MCP_WRITE_ENABLED = "true"   # optional
```

## Provider support

| Provider | Works with `mail-mcp init` | Notes |
|----------|---------------------------|-------|
| IONOS, Fastmail, mailbox.org, GMX, Web.de, Zoho, Yandex | ✅ | Native password or app password |
| Gmail | ✅ with app password | Enable 2FA, then generate at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) |
| iCloud | ✅ with app password | Required; generate at appleid.apple.com |
| Outlook.com personal | ✅ with app password | Generate at account.live.com |
| Microsoft 365 (tenant managed) | ✅ with OAuth2 (browser sign-in) | Install `mail-mcp[oauth-microsoft]`, register a public-client app in Azure AD, follow [`docs/OAUTH_MICROSOFT.md`](docs/OAUTH_MICROSOFT.md). Basic-auth is off by default on M365 since late 2022. |
| Proton Mail | ✅ via Bridge | Run Proton Bridge; `mail-mcp init` detects the `proton.me` domain and points at `127.0.0.1:1143` |
| Custom domain on any of the above | ✅ | Autoconfig resolves via MX / SRV / `autoconfig.<domain>` |

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) for the common
failures and their fixes.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MAIL_MCP_WRITE_ENABLED` | `false` | Register the organising tools (`copy_email`, `move_email`, `mark_emails`). When unset they are *not registered* — the LLM cannot enumerate them. |
| `MAIL_MCP_DESTRUCTIVE_ENABLED` | `false` | Additionally register `delete_emails` and the folder tools (`create_folder`, `rename_folder`, `delete_folder`). Only takes effect together with `MAIL_MCP_WRITE_ENABLED=true`. |
| `MAIL_MCP_ALLOW_PERMANENT_DELETE` | `false` | Allow `permanent=true` on `delete_emails`. Permanent delete uses UID-scoped `EXPUNGE` (RFC 4315 UIDPLUS) — never bare `EXPUNGE`. |
| `MAIL_MCP_ATTACHMENT_DIR` | _unset_ | Additional directory accepted as a draft-attachment source on top of the single default, `~/Documents/mail-mcp-outbox`. Upstream also allowed `~/Downloads` and `$TMPDIR`; this fork does not. |

`MAIL_MCP_SEND_ENABLED` and `MAIL_MCP_SEND_HOURLY_LIMIT` are upstream switches. This fork ignores them.
| `MAIL_MCP_LOG_LEVEL` | `WARNING` | Server log level on stderr (`DEBUG` / `INFO` / `WARNING` / `ERROR`). Logs are sanitised — bearer tokens, `XOAUTH2`/`AUTH PLAIN`/`AUTH LOGIN` blobs, and `password=…` / `secret=…` / `token=…` key-value pairs are scrubbed before write. |
| `MAIL_MCP_IMAP_CONNECT_TIMEOUT` | `15` | IMAP TCP + TLS handshake timeout, seconds. |
| `MAIL_MCP_IMAP_READ_TIMEOUT` | `30` | IMAP socket read timeout, seconds. |

## Example prompts

Once connected, talk to your AI assistant in plain language. The agent picks the right tool sequence; you stay in the conversation.

- *"Find the last email from Imma and summarise it for me."*
- *"Pull all PDFs from this week's emails with subject containing 'contract' and put them in `~/Documents/contracts`."*
- *"Draft a reply to the UID 4231 email thanking them and confirming the meeting on Thursday."*
- *"Forward this phishing report to abuse@gmail.com — as the attached `.eml` so the headers stay intact."*
- *"This Outlook email looks empty in your fetch. Can you read the raw RFC822 source?"* (the agent reaches for `get_email_raw`)
- *"Draft an evidence pack to CERT-Bund: these three `.eml` files from my outbox folder plus a short cover. I need the SHA-256 of every attachment to match what's on disk."* (the agent uses `raw_passthrough: true`; you send the draft yourself)
- *"Move all 'GitHub notifications' older than 30 days to my Archive folder."* (requires `MAIL_MCP_WRITE_ENABLED=true`)

## Security

Extensive threat model in [SECURITY.md](SECURITY.md) and [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). Short version:

- No plaintext credential storage — everything in the OS keyring.
- No outbound network beyond your IMAP host. No telemetry, no update checks, no relays.
- No send capability at all — `tests/test_no_send.py` fails if a mail-sending library is ever imported.
- Structured IMAP SEARCH (no string concatenation, no injection).
- CRLF-injection defence in every header-bound string.
- XPIA wrapper on every email body returned to the LLM, with closing-tag breakouts and zero-width invisibles neutralised.
- Provider autoconfig XML parsed through `defusedxml` — billion-laughs and XXE refused regardless of the body-size cap.
- Destructive tools require explicit opt-in flags *and* per-call argument confirmation.
- Permanent delete uses RFC 4315 UID-scoped `EXPUNGE` only — bare `EXPUNGE` (which would wipe other clients' `\Deleted`-flagged mail) is refused with a clear typed error.
- Errors sent to the LLM redact bearer tokens, SASL XOAUTH2 / AUTH PLAIN / AUTH LOGIN payloads, IMAP `LOGIN "user" "pass"` traces, plus emails and hostnames.
- Six direct dependencies, reproducible builds, no postinstall hooks.

The codebase has been hardened through repeated adversarial reviews — every release ships with the findings closed and pinned to regression tests.

To report a vulnerability: email `developer@supera.dev` with the subject prefix `[mail-mcp]`. Please do not open a public GitHub issue for security reports.

## Development

```bash
git clone https://github.com/dennisfoconnor/mail-mcp
cd mail-mcp
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest         # unit tests covering the safety boundaries
ruff check src tests
```

## Non-goals

- OAuth2 for Gmail in-tree (use BYO Cloud project or `email-oauth2-proxy`). Microsoft 365 OAuth is **supported** since v0.3 — see [`docs/OAUTH_MICROSOFT.md`](docs/OAUTH_MICROSOFT.md).
- HTTP/SSE transport.
- Web UI.
- Calendar integration.

Keeping the surface tiny is a feature, not a shortcoming.

## License

[MIT](LICENSE). Do whatever you want — credit is appreciated, pull requests even more so.

## Acknowledgements

The design was informed by a parallel audit of nine existing email MCP servers. Patterns that worked well in [`codefuturist/email-mcp`](https://github.com/codefuturist/email-mcp), [`thegreystone/mcp-email`](https://github.com/thegreystone/mcp-email), and [`bradsjm/mail-imap-mcp-rs`](https://github.com/bradsjm/mail-imap-mcp-rs) — structured SEARCH, XPIA envelopes, write-gating, prompt-injection-aware tool descriptions — were adapted here (inspiration, no code copied). The notes from that audit, including what pushed the non-goals list, live in [`SYNTHESIS.md`](SYNTHESIS.md).

A non-trivial share of the v0.3.x hardening came from external bug reports — Outlook 365 forward-as-attachment / forward-inline / `[TRYCREATE]` on localised mailboxes / BEC chain-of-custody attachments — and from Codex adversarial review rounds that caught the real silent breakages (bare `EXPUNGE`, double-encoded XOAUTH2, `update_draft` mailbox bypass). Every finding is closed and pinned to a regression test.

If you build something on top of `mail-mcp`, I'd love to hear about it.
