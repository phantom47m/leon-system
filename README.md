# Leon — Personal AI Orchestrator

A self-hosted AI system that manages multiple Claude Code agents simultaneously, responds to voice commands, monitors your projects, and runs as a persistent background service with a cyberpunk dashboard UI.

> **First-time setup is handled by a browser wizard** — no config file editing required.

---

## What It Does

Instead of juggling multiple terminals, you give Leon high-level instructions and it spawns, monitors, and coordinates Claude Code agents across all your projects.

```
You: "Build me a REST API, fix the React bug, and write tests for the auth module"

Leon:
  → Spawns Agent #1 → REST API (runs autonomously)
  → Spawns Agent #2 → Fix React bug (runs autonomously)
  → Spawns Agent #3 → Auth tests (runs autonomously)
  → Monitors all agents, notifies you on completion
  → Saves everything to persistent memory
```

**Features:**
- Multi-agent orchestration — run 10+ Claude Code agents in parallel
- Always-on voice interface with wake word detection
- Persistent memory across sessions
- Cyberpunk web dashboard with real-time agent monitoring
- WhatsApp integration — send commands from your phone
- Screen awareness — sees what you're working on
- Scheduled tasks and autonomous overnight coding mode
- Update notifications when new versions are released

---

## Requirements

- Linux (tested on Ubuntu/Pop!\_OS)
- Python 3.11+
- Node.js 18+ (for WhatsApp bridge)
- **[`claude` CLI](https://claude.ai/download)** — required for agent spawning (free with Claude Max, or any Claude subscription)
- One of for conversation: Claude Max subscription, Anthropic API key, or Groq free tier
- Optional: ElevenLabs API key (voice output), Deepgram API key (STT)
- Optional: [OpenClaw](https://openclaw.ai) — adds browser automation, cron scheduling, and 300+ AI skills

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/phantom47m/leon-system.git
cd leon-system

# 2. Install everything
bash scripts/install.sh

# 3. Install and log into the claude CLI (required for agents)
# Download from https://claude.ai/download, then:
claude   # opens browser to log in — do this once

# 4. Start Leon
bash start.sh
```

Then open **http://localhost:3000** — you'll be redirected to the setup wizard automatically.

---

## Setup Wizard

On first run, the dashboard redirects to `/setup` where you configure:

| Field | Description |
|-------|-------------|
| AI Name | What to call your AI (e.g. ARIA, JARVIS, MAX) |
| Your Name | Used to personalise responses |
| Claude Auth | Claude Max subscription (CLI) or API key |
| ElevenLabs | Optional — natural voice output |
| Groq | Optional — fast Whisper speech-to-text |

This writes `config/user_config.yaml` (git-ignored) and redirects you to the dashboard.

---

## Configuration

### `config/settings.yaml`
Main system config — brain role, agent limits, voice settings, scheduler. Edit after initial setup if needed.

### `config/projects.yaml`
Tell Leon about your codebases:
```yaml
projects:
  - name: "my-app"
    path: "/home/yourname/my-app"
    type: "fullstack"
    aliases: ["app", "frontend"]
    tech_stack: ["React", "Node.js", "TypeScript"]
```

### `config/personality.yaml`
Customise your AI's personality. Uses `{AI_NAME}` and `{OWNER_NAME}` placeholders — these are substituted at startup from your setup wizard config.

### Update notifications
Set your GitHub repo in `config/settings.yaml` before publishing, and users will get automatic update banners in the dashboard:
```yaml
update_check:
  enabled: true
  repo: "your-username/your-repo"
  check_interval_hours: 12
```

---

## Architecture

```
┌─────────────────────────────────────────────┐
│                   YOU                        │
│    Voice / Dashboard / WhatsApp / CLI        │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│              LEON CORE                       │
│  ┌─────────────────────────────────────────┐│
│  │  Orchestrator (core/leon.py)            ││
│  │  - Analyses requests                    ││
│  │  - Routes: direct reply vs spawn agent  ││
│  │  - Monitors all active agents           ││
│  └─────────────────────────────────────────┘│
│                                             │
│  Memory  │  Agent Manager  │  Task Queue    │
│  Voice   │  Scheduler      │  Night Mode    │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│           CLAUDE CODE AGENTS                 │
│  Agent #1 → REST API build                  │
│  Agent #2 → Bug fix                         │
│  Agent #3 → Test suite                      │
└─────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `core/leon.py` | Main orchestration brain |
| `core/voice.py` | Wake word detection + STT/TTS |
| `core/memory.py` | Persistent memory across sessions |
| `core/agent_manager.py` | Spawns and monitors Claude Code agents |
| `core/task_queue.py` | Concurrent task management |
| `core/update_checker.py` | GitHub release notifications |
| `dashboard/server.py` | aiohttp web server + WebSocket |
| `dashboard/templates/` | Cyberpunk dashboard UI |
| `integrations/whatsapp/` | Node.js WhatsApp bridge |
| `config/settings.yaml` | System configuration |
| `config/personality.yaml` | AI personality + prompts |
| `config/projects.yaml` | Your project definitions |

---

## Voice Interface

Leon listens for your configured wake word (set during setup). Say **"Hey [AI Name]"** to activate, then speak your command.

- STT: Groq Whisper or Deepgram
- TTS: ElevenLabs (natural voice) with pyttsx3 fallback
- Push-to-talk: Scroll Lock (configurable)
- Wake word is dynamically built from your chosen AI name

---

## WhatsApp Integration

Send commands to Leon from your phone:

```bash
cd integrations/whatsapp
LEON_API_TOKEN=<token-from-dashboard> node bridge.js
```

Scan the QR code on first run. After that, the session persists.

---

## Multi-PC Split Brain (Advanced)

Leon supports a Left Brain / Right Brain split across two machines:

- **Left Brain** (main PC): handles conversation, voice, dashboard
- **Right Brain** (homelab/server): handles compute-heavy agent spawning

Configure `brain_role` in `config/settings.yaml` and set `bridge.server_url` to the Left Brain's address.

---

## Dashboard

Open **http://localhost:3000** after starting Leon.

- Central radial hub showing agent and system status
- Live activity feed with agent progress
- System panel: CPU, RAM, disk, GPU metrics
- Agent panel: live terminal output per agent
- Voice arc: mic state and wake word indicator
- Update banner when a new version is available

---

## Updating

Run this whenever a new version is available:

```bash
cd ~/leon-system && git pull && bash stop.sh && bash start.sh
```

That's it — one command. It pulls the latest code and restarts Leon.

> If you installed Leon somewhere other than your home folder, replace `~/leon-system` with the actual path (e.g. `cd /opt/leon-system`).

> **Tip:** Leon's dashboard shows an update banner automatically when a new version is released, so you'll know when to run it. You can also ask Leon directly: *"Are there any updates?"*

---

## Security Notes

- `config/user_config.yaml` is git-ignored — your keys never leave your machine
- The dashboard API is authenticated via bearer token (generated at startup)
- Rate limiting on the external message API (20 req/60s, localhost exempt)
- Encrypted vault available for storing API keys (`/setkey` command in dashboard)

---

## License

MIT
