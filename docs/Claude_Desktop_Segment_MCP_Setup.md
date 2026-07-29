**Connecting the Segment MCP Server**

to Claude Desktop on your Mac

This guide walks you through connecting Claude Desktop to the Segment MCP server. Once set up, you can ask Claude questions about your Segment workspace in plain English — things like “list my sources,” “show me audiences in our Unify space,” or “export delivery metrics to a spreadsheet.”

You don’t need to be a developer. You will be copying and pasting a few commands into a program called Terminal, but every step is spelled out. Plan for about 30 minutes the first time.

**Before you begin: **Ask your teammate (the person who built the server) for two things: the server files (a folder named segment-mcp) and a Segment API token. You’ll need both.

# What you’ll need

- A Mac running macOS 12 (Monterey) or newer.

- Administrator access on your Mac (the ability to install software).

- About 30 minutes.

- The segment-mcp folder from your teammate (they can share it via Google Drive, Dropbox, or a USB drive).

- A Segment Public API token (your teammate or your Segment workspace admin can create one for you).

# Part 1 — Install Claude Desktop

- Open Safari (or your browser) and go to **https://claude.ai/download**.

- Click the “Download for Mac” button. A file ending in .dmg will download.

- Open your Downloads folder and double-click the Claude .dmg file.

- A window appears showing the Claude icon and an Applications folder. Drag the Claude icon onto the Applications folder.

- Open your Applications folder, find Claude, and double-click it.

- If macOS asks “Are you sure you want to open it?”, click Open.

- Sign in with your Claude account (or create one — the free plan is fine for this guide).

**Tip: **Keep Claude Desktop open in the background. You’ll come back to it at the end.

# Part 2 — Get your Segment API token

If your teammate already gave you a token (a long string starting with sgp_), skip to Part 3. Otherwise, follow these steps.

- Go to **https://app.segment.com** and sign in.

- Click the gear icon in the lower-left corner, then choose Workspace Settings.

- In the left menu, click Access Management, then click the Tokens tab.

- Click Create Token.

- Give it a name like “Claude Desktop” and choose the Workspace Owner access level (or ask your admin which access level you should have).

- Click Create. A long string starting with sgp_ appears — this is your token.

- Copy the token and paste it somewhere safe (a sticky note app or password manager). You will not be able to see it again after closing this window.

**Important: **Treat this token like a password. Anyone who has it can read and change your Segment workspace.

# Part 3 — Get the server files onto your Mac

Your teammate will share a folder called segment-mcp with you. Once you have it (downloaded or copied from a drive):

- Move the segment-mcp folder into your home folder. The easiest way:

- Open Finder.

- In the top menu, click Go, then Home (or press Shift + Cmd + H).

- Drag the segment-mcp folder into this window.

After this step, the folder should live at a path that looks like /Users/yourname/segment-mcp.

# Part 4 — Install Python

The server is written in a programming language called Python. Macs come with a version of Python, but we need a slightly newer one. The easiest way is to download the official installer.

- Go to **https://www.python.org/downloads/macos/**.

- Click the big yellow button labeled “Download Python 3.x” (any version 3.10 or newer works).

- Open the downloaded .pkg file and click through the installer. Use all the default options.

- When the installer finishes, you can close it.

# Part 5 — Install the server’s helper packages

Now we’ll open Terminal — a built-in Mac app for typing commands. Don’t worry, you’re just copying and pasting.

- Press Cmd + Space to open Spotlight Search.

- Type “Terminal” and press Enter. A window with a blinking cursor appears.

- Copy and paste this command into the Terminal window, then press Enter:

cd ~/segment-mcp

- Next, copy and paste this command and press Enter. It creates a sandbox for the server’s packages so they don’t interfere with anything else on your Mac:

python3 -m venv .venv

- Now activate the sandbox by running:

source .venv/bin/activate

You should see (.venv) appear at the start of the Terminal prompt. That means the sandbox is active.

- Install the server’s packages by running:

pip install -r requirements.txt

This downloads several packages. You’ll see lots of text scroll by — that’s normal. Wait until the prompt comes back (it takes 1–3 minutes).

**If you see an error: **Make sure you’re in the right folder. Run pwd and press Enter — it should show a path ending in /segment-mcp. If not, run cd ~/segment-mcp again.

# Part 6 — Find your two important paths

Claude Desktop needs to know exactly where Python and the server file live. Let’s find both paths.

- In the same Terminal window (still inside the sandbox), run:

echo "$(pwd)/.venv/bin/python"

echo "$(pwd)/server.py"

Two lines will print out. They’ll look something like this (but with your actual username):

/Users/yourname/segment-mcp/.venv/bin/python

/Users/yourname/segment-mcp/server.py

- Copy both lines into a text document somewhere — you’ll paste them into the configuration in Part 7.

# Part 7 — Tell Claude Desktop about the server

Now we’ll create a small configuration file that tells Claude Desktop how to start the server.

- Still in Terminal, run this command to open the configuration file:

open -e ~/Library/Application\ Support/Claude/claude_desktop_config.json

TextEdit will open. If the file is empty, that’s fine. If it already has content, be careful not to delete what’s there — show it to your teammate first.

**Before pasting: **In TextEdit, click TextEdit in the top menu bar, then Settings. Make sure “Smart Quotes” is turned OFF. Smart quotes will break the configuration file. Close Settings.

- If the file is empty, paste this entire block in, then save (Cmd + S):

{

  "mcpServers": {

    "segment-workspace": {

      "command": "/Users/yourname/segment-mcp/.venv/bin/python",

      "args": ["/Users/yourname/segment-mcp/server.py"],

      "env": {

        "SEGMENT_PUBLIC_API_TOKEN": "sgp_paste_your_token_here"

      }

    }

  }

}

- Replace the three placeholder pieces with your real values:

- Both */Users/yourname/...* paths → replace with the two paths you copied in Part 6.

- *sgp_paste_your_token_here* → replace with the Segment token from Part 2 (keep the quotes around it).

- Save the file with Cmd + S, then close TextEdit.

- Back in Terminal, double-check the file is valid by running:

python3 -m json.tool ~/Library/Application\ Support/Claude/claude_desktop_config.json

If the file prints back out cleanly, you’re good. If you see an error message in red text, there’s a typo — most often a missing comma or a curly quote. Open the file in TextEdit and check carefully.

# Part 8 — Restart Claude Desktop and test

- Click the Claude icon in your menu bar (or right-click the Claude icon in the Dock) and choose Quit Claude. Or press Cmd + Q while Claude is in front.

- Wait 5 seconds, then open Claude from your Applications folder again.

- Start a new chat.

- In the chat, type: *Using segment-workspace, show me my workspace info.*

- A small box will pop up asking permission to use a tool called get_workspace. Click Allow.

- Within a few seconds, Claude will reply with your Segment workspace’s ID, name, and slug. That means everything is working.

**Success looks like: **Claude replies with text like “Your workspace is named Acme Inc, ID wsp_abc123…” That’s the confirmation.

# Using the server

From now on, every time you open Claude Desktop the server starts automatically. You don’t need to open Terminal again.

Try prompts like:

- “List all sources in my Segment workspace.”

- “Show me the destinations connected to my mobile source.”

- “List my audiences in the Unify space.”

- “What does my workspace look like — give me a summary?”

Claude will ask permission the first time it uses each new tool. After that, it remembers your choice.

# If something goes wrong

### I don’t see “segment-workspace” anywhere in Claude Desktop

Open Claude Desktop, then in the top menu click Claude, then Settings (or press Cmd + ,). Click the Developer tab. You should see segment-workspace listed with a green status. If it’s red or missing, see the next section.

### The server shows up but errors when I try to use it

Look at the log file Claude Desktop keeps. In Terminal, run:

tail -50 ~/Library/Logs/Claude/mcp-server-segment-workspace.log

The last lines usually explain the problem. Common ones:

- **“No such file or directory”** — one of the paths in your config is wrong. Check Part 6 and Part 7.

- **“ModuleNotFoundError”** — the packages didn’t install. Re-do Part 5, making sure (.venv) appears in your Terminal prompt before running the pip install command.

- **“SEGMENT_PUBLIC_API_TOKEN env var is required”** — your token is missing or wasn’t pasted correctly into the config. Open the config file again and check.

### I changed the config but nothing’s different

Claude Desktop only reads the configuration file when it starts up. After any change, fully quit Claude (Cmd + Q) and reopen it. Closing the window isn’t enough.

### I want to share my config with a teammate

Don’t share the file as-is — it contains your Segment token. Share an empty template instead and let them add their own token.

# Getting help

If you’re stuck, your fastest path is to share three things with your teammate who built the server:

- A screenshot of the error you’re seeing in Claude Desktop.

- The output of running this in Terminal:

tail -50 ~/Library/Logs/Claude/mcp-server-segment-workspace.log

- Your config file (with the token removed) — show them what you have in claude_desktop_config.json.

With those three things, almost any problem can be diagnosed in a few minutes.

*— End of guide —*