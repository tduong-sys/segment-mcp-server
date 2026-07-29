"""
Segment Workspace Inspector & Operator — local FastMCP server for Gemini CLI,
Claude Desktop, and other MCP-compatible clients.

Read tools and gated mutation tools wrapping the Segment Public API, focused
on Segment function development and identity-resolution debugging.

------------------------------------------------------------------------------
Setup
------------------------------------------------------------------------------
1. Create a virtualenv and install deps:

       uv venv --python $(brew --prefix python@3.12)/bin/python3.12
       source .venv/bin/activate
       uv pip install fastmcp python-dotenv
       uv pip install git+https://github.com/segmentio/public-api-sdk-python.git

2. Generate a Public API token in the Segment app under
   Workspace Settings → Access Management → Tokens. Requires Workspace Owner.

3. Drop the token in a `.env` next to this file:

       SEGMENT_PUBLIC_API_TOKEN=sgp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

4. Register with your MCP client:

   Gemini CLI:
       fastmcp install gemini-cli server.py --server-name segment-workspace

   Claude Desktop: add to claude_desktop_config.json:
       {
         "mcpServers": {
           "segment-workspace": {
             "command": "/abs/path/to/.venv/bin/python",
             "args": ["/abs/path/to/server.py"],
             "env": {"SEGMENT_PUBLIC_API_TOKEN": "sgp_..."}
           }
         }
       }

------------------------------------------------------------------------------
Confirmation gate for mutations
------------------------------------------------------------------------------
Every destructive tool (create / update / delete / deploy) takes a
`confirm: bool = False` parameter. The first call returns a PREVIEW of what
would happen. The LLM is instructed to show the preview to the user and only
re-call with `confirm=True` after explicit human approval.

Because the MCP client (Claude Desktop, Gemini CLI) also prompts the user for
approval on every tool call, the effective gate is two layers:
  1. The first call (no confirm) gets approved → returns preview, no change.
  2. The second call (confirm=True) gets approved → mutation executes.
The user sees the args of both calls before either runs.

------------------------------------------------------------------------------
Method-name drift
------------------------------------------------------------------------------
The Segment SDK is auto-generated and occasionally renames methods between
versions. Any call below marked `# VERIFY` is a best-guess at the current
name. If you hit AttributeError on first run, find the right name with:

    python -c "import segment_public_api as s; print([m for m in dir(s.FooApi) if not m.startswith('_')])"

and patch the call.
"""
from __future__ import annotations

import csv
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from fastmcp import FastMCP
import httpx

import segment_public_api
from segment_public_api.rest import ApiException

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()

TOKEN = os.environ.get("SEGMENT_PUBLIC_API_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "SEGMENT_PUBLIC_API_TOKEN env var is required. Create one in the "
        "Segment app under Workspace Settings → Access Management → Tokens."
    )

_config = segment_public_api.Configuration(access_token=TOKEN)
_client = segment_public_api.ApiClient(_config)

# Core APIs
_sources = segment_public_api.SourcesApi(_client)
_destinations = segment_public_api.DestinationsApi(_client)
_functions = segment_public_api.FunctionsApi(_client)
_tracking_plans = segment_public_api.TrackingPlansApi(_client)
_delivery_metrics = segment_public_api.DeliveryOverviewApi(_client)
_workspaces = segment_public_api.WorkspacesApi(_client)

# Extended APIs
_deletion_suppression = segment_public_api.DeletionAndSuppressionApi(_client)
_audiences = segment_public_api.AudiencesApi(_client)
_computed_traits = segment_public_api.ComputedTraitsApi(_client)
_profiles_sync = segment_public_api.ProfilesSyncApi(_client)
_destination_filters = segment_public_api.DestinationFiltersApi(_client)
_transformations = segment_public_api.TransformationsApi(_client)
_warehouses = segment_public_api.WarehousesApi(_client)
_spaces = segment_public_api.SpacesApi(_client)

PAGE_SIZE = 50

# ---------------------------------------------------------------------------
# Profile API setup (separate auth, separate base URL)
# ---------------------------------------------------------------------------
# Profile API tokens are space-scoped. Configure one per space via env vars
# named SEGMENT_PROFILE_TOKEN_<SPACE_ID>, e.g.:
#     SEGMENT_PROFILE_TOKEN_spa_abc123=spa_token_value
#
# Or, for single-space prototyping, set SEGMENT_PROFILE_TOKEN as a default
# that's used for any space lookup when no per-space token is configured.
#
# SEGMENT_SPACE_ID (optional): default space ID. When set, Profile API tools
# can be called without a space_id argument, falling back to this value.
# Useful for single-space prototyping — the LLM can ask "look up jane@..."
# without naming the space every time.

PROFILE_API_DEFAULT_TOKEN = os.environ.get("SEGMENT_PROFILE_TOKEN")
PROFILE_API_DEFAULT_SPACE = os.environ.get("SEGMENT_SPACE_ID")
PROFILE_API_BASE = "https://profiles.segment.com/v1"


def _resolve_space(space_id: Optional[str]) -> Optional[str]:
    """Resolve the space ID: explicit arg wins, else fall back to env default."""
    return space_id or PROFILE_API_DEFAULT_SPACE


def _profile_token_for_space(space_id: str) -> Optional[str]:
    """Resolve the Profile API token for a given space.

    Looks up SEGMENT_PROFILE_TOKEN_<space_id> first, then falls back to
    the workspace-default SEGMENT_PROFILE_TOKEN.
    """
    return (
        os.environ.get(f"SEGMENT_PROFILE_TOKEN_{space_id}")
        or PROFILE_API_DEFAULT_TOKEN
    )


def _profile_get(space_id: str, path: str) -> dict[str, Any]:
    """GET a Profile API endpoint with Basic auth.

    `path` is the suffix after /spaces/{space_id}/collections/users/profiles/,
    e.g. "user_id:abc123/traits".
    """
    token = _profile_token_for_space(space_id)
    if not token:
        return {
            "ok": False,
            "error": (
                f"No Profile API token configured for space {space_id}. "
                f"Set SEGMENT_PROFILE_TOKEN_{space_id} or SEGMENT_PROFILE_TOKEN "
                "in your .env."
            ),
        }
    url = f"{PROFILE_API_BASE}/spaces/{space_id}/collections/users/profiles/{path}"
    try:
        # HTTP Basic with token as username, empty password
        resp = httpx.get(url, auth=(token, ""), timeout=15.0)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"HTTP error: {e}"}
    if resp.status_code == 404:
        return {
            "ok": False,
            "status": 404,
            "error": "profile_not_found",
            "lookup": path.split("/")[0],
        }
    if resp.status_code >= 400:
        body = resp.text[:500] if resp.text else ""
        return {"ok": False, "status": resp.status_code, "body": body}
    try:
        return {"ok": True, **resp.json()}
    except ValueError:
        return {"ok": False, "error": "non-json response", "body": resp.text[:500]}


def _profile_identifier(id_type: str, id_value: str) -> str:
    """Format identifier as expected by Profile API path segments.

    Examples: user_id:abc123, email:foo@example.com, anonymous_id:xyz
    """
    return f"{id_type}:{id_value}"


mcp = FastMCP("segment-workspace")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _err(e: ApiException) -> dict[str, Any]:
    """Normalize SDK errors into something the LLM can reason about."""
    body = e.body or ""
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", errors="replace")
    return {
        "ok": False,
        "status": e.status,
        "reason": e.reason,
        "body": str(body)[:500],
    }


def _next_cursor(pagination) -> Optional[str]:
    return getattr(pagination, "next", None) or getattr(pagination, "next_cursor", None)


def _preview(action: str, **details) -> dict[str, Any]:
    """Standardized preview response for a gated mutation tool.

    The LLM should display this to the user verbatim, get explicit approval,
    then re-call the same tool with confirm=True.
    """
    return {
        "ok": False,
        "status": "preview",
        "action": action,
        "details": details,
        "confirm_token": secrets.token_hex(8),
        "next_step": (
            "DESTRUCTIVE ACTION. Show this preview to the user, get explicit "
            "approval, then re-call the same tool with confirm=True."
        ),
    }


def _project_source(s) -> dict[str, Any]:
    return {
        "id": s.id,
        "slug": getattr(s, "slug", None),
        "name": getattr(s, "name", None),
        "enabled": getattr(s, "enabled", None),
        "type": getattr(getattr(s, "metadata", None), "name", None),
    }


def _project_destination(d) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": getattr(d, "name", None),
        "enabled": getattr(d, "enabled", None),
        "source_id": getattr(d, "source_id", None),
        "type": getattr(getattr(d, "metadata", None), "name", None),
    }


def _project_function(f) -> dict[str, Any]:
    return {
        "id": f.id,
        "display_name": getattr(f, "display_name", None),
        "resource_type": getattr(f, "resource_type", None),
        "code_preview": (getattr(f, "code", "") or "")[:200],
        "created_at": str(getattr(f, "created_at", "")),
    }


# ===========================================================================
# READ-ONLY TOOLS
# ===========================================================================

# ---- Workspace -----------------------------------------------------------

@mcp.tool()
def get_workspace() -> dict[str, Any]:
    """Return basic info about the currently authenticated Segment workspace."""
    try:
        resp = _workspaces.get_workspace()
    except ApiException as e:
        return _err(e)
    w = resp.data.workspace
    return {"ok": True, "id": w.id, "name": w.name, "slug": w.slug}


# ---- Sources -------------------------------------------------------------

@mcp.tool()
def list_sources(only_enabled: bool = False) -> dict[str, Any]:
    """List sources in the workspace.

    Args:
        only_enabled: If True, omit disabled sources.
    """
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            resp = _sources.list_sources(pagination=page)
            for s in resp.data.sources:
                if only_enabled and not getattr(s, "enabled", False):
                    continue
                items.append(_project_source(s))
            cursor = _next_cursor(resp.data.pagination)
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "sources": items}


@mcp.tool()
def get_source(source_id: str) -> dict[str, Any]:
    """Get the full configuration of a single source."""
    try:
        resp = _sources.get_source(source_id=source_id)
        return {"ok": True, "source": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def get_source_schema(source_id: str) -> dict[str, Any]:
    """Return the inferred event schema for a source."""
    try:
        resp = _sources.list_schema_settings_in_source(source_id=source_id)
        return {"ok": True, "schema": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


# ---- Destinations --------------------------------------------------------

@mcp.tool()
def list_destinations(source_id: Optional[str] = None) -> dict[str, Any]:
    """List destinations in the workspace.

    Args:
        source_id: If provided, only return destinations connected to that source.
    """
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            resp = _destinations.list_destinations(pagination=page)
            for d in resp.data.destinations:
                if source_id and getattr(d, "source_id", None) != source_id:
                    continue
                items.append(_project_destination(d))
            cursor = _next_cursor(resp.data.pagination)
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "destinations": items}


@mcp.tool()
def get_destination(destination_id: str) -> dict[str, Any]:
    """Get the full configuration of a single destination, including settings."""
    try:
        resp = _destinations.get_destination(destination_id=destination_id)
        return {"ok": True, "destination": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


# ---- Functions: reads ----------------------------------------------------

@mcp.tool()
def list_functions(resource_type: Optional[str] = None) -> dict[str, Any]:
    """List custom functions in the workspace.

    Args:
        resource_type: Filter to one of SOURCE, DESTINATION, or INSERT.
    """
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    rt = resource_type.upper() if resource_type else None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            if rt:
                resp = _functions.list_functions(pagination=page, resource_type=rt)
            else:
                resp = _functions.list_functions(pagination=page)
            for f in resp.data.functions:
                items.append(_project_function(f))
            cursor = _next_cursor(resp.data.pagination)
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "functions": items}


@mcp.tool()
def get_function(function_id: str, include_code: bool = True) -> dict[str, Any]:
    """Get the full definition of a single function, including its code.

    Args:
        function_id: The function's ID.
        include_code: If False, strip the `code` field to save context space.
    """
    try:
        resp = _functions.get_function(function_id=function_id)
        d = resp.data.to_dict()
        if not include_code:
            fn = d.get("function", {})
            if "code" in fn:
                fn["code"] = f"[{len(fn['code'])} chars, omitted]"
        return {"ok": True, "function": d}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def list_function_versions(function_id: str) -> dict[str, Any]:
    """List all versions of a function (deployment history)."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            resp = _functions.list_function_versions(
                function_id=function_id, pagination=page
            )
            data = resp.data.to_dict()
            for v in data.get("versions", []):
                items.append({
                    "version_id": v.get("id"),
                    "deployed": v.get("deployed"),
                    "created_at": v.get("createdAt"),
                    "created_by": v.get("createdBy"),
                })
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "versions": items}


@mcp.tool()
def get_function_version(function_id: str, version_id: str) -> dict[str, Any]:
    """Get the code and metadata of a specific function version."""
    try:
        resp = _functions.get_function_version(
            function_id=function_id, version_id=version_id
        )
        return {"ok": True, "version": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def list_insert_function_instances(function_id: str) -> dict[str, Any]:
    """List source-level instances of an insert function (where it's attached)."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            resp = _functions.list_insert_function_instances(
                function_id=function_id, pagination=page
            )
            data = resp.data.to_dict()
            for inst in data.get("insertFunctionInstances", []):
                items.append(inst)
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "instances": items}


# ---- Tracking Plans ------------------------------------------------------

@mcp.tool()
def list_tracking_plans() -> dict[str, Any]:
    """List all tracking plans in the workspace."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            resp = _tracking_plans.list_tracking_plans(pagination=page)
            for tp in resp.data.tracking_plans:
                items.append({
                    "id": tp.id,
                    "name": getattr(tp, "name", None),
                    "type": getattr(tp, "type", None),
                    "updated_at": str(getattr(tp, "updated_at", "")),
                })
            cursor = _next_cursor(resp.data.pagination)
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "tracking_plans": items}


@mcp.tool()
def get_tracking_plan_rules(tracking_plan_id: str) -> dict[str, Any]:
    """Return the event rules defined in a tracking plan."""
    rules: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            resp = _tracking_plans.list_rules_from_tracking_plan(
                tracking_plan_id=tracking_plan_id, pagination=page
            )
            for r in resp.data.rules:
                rules.append({
                    "key": getattr(r, "key", None),
                    "type": getattr(r, "type", None),
                    "version": getattr(r, "version", None),
                })
            cursor = _next_cursor(resp.data.pagination)
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(rules), "rules": rules}


# ---- Delivery Metrics ----------------------------------------------------

@mcp.tool()
def get_delivery_metrics(
    source_id: str,
    destination_id: str,
    hours: int = 24,
) -> dict[str, Any]:
    """Egress success and failure counts for a source→destination pair.

    Args:
        source_id: Source ID.
        destination_id: Destination config ID.
        hours: Lookback window in hours. Clamped to 1–696 (29 days).
    """
    hours = max(1, min(hours, 696))
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    if hours <= 4:
        granularity = "MINUTE"
    elif hours <= 336:
        granularity = "HOUR"
    else:
        granularity = "DAY"

    start_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _sum_buckets(resp_obj) -> int:
        try:
            return int(resp_obj.to_dict().get("data", {}).get("total", 0) or 0)
        except Exception:
            return 0

    try:
        success_resp = _delivery_metrics.get_egress_success_metrics_from_delivery_overview(
            source_id=source_id,
            destination_config_id=destination_id,
            start_time=start_iso,
            end_time=end_iso,
            granularity=granularity,
        )
        failed_resp = _delivery_metrics.get_egress_failed_metrics_from_delivery_overview(
            source_id=source_id,
            destination_config_id=destination_id,
            start_time=start_iso,
            end_time=end_iso,
            granularity=granularity,
        )
    except ApiException as e:
        return _err(e)

    success_total = _sum_buckets(success_resp)
    failed_total = _sum_buckets(failed_resp)
    attempted = success_total + failed_total
    failure_rate = (failed_total / attempted) if attempted else 0.0

    return {
        "ok": True,
        "window": {"start": start_iso, "end": end_iso, "granularity": granularity},
        "totals": {
            "success": success_total,
            "failed": failed_total,
            "attempted": attempted,
            "failure_rate": round(failure_rate, 4),
        },
        "raw": {"success": success_resp.to_dict(), "failed": failed_resp.to_dict()},
    }


# ---- Deletion & Suppression: reads --------------------------------------

@mcp.tool()
def list_regulations() -> dict[str, Any]:
    """List all deletion/suppression regulations in the workspace.

    Regulations are how Segment models GDPR-style deletion or suppression
    requests for specific user IDs.
    """
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            # VERIFY: method may be list_regulations or list_workspace_regulations
            resp = _deletion_suppression.list_regulations(pagination=page)
            data = resp.data.to_dict()
            for r in data.get("regulations", []):
                items.append(r)
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "regulations": items}


@mcp.tool()
def get_regulation(regulation_id: str) -> dict[str, Any]:
    """Get the status and details of a single regulation."""
    try:
        # VERIFY: method name
        resp = _deletion_suppression.get_regulation(regulation_id=regulation_id)
        return {"ok": True, "regulation": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


# ---- Spaces, Audiences, Computed Traits, Profiles Sync ------------------

@mcp.tool()
def list_spaces() -> dict[str, Any]:
    """List Unify/Engage spaces in the workspace.

    Audiences, computed traits, and profiles sync are all scoped to a space,
    so you'll typically call this first to get the space_id.
    """
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            # VERIFY: method name
            resp = _spaces.list_spaces(pagination=page)
            data = resp.data.to_dict()
            for sp in data.get("spaces", []):
                items.append({
                    "id": sp.get("id"),
                    "name": sp.get("name"),
                    "slug": sp.get("slug"),
                })
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "spaces": items}


@mcp.tool()
def list_audiences(space_id: str) -> dict[str, Any]:
    """List audiences in a given space."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            # VERIFY: method name
            resp = _audiences.list_audiences(space_id=space_id, pagination=page)
            data = resp.data.to_dict()
            for a in data.get("audiences", []):
                items.append({
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "enabled": a.get("enabled"),
                    "description": a.get("description"),
                })
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "audiences": items}


@mcp.tool()
def get_audience(space_id: str, audience_id: str) -> dict[str, Any]:
    """Get the full definition (including SQL/FQL) of an audience."""
    try:
        # VERIFY: method name and id param name
        resp = _audiences.get_audience(space_id=space_id, id=audience_id)
        return {"ok": True, "audience": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def list_computed_traits(space_id: str) -> dict[str, Any]:
    """List computed traits in a given space."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            # VERIFY: method name
            resp = _computed_traits.list_computed_traits(
                space_id=space_id, pagination=page
            )
            data = resp.data.to_dict()
            for t in data.get("computedTraits", []):
                items.append({
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "enabled": t.get("enabled"),
                    "description": t.get("description"),
                })
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "computed_traits": items}


@mcp.tool()
def get_computed_trait(space_id: str, computed_trait_id: str) -> dict[str, Any]:
    """Get the full definition of a computed trait."""
    try:
        # VERIFY: method name and id param name
        resp = _computed_traits.get_computed_trait(
            space_id=space_id, id=computed_trait_id
        )
        return {"ok": True, "computed_trait": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def list_profiles_warehouses(space_id: str) -> dict[str, Any]:
    """List warehouses configured to sync profiles for a space."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            # VERIFY: method name — could also be list_warehouses_in_space
            resp = _profiles_sync.list_profiles_warehouse_in_space(
                space_id=space_id, pagination=page
            )
            data = resp.data.to_dict()
            for w in data.get("profilesWarehouses", []):
                items.append(w)
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "profiles_warehouses": items}


# ---- Warehouses (workspace-level) ---------------------------------------

@mcp.tool()
def list_warehouses() -> dict[str, Any]:
    """List all data warehouses (Snowflake, BigQuery, etc.) in the workspace."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            resp = _warehouses.list_warehouses(pagination=page)
            data = resp.data.to_dict()
            for w in data.get("warehouses", []):
                items.append({
                    "id": w.get("id"),
                    "name": w.get("name"),
                    "enabled": w.get("enabled"),
                    "type": (w.get("metadata") or {}).get("name"),
                })
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "warehouses": items}


# ---- Destination Filters: reads -----------------------------------------

@mcp.tool()
def list_destination_filters(destination_id: str) -> dict[str, Any]:
    """List FQL filters applied to a destination."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            # VERIFY: method name
            resp = _destination_filters.list_filters_for_destination(
                destination_id=destination_id, pagination=page
            )
            data = resp.data.to_dict()
            for f in data.get("filters", []):
                items.append({
                    "id": f.get("id"),
                    "name": f.get("name"),
                    "enabled": f.get("enabled"),
                    "if": f.get("if"),
                    "actions": f.get("actions"),
                })
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "filters": items}


@mcp.tool()
def get_destination_filter(filter_id: str) -> dict[str, Any]:
    """Get the full definition of a destination filter."""
    try:
        # VERIFY: method name
        resp = _destination_filters.get_filter_in_destination(filter_id=filter_id)
        return {"ok": True, "filter": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


# ---- Transformations: reads ---------------------------------------------

@mcp.tool()
def list_transformations() -> dict[str, Any]:
    """List all transformations in the workspace."""
    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            page = segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)
            resp = _transformations.list_transformations(pagination=page)
            data = resp.data.to_dict()
            for t in data.get("transformations", []):
                items.append({
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "enabled": t.get("enabled"),
                    "if": t.get("if"),
                    "source_id": t.get("sourceId"),
                    "destination_metadata_id": t.get("destinationMetadataId"),
                })
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                break
    except ApiException as e:
        return _err(e)
    return {"ok": True, "count": len(items), "transformations": items}


@mcp.tool()
def get_transformation(transformation_id: str) -> dict[str, Any]:
    """Get the full definition of a transformation."""
    try:
        resp = _transformations.get_transformation(transformation_id=transformation_id)
        return {"ok": True, "transformation": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


# ===========================================================================
# MUTATION TOOLS (gated)
# ===========================================================================
# Every tool below:
#   1. Takes confirm: bool = False
#   2. Returns a preview if confirm is False (no API call made)
#   3. Executes the mutation only when confirm=True
# The MCP client adds another approval layer — the user sees the call args
# both times.

# ---- Functions: mutations -----------------------------------------------

@mcp.tool()
def create_function(
    display_name: str,
    code: str,
    resource_type: str,
    description: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a new custom function.

    Args:
        display_name: Name shown in the Segment UI.
        code: Full JavaScript source code.
        resource_type: One of SOURCE, DESTINATION, INSERT.
        description: Optional description.
        confirm: Set to True only after explicit user approval of the preview.
    """
    if not confirm:
        return _preview(
            "create_function",
            display_name=display_name,
            resource_type=resource_type,
            description=description,
            code_size=f"{len(code)} chars",
            code_preview=code[:200],
        )
    try:
        # VERIFY: input model name
        req = segment_public_api.CreateFunctionV1Input(
            display_name=display_name,
            code=code,
            resource_type=resource_type.upper(),
            description=description,
        )
        resp = _functions.create_function(create_function_v1_input=req)
        return {"ok": True, "function": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def update_function(
    function_id: str,
    code: Optional[str] = None,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Update an existing function's code, name, or description.

    Only provided fields are updated. Note: updating code does NOT auto-deploy;
    call create_function_deployment afterward to make the new version live.
    """
    fields = {k: v for k, v in {
        "code": code, "display_name": display_name, "description": description
    }.items() if v is not None}
    if not fields:
        return {"ok": False, "error": "Provide at least one of code, display_name, description"}

    if not confirm:
        preview_fields = dict(fields)
        if "code" in preview_fields:
            preview_fields["code"] = f"[{len(preview_fields['code'])} chars]"
            preview_fields["code_preview"] = code[:200] if code else ""
        return _preview("update_function", function_id=function_id, **preview_fields)

    try:
        # VERIFY: input model name
        req = segment_public_api.UpdateFunctionV1Input(**fields)
        resp = _functions.update_function(
            function_id=function_id, update_function_v1_input=req
        )
        return {"ok": True, "function": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def delete_function(function_id: str, confirm: bool = False) -> dict[str, Any]:
    """Permanently delete a function. DESTRUCTIVE."""
    if not confirm:
        try:
            current = _functions.get_function(function_id=function_id).data.to_dict()
            name = (current.get("function") or {}).get("displayName", "<unknown>")
        except ApiException:
            name = "<could not fetch — function may not exist>"
        return _preview("delete_function", function_id=function_id, display_name=name)
    try:
        _functions.delete_function(function_id=function_id)
        return {"ok": True, "deleted": function_id}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def create_function_deployment(function_id: str, confirm: bool = False) -> dict[str, Any]:
    """Deploy the current code of a function as a new live version.

    This makes the latest saved code the active one for all instances.
    """
    if not confirm:
        return _preview("create_function_deployment", function_id=function_id)
    try:
        # VERIFY: signature
        resp = _functions.create_function_deployment(function_id=function_id)
        return {"ok": True, "deployment": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def restore_function_version(
    function_id: str, version_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Roll back a function to a previous version. DESTRUCTIVE."""
    if not confirm:
        return _preview(
            "restore_function_version",
            function_id=function_id,
            version_id=version_id,
        )
    try:
        # VERIFY: method name
        resp = _functions.restore_function_version(
            function_id=function_id, version_id=version_id
        )
        return {"ok": True, "function": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


# ---- Deletion & Suppression: mutations ----------------------------------

@mcp.tool()
def create_workspace_regulation(
    regulation_type: str,
    user_ids: list[str],
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a workspace-wide deletion or suppression regulation.

    Args:
        regulation_type: One of SUPPRESS_ONLY, DELETE_ONLY,
            SUPPRESS_AND_DELETE, UNSUPPRESS.
        user_ids: List of user IDs (or external IDs) to apply the regulation to.
        confirm: Set to True only after explicit user approval.
    """
    if not confirm:
        return _preview(
            "create_workspace_regulation",
            regulation_type=regulation_type,
            user_id_count=len(user_ids),
            user_ids_sample=user_ids[:5],
        )
    try:
        # VERIFY: input model name and field structure
        req = segment_public_api.CreateWorkspaceRegulationV1Input(
            regulation_type=regulation_type,
            subject_type="USER_ID",
            subject_ids=user_ids,
        )
        resp = _deletion_suppression.create_workspace_regulation(
            create_workspace_regulation_v1_input=req
        )
        return {"ok": True, "regulation": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def delete_regulation(regulation_id: str, confirm: bool = False) -> dict[str, Any]:
    """Cancel a pending regulation. DESTRUCTIVE."""
    if not confirm:
        return _preview("delete_regulation", regulation_id=regulation_id)
    try:
        # VERIFY: method name
        _deletion_suppression.delete_regulation(regulation_id=regulation_id)
        return {"ok": True, "deleted": regulation_id}
    except ApiException as e:
        return _err(e)


# ---- Destination Filters: mutations -------------------------------------

@mcp.tool()
def create_destination_filter(
    destination_id: str,
    name: str,
    condition: str,
    actions: list[dict[str, Any]],
    enabled: bool = True,
    description: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a new destination filter using FQL.

    Args:
        destination_id: Target destination.
        name: Filter name.
        condition: FQL expression (e.g. "event = 'Order Completed'").
        actions: List of actions when the filter matches, e.g.
            [{"type": "ALLOW_PROPERTIES", "fields": {...}}].
        enabled: Whether to enable on creation.
        description: Optional description.
    """
    if not confirm:
        return _preview(
            "create_destination_filter",
            destination_id=destination_id,
            name=name,
            condition=condition,
            actions=actions,
            enabled=enabled,
        )
    try:
        # VERIFY: input model and field names
        req = segment_public_api.CreateFilterForDestinationV1Input(
            name=name,
            description=description,
            if_=condition,
            actions=actions,
            enabled=enabled,
        )
        resp = _destination_filters.create_filter_for_destination(
            destination_id=destination_id,
            create_filter_for_destination_v1_input=req,
        )
        return {"ok": True, "filter": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def update_destination_filter(
    filter_id: str,
    name: Optional[str] = None,
    condition: Optional[str] = None,
    enabled: Optional[bool] = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Update an existing destination filter."""
    fields = {k: v for k, v in {
        "name": name, "if_": condition, "enabled": enabled
    }.items() if v is not None}
    if not fields:
        return {"ok": False, "error": "Provide at least one field to update"}

    if not confirm:
        return _preview("update_destination_filter", filter_id=filter_id, **fields)
    try:
        # VERIFY: input model
        req = segment_public_api.UpdateFilterForDestinationV1Input(**fields)
        resp = _destination_filters.update_filter_for_destination(
            filter_id=filter_id, update_filter_for_destination_v1_input=req
        )
        return {"ok": True, "filter": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def delete_destination_filter(filter_id: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a destination filter. DESTRUCTIVE."""
    if not confirm:
        return _preview("delete_destination_filter", filter_id=filter_id)
    try:
        # VERIFY: method name
        _destination_filters.remove_filter_from_destination(filter_id=filter_id)
        return {"ok": True, "deleted": filter_id}
    except ApiException as e:
        return _err(e)


# ---- Transformations: mutations -----------------------------------------

@mcp.tool()
def create_transformation(
    name: str,
    source_id: str,
    enabled: bool = True,
    if_condition: Optional[str] = None,
    new_event_name: Optional[str] = None,
    property_renames: Optional[list[dict[str, str]]] = None,
    property_value_transformations: Optional[list[dict[str, Any]]] = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a transformation that rewrites events for a source.

    Args:
        name: Transformation name.
        source_id: Source to apply the transformation to.
        if_condition: Optional FQL expression to scope which events transform.
        new_event_name: If set, renames the event.
        property_renames: List of {oldName, newName} dicts.
        property_value_transformations: List of {property, type, value} dicts.
    """
    if not confirm:
        return _preview(
            "create_transformation",
            name=name,
            source_id=source_id,
            if_condition=if_condition,
            new_event_name=new_event_name,
            renames=property_renames,
            value_transforms=property_value_transformations,
        )
    try:
        # VERIFY: input model name and field structure
        req = segment_public_api.CreateTransformationV1Input(
            name=name,
            source_id=source_id,
            enabled=enabled,
            if_=if_condition,
            new_event_name=new_event_name,
            property_renames=property_renames or [],
            property_value_transformations=property_value_transformations or [],
        )
        resp = _transformations.create_transformation(
            create_transformation_v1_input=req
        )
        return {"ok": True, "transformation": resp.data.to_dict()}
    except ApiException as e:
        return _err(e)


@mcp.tool()
def delete_transformation(
    transformation_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Delete a transformation. DESTRUCTIVE."""
    if not confirm:
        return _preview("delete_transformation", transformation_id=transformation_id)
    try:
        # VERIFY: method name
        _transformations.delete_transformation(transformation_id=transformation_id)
        return {"ok": True, "deleted": transformation_id}
    except ApiException as e:
        return _err(e)


# ===========================================================================
# EXPORT UTILITY
# ===========================================================================
# Writes data from any list_* tool to a CSV or XLSX file in the configured
# exports directory. Default path is the Google Drive synced folder, which
# means files auto-sync to Drive within a few seconds of being written —
# convenient for "open this in Google Sheets" workflows without needing
# Google Sheets API auth.
#
# Override the default path by setting SEGMENT_EXPORT_DIR in your .env or
# shell environment.

def _default_export_dir() -> str:
    """Resolve the export directory, with a sensible fallback chain."""
    env_dir = os.environ.get("SEGMENT_EXPORT_DIR")
    if env_dir:
        return os.path.expanduser(env_dir)
    # macOS Google Drive desktop paths (try both common locations)
    candidates = [
        "~/Google Drive/My Drive/segment-exports",
        "~/Library/CloudStorage/GoogleDrive-personal/My Drive/segment-exports",
    ]
    for c in candidates:
        expanded = os.path.expanduser(c)
        parent = os.path.dirname(expanded)
        if os.path.isdir(parent):
            return expanded
    # Last resort: a local folder in the project dir
    return os.path.expanduser("~/code/segment-mcp/exports")


def _flatten_cell(v: Any) -> Any:
    """Render a value as a flat cell. Nested dicts/lists become JSON strings."""
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return v
    try:
        return json.dumps(v, default=str, ensure_ascii=False)
    except Exception:
        return str(v)


@mcp.tool()
def export_to_file(
    rows: list[dict[str, Any]],
    filename: str,
    format: Optional[str] = None,
) -> dict[str, Any]:
    """Write tabular data to CSV or XLSX in the exports directory.

    Typical use: chain after a list_* tool. For example:
      1. Call list_sources(only_enabled=True)
      2. Pass the `sources` array from the result as `rows` here
      3. Filename "sources_enabled_2026-05.xlsx" → saved to exports dir

    Args:
        rows: List of dicts. Keys become column headers; nested values are
            stored as JSON strings.
        filename: Output filename. If it ends in `.csv` or `.xlsx`, that
            sets the format. Otherwise pass `format` explicitly.
        format: Optional explicit format: "csv" or "xlsx". Overrides
            the extension if both are provided.

    The exports directory is taken from $SEGMENT_EXPORT_DIR if set,
    otherwise defaults to ~/Google Drive/My Drive/segment-exports/ (which
    auto-syncs to Drive on macOS if Google Drive Desktop is installed).
    """
    if not rows:
        return {"ok": False, "error": "No rows to export. Pass a non-empty list."}
    if not isinstance(rows, list) or not isinstance(rows[0], dict):
        return {
            "ok": False,
            "error": "rows must be a list of dicts (e.g. the 'sources' array from list_sources).",
        }

    # Resolve format
    fmt = (format or "").lower()
    if not fmt:
        lower = filename.lower()
        if lower.endswith(".csv"):
            fmt = "csv"
        elif lower.endswith(".xlsx"):
            fmt = "xlsx"
        else:
            return {
                "ok": False,
                "error": "Specify format='csv' or 'xlsx', or include the extension in filename.",
            }
    if fmt not in ("csv", "xlsx"):
        return {"ok": False, "error": f"Unsupported format: {fmt}"}

    # Normalize filename to have the right extension
    if not filename.lower().endswith(f".{fmt}"):
        filename = f"{filename}.{fmt}"

    # Resolve and create output directory
    export_dir = _default_export_dir()
    try:
        os.makedirs(export_dir, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"Could not create export dir {export_dir}: {e}"}
    output_path = os.path.join(export_dir, filename)

    # Collect column order: union of all keys, preserving first-seen order
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                columns.append(k)

    flat_rows = [{c: _flatten_cell(r.get(c)) for c in columns} for r in rows]

    try:
        if fmt == "csv":
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerows(flat_rows)
        else:  # xlsx
            try:
                from openpyxl import Workbook
            except ImportError:
                return {
                    "ok": False,
                    "error": (
                        "openpyxl is required for XLSX export. Install with: "
                        "uv pip install openpyxl"
                    ),
                }
            wb = Workbook()
            ws = wb.active
            ws.title = "data"
            ws.append(columns)
            for row in flat_rows:
                ws.append([row.get(c) for c in columns])
            # Light formatting: bold header row, autofilter
            from openpyxl.styles import Font
            for cell in ws[1]:
                cell.font = Font(bold=True)
            if len(flat_rows) > 0:
                ws.auto_filter.ref = ws.dimensions
            wb.save(output_path)
    except OSError as e:
        return {"ok": False, "error": f"Failed to write {output_path}: {e}"}

    return {
        "ok": True,
        "path": output_path,
        "format": fmt,
        "rows": len(rows),
        "columns": columns,
        "note": (
            "If this path is inside Google Drive, the file will sync to Drive "
            "within a few seconds. Open in Google Sheets via File > Open in "
            "Drive, or for CSV use File > Import."
        ),
    }


# ===========================================================================
# PROFILE API TOOLS
# ===========================================================================
# Profile API is a separate API from Public API:
#   - Different auth (HTTP Basic with token as username)
#   - Different base URL (profiles.segment.com)
#   - Space-scoped — every call needs a space_id
#
# Use list_spaces first to discover space IDs, then pass them into these tools.
# Identifiers are typed: id_type is one of:
#   user_id, email, anonymous_id, group_id, or any custom external_id type
# defined for the space (e.g. nyt_uid, sub_id).

_VALID_ID_TYPES_HINT = (
    "Common values: user_id, email, anonymous_id, group_id. "
    "Custom external_id types defined in the space (e.g. nyt_uid) also work."
)


@mcp.tool()
def get_profile_traits(
    id_type: str,
    id_value: str,
    space_id: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get all traits Segment has computed for a user profile.

    Args:
        id_type: Identifier type. {hint}
        id_value: Identifier value (e.g. an email address).
        space_id: Unify/Engage space ID. Defaults to SEGMENT_SPACE_ID env var.
        limit: Max traits to return (default 50, max 200).
    """.format(hint=_VALID_ID_TYPES_HINT)
    space_id = _resolve_space(space_id)
    if not space_id:
        return {"ok": False, "error": "No space_id provided and SEGMENT_SPACE_ID env var is not set."}
    path = f"{_profile_identifier(id_type, id_value)}/traits?limit={min(limit, 200)}"
    return _profile_get(space_id, path)


@mcp.tool()
def get_profile_external_ids(
    id_type: str,
    id_value: str,
    space_id: Optional[str] = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Get all external identifiers linked to a profile.

    This is the core lookup for identity-resolution debugging — e.g.
    "what emails / user_ids / nyt_uids are all linked to this same profile?"

    Note: the external_ids endpoint has a hard page limit of 25.

    Args:
        id_type: Identifier type. {hint}
        id_value: Identifier value.
        space_id: Unify/Engage space ID. Defaults to SEGMENT_SPACE_ID env var.
        limit: Max identifiers to return per page (capped at 25 by Segment).
    """.format(hint=_VALID_ID_TYPES_HINT)
    space_id = _resolve_space(space_id)
    if not space_id:
        return {"ok": False, "error": "No space_id provided and SEGMENT_SPACE_ID env var is not set."}
    capped = min(limit, 25)
    path = (
        f"{_profile_identifier(id_type, id_value)}/external_ids?limit={capped}"
    )
    return _profile_get(space_id, path)


@mcp.tool()
def get_profile_events(
    id_type: str,
    id_value: str,
    space_id: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get recent events sent by a user profile.

    Useful for "what did this user actually do?" debugging — see the raw
    track/page/identify calls Segment received for them.

    Args:
        id_type: Identifier type. {hint}
        id_value: Identifier value.
        space_id: Unify/Engage space ID. Defaults to SEGMENT_SPACE_ID env var.
        limit: Max events to return (default 50, max 200).
    """.format(hint=_VALID_ID_TYPES_HINT)
    space_id = _resolve_space(space_id)
    if not space_id:
        return {"ok": False, "error": "No space_id provided and SEGMENT_SPACE_ID env var is not set."}
    path = f"{_profile_identifier(id_type, id_value)}/events?limit={min(limit, 200)}"
    return _profile_get(space_id, path)


@mcp.tool()
def get_profile_links(
    id_type: str,
    id_value: str,
    space_id: Optional[str] = None,
) -> dict[str, Any]:
    """Get the identifier linkage graph for a profile.

    Shows which identifiers are joined to which, and the timeline of links.
    Useful for explaining "why are these two users merged?" or "when did
    this email get attached to this user_id?".

    Args:
        id_type: Identifier type. {hint}
        id_value: Identifier value.
        space_id: Unify/Engage space ID. Defaults to SEGMENT_SPACE_ID env var.
    """.format(hint=_VALID_ID_TYPES_HINT)
    space_id = _resolve_space(space_id)
    if not space_id:
        return {"ok": False, "error": "No space_id provided and SEGMENT_SPACE_ID env var is not set."}
    return _profile_get(space_id, f"{_profile_identifier(id_type, id_value)}/links")


@mcp.tool()
def get_profile_metadata(
    id_type: str,
    id_value: str,
    space_id: Optional[str] = None,
) -> dict[str, Any]:
    """Get profile metadata (created_at, updated_at, etc.).

    Args:
        id_type: Identifier type. {hint}
        id_value: Identifier value.
        space_id: Unify/Engage space ID. Defaults to SEGMENT_SPACE_ID env var.
    """.format(hint=_VALID_ID_TYPES_HINT)
    space_id = _resolve_space(space_id)
    if not space_id:
        return {"ok": False, "error": "No space_id provided and SEGMENT_SPACE_ID env var is not set."}
    return _profile_get(space_id, f"{_profile_identifier(id_type, id_value)}/metadata")


# ---- Convenience: NYT-style suppression status check --------------------

def _strip_suppression_prefix(email: str) -> tuple[str, Optional[str]]:
    """Strip `suppression+` or `deletion+` prefix from an email's local part.

    Returns (base_email, prefix_found_or_None).
    For input "suppression+user@example.com" returns ("user@example.com", "suppression+").
    For input "user@example.com" returns ("user@example.com", None).
    """
    if "@" not in email:
        return email, None
    local, _, domain = email.partition("@")
    for prefix in ("suppression+", "deletion+"):
        if local.startswith(prefix):
            return f"{local[len(prefix):]}@{domain}", prefix
    return email, None


@mcp.tool()
def suppression_status_check(
    email: str,
    space_id: Optional[str] = None,
) -> dict[str, Any]:
    """Check whether an email is suppressed or marked for deletion.

    Mirrors the logic of the NYT email suppression destination function:
      1. Strip `suppression+` / `deletion+` prefix from the input email for
         base comparison.
      2. Look up the profile by email.
      3. Walk its external_ids, EXCLUDING any prefixed identifiers from the
         match comparison.
      4. Report whether any prefixed (suppression/deletion) email identifier
         exists on the profile, AND whether the base email is one of the
         matching identifiers.

    Args:
        email: Email to check. Can include `suppression+` or `deletion+` prefix.
        space_id: Unify/Engage space ID. Defaults to SEGMENT_SPACE_ID env var.

    Returns:
        {
          ok: True,
          input_email: "...",
          base_email: "...",
          input_had_prefix: "suppression+" | "deletion+" | None,
          has_suppression_deletion_email: bool,
          has_matching_email_identifier: bool,
          suppression_deletion_identifiers: [...],
          all_email_identifiers: [...]
        }
    """
    space_id = _resolve_space(space_id)
    if not space_id:
        return {"ok": False, "error": "No space_id provided and SEGMENT_SPACE_ID env var is not set."}
    base_email, input_prefix = _strip_suppression_prefix(email)

    lookup = get_profile_external_ids(
        space_id=space_id, id_type="email", id_value=base_email, limit=25
    )

    if not lookup.get("ok"):
        # 404 means no profile exists — that's a valid "not suppressed" answer
        if lookup.get("status") == 404:
            return {
                "ok": True,
                "input_email": email,
                "base_email": base_email,
                "input_had_prefix": input_prefix,
                "has_suppression_deletion_email": False,
                "has_matching_email_identifier": False,
                "suppression_deletion_identifiers": [],
                "all_email_identifiers": [],
                "note": "No profile found for this email — treat as not suppressed.",
            }
        return lookup  # propagate the error

    external_ids = lookup.get("data", [])  # Profile API returns 'data' array
    all_email_ids: list[str] = []
    suppression_deletion_ids: list[str] = []
    matching_non_prefixed: list[str] = []

    for ext in external_ids:
        # External ID shape: {"type": "email", "id": "foo@bar.com", ...}
        ext_type = ext.get("type") or ext.get("id_type")
        ext_value = ext.get("id") or ext.get("id_value")
        if ext_type != "email" or not ext_value:
            continue
        all_email_ids.append(ext_value)
        _, ext_prefix = _strip_suppression_prefix(ext_value)
        if ext_prefix:
            suppression_deletion_ids.append(ext_value)
        else:
            # Non-prefixed identifier — compare against base email
            if ext_value.lower() == base_email.lower():
                matching_non_prefixed.append(ext_value)

    return {
        "ok": True,
        "input_email": email,
        "base_email": base_email,
        "input_had_prefix": input_prefix,
        "has_suppression_deletion_email": len(suppression_deletion_ids) > 0,
        "has_matching_email_identifier": len(matching_non_prefixed) > 0,
        "suppression_deletion_identifiers": suppression_deletion_ids,
        "all_email_identifiers": all_email_ids,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run()
