# 🤖 Leon - AI Orchestration System

**Leon is an autonomous AI that manages multiple Claude Code agents simultaneously.** Instead of juggling 10 terminals yourself, you give Leon high-level commands and he spawns, monitors, and coordinates coding agents across all your projects.

```
You: "Leon, build me a REST API, fix the React bug, and research the best database"

Leon:
  → Spawns Agent #1 → REST API (runs autonomously)
  → Spawns Agent #2 → Fix React bug (runs autonomously)
  → Handles research himself (responds directly)
  → Monitors all tasks, updates you on progress
  → Saves everything to persistent memory
```

---

## 🚀 QUICK START ON POP!_OS

### Step 1: Clone the repo
```bash
cd ~
git clone https://github.com/phantom47m/leon-system.git
cd leon-system
```

### Step 2: Run the installer
```bash
bash scripts/install.sh
```
This installs all system packages, creates the Python venv, and sets up systemd.

### Step 3: Get your Anthropic API key

**Option A: Generate a new key**
1. Go to https://console.anthropic.com/settings/keys
2. Click "Create Key"
3. Copy the key (starts with `sk-ant-...`)

**Option B: If your key expired or you need a new one, paste this prompt into Claude:**

> I need to set up my Anthropic API key for my Leon AI orchestrator on Pop!_OS Linux. Walk me through:
> 1. How to get/regenerate my API key from console.anthropic.com
> 2. How to set it as an environment variable permanently on Pop!_OS
> 3. How to verify it works
>
> My Leon system lives at ~/leon-system and uses the Anthropic Python SDK.
> I have a Claude Max subscription under my account.

### Step 4: Set up your API key
```bash
# Add to your shell profile so it persists
echo 'export ANTHROPIC_API_KEY="sk-ant-your-key-here"' >> ~/.bashrc
source ~/.bashrc
```

### Step 5: Add your projects
Edit `config/projects.yaml` and add your projects:
```yaml
projects:
  - name: "MotoRev"
    path: "/home/yourusername/projects/motorev"
    type: "fullstack"
    tech_stack:
      - "Node.js"
      - "React"

  - name: "RingZero"
    path: "/home/yourusername/projects/ringzero"
    type: "frontend"
    tech_stack:
      - "React"
      - "TypeScript"
```

### Step 6: Run Leon!
```bash
cd ~/leon-system
source venv/bin/activate
python3 main.py --cli
```

---

## 📋 ALL COMMANDS YOU NEED (COPY-PASTE CHEATSHEET)

```bash
# ═══════════════════════════════════════
# FIRST TIME SETUP (run these in order)
# ═══════════════════════════════════════

# 1. Clone
git clone https://github.com/phantom47m/leon-system.git
cd ~/leon-system

# 2. Install everything
bash scripts/install.sh

# 3. Set API key (replace with YOUR key)
echo 'export ANTHROPIC_API_KEY="sk-ant-XXXX"' >> ~/.bashrc
source ~/.bashrc

# 4. Edit your projects
nano config/projects.yaml

# 5. Run Leon
source venv/bin/activate
python3 main.py --cli


# ═══════════════════════════════════════
# DAILY USE
# ═══════════════════════════════════════

# Start Leon (CLI mode)
cd ~/leon-system && source venv/bin/activate && python3 main.py --cli

# Start Leon (GUI mode - needs desktop environment)
cd ~/leon-system && source venv/bin/activate && python3 main.py --gui

# Quick start script
bash ~/leon-system/scripts/start_leon.sh

# Run as background service
systemctl --user start leon.service
systemctl --user enable leon.service   # auto-start on boot
systemctl --user status leon.service   # check status
journalctl --user -u leon.service -f   # view logs


# ═══════════════════════════════════════
# INSIDE LEON (commands while running)
# ═══════════════════════════════════════

# Check status of all tasks
status

# Ask Leon to do things
# Just type naturally:
#   "Build me a REST API for user authentication"
#   "Fix the styling bug in my React dashboard"
#   "What's the status on the API build?"

# Quit
quit


# ═══════════════════════════════════════
# TROUBLESHOOTING
# ═══════════════════════════════════════

# Check logs
tail -f ~/leon-system/logs/leon_system.log

# Reinstall dependencies
cd ~/leon-system
source venv/bin/activate
pip install -r requirements.txt

# Check if OpenClaw is running
pgrep -f openclaw

# Test API key
python3 -c "import anthropic; c = anthropic.Anthropic(); print(c.messages.create(model='claude-sonnet-4-5-20250929', max_tokens=50, messages=[{'role':'user','content':'Say hi'}]).content[0].text)"

# Reset memory (start fresh)
rm data/leon_memory.json

# Update Leon from GitHub
cd ~/leon-system && git pull
```

---

## 🏗️ HOW LEON WORKS

### Architecture
```
┌──────────────────────────────────────────┐
│                 YOU                       │
│          "Build 3 things"                │
└──────────────┬───────────────────────────┘
               ▼
┌──────────────────────────────────────────┐
│            LEON CORE                      │
│  ┌─────────────────────────────────────┐ │
│  │ Orchestrator (leon.py)              │ │
│  │  - Analyzes your request            │ │
│  │  - Decides: respond vs spawn agents │ │
│  │  - Monitors all active tasks        │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  ┌────────┐ ┌───────────┐ ┌───────────┐ │
│  │ Memory │ │ Agent Mgr │ │ Task Queue│ │
│  └────────┘ └───────────┘ └───────────┘ │
└──────────────┬───────────────────────────┘
               ▼
┌──────────────────────────────────────────┐
│         CLAUDE CODE AGENTS               │
│  Agent #1 → Working on REST API          │
│  Agent #2 → Fixing React bug             │
│  Agent #3 → Building database schema     │
└──────────────────────────────────────────┘
```

### Key Components
| File | Purpose |
|------|---------|
| `core/leon.py` | Main brain - decides what to do with your requests |
| `core/memory.py` | Persistent memory - remembers everything across sessions |
| `core/agent_manager.py` | Spawns and monitors Claude Code agents |
| `core/task_queue.py` | Manages multiple simultaneous tasks |
| `core/api_client.py` | Talks to the Anthropic API |
| `core/openclaw_interface.py` | Bridge to OpenClaw for system automation |
| `ui/main_window.py` | GTK4 desktop GUI |
| `main.py` | Entry point (CLI or GUI mode) |
| `config/settings.yaml` | System configuration |
| `config/personality.yaml` | Leon's personality and prompts |
| `config/projects.yaml` | Your project definitions |

### Features
- **Multi-Agent**: Spawns multiple Claude Code agents working in parallel
- **Persistent Memory**: Remembers all projects, tasks, and preferences across restarts
- **Smart Routing**: Simple questions answered directly, complex tasks spawned to agents
- **Task Monitoring**: Background loop checks agent status every 10 seconds
- **CLI + GUI**: Terminal mode for SSH, GTK4 GUI for desktop
- **OpenClaw Integration**: Full system automation capabilities

---

## 🔑 API KEY HELP

### If you need to generate or regenerate your API key:

1. Go to **https://console.anthropic.com/settings/keys**
2. Log in with your Anthropic account
3. Click **"Create Key"**
4. Give it a name like "Leon System"
5. Copy the key immediately (you can't see it again)
6. Run: `echo 'export ANTHROPIC_API_KEY="your-key"' >> ~/.bashrc && source ~/.bashrc`

### Claude Prompt to Help You Set It Up:

If you're on your new Linux machine and need help, paste this into Claude:

```
I just installed Pop!_OS and I need to set up my Leon AI orchestrator system.
The code is cloned at ~/leon-system from GitHub (phantom47m/leon-system).

Help me:
1. Run the install script (bash scripts/install.sh)
2. Set up my Anthropic API key as an environment variable
3. Add my projects to config/projects.yaml
4. Test that Leon starts correctly with: python3 main.py --cli
5. Set up the systemd service for auto-start

If anything breaks, check ~/leon-system/logs/leon_system.log for errors.
```

---

## 💰 COST SAVINGS

| Approach | Monthly Cost |
|----------|-------------|
| Manual 10 terminals | ~$360/month |
| Leon orchestration | ~$45/month |
| **Savings** | **~$315/month (87%)** |

Leon reduces API costs because:
- Memory eliminates re-explaining context
- Smart routing avoids unnecessary agent spawns
- Agents get precise task briefs instead of full conversations

---

## 📝 CONVERSATION CONTEXT

This system was designed and built on 2026-02-11 in a conversation between
the user and Claude. Key decisions:

- **Name**: Leon (renamed from original "Heather" concept)
- **Platform**: Pop!_OS Linux (migrating from Windows 11)
- **Core tech**: Python + OpenClaw + Claude Code
- **Hardware**: AMD Ryzen 5 3600, 16GB RAM, RTX 3070
- **Auth**: Claude Max subscription (can use OAuth token or API key)
- **User projects**: MotoRev, RingZero, Spark 8, za-productions.com, Speeler

The full system design document is included in this repo as
`LEON_SYSTEM_COMPLETE_GUIDE.md`.

---

*Leon v1.0 - Built for autonomous multi-agent coding*
