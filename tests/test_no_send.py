"""Guard rail: this fork must never be able to send mail.

Upstream mail-mcp ships ``send_email`` / ``send_draft`` behind environment
switches. This fork removes the capability instead of gating it, because an
agent that reads untrusted mail and can also transmit is exploitable by
anyone who can put a message in the inbox. These tests fail if sending comes
back by any route: a merge from upstream, a new tool, or a new import.

They check the package under ``src/`` only. The integration suite still
uses ``smtplib`` as a fixture to seed mail into its test server.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

import mail_mcp
from mail_mcp import server as server_module

PACKAGE_DIR = Path(mail_mcp.__file__).resolve().parent

# Libraries whose only purpose here would be to transmit mail.
FORBIDDEN_MODULES = {"smtplib", "aiosmtplib", "smtpd", "aiosmtpd"}

_ALL_GATES = {
    "MAIL_MCP_WRITE_ENABLED": "true",
    "MAIL_MCP_DESTRUCTIVE_ENABLED": "true",
    "MAIL_MCP_ALLOW_PERMANENT_DELETE": "true",
    # The upstream switches. Setting them must change nothing.
    "MAIL_MCP_SEND_ENABLED": "true",
    "MAIL_MCP_SEND_HOURLY_LIMIT": "1000",
}


def _package_sources() -> list[Path]:
    files = sorted(PACKAGE_DIR.rglob("*.py"))
    assert files, f"no sources found under {PACKAGE_DIR}"
    return files


def _imported_modules(tree: ast.AST) -> set[str]:
    """Top-level names of every module imported anywhere in ``tree``.

    Covers ``import x``, ``from x import y`` and the string-based forms
    ``__import__("x")`` / ``importlib.import_module("x")``.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            first = node.args[0]
            if (
                name in {"__import__", "import_module"}
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                found.add(first.value.split(".")[0])
    return found


@pytest.mark.parametrize("path", _package_sources(), ids=lambda p: p.name)
def test_no_module_imports_a_mail_sending_library(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad = _imported_modules(tree) & FORBIDDEN_MODULES
    assert not bad, f"{path.relative_to(PACKAGE_DIR)} imports {sorted(bad)}"


def test_import_scanner_catches_every_form() -> None:
    """The scanner itself must not have a blind spot."""
    for source in (
        "import smtplib",
        "import smtplib as s",
        "from smtplib import SMTP",
        "def f():\n    import smtplib",
        "__import__('smtplib')",
        "import importlib\nimportlib.import_module('smtplib')",
        "from importlib import import_module\nimport_module('smtplib')",
    ):
        assert "smtplib" in _imported_modules(ast.parse(source)), source
    assert "smtplib" not in _imported_modules(ast.parse("x = 'smtplib'  # just a string"))


def test_send_module_is_gone() -> None:
    assert importlib.util.find_spec("mail_mcp.tools.send") is None


def test_message_builder_module_has_no_transport() -> None:
    from mail_mcp import smtp_client

    for name in ("send", "test_login", "_smtp_authenticate", "smtplib", "PartialDeliveryError"):
        assert not hasattr(smtp_client, name), f"smtp_client.{name} is back"


def test_drafts_module_has_no_send_draft() -> None:
    from mail_mcp.tools import drafts, schemas

    assert not hasattr(drafts, "send_draft")
    assert not hasattr(schemas, "SendDraftInput")
    assert not hasattr(schemas, "SendEmailInput")


def test_no_switch_registers_a_send_tool(monkeypatch) -> None:
    for name, value in _ALL_GATES.items():
        monkeypatch.setenv(name, value)

    names = {tool.name for tool, _s, _h in server_module.build_tool_table()}
    assert not {n for n in names if "send" in n.lower()}, names


def test_no_tool_is_marked_open_world(monkeypatch) -> None:
    """``openWorldHint`` marks tools that reach other people; upstream set it on send only."""
    for name, value in _ALL_GATES.items():
        monkeypatch.setenv(name, value)

    for tool, _s, _h in server_module.build_tool_table():
        annotations = tool.annotations
        hint = (
            annotations.get("openWorldHint")
            if isinstance(annotations, dict)
            else getattr(annotations, "openWorldHint", None)
        )
        assert not hint, tool.name


def test_instructions_do_not_coach_the_model_to_enable_sending() -> None:
    text = server_module.SERVER_INSTRUCTIONS
    assert "SEND_ENABLED" not in text
    assert "send_email" not in text
    assert "send_draft" not in text
    assert "CANNOT send" in text
    assert not hasattr(server_module, "SEND_REMEDIATION")


def test_oauth_does_not_request_smtp_scope() -> None:
    from mail_mcp import oauth

    assert not [scope for scope in oauth.SCOPES if "smtp" in scope.lower()]


def test_default_attachment_allowlist_is_the_outbox_only(monkeypatch) -> None:
    from mail_mcp.safety import attachments

    monkeypatch.delenv("MAIL_MCP_ATTACHMENT_DIR", raising=False)
    assert attachments._allowed_roots() == [Path.home() / "Documents" / "mail-mcp-outbox"]


def test_temp_and_home_files_cannot_be_attached(tmp_path, monkeypatch) -> None:
    """Upstream allowed ``$TMPDIR`` and ``~/Downloads``; the fork allows neither."""
    from mail_mcp.safety import attachments
    from mail_mcp.safety.validation import ValidationError

    monkeypatch.delenv("MAIL_MCP_ATTACHMENT_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    downloads = tmp_path / "home" / "Downloads"
    downloads.mkdir(parents=True)

    for target in (tmp_path / "in-tmp.txt", downloads / "in-downloads.txt"):
        target.write_text("secret", encoding="utf-8")
        with pytest.raises(ValidationError, match="outside the allowed"):
            attachments.resolve(
                raw_path=str(target), filename_override=None, content_type_override=None,
            )


def test_outbox_files_can_be_attached(tmp_path, monkeypatch) -> None:
    from mail_mcp.safety import attachments

    monkeypatch.delenv("MAIL_MCP_ATTACHMENT_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    outbox = tmp_path / "Documents" / "mail-mcp-outbox"
    outbox.mkdir(parents=True)
    target = outbox / "report.txt"
    target.write_text("ok", encoding="utf-8")

    resolved = attachments.resolve(
        raw_path=str(target), filename_override=None, content_type_override=None,
    )
    assert resolved.path == target.resolve()
