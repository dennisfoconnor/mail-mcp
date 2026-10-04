"""Which tools are registered under which environment switches.

The tool table is the security boundary: a tool that is not in it cannot be
listed or called by the model. These tests pin the exact set for every
combination of switches, so adding a tool (or moving one to a weaker gate)
has to be a deliberate change to this file.
"""

from pathlib import Path

import pytest

from mail_mcp import server as server_module

READ_AND_DRAFT_TOOLS = {
    "list_accounts",
    "get_account_info",
    "get_special_folders",
    "get_quota",
    "list_drafts",
    "get_thread",
    "list_folders",
    "search_emails",
    "get_email",
    "list_attachments",
    "get_email_raw",
    "download_attachment",
    "save_draft",
    "update_draft",
    "reply_draft",
    "forward_draft",
}
ORGANIZE_TOOLS = {"copy_email", "move_email", "mark_emails"}
DESTRUCTIVE_TOOLS = {"delete_emails", "create_folder", "rename_folder", "delete_folder"}

_GATES = ("MAIL_MCP_WRITE_ENABLED", "MAIL_MCP_DESTRUCTIVE_ENABLED")


class _FakeCfg:
    path = Path("/tmp/x")

    class model:
        accounts: list = []
        default_alias = None


def _table_names() -> set[str]:
    return {tool.name for tool, _schema, _handler in server_module.build_tool_table()}


@pytest.fixture(autouse=True)
def _clean_gates(monkeypatch):
    for name in _GATES:
        monkeypatch.delenv(name, raising=False)


def test_default_is_read_and_drafts_only():
    assert _table_names() == READ_AND_DRAFT_TOOLS
    assert server_module.write_enabled() is False
    assert server_module.destructive_enabled() is False


def test_write_adds_only_copy_move_and_flags(monkeypatch):
    monkeypatch.setenv("MAIL_MCP_WRITE_ENABLED", "true")

    assert _table_names() == READ_AND_DRAFT_TOOLS | ORGANIZE_TOOLS
    assert server_module.destructive_enabled() is False


def test_destructive_needs_both_switches(monkeypatch):
    monkeypatch.setenv("MAIL_MCP_WRITE_ENABLED", "true")
    monkeypatch.setenv("MAIL_MCP_DESTRUCTIVE_ENABLED", "true")

    assert _table_names() == READ_AND_DRAFT_TOOLS | ORGANIZE_TOOLS | DESTRUCTIVE_TOOLS


def test_destructive_switch_alone_does_nothing(monkeypatch):
    """A config that only sets the destructive switch must not expose delete."""
    monkeypatch.setenv("MAIL_MCP_DESTRUCTIVE_ENABLED", "true")

    assert _table_names() == READ_AND_DRAFT_TOOLS
    assert server_module.destructive_enabled() is False


@pytest.mark.parametrize("value", ["1", "yes", "on", "True ", "enabled", ""])
def test_switches_only_accept_the_word_true(monkeypatch, value):
    """Anything but ``true`` (any case) leaves the gate shut — a typo fails closed."""
    monkeypatch.setenv("MAIL_MCP_WRITE_ENABLED", value)
    monkeypatch.setenv("MAIL_MCP_DESTRUCTIVE_ENABLED", value)

    assert _table_names() == READ_AND_DRAFT_TOOLS


def test_every_tool_has_a_schema_and_a_handler(monkeypatch):
    monkeypatch.setenv("MAIL_MCP_WRITE_ENABLED", "true")
    monkeypatch.setenv("MAIL_MCP_DESTRUCTIVE_ENABLED", "true")

    table = server_module.build_tool_table()
    names = [tool.name for tool, _s, _h in table]
    assert len(names) == len(set(names)), "duplicate tool name"
    for tool, schema, handler in table:
        assert callable(handler), tool.name
        assert hasattr(schema, "model_validate"), tool.name


def test_server_lists_exactly_the_table(monkeypatch):
    """The MCP server must expose the table and nothing else.

    Goes through the MCP framework's own ``tools/list`` handler. That
    low-level API only exists in the ``mcp<2`` line this project pins, so
    the test is skipped on a newer SDK instead of failing for the wrong
    reason.
    """
    import mcp
    from mcp.server import Server

    if not hasattr(Server, "list_tools"):
        pytest.skip("installed mcp SDK has no low-level Server.list_tools API")

    monkeypatch.setenv("MAIL_MCP_WRITE_ENABLED", "true")
    server = server_module.build_server(cfg=_FakeCfg())  # type: ignore[arg-type]
    handler = server.request_handlers.get(mcp.types.ListToolsRequest)
    assert handler is not None

    import asyncio

    result = asyncio.run(
        handler(mcp.types.ListToolsRequest(method="tools/list", params=None))
    )
    assert {t.name for t in result.root.tools} == READ_AND_DRAFT_TOOLS | ORGANIZE_TOOLS
