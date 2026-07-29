# Segment Workspace MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude Desktop, Gemini CLI, or any other MCP-compatible client query and operate on a [Segment](https://segment.com) workspace through the Segment Public API — built on [FastMCP](https://github.com/jlowin/fastmcp).

It's aimed at engineers who develop Segment functions and debug identity resolution, and exposes ~40 tools: read-only inspection across sources, destinations, functions, tracking plans, delivery metrics, audiences, computed traits, warehouses, destination filters, and transformations, plus gated mutation tools (create/update/delete/deploy) for functions, destination filters, transformations, and deletion/suppression regulations.

## How it fits together

```
You (natural language)
        ↓
Claude Desktop / Gemini CLI
        ↓
MCP protocol (stdio)
        ↓
This FastMCP server (runs locally)
        ↓
Segment Public API SDK
        ↓
Your Segment workspace
```

## Quick start

1. **Create a virtualenv and install dependencies:**

   ```bash
   uv venv --python $(brew --prefix python@3.12)/bin/python3.12
   source .venv/bin/activate
   uv pip install fastmcp python-dotenv httpx
   uv pip install git+https://github.com/segmentio/public-api-sdk-python.git
   ```

2. **Generate a Segment Public API token.** In the Segment app: Workspace Settings → Access Management → Tokens. Requires Workspace Owner.

3. **Add the token to a `.env` file** next to `server.py` (never commit this file — see `.gitignore`):

   ```
   SEGMENT_PUBLIC_API_TOKEN=sgp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

   For Profile API tools (`get_profile_traits`, `get_profile_events`, etc.), also set a space-scoped token:

   ```
   SEGMENT_PROFILE_TOKEN_<space_id>=spa_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   # or, for single-space setups:
   SEGMENT_PROFILE_TOKEN=spa_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

4. **Register with your MCP client.**

   Gemini CLI:
   ```bash
   fastmcp install gemini-cli server.py --server-name segment-workspace
   ```

   Claude Desktop — add to `claude_desktop_config.json`:
   ```json
   {
     "mcpServers": {
       "segment-workspace": {
         "command": "/abs/path/to/.venv/bin/python",
         "args": ["/abs/path/to/server.py"],
         "env": { "SEGMENT_PUBLIC_API_TOKEN": "sgp_..." }
       }
     }
   }
   ```

For a full walkthrough (including first-time Homebrew/Python/Node setup on a Mac), see [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) and [`docs/Claude_Desktop_Segment_MCP_Setup.md`](docs/Claude_Desktop_Segment_MCP_Setup.md).

## Safety: the confirmation gate

Every destructive tool (create / update / delete / deploy) takes a `confirm: bool = False` parameter. The first call always returns a **preview** of what would happen and makes no change. The client is instructed to show that preview to the user and only re-call with `confirm=True` after explicit approval. Combined with the MCP client's own per-call approval prompt, every mutation is confirmed twice before it executes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how the code in `server.py` is organized and how to add or change a tool.

## License

See [`LICENSE`](LICENSE).
