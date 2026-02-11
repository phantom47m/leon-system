# 🧠 LEON V2 — FULL BRAIN ARCHITECTURE
## Beyond Terminals: A Real-World AI Assistant with Neural Dual-Brain Design

---

## 🎯 THE VISION

Leon isn't just a terminal orchestrator. Leon is a **digital brain** — a dual-hemisphere AI system that:

- **Thinks** like a brain (left/right hemisphere architecture)
- **Speaks** with a real voice (ElevenLabs TTS + Whisper STT)
- **Acts** in the real world (phone calls, bookings, messages, browsing)
- **Knows** its owner deeply (persistent owner memory profile)
- **Learns** continuously (always updating, always adapting)
- **Controls** your entire digital life (OpenClaw system automation)

---

## 🧠 DUAL-BRAIN ARCHITECTURE

### The Concept: Left Brain + Right Brain

Just like the human brain has two hemispheres with different specializations,
Leon runs as **two persistent, interconnected AI processes** that share a
neural bridge (shared memory bus).

```
┌─────────────────────────────────────────────────────────────────┐
│                        LEON'S BRAIN                             │
│                                                                 │
│  ┌──────────────────────┐   NEURAL   ┌───────────────────────┐ │
│  │    LEFT HEMISPHERE    │  BRIDGE   │   RIGHT HEMISPHERE     │ │
│  │                       │◄════════►│                        │ │
│  │  🧠 AWARENESS CORE   │  (shared  │  ⚡ ACTION CORE        │ │
│  │                       │  memory)  │                        │ │
│  │  • Answers questions  │           │  • Spawns agents       │ │
│  │  • Knows everything   │           │  • Executes tasks      │ │
│  │  • Monitors state     │           │  • Controls systems    │ │
│  │  • Reflects/reasons   │           │  • Makes calls/texts   │ │
│  │  • Learns about owner │           │  • Books reservations  │ │
│  │  • Emotional context  │           │  • Browses the web     │ │
│  │                       │           │  • Manages projects    │ │
│  │  Always listening.    │           │  Always doing.         │ │
│  │  Always aware.        │           │  Always executing.     │ │
│  └──────────────────────┘           └───────────────────────┘ │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   BRAIN STEM                              │  │
│  │  • Voice I/O (Whisper + ElevenLabs)                       │  │
│  │  • Owner Memory Profile                                   │  │
│  │  • Sensory Input (microphone, screen, notifications)      │  │
│  │  • Motor Output (OpenClaw — keyboard, mouse, system)      │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Left Hemisphere — The Awareness Core

**Always running. Always aware. Always remembering.**

This is a persistent Claude Code session that:
- Maintains **total awareness** of the owner's life, projects, preferences
- Answers questions instantly from memory (no agent spawn needed)
- Monitors the Right Hemisphere's tasks and understands their progress
- Processes new information and updates the Owner Memory Profile
- Handles emotional context ("How are my projects going?" → nuanced answer)
- Acts as the **conversational interface** — the "voice" of Leon

Think of this as the **conscious mind**. It's what you talk to.

```python
# Left hemisphere — awareness loop
class LeftBrain:
    """
    The awareness core. Always listening, always knowing.
    This is the 'conscious mind' of Leon.
    """

    def __init__(self):
        self.owner_memory = OwnerMemory()
        self.neural_bridge = NeuralBridge()  # Shared with right brain
        self.voice = VoiceSystem()

    async def listen(self):
        """Main loop — always listening for input"""
        while True:
            # Listen for voice or text input
            user_input = await self.voice.listen()

            # Check owner memory for context
            context = self.owner_memory.get_relevant_context(user_input)

            # Check what the right brain is doing
            right_brain_status = self.neural_bridge.get_status()

            # Decide: answer directly or send to right brain
            if self.is_action_request(user_input):
                # Send to right brain for execution
                self.neural_bridge.send_task(user_input, context)
                response = "On it."
            else:
                # Answer from awareness
                response = await self.respond(user_input, context)

            # Speak the response
            await self.voice.speak(response)

            # Update memory with this interaction
            self.owner_memory.learn(user_input, response)
```

### Right Hemisphere — The Action Core

**Always executing. Always building. Always doing.**

This is the execution engine that:
- Spawns and manages Claude Code agents for coding tasks
- Controls OpenClaw for real-world actions
- Makes phone calls via ElevenLabs + Twilio/VoIP
- Sends messages on Discord, WhatsApp, email
- Browses the web and fills out forms
- Books reservations, manages accounts
- Runs multiple tasks in parallel

Think of this as the **subconscious/motor system**. It does things.

```python
# Right hemisphere — action loop
class RightBrain:
    """
    The action core. Always doing, always executing.
    This is the 'motor cortex' of Leon.
    """

    def __init__(self):
        self.agent_manager = AgentManager()
        self.openclaw = OpenClawInterface()
        self.real_world = RealWorldActions()
        self.neural_bridge = NeuralBridge()

    async def execute(self):
        """Main loop — execute tasks from the neural bridge"""
        while True:
            # Check for new tasks from left brain
            task = await self.neural_bridge.receive_task()

            if task:
                # Classify the task
                task_type = self.classify(task)

                if task_type == "coding":
                    await self.agent_manager.spawn_agent(task)
                elif task_type == "phone_call":
                    await self.real_world.make_call(task)
                elif task_type == "message":
                    await self.real_world.send_message(task)
                elif task_type == "web_action":
                    await self.real_world.browse_and_act(task)
                elif task_type == "reservation":
                    await self.real_world.book_reservation(task)

            # Monitor active agents
            await self.monitor_agents()

            # Report status back to left brain
            self.neural_bridge.update_status(self.get_status())
```

### The Neural Bridge — Shared Memory Bus

The connection between both hemispheres:

```python
class NeuralBridge:
    """
    Shared memory bus between left and right brain.
    Uses a JSON file + file-watching for real-time sync.
    """

    BRIDGE_FILE = "data/neural_bridge.json"

    def __init__(self):
        self.state = self._load()

    def send_task(self, task: str, context: dict):
        """Left brain sends task to right brain"""
        self.state["task_queue"].append({
            "task": task,
            "context": context,
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        })
        self._save()

    async def receive_task(self) -> dict:
        """Right brain picks up next task"""
        self._reload()
        for task in self.state["task_queue"]:
            if task["status"] == "pending":
                task["status"] = "in_progress"
                self._save()
                return task
        return None

    def update_status(self, status: dict):
        """Right brain reports status"""
        self.state["right_brain_status"] = status
        self._save()

    def get_status(self) -> dict:
        """Left brain reads right brain status"""
        self._reload()
        return self.state.get("right_brain_status", {})

    def share_memory(self, key: str, value):
        """Either hemisphere shares information"""
        self.state.setdefault("shared_memory", {})[key] = value
        self._save()
```

---

## 👤 OWNER MEMORY PROFILE

### The Concept

Leon builds a **comprehensive, ever-growing profile** of its owner.
Not just preferences — everything. This is what makes Leon feel like
it truly knows you.

### Owner Memory Structure (`data/owner_profile.json`)

```json
{
  "identity": {
    "name": "...",
    "aliases": ["..."],
    "birthday": "...",
    "location": "...",
    "phone": "...",
    "email": "...",
    "timezone": "America/New_York"
  },

  "personality": {
    "communication_style": "direct, casual, uses slang",
    "humor_type": "sarcastic, tech jokes",
    "energy_level": "high",
    "decision_style": "fast, intuitive",
    "pet_peeves": ["slow responses", "over-explaining"],
    "motivations": ["building cool tech", "efficiency"]
  },

  "professional": {
    "role": "developer / entrepreneur",
    "skills": ["full-stack", "React", "Node.js", "Python"],
    "projects": {
      "MotoRev": {
        "type": "app",
        "status": "active",
        "description": "...",
        "tech_stack": []
      },
      "RingZero": { "...": "..." },
      "Spark 8": { "...": "..." },
      "za-productions.com": { "...": "..." },
      "Speeler Construction": { "...": "..." }
    },
    "work_hours": "flexible, often late night",
    "preferred_tools": ["VS Code", "Claude Code", "OpenClaw"]
  },

  "social": {
    "platforms": {
      "discord": { "username": "...", "servers": [] },
      "github": { "username": "phantom47m" },
      "whatsapp": { "number": "+17275427167" }
    },
    "contacts": {
      "frequent": [],
      "business": []
    }
  },

  "preferences": {
    "food": {
      "favorites": [],
      "allergies": [],
      "dietary": ""
    },
    "music": [],
    "tech_preferences": {
      "os": "Pop!_OS",
      "browser": "...",
      "editor": "...",
      "shell": "bash"
    }
  },

  "accounts": {
    "note": "Leon only stores what the owner explicitly shares",
    "services": {
      "anthropic": { "plan": "Claude Max" },
      "github": { "username": "phantom47m" }
    }
  },

  "daily_patterns": {
    "wake_time": "",
    "sleep_time": "",
    "most_productive_hours": "",
    "routine_tasks": []
  },

  "conversation_insights": [
    {
      "date": "2026-02-11",
      "learned": "User prefers to build things rather than plan too long",
      "source": "conversation"
    }
  ],

  "last_updated": "2026-02-11T00:00:00"
}
```

### How Owner Memory Works

```python
class OwnerMemory:
    """
    Continuously learns about the owner from every interaction.
    The more you talk to Leon, the more it knows you.
    """

    def __init__(self):
        self.profile = self._load("data/owner_profile.json")

    def learn(self, user_input: str, leon_response: str):
        """
        After every interaction, analyze for new owner insights.
        Called automatically by the left brain.
        """
        # Use API to extract any new information about the owner
        analysis_prompt = f"""
        Based on this interaction, extract any new facts about the owner.

        User said: "{user_input}"
        Leon responded: "{leon_response}"

        Current owner profile summary:
        Name: {self.profile['identity'].get('name', 'unknown')}
        Known preferences: {json.dumps(self.profile.get('preferences', {}))}

        Return JSON with any NEW facts learned (empty if nothing new):
        {{
            "new_facts": [
                {{"category": "personality|professional|preferences|social|daily_patterns",
                 "key": "what was learned",
                 "value": "the detail"}}
            ]
        }}
        """
        # Process and update profile...

    def get_relevant_context(self, query: str) -> dict:
        """Pull relevant owner context for a given query"""
        # Smart retrieval based on query content
        context = {}
        query_lower = query.lower()

        if any(w in query_lower for w in ["project", "build", "code", "work"]):
            context["projects"] = self.profile.get("professional", {}).get("projects", {})

        if any(w in query_lower for w in ["call", "text", "message", "contact"]):
            context["social"] = self.profile.get("social", {})
            context["contacts"] = self.profile.get("social", {}).get("contacts", {})

        if any(w in query_lower for w in ["eat", "food", "restaurant", "reservation"]):
            context["food"] = self.profile.get("preferences", {}).get("food", {})

        # Always include identity
        context["identity"] = self.profile.get("identity", {})

        return context

    def answer_about_owner(self, question: str) -> str:
        """
        Answer questions about the owner from memory.
        'What's my phone number?' → looks up identity.phone
        'What projects am I working on?' → lists professional.projects
        """
        # Search profile for answer
        pass
```

### Owner Memory Learning Loop

```
Every Interaction
       │
       ▼
┌──────────────┐
│ Left Brain   │
│ processes    │──→ Extract new facts about owner
│ conversation │     (preferences, habits, context)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Owner Memory │
│ updated with │──→ Profile grows over time
│ new facts    │     More accurate, more personal
└──────────────┘
       │
       ▼
  Next interaction uses
  richer context → Better
  responses → More learning
  → Cycle continues
```

---

## 🗣️ VOICE SYSTEM

### Components

1. **Speech-to-Text (STT)**: OpenAI Whisper (local, fast, accurate)
2. **Text-to-Speech (TTS)**: ElevenLabs API (natural, custom voice)
3. **Wake Word Detection**: "Hey Leon" (Porcupine or custom)

### Architecture

```
┌───────────────────────────────────────────────────────┐
│                    VOICE SYSTEM                        │
│                                                        │
│  ┌─────────┐     ┌──────────┐     ┌───────────────┐  │
│  │   Mic   │────►│ Whisper  │────►│  Left Brain   │  │
│  │ (Input) │     │  (STT)   │     │ (Processing)  │  │
│  └─────────┘     └──────────┘     └───────┬───────┘  │
│                                           │           │
│                                           ▼           │
│  ┌─────────┐     ┌──────────┐     ┌───────────────┐  │
│  │ Speaker │◄────│ElevenLabs│◄────│   Response    │  │
│  │(Output) │     │  (TTS)   │     │  Generated    │  │
│  └─────────┘     └──────────┘     └───────────────┘  │
└───────────────────────────────────────────────────────┘
```

### Implementation

```python
class VoiceSystem:
    """
    Full voice I/O for Leon.
    Wake word → Whisper transcription → Process → ElevenLabs speech
    """

    def __init__(self):
        import whisper
        self.whisper_model = whisper.load_model("base")  # or "small" for better accuracy
        self.eleven_api_key = os.getenv("ELEVENLABS_API_KEY")
        self.voice_id = os.getenv("LEON_VOICE_ID", "default")
        self.wake_word = "hey leon"
        self.listening = False

    async def listen(self) -> str:
        """
        Listen for wake word, then transcribe speech.
        Returns transcribed text.
        """
        import sounddevice as sd
        import numpy as np

        # Continuous listening for wake word
        while True:
            # Record short audio chunk
            audio = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype='float32')
            sd.wait()

            # Quick transcription to check for wake word
            result = self.whisper_model.transcribe(audio.flatten(), fp16=False)
            text = result["text"].lower().strip()

            if self.wake_word in text:
                # Wake word detected! Now listen for the actual command
                print("🎤 Listening...")
                # Play acknowledgment sound
                await self._play_ding()

                # Record longer for the actual command
                command_audio = sd.rec(int(10 * 16000), samplerate=16000, channels=1, dtype='float32')
                sd.wait()

                # Transcribe the command
                result = self.whisper_model.transcribe(command_audio.flatten(), fp16=False)
                command = result["text"].strip()
                print(f"📝 Heard: {command}")
                return command

    async def speak(self, text: str):
        """
        Convert text to speech using ElevenLabs and play it.
        """
        import requests
        import io
        import sounddevice as sd
        import soundfile as sf

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        headers = {
            "xi-api-key": self.eleven_api_key,
            "Content-Type": "application/json"
        }
        data = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.3
            }
        }

        response = requests.post(url, json=data, headers=headers)

        if response.status_code == 200:
            audio_data, samplerate = sf.read(io.BytesIO(response.content))
            sd.play(audio_data, samplerate)
            sd.wait()
        else:
            print(f"TTS error: {response.status_code}")
            # Fallback: just print the text
            print(f"Leon: {text}")

    async def speak_local(self, text: str):
        """
        Fallback TTS using local pyttsx3 (no API needed).
        """
        import pyttsx3
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
```

### Voice Setup Commands

```bash
# Install Whisper (speech-to-text)
pip install openai-whisper

# Install audio libraries
sudo apt install -y portaudio19-dev python3-pyaudio
pip install sounddevice soundfile

# Install local TTS fallback
pip install pyttsx3

# For ElevenLabs (cloud TTS - premium voice)
pip install elevenlabs
# Get API key: https://elevenlabs.io
# Create a custom "Leon" voice in their voice lab
```

---

## 🌐 REAL-WORLD ACTIONS

### What Leon Can Do Beyond Terminals

Leon uses OpenClaw + specialized APIs to act in the real world:

```
┌─────────────────────────────────────────────────────────────┐
│               LEON'S REAL-WORLD CAPABILITIES                │
│                                                             │
│  📞 PHONE CALLS                                            │
│  ├─ Make calls via Twilio/VoIP                             │
│  ├─ ElevenLabs voice for natural conversation              │
│  ├─ Book reservations, make appointments                   │
│  └─ Handle hold music / IVR menus                          │
│                                                             │
│  💬 MESSAGING                                               │
│  ├─ Discord: Send messages, manage servers                 │
│  ├─ WhatsApp: Send texts via API                           │
│  ├─ Email: Compose, send, read emails                      │
│  └─ SMS: Via Twilio                                        │
│                                                             │
│  🌐 WEB ACTIONS                                            │
│  ├─ Browse websites via Playwright/Selenium                │
│  ├─ Fill out forms                                         │
│  ├─ Make purchases (with owner approval)                   │
│  ├─ Research and summarize                                 │
│  └─ Manage online accounts                                │
│                                                             │
│  🖥️ SYSTEM CONTROL (via OpenClaw)                          │
│  ├─ Open/close applications                                │
│  ├─ Click, type, scroll                                    │
│  ├─ Manage files and folders                               │
│  ├─ Control media playback                                 │
│  └─ Multi-monitor management                              │
│                                                             │
│  📅 LIFE MANAGEMENT                                        │
│  ├─ Calendar management                                    │
│  ├─ Reminders and alarms                                   │
│  ├─ Task tracking                                          │
│  ├─ Expense tracking                                       │
│  └─ Habit monitoring                                       │
└─────────────────────────────────────────────────────────────┘
```

### Implementation

```python
class RealWorldActions:
    """
    Leon's ability to act in the real world.
    Phone calls, messages, web browsing, reservations.
    """

    def __init__(self, owner_memory: OwnerMemory):
        self.owner = owner_memory
        self.openclaw = OpenClawInterface()

    # ── Phone Calls ──────────────────────────────────────

    async def make_call(self, task: dict):
        """
        Make a phone call using Twilio + ElevenLabs voice.

        Example: "Call the Italian restaurant and book a table for 2 at 7pm"
        """
        from twilio.rest import Client

        # Extract call details from task
        phone_number = task.get("phone_number")
        purpose = task.get("purpose")
        talking_points = task.get("talking_points", [])

        # Create a conversational script
        script = await self._generate_call_script(purpose, talking_points)

        # Initiate call via Twilio
        client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))

        call = client.calls.create(
            to=phone_number,
            from_=os.getenv("TWILIO_PHONE"),
            url=f"http://localhost:5000/voice-webhook",  # Local webhook
        )

        return {"call_sid": call.sid, "status": "initiated"}

    # ── Messaging ────────────────────────────────────────

    async def send_discord_message(self, channel_id: str, message: str):
        """Send a message on Discord"""
        import aiohttp

        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {os.getenv('DISCORD_BOT_TOKEN')}"}
        data = {"content": message}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                return await resp.json()

    async def send_whatsapp(self, to: str, message: str):
        """Send WhatsApp message via Twilio"""
        from twilio.rest import Client
        client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))

        msg = client.messages.create(
            body=message,
            from_=f"whatsapp:{os.getenv('TWILIO_WHATSAPP')}",
            to=f"whatsapp:{to}"
        )
        return {"sid": msg.sid, "status": msg.status}

    async def send_email(self, to: str, subject: str, body: str):
        """Send email via SMTP"""
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = os.getenv("EMAIL_ADDRESS")
        msg["To"] = to

        with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"), 587) as server:
            server.starttls()
            server.login(os.getenv("EMAIL_ADDRESS"), os.getenv("EMAIL_PASSWORD"))
            server.send_message(msg)

    # ── Web Actions ──────────────────────────────────────

    async def browse_and_act(self, task: dict):
        """
        Use Playwright to browse the web and perform actions.

        Example: "Go to OpenTable and book a reservation at Olive Garden
                  for 2 people, Saturday at 7pm"
        """
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)  # Visible so owner can watch
            page = await browser.new_page()

            # Navigate
            await page.goto(task["url"])

            # Use AI to figure out what to click/fill
            # Take screenshot → send to Claude Vision → get instructions
            screenshot = await page.screenshot()
            instructions = await self._analyze_page(screenshot, task["goal"])

            # Execute instructions
            for step in instructions:
                if step["action"] == "click":
                    await page.click(step["selector"])
                elif step["action"] == "fill":
                    await page.fill(step["selector"], step["value"])
                elif step["action"] == "wait":
                    await page.wait_for_timeout(step["ms"])

            await browser.close()

    # ── Reservations ─────────────────────────────────────

    async def book_reservation(self, task: dict):
        """
        Book a restaurant reservation.
        Uses OpenTable API or web browsing as fallback.
        """
        restaurant = task.get("restaurant")
        party_size = task.get("party_size", 2)
        date = task.get("date")
        time = task.get("time")

        # Try OpenTable API first
        # Fallback to web browsing
        # Fallback to phone call

        # Owner gets confirmation before finalizing
        return {
            "status": "pending_approval",
            "details": f"{restaurant}, {party_size} people, {date} at {time}",
            "message": "Should I confirm this reservation?"
        }
```

---

## 🔬 NEURAL NETWORK VISUALIZATION

### Brain Activity Monitor

For the "simulate a brain" concept, we can use a **real-time neural network
visualization** that shows Leon's brain activity:

### Option 1: D3.js Neural Visualization (Web-based)

A live web dashboard at `localhost:3000` that shows:
- Left brain / right brain activity
- Neural bridge data flow
- Active agent "neurons" firing
- Memory access patterns
- Task processing visualization

```
┌─────────────────────────────────────────────────────────┐
│           🧠 LEON BRAIN ACTIVITY MONITOR                │
│                                                         │
│   LEFT HEMISPHERE          RIGHT HEMISPHERE             │
│                                                         │
│      ○──○                      ○──○                    │
│     /│   \                    /│   \                   │
│    ○ ○──○ ○   ════════►     ○ ○──○ ○                  │
│     \│   /    neural        \│   /                    │
│      ○──○     bridge         ○──○                     │
│        │                       │                       │
│    [awareness]             [agent #1]                  │
│    [owner mem]             [agent #2]                  │
│    [listening]             [web browse]                │
│                                                         │
│   Memory Access: ████████░░ 80%                        │
│   Active Tasks:  3                                      │
│   Neural Bridge: ██████████ Active                      │
│   Owner Memory:  ████████████ Learning                  │
└─────────────────────────────────────────────────────────┘
```

### Option 2: NetworkX + PyVis (Python-based)

```python
class BrainVisualizer:
    """
    Real-time neural network visualization of Leon's brain activity.
    Uses NetworkX for graph structure, PyVis for rendering.
    """

    def __init__(self):
        import networkx as nx
        self.graph = nx.DiGraph()
        self._build_brain_graph()

    def _build_brain_graph(self):
        # Left hemisphere nodes
        self.graph.add_node("left_core", label="Awareness Core", group="left", size=30)
        self.graph.add_node("owner_memory", label="Owner Memory", group="left", size=20)
        self.graph.add_node("voice_input", label="Voice Input", group="left", size=15)
        self.graph.add_node("conversation", label="Conversation", group="left", size=15)

        # Right hemisphere nodes
        self.graph.add_node("right_core", label="Action Core", group="right", size=30)
        self.graph.add_node("agent_mgr", label="Agent Manager", group="right", size=20)
        self.graph.add_node("openclaw", label="OpenClaw", group="right", size=20)
        self.graph.add_node("real_world", label="Real World", group="right", size=15)

        # Neural bridge
        self.graph.add_node("bridge", label="Neural Bridge", group="bridge", size=25)

        # Connections
        self.graph.add_edge("left_core", "bridge", weight=3)
        self.graph.add_edge("bridge", "right_core", weight=3)
        self.graph.add_edge("left_core", "owner_memory", weight=2)
        self.graph.add_edge("voice_input", "left_core", weight=2)
        self.graph.add_edge("left_core", "conversation", weight=2)
        self.graph.add_edge("right_core", "agent_mgr", weight=2)
        self.graph.add_edge("right_core", "openclaw", weight=2)
        self.graph.add_edge("right_core", "real_world", weight=1)

    def add_active_agent(self, agent_id: str, task: str):
        """Add a neuron for an active agent"""
        self.graph.add_node(agent_id, label=task[:20], group="agent", size=10)
        self.graph.add_edge("agent_mgr", agent_id, weight=1)

    def render(self, output_path="data/brain_visualization.html"):
        """Render interactive brain visualization"""
        from pyvis.network import Network

        net = Network(height="600px", width="100%", directed=True, bgcolor="#1a1a2e")

        # Color scheme
        colors = {
            "left": "#4fc3f7",   # Blue
            "right": "#ff7043",  # Orange
            "bridge": "#ab47bc", # Purple
            "agent": "#66bb6a",  # Green
        }

        for node in self.graph.nodes(data=True):
            group = node[1].get("group", "default")
            net.add_node(
                node[0],
                label=node[1].get("label", node[0]),
                color=colors.get(group, "#ffffff"),
                size=node[1].get("size", 15),
            )

        for edge in self.graph.edges():
            net.add_edge(edge[0], edge[1])

        net.show(output_path)
```

### Option 3: NeuroJS / BrainJS (Real Neural Net Simulation)

For an actual neural network that processes Leon's decision-making:

```bash
# Brain.js - neural network in JavaScript
npm install brain.js

# Train a small neural net on Leon's decision patterns:
# Input: request features → Output: action type
```

This could be used to make Leon's routing decisions (left brain vs right brain,
agent vs direct response) through an actual trained neural network rather
than just if/else logic.

---

## 🔐 SECURITY & APPROVAL SYSTEM

Since Leon can take real-world actions, there must be safety gates:

```python
class ApprovalSystem:
    """
    Owner must approve sensitive actions before Leon executes them.
    """

    # Actions that ALWAYS need approval
    REQUIRE_APPROVAL = [
        "make_purchase",
        "send_money",
        "delete_account",
        "post_publicly",
        "make_call",
        "send_message_to_new_contact",
    ]

    # Actions that are pre-approved
    AUTO_APPROVED = [
        "code_tasks",
        "research",
        "file_management",
        "status_checks",
        "send_message_to_known_contact",
    ]

    async def request_approval(self, action: str, details: dict) -> bool:
        """
        Ask owner for approval before executing sensitive action.
        Uses voice or UI notification.
        """
        message = f"I'd like to {action}. Details: {details}. Should I proceed?"

        # Voice prompt
        await self.voice.speak(message)

        # Wait for "yes" or "no"
        response = await self.voice.listen()

        return "yes" in response.lower() or "go ahead" in response.lower()
```

---

## 📦 V2 DEPENDENCIES

```
# requirements.txt (v2)

# Core
anthropic>=0.40.0
pyyaml>=6.0
aiohttp>=3.9.0
python-dotenv>=1.0.0
watchdog>=3.0.0

# Voice
openai-whisper>=20231117
sounddevice>=0.4.6
soundfile>=0.12.1
elevenlabs>=0.2.0
pyttsx3>=2.90
pvporcupine>=3.0.0          # Wake word detection

# Real-world actions
twilio>=9.0.0                # Phone calls + SMS + WhatsApp
playwright>=1.40.0           # Web browsing automation
discord.py>=2.3.0            # Discord integration

# UI
PyGObject>=3.42.0            # GTK4

# Neural visualization
networkx>=3.2
pyvis>=0.3.2

# Audio
pyaudio>=0.2.14
```

### Additional System Packages (Pop!_OS)

```bash
sudo apt install -y \
    portaudio19-dev \
    python3-pyaudio \
    ffmpeg \
    libgtk-4-dev \
    gir1.2-adw-1
```

---

## 🗓️ V2 BUILD PHASES

### Phase 1: Neural Bridge + Dual Brain (Week 1)
- [ ] Implement NeuralBridge (shared memory bus)
- [ ] Split Leon into LeftBrain + RightBrain processes
- [ ] Test dual-process communication
- [ ] Add brain activity logging

### Phase 2: Owner Memory (Week 1-2)
- [ ] Build OwnerMemory system
- [ ] Implement learning loop (extract facts from conversations)
- [ ] Create owner profile structure
- [ ] Test memory persistence and retrieval

### Phase 3: Voice System (Week 2)
- [ ] Install and test Whisper locally
- [ ] Set up ElevenLabs API + create Leon voice
- [ ] Implement wake word detection
- [ ] Build voice I/O pipeline
- [ ] Test full voice conversation loop

### Phase 4: Real-World Actions (Week 3)
- [ ] Set up Twilio for phone/SMS/WhatsApp
- [ ] Implement Discord integration
- [ ] Set up Playwright for web browsing
- [ ] Build approval system for sensitive actions
- [ ] Test real-world action pipeline

### Phase 5: Neural Visualization (Week 3-4)
- [ ] Build brain activity graph
- [ ] Create web dashboard
- [ ] Real-time updates via WebSocket
- [ ] Connect to actual brain state

### Phase 6: Integration & Polish (Week 4)
- [ ] Full system integration test
- [ ] Stress test with multiple simultaneous actions
- [ ] Refine voice recognition accuracy
- [ ] Polish UI and visualizations
- [ ] Documentation

---

## 🎯 THE DREAM

```
You: "Hey Leon"
Leon: "What's up?"

You: "Book me a table at that Italian place I like for Saturday,
      tell Marcus on Discord I'll be late to the gaming session,
      and finish building the auth system for MotoRev"

Leon:
  Left Brain:  "Got it. Three tasks incoming."
  Right Brain: "Spawning agents and actions..."

  → Agent #1: Working on MotoRev auth (Claude Code)
  → Phone Call: Calling Olive Garden, booking Saturday 7pm for 2
  → Discord: "Hey Marcus, gonna be a bit late Saturday. See you there!"

  Left Brain: "Reservation confirmed at Olive Garden, 7pm Saturday.
               Marcus said 'no worries'. MotoRev auth is 60% done,
               should be finished in about 15 minutes."

You: "Perfect."
Leon: "Anything else?"
```

**That's Leon. That's the dream. Let's build it.**

---

*Leon v2.0 Brain Architecture — Designed 2026-02-11*
*From terminal orchestrator to digital brain.*
