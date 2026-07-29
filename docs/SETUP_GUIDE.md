Table of Contents

# Segment ↔ Gemini MCP Prototype: Setup & Test Guide

A local prototype that lets Gemini CLI (or Claude Desktop) query and operate on the Segment Public API through the Model Context Protocol (MCP). This guide walks a Twilio engineer through the same setup, end-to-end, on a managed MacBook.

**Estimated time:** 45–90 minutes start to finish (most of it one-time environment setup).

## What you’re building

You (natural language)
        ↓
Gemini CLI or Claude Desktop (in your terminal / desktop app)
        ↓
MCP protocol (stdio)
        ↓
FastMCP Python server (running locally)
        ↓
Segment Public API SDK
        ↓
Your Segment workspace

**40 tools total** across read-only inspection and gated mutations. High-level categories:

| Category | Read tools | Mutation tools (gated) |
| --- | --- | --- |
| Workspace, Sources, Destinations | get_workspace, list_sources, get_source, get_source_schema, list_destinations, get_destination | — |
| Functions | list_functions, get_function, list_function_versions, get_function_version, list_insert_function_instances | create_function, update_function, delete_function, create_function_deployment, restore_function_version |
| Tracking Plans | list_tracking_plans, get_tracking_plan_rules | — |
| Delivery Metrics | get_delivery_metrics | — |
| Deletion & Suppression | list_regulations, get_regulation | create_workspace_regulation, delete_regulation |
| Spaces / Audiences / Computed Traits / Profiles Sync | list_spaces, list_audiences, get_audience, list_computed_traits, get_computed_trait, list_profiles_warehouses | — |
| Warehouses | list_warehouses | — |
| Destination Filters | list_destination_filters, get_destination_filter | create_destination_filter, update_destination_filter, delete_destination_filter |
| Transformations | list_transformations, get_transformation | create_transformation, delete_transformation |
| Export utility | export_to_file (writes CSV/XLSX to Google Drive synced folder) | — |

Every mutation tool uses a two-step confirmation gate — see How the confirmation gate works below.

## Prerequisites

- macOS 12 or newer (Apple Silicon or Intel)

- Admin access on your laptop (you’ll install Homebrew, Node, Python)

- A Google account for Gemini CLI authentication (personal Gmail recommended — see Part 2)

- A Segment dev/test workspace where you have Workspace Owner permissions

- Comfort with the terminal (zsh)

- (Optional) Google Drive Desktop installed and signed in, if you want exports to auto-sync to Drive

## Part 1 — Install Homebrew and Gemini CLI

Open Terminal.

# Install Homebrew if you don't have it already
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

Follow the post-install instructions Homebrew prints (it’ll give you exact eval commands to add it to your PATH).

Then install Gemini CLI:

brew install gemini-cli
gemini --version

You should see a version number (0.40+ as of mid-2026).

## Part 2 — Get an AI Studio API key (shared by Gemini CLI and VS Code sidebar)

If you want to use either Gemini surface (CLI or VS Code Code Assist sidebar), you’ll need a Google AI Studio API key. The same key works for both, so you only need to do this once.

Twilio Google Workspace accounts (yourname@twilio.com) are usually **not eligible** for the free Gemini Code Assist tier, so the API key path is the most reliable for managed-laptop users.

### Recommended: AI Studio API Key

- In a browser, go to https://aistudio.google.com/app/apikey

- Sign in with a **personal** Google account (a @gmail.com address)

- Click “Create API Key” → copy it

- Add it to your shell so Gemini CLI picks it up:

echo 'export GEMINI_API_KEY="paste_key_here"' >> ~/.zshrc
source ~/.zshrc

When you launch gemini later, pick option 2 (“Use Gemini API Key”) at the auth prompt.

**Note:** The shell GEMINI_API_KEY variable works for Gemini CLI but is NOT automatically picked up by the VS Code Gemini Code Assist extension. If you also want to use the VS Code sidebar, see Part 9 Option B for the extra configuration step (adding geminicodeassist.geminiApiKey to VS Code’s user settings).

**Free tier:** ~100 requests/day on Gemini 3, ~1,500/day on Gemini 2.5 Flash. For prototyping, switch to Flash inside Gemini CLI with /model and pick gemini-2.5-flash — it handles MCP tool selection just fine and gives you many more daily runs. Gemini Code Assist’s agent mode auto-selects the model and will fall back to Flash when Pro quota runs out.

### Alternative: personal Gmail OAuth

If you’d rather not deal with API keys, sign out of your Twilio account in your default browser, sign in with a personal Gmail, then run gemini and pick option 1 (“Sign in with Google”). The OAuth flow will pick up the personal account.

For the VS Code sidebar, OAuth works via Cmd+Shift+P → “Gemini Code Assist: Sign In” — see Part 9 Option B Step 3 for details.

### Alternative: Claude Desktop instead of Gemini

If you’d prefer to skip Gemini entirely and use Claude Desktop as the chat UI, you can — your server.py works the same way. See Part 9 Option C.

## Part 3 — Configure trust for Zscaler (Twilio-managed laptops)

Twilio laptops route traffic through Zscaler, which intercepts TLS for certain domains (notably PyPI). Most tools that bundle their own TLS stack — including uv — will fail with invalid peer certificate: UnknownIssuer until you point them at macOS’s system trust store, which has the Zscaler root CA pre-installed.

**Check if you need this:**

curl -vI https://files.pythonhosted.org 2>&1 | grep issuer

If the issuer line mentions Zscaler, apply the fix:

# Export macOS system trust store to a PEM file
security find-certificate -a -p /Library/Keychains/System.keychain > ~/corp-ca-bundle.pem
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >> ~/corp-ca-bundle.pem

# Add env vars to ~/.zshrc
cat >> ~/.zshrc <<'EOF'

# Corporate CA trust for Python/uv/pip/node behind Zscaler
export SSL_CERT_FILE=$HOME/corp-ca-bundle.pem
export REQUESTS_CA_BUNDLE=$HOME/corp-ca-bundle.pem
export NODE_EXTRA_CA_CERTS=$HOME/corp-ca-bundle.pem
export UV_SYSTEM_CERTS=1
EOF

source ~/.zshrc

Verify:

echo $UV_SYSTEM_CERTS    # should print: 1
echo $SSL_CERT_FILE      # should print: /Users/yourname/corp-ca-bundle.pem

If your curl output shows a normal public CA (Sectigo, DigiCert, Let’s Encrypt), you’re not on Zscaler and can skip this part entirely.

## Part 4 — Install the Python toolchain

brew install uv python@3.12

uv is the fast Python package manager we’ll use. Python 3.12 from Homebrew avoids uv trying to download its own Python build from GitHub (which can be flaky behind corporate proxies).

## Part 5 — Create the project

mkdir -p ~/code/segment-mcp && cd ~/code/segment-mcp

uv venv --python $(brew --prefix python@3.12)/bin/python3.12
source .venv/bin/activate

Your prompt should now show (segment-mcp) indicating the venv is active.

Install dependencies (Segment SDK is GitHub-only, not on PyPI):

uv pip install fastmcp python-dotenv openpyxl
uv pip install git+https://github.com/segmentio/public-api-sdk-python.git

All four packages should succeed. openpyxl is needed for XLSX export; CSV is in the standard library. If the first command fails with a TLS error, revisit Part 3.

## Part 6 — Get a Segment Public API token

- Log into the Segment app and switch to your dev/test workspace (⚠️ NOT production)

- Click your workspace name (top left) → **Workspace Settings**

- **Access Management** tab → **Tokens** sub-tab

- **+ Create Token**

- Description: “Gemini MCP local testing”

- Workspace Access: **Workspace Owner** (required for the Public API)

- Copy the token immediately — it’s only shown once

⚠️ **Security:** Segment partners with GitHub to scan public commits for these tokens and auto-revoke them within seconds. Don’t commit .env files, even to private repos. The .gitignore below handles this.

⚠️ **Mutation scope:** This token can create, update, and delete functions / regulations / filters / transformations. Even with the in-code confirmation gate, treat it like a production credential. Don’t use a token from your production workspace for prototyping.

### Also: get a Profile API token (separate from Public API)

The Profile API is a different API with different auth — it lets you look up per-user identities, traits, and events. Required for the get_profile_* and suppression_status_check tools.

- In the Segment app, switch to your Unify/Engage **space** (not workspace)

- Click **Settings** → **API Access**

- Click **Generate New Token**

- Description: “Gemini MCP profile lookups”

- Copy the token immediately — only shown once

⚠️ **Per-space scoping:** Profile API tokens only work for the space they were generated in. If you work in multiple spaces, you’ll need one token per space. The server supports per-space tokens via SEGMENT_PROFILE_TOKEN_<space_id> env vars, or a single default via SEGMENT_PROFILE_TOKEN.

⚠️ **Tokens leak fast:** A leaked Profile API token grants read access to every profile in the space. If you ever paste one into a chat, email, or commit it to git, rotate immediately (Settings → API Access → revoke → generate new).

## Part 7 — Drop in the server file and config

You should have a server.py file alongside this guide. Copy it into the project directory:

cp /path/to/server.py ~/code/segment-mcp/server.py

Create .env and .gitignore:

cd ~/code/segment-mcp

cat > .env <<'EOF'
SEGMENT_PUBLIC_API_TOKEN=paste_your_public_api_token_here

# Profile API token (separate from Public API). Generate in each Unify space:
# Space Settings → API Access → Generate New Token.
# For multi-space setups, use per-space variables: SEGMENT_PROFILE_TOKEN_<space_id>
SEGMENT_PROFILE_TOKEN=paste_your_profile_api_token_here

# Default Unify space ID. When set, Profile API tools can omit space_id.
# Get from list_spaces() or from your Segment app URL.
SEGMENT_SPACE_ID=spa_your_space_id_here

# Optional: override the default export folder for export_to_file.
# If unset, the tool tries:
#   1) ~/Google Drive/My Drive/segment-exports/
#   2) ~/Library/CloudStorage/GoogleDrive-*/My Drive/segment-exports/
#   3) ~/code/segment-mcp/exports/  (fallback)
# SEGMENT_EXPORT_DIR=/Users/yourname/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/segment-exports
EOF

cat > .gitignore <<'EOF'
.env
.venv/
__pycache__/
*.pyc
corp-ca-bundle.pem
exports/
EOF

Open .env in any editor and replace the placeholder with your real token:

open -a TextEdit .env

Save and close.

### (Optional) Configure the export folder

If you want exports to auto-sync to Google Drive, find your actual Drive path on macOS:

ls -d ~/Library/CloudStorage/GoogleDrive-*/My\ Drive 2>/dev/null
# or
ls -d ~/Google\ Drive/My\ Drive 2>/dev/null

Take that path, append /segment-exports, and uncomment the SEGMENT_EXPORT_DIR line in .env. Set it to your real path. Example:

SEGMENT_EXPORT_DIR=/Users/<you>/Library/CloudStorage/GoogleDrive-yourname@gmail.com/My Drive/segment-exports

If you skip this step, exports will land wherever the default resolution succeeds — usually fine.

## Part 8 — Smoke test before involving any chat client

Always validate the Python side independently before adding Gemini/Claude as a moving part. If something breaks at this stage, the error messages are much cleaner.

### Test 1: workspace auth

source .venv/bin/activate
python -c "from server import get_workspace; print(get_workspace())"

**Expected output** (your values will differ):

{'ok': True, 'id': '6RqXrT4ejeDtcLooLa85LA', 'name': 'Your-Workspace-Name', 'slug': 'your-workspace-name'}

### Test 2: source list

python -c "from server import list_sources; print(list_sources(only_enabled=True))"

**Expected:** a dict with ok: True, a count, and a sources array.

### Test 3: a tool from each new area

Smoke-test one tool from each new category to surface any SDK method drift early:

python -c "from server import list_spaces; print(list_spaces())"
python -c "from server import list_regulations; print(list_regulations())"
python -c "from server import list_transformations; print(list_transformations())"

Some of these may error with AttributeError: '<...>Api' object has no attribute '<...>' — see SDK method drift for the patching pattern.

### Test 4: mutation tool in preview mode (safe — no API call made)

python -c "from server import delete_function; print(delete_function(function_id='any-id'))"

**Expected:** a dict with ok: False, status: 'preview', action: 'delete_function', ... and a confirm_token. No call is made to Segment. This is the gate working as designed.

### Test 5: export

After at least Test 2 passes:

python -c "
from server import list_sources, export_to_file
r = list_sources(only_enabled=True)
print(export_to_file(rows=r['sources'], filename='sources_smoke_test.xlsx'))
"

**Expected:** a dict with ok: True, path: '/Users/.../sources_smoke_test.xlsx', rows: <count>, columns: [...]. Check the file exists in Finder at the printed path.

## Part 9 — Register with your MCP client

You have three coequal options for using the MCP server. Pick whichever fits your workflow. **You don’t need all three** — the same server.py and config work across all of them.

| Option | Surface | LLM | Auth method | Best for |
| --- | --- | --- | --- | --- |
| A. Gemini CLI | Terminal | Google Gemini | Google AI Studio API key (recommended) or personal Gmail OAuth | Quickest setup, scriptable workflows |
| B. Gemini Code Assist sidebar (VS Code) | IDE | Google Gemini | Google AI Studio API key (recommended) or personal Gmail OAuth | Rich UI, side-by-side with code editing |
| C. Claude Desktop | Standalone macOS app | Anthropic Claude | Claude.ai account login (no API key needed) | Cleanest demo UX, best for non-technical viewers |

All three read the same Python server (server.py) over MCP stdio. The differences are only in *where the chat happens* and *which LLM responds*.

⚠️ **Important about auth:** Options A and B use Google’s Gemini, so they share the same free-tier path — get a Google AI Studio API key (Part 2) and reuse it for both. Twilio Google Workspace accounts (@twilio.com) are typically NOT eligible for the free Gemini tier, so the API key is the most reliable auth method for managed-laptop users. Option C is a different LLM entirely (Claude) with its own free tier and login, no Google involvement.

### Option A: Gemini CLI

**Auth required:** Google AI Studio API key (free tier) or personal Gmail OAuth. If you completed Part 2, you already have this set up.

If you skipped Part 2 (jumping straight to Option A), do this first:

- Get a free API key from https://aistudio.google.com/app/apikey (sign in with personal Gmail, NOT your Twilio account)

- Add it to your shell:

echo 'export GEMINI_API_KEY="paste_key_here"' >> ~/.zshrc
source ~/.zshrc

Then when you launch gemini, pick option 2 (“Use Gemini API Key”) at the auth prompt.

**Register the MCP server:**

cd ~/code/segment-mcp
source .venv/bin/activate
fastmcp install gemini-cli server.py --server-name segment-workspace

(If --server-name errors, try --name segment-workspace — the flag has changed between FastMCP versions.)

**Verify:**

gemini mcp list

You should see segment-workspace in the output.

**Launch and test:**

cd ~/code/segment-mcp
gemini

Then in the prompt:

Using segment-workspace, show me my workspace info.

Inside Gemini CLI you can switch models with /model, list MCP servers with /mcp, and exit with /quit.

### Option B: Gemini Code Assist sidebar (VS Code)

This is the richer UI alternative to the CLI. Same server.py, same ~/.gemini/settings.json config — only the surface changes.

**Auth required:** Google AI Studio API key (free tier) or personal Gmail OAuth. The VS Code extension needs its own auth config, separate from Gemini CLI’s — see Step 3 below.

If you don’t have an API key yet, get one from https://aistudio.google.com/app/apikey (sign in with personal Gmail, NOT your Twilio account). Same key works for both Gemini CLI and the VS Code sidebar.

#### Step 1: Check if VS Code is installed

Open Terminal and run:

code --version

**If you see a version number** (something like 1.95.0), skip to Step 2.

**If you see**** ****command not found: code**, check whether VS Code is installed but the code shell command isn’t:

ls -d /Applications/Visual\ Studio\ Code.app 2>/dev/null && echo "FOUND" || echo "NOT FOUND"

- **“****FOUND****”** → VS Code is installed but the shell command isn’t registered. Open VS Code from /Applications, then Cmd+Shift+P → type **“****Shell Command: Install**** ****‘****code****’**** ****command in PATH****”** → Enter. Then close and reopen Terminal and re-run code --version.

- **“****NOT FOUND****”** → Install VS Code (next step).

#### Step 1a: Install VS Code (only if not installed)

Three install paths:

**Recommended for Twilio laptops: Self Service / Software Center**

- Open **Self Service** (or whatever your managed-laptop software portal is called) from /Applications

- Search “Visual Studio Code”

- Install

- Open VS Code once, then Cmd+Shift+P → “Shell Command: Install ‘code’ command in PATH”

- Close and reopen Terminal, verify with code --version

**Alternative: Homebrew**

brew install --cask visual-studio-code

This may fail on managed laptops if --cask installs are restricted. If it does, fall back to Self Service.

**Last resort: direct download**

If both fail, download from https://code.visualstudio.com/Download (Apple Silicon or Intel build, drag to /Applications), then run the “Shell Command” step from above.

#### Step 2: Install the Gemini Code Assist extension

code --install-extension google.geminicodeassist

Or in VS Code: Cmd+Shift+X (Extensions tab) → search “Gemini Code Assist” → click Install.

#### Step 3: Authenticate the Gemini Code Assist extension

⚠️ **Important:** The VS Code extension uses different auth than Gemini CLI. The CLI’s GEMINI_API_KEY shell variable is NOT automatically picked up — you have to configure auth specifically for the extension.

You have two auth options. Pick one.

##### Option 3a (recommended): AI Studio API Key

Use the same API key you set up in Part 2. This is the easiest path if your Twilio Google Workspace account isn’t eligible for free Gemini Code Assist (as is typically the case).

- Open VS Code’s user settings JSON:

- Cmd+Shift+P → “Preferences: Open User Settings (JSON)” → Enter

- This opens ~/Library/Application Support/Code/User/settings.json

- Add the API key. The final file should look something like:

- {
  "editor.fontSize": 14,
  "geminicodeassist.geminiApiKey": "AIzaSy_your_key_here"
}

- ⚠️ Mind the comma syntax — JSON requires a comma after the previous line if you’re adding to an existing file. No trailing commas allowed.

- Save the file (Cmd+S).

- Get your API key value if you don’t remember it:

- echo $GEMINI_API_KEY

- That’s the same value you put in ~/.zshrc in Part 2.

⚠️ **This is NOT the same file as**** ****~/.gemini/settings.json****.** Two different settings.json files exist:

- ~/.gemini/settings.json → MCP server config (the mcpServers block)

- ~/Library/Application Support/Code/User/settings.json → VS Code user settings (the geminicodeassist.geminiApiKey line)

Don’t put the API key in the MCP file or the MCP config in the user settings file — neither will work.

##### Option 3b: Google OAuth sign-in

- Cmd+Shift+P → “Gemini Code Assist: Sign In”

- Browser opens → sign in with **personal Gmail** (NOT your Twilio account — Workspace accounts are typically rejected with “not eligible for Gemini Code Assist for individuals”)

- Approve permissions, return to VS Code

If the sign-in command doesn’t appear in the palette or doesn’t open a browser, fall back to Option 3a.

#### Step 4: Verify or write your MCP config

The extension reads MCP server definitions from ~/.gemini/settings.json — the same file Gemini CLI uses.

If you completed Option A above, your server is already registered. Verify with:

cat ~/.gemini/settings.json

If you skipped Option A (sidebar-only setup), write the config manually:

mkdir -p ~/.gemini
cat > ~/.gemini/settings.json <<'EOF'
{
  "mcpServers": {
    "segment-workspace": {
      "command": "/Users/<you>/code/segment-mcp/.venv/bin/python",
      "args": ["/Users/<you>/code/segment-mcp/server.py"],
      "env": {
        "SEGMENT_PUBLIC_API_TOKEN": "sgp_your_token_here",
        "SEGMENT_PROFILE_TOKEN": "your_profile_token_here",
        "SEGMENT_SPACE_ID": "spa_your_space_id_here",
        "SEGMENT_EXPORT_DIR": "/Users/<you>/Library/CloudStorage/GoogleDrive-yourname@gmail.com/My Drive/segment-exports"
      }
    }
  }
}
EOF

Critical details for this config file:

- Use **absolute paths**. ~ doesn’t expand inside JSON values.

- The command must point at your venv’s Python (.venv/bin/python), not system Python. The extension spawns the server with a clean environment, so system Python won’t see your installed packages.

- The env block must include every env var your server reads at startup. Unlike the CLI where your shell environment carries through, the extension starts fresh.

- Substitute your real paths and tokens. Replace <you> with your actual macOS username (whoami will tell you).

Validate the JSON before continuing:

python3 -m json.tool ~/.gemini/settings.json

Should print the file back cleanly. If it errors, fix the syntax before moving on — VS Code silently ignores malformed JSON, which is the #1 reason /mcp shows nothing.

#### Step 5: Open the project in VS Code

The Gemini Code Assist extension is workspace-aware. Open VS Code on your project folder:

code ~/code/segment-mcp

⚠️ If you open VS Code on a parent folder (like ~/code) the extension may not discover the MCP server. Always open on ~/code/segment-mcp directly.

#### Step 6: Enable Agent mode

MCP servers only work when **Agent mode** is on. Regular chat won’t see them.

- Open the Gemini chat: Cmd+Shift+P → “Gemini: Open Chat” (or click the Gemini icon in the activity bar)

- At the top of the chat panel, toggle **Agent mode** on

- Reload VS Code: Cmd+Shift+P → “Developer: Reload Window”

#### Step 7: Verify the server connected

In the Gemini chat (Agent mode on), type:

/mcp

You should see segment-workspace listed with the tool count.

#### Step 8: Test it

Using segment-workspace, show me my workspace info and list all enabled sources.

The first time the extension calls each tool, it’ll ask for permission. Approve read-only tools with one click; be deliberate about mutation tools (each approval bypasses one layer of the confirmation gate).

#### Sidebar-specific troubleshooting

**/mcp**** ****shows nothing:** JSON syntax error in ~/.gemini/settings.json. Validate with python3 -m json.tool ~/.gemini/settings.json — the error message points at the broken line.

**Generic**** ****“****There was a problem getting a response****”**** ****error:** Check the Output panel (View → Output → “Gemini Code Assist” channel). Common errors:

- FatalAuthenticationError: COMPUTE_ADC failed → auth not configured. Go back to Step 3.

- LOGIN_WITH_GOOGLE fallback skipped due to headless mode → the extension couldn’t open an OAuth browser window. Use API key auth (Option 3a) instead.

**“****Connecting****”**** ****state forever:** Known Google bug as of early 2026 — MCP servers connect in the CLI but stall in the VS Code extension. Two workarounds:

- Enable the Insiders channel: VS Code settings (Cmd+,) → search “Gemini Code Assist update channel” → set to “Insiders” → reload

- Fall back to Gemini CLI. Same config works in both.

**Tools work in CLI but not in sidebar:** The env block in ~/.gemini/settings.json is missing or has wrong values. The CLI inherits your shell vars; the extension doesn’t. Make sure every env var your server needs is in the env block.

**Daily quota exhausted:** Gemini 3 Pro has tight free-tier limits and Code Assist doesn’t let you pick a model manually in agent mode. Options:

- Send another prompt — Code Assist may auto-fall-back to Gemini 2.5 Flash

- Switch to Gemini CLI (in CLI, /model lets you pick gemini-2.5-flash explicitly)

- Upgrade your Google AI account to a paid tier

- Wait for quota reset (midnight Pacific)

### Option C: Claude Desktop

**Auth required:** A free Claude.ai account login. NO Google AI Studio API key is needed — Claude Desktop uses Anthropic’s Claude model, not Google’s Gemini. If you came from Options A or B and have a Google API key set up, that key is unused here.

- Download Claude Desktop from https://claude.ai/download

- Sign in with your Claude account (free tier works for basic use; Pro for higher usage and longer chat history)

- Open the config file:

mkdir -p ~/Library/Application\ Support/Claude
open -a TextEdit ~/Library/Application\ Support/Claude/claude_desktop_config.json

- Add (or merge with existing) the following — adjust the absolute paths and your tokens:

{
  "mcpServers": {
    "segment-workspace": {
      "command": "/Users/<you>/code/segment-mcp/.venv/bin/python",
      "args": ["/Users/<you>/code/segment-mcp/server.py"],
      "env": {
        "SEGMENT_PUBLIC_API_TOKEN": "sgp_paste_token_here",
        "SEGMENT_PROFILE_TOKEN": "paste_profile_token_here",
        "SEGMENT_SPACE_ID": "spa_paste_space_id_here",
        "SEGMENT_EXPORT_DIR": "/Users/<you>/Library/CloudStorage/GoogleDrive-yourname@gmail.com/My Drive/segment-exports"
      }
    }
  }
}

- Save, fully quit Claude Desktop (Cmd+Q — not just close the window), then relaunch. The Segment tools should appear in the tool indicator at the bottom of the input box.

**Test it:**

Using segment-workspace, show me my workspace info.

Claude Desktop reads MCP configs only at startup, so any time you edit the config file you need to fully quit (Cmd+Q) and relaunch — reload window won’t pick up changes.

## Part 10 — Test prompts with expected behavior

Launch your client of choice in the project directory:

cd ~/code/segment-mcp
gemini   # or open Claude Desktop

**Tip for Gemini:** Inside Gemini CLI, run /model and select gemini-2.5-flash to conserve daily quota on the free tier. Flash handles MCP tool selection well; save Pro for harder reasoning.

The first time the client calls each tool, it’ll ask for permission. Approve with y for read-only tools. **Be deliberate** about always-allow (a) for mutation tools — every approval bypasses one layer of the gate.

### Test A — Single tool call

**Prompt:** > Using segment-workspace, show me my workspace info.

**Expected:** Calls get_workspace(), displays the JSON, summarizes.

### Test B — Tool call with reasoning on the output

**Prompt:** > List all enabled sources in my workspace, then group them by type and format as a table.

**Expected:** One list_sources(only_enabled=True) call, then the LLM groups results into a markdown table without a second tool call.

### Test C — Multi-tool chaining

**Prompt:** > Find the source named “TEST - Main HTTP API Source”, then list its destinations. Which ones are enabled vs disabled?

**Expected:** Chains list_sources() → list_destinations(source_id="<id>"), organizes results into enabled/disabled buckets.

### Test D — Full debugging-style workflow

**Prompt:** > For [source name] connected to [destination name], pull delivery metrics for the last 29 days. Tell me the failure rate and which specific dates had failures.

**Expected:** Three tool calls (list_sources → list_destinations → get_delivery_metrics), then reads the per-day breakdown out of the raw.failed.data.dataset field.

Sample real output from the prototype:

Window: 2026-04-14 → 2026-05-13 (DAY granularity)
Totals: 7 success, 6 failed, 13 attempted, 46.15% failure rate

Failures occurred on:
  • 2026-04-17 (1 event)
  • 2026-04-20 (5 events)

### Test E — Export to Google Sheets

**Prompt:** > List all destinations for the source named “TEST - Main HTTP API Source” and export them to an Excel file called “main-http-destinations”.

**Expected:** Chains list_sources → list_destinations(source_id=...) → export_to_file(rows=[...], filename="main-http-destinations.xlsx"). Returns a file path. If SEGMENT_EXPORT_DIR points into Google Drive, the file appears in Drive within a few seconds.

To open as a Google Sheet: in drive.google.com, right-click the file → **Open with → Google Sheets**.

### Test F — Mutation tool with the confirmation gate

**Prompt:** > Show me the function with display name “NYT Insert Function FBCLID” and then delete it.

**Expected behavior:** 1. LLM calls list_functions() to find the function ID. 2. LLM calls delete_function(function_id="...") *without* confirm=True. 3. Tool returns the preview dict (status: "preview", includes the display name as confirmation). 4. LLM shows the preview to you and asks for explicit approval. 5. After you confirm, LLM calls delete_function(function_id="...", confirm=True). 6. MCP client prompts for permission a second time (you see the args). 7. Approve → function is deleted.

If the LLM tries to set confirm=True on the first call (skipping the preview), refuse permission in the MCP client and call this out in your feedback — it’s an alignment issue with the model, not the gate. The gate itself is enforced server-side; you can’t accidentally delete by approving one click.

## Part 11 — Test Prompts Library

Once everything is wired up, run these prompts in roughly this order. Each section gets progressively harder and exercises a different tool category. Use Gemini CLI or the VS Code sidebar — both work with the same prompts.

### A. Basic reads (warm-up — verify it’s all working)

1. Show me my Segment workspace info.

2. List all enabled sources in my workspace, group them by type,
   format as a table.

3. List all destinations connected to the source "TEST - Main HTTP API Source".
   Group them by enabled vs disabled.

4. Show me every custom function in my workspace. For each, show the display name,
   resource type, and a one-line summary of what its code seems to do.

### B. Drill-down reads (multi-step chaining)

5. Find the function whose display name contains "NYT" or "fbclid".
   Show me its full code and tell me its deployment history.

6. For the source "TEST - Main HTTP API Source" connected to the destination
   "Adobe Experience Platform (AEP) - Connections", pull delivery metrics for the
   last 29 days. Tell me total events, failure rate, and which specific dates had
   failures.

7. List all my tracking plans. For the first one, show me the event rules and
   tell me roughly how comprehensive the schema is.

8. List all transformations in my workspace. If there are any, show me what
   each one does in plain English.

### C. New-area reads (likely to surface SDK drift)

These are the ones with # VERIFY comments in the code — first time hitting them is when you’ll find out if the SDK method names need patching:

9.  List all Unify spaces in my workspace.

10. For my Unify space, list all audiences. Tell me which are enabled and
    summarize what each is for based on its description.

11. List all computed traits in my Unify space.

12. List all deletion or suppression regulations currently active in my workspace.

13. List all destination filters on the Adobe Experience Platform destination.

If any of these error with AttributeError: '<...>Api' object has no attribute '<method>', that’s an SDK drift — patch using the pattern in the SDK method drift section.

### D. Export workflow

14. List all enabled sources and export them to an Excel file called
    "segment-sources-may2026". Then tell me the file path so I can find it
    in Drive.

15. Pull delivery metrics for AEP for the last 29 days. Export the daily
    failure series to a CSV called "aep-failures-may2026".
    Hint: the per-day data is in raw.failed.data.dataset.

16. List all my custom functions and their resource types. Save as an xlsx
    called "function-inventory" so I can share with my manager.

### E. Profile API (identity-resolution debugging)

These exercise the Profile API tools. Substitute a real email from your test space for <test_email>:

17. Look up the profile traits for email "<test_email>" in my Segment space.
    Tell me what Segment knows about this user.

18. Show me the external identifiers linked to "<test_email>".
    Are any of them other email addresses? Any suppression+ or deletion+ variants?

19. Show me the most recent 20 events for "<test_email>". Tell me
    what kinds of activity this user has had.

20. Run a suppression status check on "<test_email>". Explain in plain
    English whether this email is suppressed, marked for deletion, or neither.

21. Run a suppression status check on "suppression+<test_email>".
    Explain what this returns and why it differs from the non-prefixed version.

### F. Mutation gate (the safety story)

These prove the two-step confirmation gate works. **None of these will actually mutate anything if you decline at the second approval prompt.** Use a test function ID you don’t care about.

22. Find a function in my workspace whose name starts with "TEST" or "DEMO"
    and delete it. But STOP and show me the preview before confirming.

23. Show me what a destination filter would look like if I created one called
    "test-filter" on the AEP destination that drops all events where
    "event = 'Test Event'". Don't actually create it — just preview.

24. Roll back the [function name] to its previous version. Show me what
    version we'd be rolling back to before confirming.

Watch for the LLM to: 1. Call the tool *without* confirm=True 2. Get back a preview JSON 3. Show you the preview 4. Ask “should I confirm?” 5. Only after you explicitly say yes, call again with confirm=True

If it ever skips step 4 and jumps to a confirm call, that’s a misalignment — refuse the second approval and note which prompt triggered it.

### G. Synthesis / report workflows (the demo-worthy stuff)

These show off agentic multi-tool reasoning. They’re the ones to put on a slide for an internal demo:

25. Audit my Segment workspace. List every enabled source, its connected
    destinations, and the 29-day delivery failure rate for each pair.
    Highlight any pair with a failure rate above 10%. Format as a markdown
    report I could paste into a Confluence doc.

26. Review all my custom functions. For each, identify: what it does, when
    it was last deployed, whether it has a recent rollback option, and a
    risk-level (low/medium/high) based on what it touches. Format as a table.

27. I'm preparing for a code review of my NYT email suppression destination
    function. Pull its code, list its versions, and run a suppression status
    check on [test email]. Summarize the full picture so I can walk a
    colleague through how it works.

28. Find all destinations across all sources that are CURRENTLY DISABLED.
    For each, tell me when it was last enabled (if you can find that info
    in the destination config) and whether anything still depends on it.

### H. Failure-mode tests (good for debugging the tool layer itself)

29. Look up the profile for "definitely-not-a-real-email-xyzzy@nowhere.test".
    What does Segment return? (Tests the 404 handling.)

30. Try to get delivery metrics for the last 720 hours. What happens?
    (Tests the clamp — should silently cap at 696.)

31. Try to delete a function with ID "fake-id-12345". Walk me through
    what the preview shows. (Tests preview-mode without a real function.)

### Recommended testing order

If you want to spend testing budget efficiently, run **A → C → D → E** end-to-end first. That covers:

- Every category of tool

- The mostly likely SDK drift issues (in C)

- The export workflow

- The Profile API / NYT suppression use case

Skip the mutation tests (F) unless you have a throwaway function you can actually delete. The gate is mechanically verified by Test 4 in Part 8’s smoke tests — running the mutation in Gemini is just a UX test, not a logic test.

## How the confirmation gate works

Every mutation tool takes a confirm: bool = False argument.

**First call (no**** ****confirm****):** - Tool returns a preview dict immediately. No API call to Segment. - Shape: {"ok": false, "status": "preview", "action": "...", "details": {...}, "confirm_token": "...", "next_step": "..."} - The next_step text instructs the LLM to show this to the user and re-call with confirm=True.

**Second call (****confirm=True****):** - Tool executes the actual Segment API call. - The MCP client (Claude Desktop / Gemini CLI) prompts the user a second time before this call runs, because every tool invocation requires approval.

Net result: two human approvals between “ask the LLM to do a destructive thing” and “the destructive thing happens.” The user sees the arguments of both calls before either runs.

### Why not block destructive actions outright?

Because some are useful (rolling a function back to a previous version is “destructive” but exactly what you want during debugging). The gate makes them explicit, not impossible.

## SDK method drift

The Segment Python SDK is auto-generated from the OpenAPI spec, and method names occasionally rename between versions. The server.py provided here has been tested against the SDK as of May 2026, but some tools have lines marked # VERIFY where the method or input-model name is a best guess.

If a tool throws AttributeError, find the correct name with:

# Check what methods exist on the API class
python -c "import segment_public_api as s; print([m for m in dir(s.FunctionsApi) if not m.startswith('_')])"

# Or check what API classes exist
python -c "import segment_public_api as s; print([m for m in dir(s) if 'Api' in m])"

Then in server.py, find the offending line (it’ll be marked # VERIFY) and rename. Apply with sed for one-line fixes:

cd ~/code/segment-mcp
sed -i '' 's/old_method_name/new_method_name/g' server.py

(The empty '' after -i is a macOS quirk; on Linux it’d be -i alone.)

Specific drifts already patched in the provided file: - DeliveryMetricsApi → DeliveryOverviewApi - get_egressed_events_from_destination → split into get_egress_success_metrics_from_delivery_overview and get_egress_failed_metrics_from_delivery_overview - Delivery metrics 30-day max is exclusive — get_delivery_metrics clamps to 696 hours (29 days)

Likely remaining VERIFY points: - DeletionAndSuppressionApi method names (list_regulations, get_regulation, delete_regulation, create_workspace_regulation) - SpacesApi.list_spaces - AudiencesApi.list_audiences, get_audience - ComputedTraitsApi.list_computed_traits, get_computed_trait - ProfilesSyncApi.list_profiles_warehouse_in_space - DestinationFiltersApi.list_filters_for_destination, get_filter_in_destination, etc. - Input model names for create_* and update_* tools

## Troubleshooting reference

### uv venv fails with invalid peer certificate: UnknownIssuer

Zscaler is intercepting GitHub or the Python build CDN. Either: - Use Homebrew Python: uv venv --python $(brew --prefix python@3.12)/bin/python3.12 - Or set UV_SYSTEM_CERTS=1 per Part 3

### uv pip install fails on PyPI specifically

Same root cause but for files.pythonhosted.org. Make sure UV_SYSTEM_CERTS=1 is set in your current shell (echo $UV_SYSTEM_CERTS).

### segment-public-api was not found in the package registry

The Segment Public API SDK isn’t published on PyPI. You must install it from GitHub:

uv pip install git+https://github.com/segmentio/public-api-sdk-python.git

### AttributeError: module 'segment_public_api' has no attribute 'XxxApi'

SDK class was renamed. The server.py provided here is patched for the renames known as of May 2026. For new ones, find the correct name:

python -c "import segment_public_api as s; print([m for m in dir(s) if 'Api' in m])"

### AttributeError: '<...>Api' object has no attribute '<method>'

SDK method was renamed. See SDK method drift.

### bad-request: timerange too large. 30 day max timerange with day granularity

Segment’s 30-day window is exclusive. The get_delivery_metrics tool clamps to 29 days (696 hours) to stay inside the limit.

### Gemini CLI: “Your current account is not eligible for Gemini Code Assist for individuals”

You’re signed in with a Twilio Google Workspace account. Use an AI Studio API key or sign in with a personal Gmail (see Part 2).

### Gemini hits daily quota

Switch to gemini-2.5-flash via /model for ~15× more daily requests.

### openpyxl is required for XLSX export

You skipped the openpyxl install. Run:

source .venv/bin/activate
uv pip install openpyxl

### Exports aren’t appearing in Google Drive

Three checks:

- Is Google Drive Desktop installed and signed in? Open the Drive app from /Applications.

- Is SEGMENT_EXPORT_DIR pointing at the correct path? Verify with ls -ld "$SEGMENT_EXPORT_DIR".

- The tool prints the actual path it wrote to in its response — make sure that path is inside the Drive folder. If not, set SEGMENT_EXPORT_DIR explicitly in .env.

### IndentationError from python -c

Multi-line python -c "..." commands sometimes get leading whitespace eaten by the shell. Collapse to a single line.

### Claude Desktop doesn’t see the tools

Confirm the config file path is exactly ~/Library/Application Support/Claude/claude_desktop_config.json (note the space and capitalization). After editing, fully quit Claude Desktop (Cmd+Q from menu, not just close the window) and relaunch. If the tool indicator at the bottom of the input still doesn’t show “segment-workspace”, check Claude Desktop’s logs at ~/Library/Logs/Claude/.

### Confirm gate didn’t trigger — mutation ran immediately

Either: - The LLM correctly determined confirm=True was appropriate (it shouldn’t, on the first call — flag this in feedback) - You approved a tool call with confirm=True in its args without reading the args carefully. The MCP client shows the full args before each call; this is the second layer of the gate.

### Profile API tools return “No Profile API token configured”

You haven’t set SEGMENT_PROFILE_TOKEN (or SEGMENT_PROFILE_TOKEN_<space_id> for per-space tokens). Add it to your .env AND, if using the VS Code sidebar, to the env block of ~/.gemini/settings.json. Re-register with fastmcp install gemini-cli server.py --server-name segment-workspace or reload VS Code.

### Profile API tools return “No space_id provided and SEGMENT_SPACE_ID env var is not set”

Either pass space_id explicitly in the prompt, or set SEGMENT_SPACE_ID in .env and the sidebar’s env block.

### python3 -m json.tool ~/.gemini/settings.json fails

The file has a JSON syntax error. The error message points at the line. Most common causes: - Missing closing quote on a value - Missing comma between key-value pairs - Trailing comma after the last entry (JSON doesn’t allow trailing commas)

Open in TextEdit (open -a TextEdit ~/.gemini/settings.json), fix the syntax, save, re-validate.

### Wrong settings.json — accidentally overwrote VS Code’s user settings

If you confused ~/.gemini/settings.json (MCP config) with ~/Library/Application Support/Code/User/settings.json (VS Code user settings) and overwrote one with the other: - VS Code falls back to defaults for any keys it doesn’t see — no permanent damage - Reset VS Code’s user settings to just {"geminicodeassist.geminiApiKey": "..."} and personal preferences - Restore ~/.gemini/settings.json to its mcpServers content from the canonical example in Part 9

### zsh: event not found when running a command

You typed ! instead of ~. The tilde character is your home directory shortcut; ! triggers zsh history expansion. On a US keyboard, ~ is Shift + backtick (top-left, under Esc). Verify with echo ~ — should print /Users/yourname.

### VS Code workspace is the wrong folder

Gemini Code Assist’s MCP discovery is workspace-scoped. If you opened VS Code on ~/code instead of ~/code/segment-mcp, the extension may not find your project-level config. Run code ~/code/segment-mcp to reopen on the right folder, or copy your settings to a higher-level .gemini/settings.json (but remember to add .gemini/ to .gitignore).

## What’s NOT in this prototype (intentionally)

These are reasonable next steps once you’ve trusted the existing tool set:

- **Function logs** — pulling per-execution error logs by function ID so the LLM can summarize failure patterns. Useful for “why is my NYT function failing 46% of the time?” investigations.

- **Profile API tools** — different auth (Basic with write key) and base URL, so they live in their own tool group. Useful for identity-resolution debugging at the per-user level (which the Public API can’t do).

- **GitHub MCP integration** — chain “get function logs → group failures → open a GitHub issue per pattern” workflows.

- **IAM tools** — IAMUsersApi, IAMGroupsApi, IAMRolesApi. Deliberately omitted because they’re high-sensitivity and rarely needed for the debugging workflows this prototype targets.

- **Multi-sheet XLSX exports** — current export tool writes single sheets only. Easy to extend if needed.

## File checklist

When you’re done, your ~/code/segment-mcp/ directory should contain:

~/code/segment-mcp/
├── .env                  ← your Segment token + optional export dir (gitignored)
├── .gitignore
├── .venv/                ← Python virtualenv (gitignored)
└── server.py             ← the FastMCP server

And the exports directory (auto-created on first export):

~/.../My Drive/segment-exports/
├── sources_enabled_2026-05-13.xlsx
├── aep-failures-may2026.csv
└── ...

Your ~/.zshrc should include the Zscaler env vars (if applicable) and your GEMINI_API_KEY (if using API key auth).

## Quick reference: commands you’ll re-run

# Activate the project environment
cd ~/code/segment-mcp && source .venv/bin/activate

# Standalone smoke tests
python -c "from server import get_workspace; print(get_workspace())"
python -c "from server import list_sources; print(list_sources(only_enabled=True))"
python -c "from server import delete_function; print(delete_function(function_id='any'))"  # safe preview

# Launch a client in the project context
gemini                   # CLI
# or open Claude Desktop from /Applications

# Inside Gemini CLI
/model              # change model
/mcp                # show connected MCP servers
/quit               # exit

# Re-register the MCP server after editing server.py
fastmcp install gemini-cli server.py --server-name segment-workspace
# Claude Desktop: just quit (Cmd+Q) and relaunch — picks up changes automatically

## Tool catalog quick reference

For the full list of 40 tools, run inside Gemini/Claude:

List all available tools in the segment-workspace MCP server.

Or in Python:

python -c "
import server
import inspect
tools = [name for name, obj in inspect.getmembers(server) if inspect.isfunction(obj) and not name.startswith('_')]
for t in sorted(tools):
    print(t)
"