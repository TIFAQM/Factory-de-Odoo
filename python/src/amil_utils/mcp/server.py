"""Odoo MCP server for model introspection.

Exposes tools for querying model schemas, field definitions,
installed modules, and view architectures from a live Odoo instance.

Transport: stdio (for Claude Code integration).

IMPORTANT: Never use print() in this module. All logging must go to stderr.
Using print() corrupts the JSON-RPC stdio transport protocol.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import xmlrpc.client

try:
    from mcp.server.fastmcp import FastMCP
    _HAS_MCP = True
except ImportError:
    FastMCP = None  # type: ignore[assignment,misc]
    _HAS_MCP = False

from amil_utils.mcp.odoo_client import OdooClient, OdooConfig

# Configure logging to stderr -- stdout is the JSON-RPC transport
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("odoo-mcp")

_LOCALHOST_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


def _warn_if_cleartext_remote(url: str) -> None:
    """Warn if XML-RPC URL uses HTTP for a non-localhost host."""
    if not url.startswith("http://"):
        return
    if any(host in url for host in _LOCALHOST_HOSTS):
        return
    logger.warning(
        "ODOO_URL uses HTTP for a remote host (%s). "
        "XML-RPC credentials will be sent in cleartext. "
        "Consider using HTTPS.",
        url,
    )


# FastMCP server instance -- tool decorators register against this object
if _HAS_MCP:
    mcp = FastMCP("odoo-introspection")
else:
    # Stub that absorbs @mcp.tool() decorators so the module can be imported
    # without the mcp package.  Functions remain plain callables.
    class _StubMCP:
        """No-op stand-in for FastMCP when the mcp package is absent."""

        def tool(self, *_a, **_kw):  # noqa: ANN002,ANN003
            """Return identity decorator -- leaves the function unchanged."""
            def _identity(fn):  # noqa: ANN001
                return fn
            return _identity

    mcp = _StubMCP()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Client lifecycle -- lazy singleton
# ---------------------------------------------------------------------------

_client: OdooClient | None = None


def _get_client() -> OdooClient:
    """Get or create the OdooClient from environment variables.

    Reads ODOO_URL, ODOO_DB, ODOO_USER, ODOO_API_KEY from environment.
    Defaults match the Phase 15 dev instance (localhost:8069, odoo_dev, admin).
    Client is a lazy singleton: created once, reused across tool calls.
    """
    global _client
    if _client is None:
        api_key = os.environ.get("ODOO_API_KEY", "")
        if not api_key:
            logger.error("ODOO_API_KEY environment variable is required but not set")
            raise SystemExit(
                "ODOO_API_KEY environment variable is required. "
                "Set it to your Odoo instance API key."
            )
        config = OdooConfig(
            url=os.environ.get("ODOO_URL", "http://localhost:8069"),
            db=os.environ.get("ODOO_DB", "odoo_dev"),
            username=os.environ.get("ODOO_USER", "admin"),
            api_key=api_key,
        )
        _warn_if_cleartext_remote(config.url)
        _client = OdooClient(config)
        logger.info("OdooClient created for %s", config.url)
    return _client


_MODEL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.]+$")
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VIEW_TYPES = frozenset({"form", "tree", "kanban", "search", "calendar", "graph", "pivot", "activity"})


def _validate_model_name(name: str) -> str | None:
    """Return error string if model_name is invalid, else None."""
    if not name or not _MODEL_NAME_RE.match(name):
        return f"ERROR: Invalid model name '{name}'. Expected format: 'module.model' (e.g. 'res.partner')"
    return None


def _validate_field_name(name: str) -> str | None:
    """Return error string if field_name is invalid, else None."""
    if not name or not _FIELD_NAME_RE.match(name):
        return f"ERROR: Invalid field name '{name}'. Expected format: lowercase with underscores (e.g. 'partner_id')"
    return None


def _handle_error(exc: Exception) -> str:
    """Format exception as a tool-safe error string.

    Returns a string prefixed with "ERROR:" describing the failure.
    Never raises -- always returns a string so the MCP server keeps running.
    """
    if isinstance(exc, (ConnectionRefusedError, OSError)):
        return f"ERROR: Cannot connect to Odoo instance: {exc}"
    if isinstance(exc, xmlrpc.client.Fault):
        return f"ERROR: Odoo XML-RPC fault: {exc.faultString}"
    if isinstance(exc, ConnectionError):
        return f"ERROR: {exc}"
    return f"ERROR: Unexpected error: {exc}"


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def check_connection() -> str:
    """Check connectivity and authentication to the Odoo instance.

    Returns server version and authenticated user uid, or an error message.
    Use this tool first to verify the connection before calling other tools.
    """
    try:
        client = _get_client()
        version = client._common.version()
        uid = client.uid  # triggers lazy authentication
        return (
            f"Connected to Odoo {version.get('server_version', '?')} "
            f"at {client._config.url}, authenticated as uid={uid}"
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def list_models(name_filter: str = "", limit: int = 100) -> str:
    """List all Odoo models available in the instance.

    Args:
        name_filter: Optional substring to filter model technical names (e.g. 'sale').
                     Leave empty to list all models (up to limit).
        limit: Maximum number of models to return. Default 100. Clamped to 1-1000.

    Returns a bullet list of models with their technical name and description.
    For large instances (500+ models), use name_filter to narrow results.
    """
    try:
        client = _get_client()
        clamped_limit = max(1, min(limit or 100, 1000))
        domain = [["model", "ilike", name_filter]] if name_filter else []
        results = client.search_read(
            "ir.model", domain, ["model", "name"], limit=clamped_limit
        )
        lines = [f"- {r['model']}: {r['name']}" for r in results]
        return f"Found {len(results)} models:\n" + "\n".join(lines)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def get_model_fields(model_name: str) -> str:
    """Get field definitions for an Odoo model.

    Args:
        model_name: Technical model name (e.g. 'res.partner', 'sale.order').

    Returns field name, type (ttype), relation target (for relational fields),
    and flags (required, readonly) for each field in the model.
    """
    try:
        client = _get_client()
        fields = client.search_read(
            "ir.model.fields",
            [["model", "=", model_name]],
            ["name", "ttype", "relation", "required", "readonly", "field_description"],
        )
        if not fields:
            return (
                f"No fields found for model '{model_name}'. "
                "Does it exist? Try list_models to find the correct name."
            )
        lines = []
        for f in fields:
            rel = f" -> {f['relation']}" if f.get("relation") else ""
            req = " [required]" if f.get("required") else ""
            ro = " [readonly]" if f.get("readonly") else ""
            lines.append(
                f"- {f['name']} ({f['ttype']}{rel}){req}{ro}"
                f"  # {f.get('field_description', '')}"
            )
        return (
            f"Fields for {model_name} ({len(fields)} fields):\n"
            + "\n".join(lines)
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def list_installed_modules() -> str:
    """List all installed Odoo modules with their versions.

    Returns module technical name, installed version, and short description
    for every module in state 'installed'.
    """
    try:
        client = _get_client()
        modules = client.search_read(
            "ir.module.module",
            [["state", "=", "installed"]],
            ["name", "installed_version", "shortdesc"],
        )
        lines = [
            f"- {m['name']} v{m.get('installed_version', '?')}"
            f"  ({m.get('shortdesc', '')})"
            for m in modules
        ]
        return (
            f"Installed modules ({len(modules)}):\n" + "\n".join(lines)
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def check_module_dependency(module_name: str) -> str:
    """Check if a specific Odoo module is installed in the instance.

    Args:
        module_name: Technical module name (e.g. 'sale', 'hr', 'account').

    Returns the installation state: INSTALLED (with version), NOT installed
    (with current state), or not found if the module does not exist.
    """
    try:
        client = _get_client()
        result = client.search_read(
            "ir.module.module",
            [["name", "=", module_name]],
            ["name", "state", "installed_version"],
        )
        if not result:
            return f"Module '{module_name}' not found in the Odoo instance."
        mod = result[0]
        if mod["state"] == "installed":
            return (
                f"Module '{module_name}' is INSTALLED "
                f"(version {mod.get('installed_version', '?')})"
            )
        return (
            f"Module '{module_name}' exists but is NOT installed "
            f"(state: {mod['state']})"
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def get_view_arch(model_name: str, view_type: str = "") -> str:
    """Get XML view architecture for an Odoo model from ir.ui.view.

    Args:
        model_name: Technical model name (e.g. 'res.partner').
        view_type: Optional view type filter ('form', 'tree', 'kanban', 'search').
                   Leave empty to return all view types.

    Returns the raw XML architecture from ir.ui.view records.
    For inherited views, the arch contains XPATH expressions showing
    what is added/modified, which is useful for code generation.
    """
    try:
        client = _get_client()
        domain: list = [["model", "=", model_name]]
        if view_type:
            domain.append(["type", "=", view_type])
        views = client.search_read(
            "ir.ui.view",
            domain,
            ["name", "type", "arch", "inherit_id"],
        )
        if not views:
            return (
                f"No views found for model '{model_name}'"
                + (f" of type '{view_type}'" if view_type else "")
            )
        parts = []
        for v in views:
            inherit = (
                f" (inherits: {v['inherit_id'][1]})"
                if v.get("inherit_id")
                else ""
            )
            parts.append(
                f"### {v['name']} ({v['type']}{inherit})\n"
                f"```xml\n{v.get('arch', '')}\n```"
            )
        return (
            f"Views for {model_name} ({len(views)} views):\n\n"
            + "\n\n".join(parts)
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def get_view_inheritance_chain(model_name: str, view_type: str = "form") -> str:
    """Trace the full view inheritance chain for an Odoo model.

    Queries ir.ui.view to find the base view (inherit_id=False) and all
    inherited views, sorted by priority. Essential for avoiding XPATH
    conflicts when multiple modules extend the same view.

    Args:
        model_name: Technical model name (e.g. 'res.partner').
        view_type: View type to trace ('form', 'tree', 'kanban', 'search').
                   Defaults to 'form'.

    Returns:
        Chain showing base view → inherited views with priority and module.
    """
    if err := _validate_model_name(model_name):
        return err
    if view_type not in _VIEW_TYPES:
        return f"ERROR: Invalid view_type '{view_type}'. Expected one of: {', '.join(sorted(_VIEW_TYPES))}"
    try:
        client = _get_client()
        all_views = client.search_read(
            "ir.ui.view",
            [["model", "=", model_name], ["type", "=", view_type]],
            ["name", "inherit_id", "priority", "arch", "xml_id"],
        )
        if not all_views:
            return f"No {view_type} views found for model '{model_name}'"

        base_views = [v for v in all_views if not v.get("inherit_id")]
        inherited = [v for v in all_views if v.get("inherit_id")]
        inherited.sort(key=lambda v: v.get("priority", 16))

        parts = [f"View inheritance chain for {model_name} ({view_type}):"]
        parts.append(f"Total views: {len(all_views)} ({len(base_views)} base, {len(inherited)} inherited)")
        parts.append("")

        for bv in base_views:
            parts.append(f"BASE: {bv['name']} (xml_id: {bv.get('xml_id', 'N/A')}, priority: {bv.get('priority', 16)})")

        parts.append("")
        for iv in inherited:
            parent_name = iv["inherit_id"][1] if iv.get("inherit_id") else "unknown"
            parts.append(
                f"  INHERITS: {iv['name']} → parent: {parent_name} "
                f"(priority: {iv.get('priority', 16)}, xml_id: {iv.get('xml_id', 'N/A')})"
            )

        return "\n".join(parts)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def get_model_relations(model_name: str) -> str:
    """Get all relational fields pointing to/from an Odoo model.

    Queries ir.model.fields to find:
    - Outgoing relations: Many2one/One2many/Many2many fields ON this model
    - Incoming relations: Many2one/One2many/Many2many fields on OTHER models pointing TO this model

    Args:
        model_name: Technical model name (e.g. 'res.partner').

    Returns:
        Formatted list of outgoing and incoming relational fields.
    """
    if err := _validate_model_name(model_name):
        return err
    try:
        client = _get_client()
        relation_types = ["many2one", "one2many", "many2many"]

        outgoing = client.search_read(
            "ir.model.fields",
            [["model", "=", model_name], ["ttype", "in", relation_types]],
            ["name", "ttype", "relation", "field_description"],
        )

        incoming = client.search_read(
            "ir.model.fields",
            [["relation", "=", model_name], ["ttype", "in", relation_types]],
            ["name", "ttype", "model", "field_description"],
        )

        parts = [f"Relations for {model_name}:"]
        parts.append(f"\nOutgoing ({len(outgoing)} fields — this model references others):")
        if outgoing:
            for f in outgoing:
                parts.append(f"  {f['name']} ({f['ttype']}) → {f['relation']}")
        else:
            parts.append("  (none)")

        parts.append(f"\nIncoming ({len(incoming)} fields — other models reference this):")
        if incoming:
            for f in incoming:
                parts.append(f"  {f['model']}.{f['name']} ({f['ttype']}) → {model_name}")
        else:
            parts.append("  (none)")

        return "\n".join(parts)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def find_field_conflicts(model_name: str, field_name: str) -> str:
    """Check if a field already exists on an Odoo model (potential conflict).

    Queries ir.model.fields to see if field_name is already defined on
    model_name by any installed module. Prevents duplicate field definitions
    across 90+ generated modules.

    Args:
        model_name: Technical model name (e.g. 'res.partner').
        field_name: Field name to check (e.g. 'x_custom_score').

    Returns:
        CONFLICT with details if field exists, CLEAR if not.
    """
    if err := _validate_model_name(model_name):
        return err
    if err := _validate_field_name(field_name):
        return err
    try:
        client = _get_client()
        existing = client.search_read(
            "ir.model.fields",
            [["model", "=", model_name], ["name", "=", field_name]],
            ["name", "ttype", "field_description", "modules"],
        )
        if existing:
            f = existing[0]
            modules = f.get("modules", "unknown")
            return (
                f"CONFLICT: Field '{field_name}' already exists on '{model_name}' "
                f"(type: {f['ttype']}, label: {f.get('field_description', 'N/A')}, "
                f"defined by: {modules})"
            )
        return f"CLEAR: Field '{field_name}' does not exist on '{model_name}'"
    except Exception as exc:
        return _handle_error(exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server on stdio transport.

    Invoked via `python -m amil_utils.mcp.server` or
    `python -m amil_utils.mcp` (via __main__.py).
    """
    if not _HAS_MCP:
        raise RuntimeError(
            "mcp package not installed. Install with: pip install 'amil-utils[mcp]'"
        )
    logger.info("Starting Odoo MCP server (stdio transport)...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
