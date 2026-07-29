# Contributing to `server.py`

This is a single-file FastMCP server. Almost all changes take the shape of "add, remove, or fix one `@mcp.tool()` function." This doc covers the conventions the file follows so new tools fit in cleanly.

## Local setup

```bash
uv venv --python $(brew --prefix python@3.12)/bin/python3.12
source .venv/bin/activate
uv pip install fastmcp python-dotenv httpx
uv pip install git+https://github.com/segmentio/public-api-sdk-python.git
```

Add a `.env` file (gitignored) with `SEGMENT_PUBLIC_API_TOKEN` pointed at a **dev/test workspace**, not production. Run the server directly to sanity-check it starts:

```bash
python server.py
```

## File layout

`server.py` is organized top to bottom as:

1. Module docstring — setup instructions and the confirmation-gate contract (keep this in sync if you change either).
2. Client/API setup — one `segment_public_api.*Api(_client)` instance per Segment API surface, plus Profile API token resolution (separate auth, separate base URL — see `_profile_get`).
3. Helpers (`_err`, `_next_cursor`, `_preview`, `_project_source`, `_project_destination`, `_project_function`) — shared plumbing every tool should reuse rather than reimplementing.
4. `READ-ONLY TOOLS` — grouped by Segment API surface (Sources, Destinations, Functions, Tracking Plans, etc.), each preceded by a `# ---- Section ----` comment banner.
5. Mutation tools — same grouping, each gated by `confirm`.
6. Export / Profile API tools near the bottom.

Add a new tool inside the section banner for the API surface it belongs to, not at the end of the file. Add a new banner if it's a genuinely new surface.

## Conventions every tool follows

**Return shape.** Every tool returns a `dict[str, Any]` with `"ok": True/False` — never raises. Wrap SDK calls in `try/except ApiException as e: return _err(e)`.

**Pagination.** List tools that page through results follow the `list_sources` pattern: loop with a `cursor`, build `segment_public_api.PaginationInput(count=PAGE_SIZE, cursor=cursor)`, advance with `_next_cursor(page.pagination)` (or equivalent field on the response), and stop when there's no next cursor.

**Projection helpers.** Don't return raw SDK objects — write a `_project_x(obj) -> dict` helper (see `_project_source`, `_project_destination`, `_project_function`) that pulls out only the fields the LLM needs, using `getattr(obj, "field", None)` defensively since SDK models vary in which fields are populated.

**The confirmation gate (mutations only).** Every create/update/delete/deploy tool:

- Takes `confirm: bool = False` as its last parameter.
- When `confirm` is `False`, does no API call and returns `_preview("tool_name", **details)` with enough detail (including a truncated preview of any large field, e.g. `code[:200]`) for a human to sanity-check the action.
- When `confirm` is `True`, performs the real call and returns `{"ok": True, ...}` or `_err(e)`.
- For deletes, the preview branch should fetch the current object first (best-effort, wrapped in its own try/except) so the preview shows what's about to be destroyed — see `delete_function`.

Do not add a mutation tool that skips this gate, and do not make `confirm` do anything other than gate the single API call it guards.

**Docstrings are the tool's interface.** The docstring is what the LLM sees to decide how to call the tool and what to tell the user. Every parameter needs an `Args:` entry, and any non-obvious side effect (e.g. "updating code does NOT auto-deploy" on `update_function`) belongs in the docstring, not just a code comment.

**`# VERIFY` markers.** The Segment SDK is auto-generated and renames methods/models across versions. If you're not 100% sure a method or input-model name is current, mark the line `# VERIFY` (see `create_function`). If you hit an `AttributeError` on a marked or unmarked line, find the current name with:

```bash
python -c "import segment_public_api as s; print([m for m in dir(s.FooApi) if not m.startswith('_')])"
```

and fix the call — remove the `# VERIFY` comment once you've confirmed it against a live workspace.

## Testing a change

There's no mock/unit test suite yet — validate against a real dev workspace:

1. Point `.env` at a workspace you're allowed to mutate.
2. Restart your MCP client (Claude Desktop / Gemini CLI) so it picks up the new tool.
3. For a new mutation tool, call it once with default args (`confirm=False`) and check the preview looks right before ever calling it with `confirm=True`.
4. For a new read tool, check pagination terminates and the projected fields match what's in the Segment app UI.

If you can add a lightweight test (e.g. mocking `ApiException` paths, or a fixture for `_project_*` helpers), that's a welcome addition — just don't block a PR on it existing yet.

## Submitting a change

1. Fork or branch, make the change following the conventions above.
2. Update the module docstring / README if setup steps or the tool list changed.
3. Open a PR describing which tool(s) changed and what you tested it against (dev workspace, which Segment API responses you saw).
4. Flag any `# VERIFY` markers you added or resolved in the PR description.
