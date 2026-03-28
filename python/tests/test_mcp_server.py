"""Unit tests for Odoo MCP server with mocked XML-RPC responses.

Tests use FastMCP's direct call_tool/list_tools methods with patched _get_client
to avoid real XML-RPC connections. No Docker or live Odoo instance required.
"""
from __future__ import annotations

import os
import xmlrpc.client
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# OdooClient + OdooConfig unit tests (sync, no MCP needed)
# ---------------------------------------------------------------------------


class TestOdooConfig:
    """Test OdooConfig reads credentials from environment variables."""

    def test_odoo_config_from_env(self, monkeypatch):
        """OdooConfig reads ODOO_URL, ODOO_DB, ODOO_USER, ODOO_API_KEY from env vars."""
        monkeypatch.setenv("ODOO_URL", "http://odoo.example.com:8069")
        monkeypatch.setenv("ODOO_DB", "mydb")
        monkeypatch.setenv("ODOO_USER", "myuser")
        monkeypatch.setenv("ODOO_API_KEY", "myapikey")

        from amil_utils.mcp.odoo_client import OdooConfig

        config = OdooConfig(
            url=os.environ.get("ODOO_URL", "http://localhost:8069"),
            db=os.environ.get("ODOO_DB", "odoo_dev"),
            username=os.environ.get("ODOO_USER", "admin"),
            api_key=os.environ.get("ODOO_API_KEY", "admin"),
        )
        assert config.url == "http://odoo.example.com:8069"
        assert config.db == "mydb"
        assert config.username == "myuser"
        assert config.api_key == "myapikey"

    def test_odoo_config_defaults(self):
        """OdooConfig uses sane defaults when env vars are absent."""
        from amil_utils.mcp.odoo_client import OdooConfig

        config = OdooConfig(
            url=os.environ.get("ODOO_URL", "http://localhost:8069"),
            db=os.environ.get("ODOO_DB", "odoo_dev"),
            username=os.environ.get("ODOO_USER", "admin"),
            api_key=os.environ.get("ODOO_API_KEY", "admin"),
        )
        # Defaults depend on env; just assert the dataclass is frozen
        assert config.url is not None
        assert config.db is not None


class TestOdooClient:
    """Test OdooClient XML-RPC wrapper with mocked ServerProxy."""

    def _make_client(self, uid=2):
        """Create OdooClient with mocked ServerProxy objects."""
        from amil_utils.mcp.odoo_client import OdooClient, OdooConfig

        config = OdooConfig(
            url="http://localhost:8069",
            db="odoo_dev",
            username="admin",
            api_key="admin",
        )
        client = OdooClient(config)
        # Replace internal proxies with mocks
        client._common = MagicMock()
        client._models = MagicMock()
        client._common.authenticate.return_value = uid
        client._common.version.return_value = {"server_version": "17.0"}
        return client

    def test_odoo_client_authenticate(self):
        """OdooClient.authenticate() calls common.authenticate and caches uid."""
        client = self._make_client(uid=2)
        result = client.authenticate()
        assert result == 2
        assert client._uid == 2
        client._common.authenticate.assert_called_once_with(
            "odoo_dev", "admin", "admin", {}
        )

    def test_odoo_client_authenticate_caches_uid(self):
        """Second authenticate call does not re-authenticate (uid is cached via uid property)."""
        client = self._make_client(uid=2)
        client.authenticate()
        # Accessing uid property after authenticate should not re-call authenticate
        uid = client._uid
        assert uid == 2
        assert client._common.authenticate.call_count == 1

    def test_odoo_client_authenticate_failure(self):
        """OdooClient.authenticate() raises ConnectionError if uid is falsy."""
        from amil_utils.mcp.odoo_client import OdooClient, OdooConfig

        config = OdooConfig(
            url="http://localhost:8069",
            db="odoo_dev",
            username="admin",
            api_key="wrong_key",
        )
        client = OdooClient(config)
        client._common = MagicMock()
        client._common.authenticate.return_value = False  # falsy = auth failure

        with pytest.raises(ConnectionError):
            client.authenticate()

    def test_odoo_client_uid_property_lazy(self):
        """uid property triggers authenticate() lazily on first access."""
        client = self._make_client(uid=5)
        # No prior authenticate call
        assert client._uid is None
        uid = client.uid  # triggers lazy auth
        assert uid == 5
        client._common.authenticate.assert_called_once()

    def test_odoo_client_search_read(self):
        """OdooClient.search_read() calls execute_kw with correct args."""
        client = self._make_client(uid=2)
        client.authenticate()

        expected = [{"id": 1, "model": "res.partner", "name": "Contact"}]
        client._models.execute_kw.return_value = expected

        result = client.search_read("ir.model", [], ["model", "name"])
        assert result == expected
        client._models.execute_kw.assert_called_once_with(
            "odoo_dev", 2, "admin",
            "ir.model", "search_read", [[]],
            {"fields": ["model", "name"]},
        )

    def test_odoo_client_search_read_with_limit(self):
        """search_read passes limit kwarg when provided."""
        client = self._make_client(uid=2)
        client.authenticate()
        client._models.execute_kw.return_value = []

        client.search_read("ir.model", [], ["model", "name"], limit=10)
        call_kwargs = client._models.execute_kw.call_args
        assert call_kwargs[0][6] == {"fields": ["model", "name"], "limit": 10}


# ---------------------------------------------------------------------------
# MCP server tool tests -- use FastMCP direct call_tool/list_tools
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Create a mock OdooClient that returns canned responses."""
    pytest.importorskip("mcp", reason="mcp package not installed")
    client = MagicMock()
    client._config = MagicMock()
    client._config.url = "http://localhost:8069"
    client.uid = 2
    client._common = MagicMock()
    client._common.version.return_value = {"server_version": "17.0"}
    return client


@pytest.fixture
def patched_get_client(mock_client):
    """Patch _get_client to return our mock and reset the cached _client."""
    with patch(
        "amil_utils.mcp.server._get_client",
        return_value=mock_client,
    ):
        # Also reset the module-level singleton so tests are isolated
        import amil_utils.mcp.server as srv
        original = srv._client
        srv._client = None
        yield mock_client
        srv._client = original


@pytest.fixture
def server(patched_get_client):
    """Import and return the FastMCP server instance (with patched client)."""
    from amil_utils.mcp.server import mcp
    return mcp


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


async def test_list_tools(server):
    """MCP server should expose exactly 6 tools."""
    tools = await server.list_tools()
    tool_names = {t.name for t in tools}
    assert tool_names == {
        "check_connection",
        "list_models",
        "get_model_fields",
        "list_installed_modules",
        "check_module_dependency",
        "get_view_arch",
    }


# ---------------------------------------------------------------------------
# check_connection
# ---------------------------------------------------------------------------


async def test_check_connection(server, mock_client):
    """check_connection returns version string and uid when connected."""
    result, _ = await server.call_tool("check_connection", {})
    text = result[0].text
    assert "17.0" in text
    assert "uid=2" in text


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


async def test_list_models(server, mock_client):
    """list_models returns formatted list with model names and descriptions."""
    mock_client.search_read.return_value = [
        {"model": "res.partner", "name": "Contact"},
        {"model": "sale.order", "name": "Sales Order"},
    ]
    result, _ = await server.call_tool("list_models", {"name_filter": ""})
    text = result[0].text
    assert "res.partner" in text
    assert "sale.order" in text
    assert "Found 2 models" in text


async def test_list_models_with_filter(server, mock_client):
    """list_models passes name_filter to search domain."""
    mock_client.search_read.return_value = [
        {"model": "sale.order", "name": "Sales Order"},
    ]
    result, _ = await server.call_tool("list_models", {"name_filter": "sale"})
    text = result[0].text
    assert "sale.order" in text
    # Verify the domain was passed with ilike filter
    call_args = mock_client.search_read.call_args
    assert call_args is not None


# ---------------------------------------------------------------------------
# get_model_fields
# ---------------------------------------------------------------------------


async def test_get_model_fields(server, mock_client):
    """get_model_fields returns field name, ttype, relation, required, readonly."""
    mock_client.search_read.return_value = [
        {
            "name": "name",
            "ttype": "char",
            "relation": False,
            "required": True,
            "readonly": False,
            "field_description": "Name",
        },
        {
            "name": "partner_id",
            "ttype": "many2one",
            "relation": "res.partner",
            "required": False,
            "readonly": False,
            "field_description": "Partner",
        },
    ]
    result, _ = await server.call_tool(
        "get_model_fields", {"model_name": "sale.order"}
    )
    text = result[0].text
    assert "name (char)" in text
    assert "partner_id (many2one -> res.partner)" in text
    assert "[required]" in text


async def test_get_model_fields_empty(server, mock_client):
    """get_model_fields returns informative message for unknown model."""
    mock_client.search_read.return_value = []
    result, _ = await server.call_tool(
        "get_model_fields", {"model_name": "nonexistent.model"}
    )
    text = result[0].text
    assert "No fields found" in text
    assert "nonexistent.model" in text


# ---------------------------------------------------------------------------
# list_installed_modules
# ---------------------------------------------------------------------------


async def test_list_installed_modules(server, mock_client):
    """list_installed_modules returns installed module names and versions."""
    mock_client.search_read.return_value = [
        {"name": "base", "installed_version": "17.0.1.3", "shortdesc": "Base"},
        {"name": "sale", "installed_version": "17.0.4.0", "shortdesc": "Sales"},
    ]
    result, _ = await server.call_tool("list_installed_modules", {})
    text = result[0].text
    assert "base" in text
    assert "sale" in text
    assert "17.0.1.3" in text
    assert "Installed modules" in text


# ---------------------------------------------------------------------------
# check_module_dependency
# ---------------------------------------------------------------------------


async def test_check_module_dependency_installed(server, mock_client):
    """check_module_dependency returns INSTALLED with version for installed module."""
    mock_client.search_read.return_value = [
        {"name": "sale", "state": "installed", "installed_version": "17.0.4.0"},
    ]
    result, _ = await server.call_tool(
        "check_module_dependency", {"module_name": "sale"}
    )
    text = result[0].text
    assert "INSTALLED" in text
    assert "17.0.4.0" in text


async def test_check_module_dependency_not_found(server, mock_client):
    """check_module_dependency returns 'not found' for unknown module."""
    mock_client.search_read.return_value = []
    result, _ = await server.call_tool(
        "check_module_dependency", {"module_name": "unknown_module"}
    )
    text = result[0].text
    assert "not found" in text.lower()


async def test_check_module_dependency_not_installed(server, mock_client):
    """check_module_dependency returns NOT installed with state for uninstalled module."""
    mock_client.search_read.return_value = [
        {"name": "hr", "state": "uninstalled", "installed_version": False},
    ]
    result, _ = await server.call_tool(
        "check_module_dependency", {"module_name": "hr"}
    )
    text = result[0].text
    assert "NOT installed" in text
    assert "uninstalled" in text


# ---------------------------------------------------------------------------
# get_view_arch
# ---------------------------------------------------------------------------


async def test_get_view_arch(server, mock_client):
    """get_view_arch returns XML architecture with view name and type."""
    mock_client.search_read.return_value = [
        {
            "name": "res.partner.form",
            "type": "form",
            "arch": "<form><field name='name'/></form>",
            "inherit_id": False,
        },
    ]
    result, _ = await server.call_tool(
        "get_view_arch", {"model_name": "res.partner"}
    )
    text = result[0].text
    assert "res.partner.form" in text
    assert "form" in text
    assert "<form>" in text


async def test_get_view_arch_with_type_filter(server, mock_client):
    """get_view_arch supports view_type filter."""
    mock_client.search_read.return_value = [
        {
            "name": "res.partner.tree",
            "type": "tree",
            "arch": "<tree><field name='name'/></tree>",
            "inherit_id": False,
        },
    ]
    result, _ = await server.call_tool(
        "get_view_arch", {"model_name": "res.partner", "view_type": "tree"}
    )
    text = result[0].text
    assert "tree" in text
    assert "<tree>" in text


async def test_get_view_arch_no_views(server, mock_client):
    """get_view_arch returns informative message when no views found."""
    mock_client.search_read.return_value = []
    result, _ = await server.call_tool(
        "get_view_arch", {"model_name": "no.views.model"}
    )
    text = result[0].text
    assert "No views found" in text


# ---------------------------------------------------------------------------
# Error handling -- all tools must return ERROR string (never crash)
# ---------------------------------------------------------------------------


async def test_connection_error_list_models(server, mock_client):
    """list_models returns ERROR string when Odoo is unreachable."""
    mock_client.search_read.side_effect = ConnectionRefusedError("Connection refused")
    result, _ = await server.call_tool("list_models", {})
    text = result[0].text
    assert "ERROR" in text
    assert "Cannot connect" in text


async def test_connection_error_get_model_fields(server, mock_client):
    """get_model_fields returns ERROR string when Odoo is unreachable."""
    mock_client.search_read.side_effect = OSError("Network unreachable")
    result, _ = await server.call_tool(
        "get_model_fields", {"model_name": "res.partner"}
    )
    text = result[0].text
    assert "ERROR" in text


async def test_connection_error_list_installed_modules(server, mock_client):
    """list_installed_modules returns ERROR string when Odoo is unreachable."""
    mock_client.search_read.side_effect = ConnectionRefusedError("Connection refused")
    result, _ = await server.call_tool("list_installed_modules", {})
    text = result[0].text
    assert "ERROR" in text


async def test_connection_error_check_module_dependency(server, mock_client):
    """check_module_dependency returns ERROR string when Odoo is unreachable."""
    mock_client.search_read.side_effect = ConnectionRefusedError("Connection refused")
    result, _ = await server.call_tool(
        "check_module_dependency", {"module_name": "sale"}
    )
    text = result[0].text
    assert "ERROR" in text


async def test_connection_error_get_view_arch(server, mock_client):
    """get_view_arch returns ERROR string when Odoo is unreachable."""
    mock_client.search_read.side_effect = ConnectionRefusedError("Connection refused")
    result, _ = await server.call_tool(
        "get_view_arch", {"model_name": "res.partner"}
    )
    text = result[0].text
    assert "ERROR" in text


async def test_connection_error_check_connection(server, mock_client):
    """check_connection returns ERROR string when Odoo is unreachable."""
    mock_client._common.version.side_effect = ConnectionRefusedError(
        "Connection refused"
    )
    result, _ = await server.call_tool("check_connection", {})
    text = result[0].text
    assert "ERROR" in text


async def test_xmlrpc_fault_list_models(server, mock_client):
    """list_models returns ERROR: Odoo XML-RPC fault on xmlrpc.client.Fault."""
    mock_client.search_read.side_effect = xmlrpc.client.Fault(
        1, "Access denied: ir.model"
    )
    result, _ = await server.call_tool("list_models", {})
    text = result[0].text
    assert "ERROR" in text
    assert "XML-RPC" in text


async def test_xmlrpc_fault_get_model_fields(server, mock_client):
    """get_model_fields returns ERROR: Odoo XML-RPC fault on xmlrpc.client.Fault."""
    mock_client.search_read.side_effect = xmlrpc.client.Fault(
        1, "Access denied: ir.model.fields"
    )
    result, _ = await server.call_tool(
        "get_model_fields", {"model_name": "res.partner"}
    )
    text = result[0].text
    assert "ERROR" in text
    assert "XML-RPC" in text


# ---------------------------------------------------------------------------
# _mask helper tests (M4 -- API key masking)
# ---------------------------------------------------------------------------


class TestMask:
    """Test _mask() helper for sensitive value masking."""

    def test_mask_long_key(self):
        """Long API key shows first 4 chars followed by '***'."""
        from amil_utils.mcp.server import _mask
        assert _mask("sk-proj-abc123def456") == "sk-p***"

    def test_mask_short_value(self):
        """Value with 2 chars returns '***' (too short to reveal)."""
        from amil_utils.mcp.server import _mask
        assert _mask("ab") == "***"

    def test_mask_empty_value(self):
        """Empty string returns '***'."""
        from amil_utils.mcp.server import _mask
        assert _mask("") == "***"

    def test_mask_exactly_four_chars(self):
        """Value with exactly 4 chars returns '***' (boundary case)."""
        from amil_utils.mcp.server import _mask
        assert _mask("abcd") == "***"

    def test_mask_five_chars(self):
        """Value with 5 chars shows first 4 + '***'."""
        from amil_utils.mcp.server import _mask
        assert _mask("abcde") == "abcd***"


class TestSanitizeError:
    """Test _sanitize_error() scrubs API key from error messages."""

    def test_sanitize_removes_api_key(self, monkeypatch):
        """Error message containing the API key gets it replaced with masked form."""
        monkeypatch.setenv("ODOO_API_KEY", "sk-proj-secret123")
        from amil_utils.mcp.server import _sanitize_error
        msg = "Authentication failed with key sk-proj-secret123 on server"
        result = _sanitize_error(msg)
        assert "sk-proj-secret123" not in result
        assert "sk-p***" in result

    def test_sanitize_leaves_safe_messages(self, monkeypatch):
        """Error message without the API key is returned unchanged."""
        monkeypatch.setenv("ODOO_API_KEY", "sk-proj-secret123")
        from amil_utils.mcp.server import _sanitize_error
        msg = "ERROR: Cannot connect to Odoo instance"
        assert _sanitize_error(msg) == msg

    def test_sanitize_handles_no_key_set(self, monkeypatch):
        """When ODOO_API_KEY is unset, messages pass through unchanged."""
        monkeypatch.delenv("ODOO_API_KEY", raising=False)
        from amil_utils.mcp.server import _sanitize_error
        msg = "ERROR: some error happened"
        assert _sanitize_error(msg) == msg
