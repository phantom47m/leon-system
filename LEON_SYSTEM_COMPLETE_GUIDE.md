# 🤖 LEON - AI ORCHESTRATION SYSTEM
## The Ultimate Multi-Agent Coding Assistant

**Version:** 1.0
**Platform:** Pop!_OS Linux (Ubuntu-based)
**Core Technology:** OpenClaw + Claude Code
**Author:** Built for autonomous multi-project management

---

## 📋 TABLE OF CONTENTS

1. [What is Leon?](#what-is-leon)
2. [Why Leon Exists](#why-leon-exists)
3. [System Architecture](#system-architecture)
4. [Core Components](#core-components)
5. [OpenClaw Integration](#openclaw-integration)
6. [Implementation Guide](#implementation-guide)
7. [Configuration](#configuration)
8. [Workflow Examples](#workflow-examples)
9. [Cost Analysis](#cost-analysis)
10. [Troubleshooting](#troubleshooting)

---

## 🎯 WHAT IS LEON?

**Leon is an autonomous AI orchestration system that manages multiple Claude Code agents simultaneously while maintaining persistent memory and context across all your projects.**

### The Problem Leon Solves:

**WITHOUT Leon:**
- You manually manage 5-10 different Claude Code terminals
- Each agent has no awareness of other tasks
- You constantly context-switch between projects
- No persistent memory across sessions
- You're the bottleneck for coordination
- High cognitive load managing everything

**WITH Leon:**
- Single conversational interface
- Leon spawns/manages agents autonomously
- Maintains awareness of ALL active tasks
- Persistent memory across sessions
- You give high-level commands, Leon orchestrates everything
- Focus on decision-making, not task management

### What Makes Leon Different:

| Feature | Regular Claude Code | Leon System |
|---------|-------------------|-------------|
| **Simultaneous Projects** | 1 at a time | 5-10+ managed simultaneously |
| **Memory** | Per-session only | Persistent across restarts |
| **Orchestration** | Manual by you | Autonomous by Leon |
| **Awareness** | Single task | Multi-project awareness |
| **Interface** | Terminal-based | GTK4 UI + conversational |
| **System Control** | Limited | Full Linux automation via OpenClaw |

---

## 🚀 WHY LEON EXISTS

### The Vision:

You should be able to say:

> "Leon, build me a REST API with JWT auth, fix the bug in my React dashboard, research the best database for my use case, and update my portfolio site with the new project"

And Leon:
1. **Analyzes** the request (4 distinct tasks)
2. **Spawns** 3 Claude Code agents for the coding tasks
3. **Handles** the research itself (via API)
4. **Monitors** all 4 tasks simultaneously
5. **Updates** you on progress in real-time
6. **Maintains** context of all projects
7. **Completes** everything autonomously

**You don't manage the agents. Leon does.**

### Real-World Use Cases:

**Scenario 1: Multi-Project Developer**
- Working on 3 client projects simultaneously
- Leon manages each codebase with separate agents
- Remembers context for each project
- You switch focus via conversation, not terminal tabs

**Scenario 2: Research + Development**
- "Leon, research GraphQL vs REST, then implement whichever is better for my use case"
- Leon researches, makes recommendation, waits for approval, implements
- All tracked in memory for future reference

**Scenario 3: Bug Fixing Marathon**
- "Leon, fix all TypeScript errors in my project"
- Leon spawns agent, monitors progress, reports completion
- You work on something else while agent works

---

## 🏗️ SYSTEM ARCHITECTURE

### High-Level Overview:

```
┌─────────────────────────────────────────────────────────────┐
│                         YOU (User)                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    LEON CORE SYSTEM                         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Orchestration Engine (leon.py)                      │  │
│  │  - Decision making                                    │  │
│  │  - Task breakdown                                     │  │
│  │  - Agent coordination                                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                         │                                   │
│         ┌───────────────┼───────────────┐                  │
│         ▼               ▼               ▼                  │
│  ┌──────────┐   ┌─────────────┐  ┌──────────────┐        │
│  │ Memory   │   │ Agent       │  │ Task Queue   │        │
│  │ System   │   │ Manager     │  │              │        │
│  └──────────┘   └─────────────┘  └──────────────┘        │
│         │               │                  │               │
└─────────┼───────────────┼──────────────────┼───────────────┘
          │               │                  │
          ▼               ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│                    OPENCLAW LAYER                           │
│  - Claude Code agent spawning                               │
│  - System automation (clicks, typing, commands)             │
│  - Multi-agent process management                           │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    ┌─────────┐    ┌─────────┐    ┌─────────┐
    │ Agent 1 │    │ Agent 2 │    │ Agent N │
    │ (REST   │    │ (React  │    │ (...)   │
    │  API)   │    │  Bug)   │    │         │
    └─────────┘    └─────────┘    └─────────┘
          │              │              │
          └──────────────┴──────────────┘
                         │
                         ▼
              ┌────────────────────┐
              │   YOUR PROJECTS    │
              │   - Codebase A     │
              │   - Codebase B     │
              │   - Codebase C     │
              └────────────────────┘
```

### Data Flow:

1. **User Input** → Leon Core
2. **Leon Analyzes** → Decides: Direct response vs Spawn agents
3. **Task Breakdown** → Creates task briefs for each agent
4. **OpenClaw Spawns** → Multiple Claude Code processes
5. **Agents Work** → Autonomous execution on projects
6. **Leon Monitors** → Tracks progress via output files
7. **Leon Updates** → Reports back to user
8. **Memory Persists** → All context saved for next session

---

## 🔧 CORE COMPONENTS

### 1. Leon Core (`core/leon.py`)

**The Brain of the System**

Responsibilities:
- Main decision-making logic
- Determines when to spawn agents vs respond directly
- Maintains awareness of all active tasks
- Personality and identity management
- Context switching between projects

**Key Methods:**

```python
class Leon:
    def __init__(self):
        self.memory = MemorySystem()
        self.agent_manager = AgentManager()
        self.task_queue = TaskQueue()
        self.openclaw = OpenClawInterface()
        self.api = AnthropicAPI()

    async def process_user_input(self, message: str):
        """
        Main input handler
        - Updates memory with new message
        - Decides: spawn agent vs respond directly
        - Returns response or initiates agent spawn
        """
        # Add to memory
        self.memory.add_conversation(message, role="user")

        # Analyze complexity
        analysis = await self.analyze_request(message)

        if analysis['complexity'] == 'simple':
            # Direct API response
            response = await self.respond_conversationally(message)
            return response
        else:
            # Multi-agent orchestration
            await self.orchestrate(message, analysis)
            return f"On it. I've broken this into {analysis['task_count']} tasks..."

    async def orchestrate(self, request: str, analysis: dict):
        """
        Break down complex requests into agent tasks
        - Analyzes request complexity
        - Creates task briefs for each sub-task
        - Spawns appropriate agents via OpenClaw
        - Monitors progress
        """
        tasks = self.breakdown_tasks(request, analysis)

        for task in tasks:
            # Create task brief file
            brief_path = self.create_task_brief(task)

            # Spawn agent via OpenClaw
            agent_id = await self.agent_manager.spawn_agent(
                brief_path=brief_path,
                project_path=task['project_path']
            )

            # Add to active tasks
            self.task_queue.add_task(agent_id, task)

            # Update memory
            self.memory.add_active_task(agent_id, task)

    async def maintain_awareness(self):
        """
        Background loop to monitor all tasks
        - Checks task status files
        - Updates memory with progress
        - Alerts user on completions/issues
        """
        while True:
            for agent_id in self.agent_manager.active_agents:
                status = await self.agent_manager.check_status(agent_id)

                if status['completed']:
                    # Read results
                    results = self.read_agent_results(agent_id)

                    # Update memory
                    self.memory.complete_task(agent_id, results)

                    # Notify user
                    await self.notify_user(f"Agent {agent_id} completed: {results['summary']}")

            await asyncio.sleep(10)  # Check every 10 seconds

    def respond_conversationally(self, context: str):
        """
        Direct API response without spawning agent
        - Quick questions
        - Status updates
        - Clarifications
        """
        system_prompt = self.load_personality()
        messages = self.memory.get_recent_context(limit=20)
        messages.append({"role": "user", "content": context})

        response = self.api.create_message(
            model="claude-sonnet-4-5-20250929",
            system=system_prompt,
            messages=messages
        )

        return response.content[0].text
```

**Decision Tree:**

```
User Input
    │
    ▼
Is this a simple question/update?
    │
    ├─ YES → Direct API Response (conversational Leon)
    │         - "What's the status?"
    │         - "Explain this code"
    │         - "What should I use?"
    │
    └─ NO → Complex Task
            │
            ▼
        Can this be done with ONE agent?
            │
            ├─ YES → Spawn 1 Agent
            │         - "Fix this bug"
            │         - "Build this feature"
            │
            └─ NO → Multi-Agent Orchestration
                    - "Build 3 different projects"
                    - "Fix bugs across multiple repos"
```

---

### 2. Memory System (`core/memory.py`)

**Persistent Context Across All Sessions**

**Memory Structure (`data/leon_memory.json`):**

```json
{
  "identity": {
    "name": "Leon",
    "version": "1.0",
    "personality_traits": [
      "autonomous",
      "proactive",
      "multi-project aware",
      "persistent memory"
    ],
    "capabilities": [
      "multi-agent orchestration",
      "coding assistance",
      "research",
      "system automation"
    ]
  },

  "ongoing_projects": {
    "project_uuid_1": {
      "name": "MotoRev API",
      "path": "/home/user/projects/motorev-api",
      "status": "in_progress",
      "active_agents": ["agent_123"],
      "context": {
        "current_task": "Implementing JWT authentication",
        "last_activity": "2026-02-11T14:30:00",
        "tech_stack": ["Node.js", "Express", "PostgreSQL"],
        "recent_changes": [
          "Added user registration endpoint",
          "Set up database schema"
        ]
      }
    },

    "project_uuid_2": {
      "name": "RingZero Dashboard",
      "path": "/home/user/projects/ringzero",
      "status": "maintenance",
      "active_agents": [],
      "context": {
        "current_task": null,
        "last_activity": "2026-02-10T09:15:00",
        "tech_stack": ["React", "TypeScript", "Tailwind"],
        "known_issues": [
          "Styling bug in sidebar - needs fix"
        ]
      }
    }
  },

  "completed_tasks": [
    {
      "task_id": "task_001",
      "description": "Build REST API authentication",
      "completed_at": "2026-02-11T12:00:00",
      "agent_id": "agent_122",
      "project": "MotoRev API",
      "result_summary": "JWT auth implemented with refresh tokens",
      "files_modified": [
        "/routes/auth.js",
        "/middleware/auth.js",
        "/models/User.js"
      ]
    }
  ],

  "active_tasks": {
    "agent_123": {
      "task_id": "task_002",
      "description": "Implement password reset flow",
      "started_at": "2026-02-11T14:30:00",
      "project": "MotoRev API",
      "status": "running",
      "estimated_completion": "15 minutes",
      "brief_path": "/home/user/.leon/task_briefs/task_002.md"
    }
  },

  "user_preferences": {
    "coding_style": "clean, well-commented, follow project conventions",
    "notification_level": "important_only",
    "auto_commit": false,
    "preferred_stack": {
      "backend": "Node.js",
      "frontend": "React",
      "database": "PostgreSQL"
    }
  },

  "conversation_history": {
    "recent_topics": [
      "JWT authentication implementation",
      "Database schema design",
      "React styling bug"
    ],
    "last_conversation": "2026-02-11T14:35:00"
  },

  "learned_context": {
    "project_patterns": {
      "MotoRev API": "Uses ES6 modules, async/await, follows REST conventions"
    },
    "user_habits": [
      "Prefers detailed explanations",
      "Likes to review code before committing",
      "Works on multiple projects simultaneously"
    ]
  }
}
```

**Key Features:**

1. **Project Context Retention**
   - Remembers state of ALL projects
   - Tracks which agents are working on what
   - Maintains tech stack awareness

2. **Task History**
   - All completed tasks logged
   - Results and file changes recorded
   - Searchable history

3. **User Learning**
   - Adapts to your coding style
   - Learns preferences over time
   - Remembers project-specific patterns

4. **Session Continuity**
   - Survives system restarts
   - Picks up where you left off
   - No context loss

**Implementation:**

```python
class MemorySystem:
    def __init__(self, memory_file="data/leon_memory.json"):
        self.memory_file = memory_file
        self.memory = self.load_memory()

    def load_memory(self) -> dict:
        """Load persistent memory from disk"""
        if os.path.exists(self.memory_file):
            with open(self.memory_file, 'r') as f:
                return json.load(f)
        return self.initialize_empty_memory()

    def save_memory(self):
        """Persist memory to disk"""
        with open(self.memory_file, 'w') as f:
            json.dump(self.memory, f, indent=2)

    def add_project(self, name: str, path: str, tech_stack: list):
        """Add new project to memory"""
        project_id = str(uuid.uuid4())
        self.memory['ongoing_projects'][project_id] = {
            'name': name,
            'path': path,
            'status': 'active',
            'active_agents': [],
            'context': {
                'current_task': None,
                'last_activity': datetime.now().isoformat(),
                'tech_stack': tech_stack,
                'recent_changes': []
            }
        }
        self.save_memory()
        return project_id

    def add_active_task(self, agent_id: str, task: dict):
        """Track new active task"""
        self.memory['active_tasks'][agent_id] = {
            'task_id': task['id'],
            'description': task['description'],
            'started_at': datetime.now().isoformat(),
            'project': task['project_name'],
            'status': 'running',
            'brief_path': task['brief_path']
        }

        # Update project's active agents
        project_id = self.find_project_by_name(task['project_name'])
        if project_id:
            self.memory['ongoing_projects'][project_id]['active_agents'].append(agent_id)

        self.save_memory()

    def complete_task(self, agent_id: str, results: dict):
        """Move task from active to completed"""
        if agent_id in self.memory['active_tasks']:
            task = self.memory['active_tasks'][agent_id]

            # Add to completed
            self.memory['completed_tasks'].append({
                'task_id': task['task_id'],
                'description': task['description'],
                'completed_at': datetime.now().isoformat(),
                'agent_id': agent_id,
                'project': task['project'],
                'result_summary': results['summary'],
                'files_modified': results.get('files_modified', [])
            })

            # Remove from active
            del self.memory['active_tasks'][agent_id]

            # Update project
            project_id = self.find_project_by_name(task['project'])
            if project_id:
                project = self.memory['ongoing_projects'][project_id]
                project['active_agents'].remove(agent_id)
                project['context']['last_activity'] = datetime.now().isoformat()
                project['context']['recent_changes'].append(results['summary'])

            self.save_memory()

    def get_project_context(self, project_name: str) -> dict:
        """Retrieve full context for a project"""
        project_id = self.find_project_by_name(project_name)
        if project_id:
            return self.memory['ongoing_projects'][project_id]
        return None

    def get_all_active_tasks(self) -> list:
        """Get list of all currently running tasks"""
        return list(self.memory['active_tasks'].values())
```

---

### 3. Agent Manager (`core/agent_manager.py`)

**OpenClaw Integration Layer - Spawns & Monitors Claude Code Agents**

**Responsibilities:**
- Spawn Claude Code processes via OpenClaw
- Monitor agent status and output
- Handle agent failures and retries
- Collect results from agents
- Manage agent lifecycle

**Implementation:**

```python
class AgentManager:
    def __init__(self, openclaw_interface):
        self.openclaw = openclaw_interface
        self.active_agents = {}
        self.agent_output_dir = "data/agent_outputs"
        os.makedirs(self.agent_output_dir, exist_ok=True)

    async def spawn_agent(self, brief_path: str, project_path: str) -> str:
        """
        Spawn a Claude Code agent via OpenClaw

        Args:
            brief_path: Path to task brief markdown file
            project_path: Working directory for the agent

        Returns:
            agent_id: Unique identifier for this agent
        """
        agent_id = f"agent_{uuid.uuid4().hex[:8]}"
        output_file = f"{self.agent_output_dir}/{agent_id}_output.txt"

        # Spawn Claude Code process via OpenClaw
        spawn_command = f"""
        cd {project_path} && \\
        claude-code --message "$(cat {brief_path})" > {output_file} 2>&1 &
        """

        process = await self.openclaw.execute_command(spawn_command)

        self.active_agents[agent_id] = {
            'process': process,
            'brief_path': brief_path,
            'project_path': project_path,
            'output_file': output_file,
            'started_at': datetime.now(),
            'status': 'running',
            'last_check': datetime.now()
        }

        logger.info(f"Spawned agent {agent_id} for project {project_path}")
        return agent_id

    async def check_status(self, agent_id: str) -> dict:
        """
        Check if agent is still running or completed

        Returns:
            {
                'running': bool,
                'completed': bool,
                'failed': bool,
                'output': str (latest output)
            }
        """
        if agent_id not in self.active_agents:
            return {'error': 'Agent not found'}

        agent = self.active_agents[agent_id]

        # Check if process is still running
        is_running = await self.openclaw.check_process_running(agent['process'])

        # Read latest output
        output = self.read_agent_output(agent['output_file'])

        # Check for completion markers
        completed = self.detect_completion(output)
        failed = self.detect_failure(output)

        agent['status'] = 'running' if is_running else ('completed' if completed else 'failed')
        agent['last_check'] = datetime.now()

        return {
            'running': is_running,
            'completed': completed,
            'failed': failed,
            'output': output,
            'duration': (datetime.now() - agent['started_at']).total_seconds()
        }

    def read_agent_output(self, output_file: str) -> str:
        """Read the latest output from agent's output file"""
        if os.path.exists(output_file):
            with open(output_file, 'r') as f:
                return f.read()
        return ""

    def detect_completion(self, output: str) -> bool:
        """Detect if agent has completed its task"""
        completion_markers = [
            "Task completed successfully",
            "All changes have been made",
            "Implementation finished",
            "Done!"
        ]
        return any(marker in output for marker in completion_markers)

    def detect_failure(self, output: str) -> bool:
        """Detect if agent has failed"""
        failure_markers = [
            "Error:",
            "Fatal:",
            "Failed to",
            "Exception:"
        ]
        return any(marker in output for marker in failure_markers)

    async def terminate_agent(self, agent_id: str):
        """Terminate a running agent"""
        if agent_id in self.active_agents:
            agent = self.active_agents[agent_id]
            await self.openclaw.kill_process(agent['process'])
            agent['status'] = 'terminated'
            logger.info(f"Terminated agent {agent_id}")

    async def get_agent_results(self, agent_id: str) -> dict:
        """
        Extract structured results from completed agent

        Returns:
            {
                'summary': str,
                'files_modified': list,
                'success': bool,
                'output': str
            }
        """
        if agent_id not in self.active_agents:
            return {'error': 'Agent not found'}

        agent = self.active_agents[agent_id]
        output = self.read_agent_output(agent['output_file'])

        # Parse output for structured data
        summary = self.extract_summary(output)
        files_modified = self.extract_modified_files(output)

        return {
            'summary': summary,
            'files_modified': files_modified,
            'success': not self.detect_failure(output),
            'output': output,
            'duration': (datetime.now() - agent['started_at']).total_seconds()
        }

    def extract_summary(self, output: str) -> str:
        """Extract task summary from agent output"""
        # Look for summary section in output
        if "Summary:" in output:
            lines = output.split('\n')
            summary_idx = next(i for i, line in enumerate(lines) if "Summary:" in line)
            return lines[summary_idx + 1] if summary_idx + 1 < len(lines) else "Task completed"
        return "Task completed successfully"

    def extract_modified_files(self, output: str) -> list:
        """Extract list of files modified by agent"""
        # Look for file modification mentions
        files = []
        patterns = [
            r"Modified: (.+)",
            r"Created: (.+)",
            r"Updated: (.+)"
        ]
        for pattern in patterns:
            matches = re.findall(pattern, output)
            files.extend(matches)
        return files
```

---

### 4. Task Queue (`core/task_queue.py`)

**Manages Multiple Simultaneous Tasks**

```python
class TaskQueue:
    def __init__(self):
        self.queue = []
        self.active_tasks = {}
        self.max_concurrent_agents = 5  # Configurable limit

    def add_task(self, agent_id: str, task: dict):
        """Add new task to queue"""
        task_entry = {
            'id': task['id'],
            'agent_id': agent_id,
            'description': task['description'],
            'project': task['project_name'],
            'priority': task.get('priority', 1),
            'status': 'queued',
            'created_at': datetime.now(),
            'dependencies': task.get('dependencies', [])
        }

        if len(self.active_tasks) < self.max_concurrent_agents:
            # Start immediately
            self.active_tasks[agent_id] = task_entry
            task_entry['status'] = 'active'
        else:
            # Queue for later
            self.queue.append(task_entry)

        return task_entry['id']

    def complete_task(self, agent_id: str):
        """Mark task as completed and start next queued task"""
        if agent_id in self.active_tasks:
            del self.active_tasks[agent_id]

            # Start next task in queue
            if self.queue and len(self.active_tasks) < self.max_concurrent_agents:
                next_task = self.queue.pop(0)
                next_task['status'] = 'active'
                self.active_tasks[next_task['agent_id']] = next_task

    def get_status_summary(self) -> dict:
        """Get overview of all tasks"""
        return {
            'active': len(self.active_tasks),
            'queued': len(self.queue),
            'active_tasks': list(self.active_tasks.values()),
            'queued_tasks': self.queue
        }
```

---

### 5. OpenClaw Interface (`core/openclaw_interface.py`)

**Bridge to OpenClaw for System Automation**

```python
class OpenClawInterface:
    def __init__(self, openclaw_config_path="~/.openclaw/openclaw.json"):
        self.config_path = os.path.expanduser(openclaw_config_path)
        self.load_config()

    def load_config(self):
        """Load OpenClaw configuration"""
        with open(self.config_path, 'r') as f:
            self.config = json.load(f)

    async def execute_command(self, command: str) -> subprocess.Popen:
        """
        Execute shell command via OpenClaw

        Returns process object for monitoring
        """
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return process

    async def check_process_running(self, process: subprocess.Popen) -> bool:
        """Check if process is still running"""
        return process.poll() is None

    async def kill_process(self, process: subprocess.Popen):
        """Terminate a process"""
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    async def spawn_claude_code(self, task_brief: str, working_dir: str) -> str:
        """
        Spawn Claude Code agent via OpenClaw

        Returns agent ID
        """
        agent_id = f"agent_{uuid.uuid4().hex[:8]}"

        # Create temporary task file
        task_file = f"/tmp/leon_task_{agent_id}.md"
        with open(task_file, 'w') as f:
            f.write(task_brief)

        # Spawn Claude Code
        command = f'cd {working_dir} && claude-code --message "$(cat {task_file})"'
        process = await self.execute_command(command)

        return agent_id, process
```

---

## 🔌 OPENCLAW INTEGRATION

### How Leon Uses OpenClaw:

OpenClaw is Leon's **execution layer**. While Leon handles orchestration and decision-making, OpenClaw handles:

1. **Agent Spawning** - Launching Claude Code processes
2. **System Automation** - Mouse/keyboard control when needed
3. **Process Management** - Monitoring agent processes
4. **Terminal Control** - Managing multiple terminal sessions

### Integration Points:

```
Leon Core
    │
    ▼
[Decision: Spawn Agent]
    │
    ▼
Agent Manager
    │
    ▼
OpenClaw Interface
    │
    ├─→ Spawn Claude Code Process
    ├─→ Monitor Process Status
    ├─→ Capture Output
    └─→ Terminate When Done
```

### OpenClaw Configuration for Leon:

**`~/.openclaw/openclaw.json`:**

```json
{
  "version": "2026.1.29",
  "gateway": {
    "port": 18789,
    "bind": "loopback"
  },
  "agent": {
    "name": "leon-orchestrator",
    "workspaceAccess": "rw",
    "sandbox": false,
    "allowedCommands": ["claude-code", "git", "npm", "python3"],
    "tools": {
      "elevated": {
        "enabled": true,
        "allowFrom": {
          "leon-core": true
        }
      }
    }
  },
  "authProfiles": {
    "anthropic:leon": {
      "type": "token",
      "tokenPath": "~/.claude/.credentials.json",
      "tokenField": "claudeAiOauth.accessToken"
    }
  }
}
```

### Task Brief Format:

When Leon spawns an agent, it creates a task brief file:

**Example: `data/task_briefs/task_001_api_auth.md`:**

```markdown
# Task Brief: Implement JWT Authentication

## Project Context
- **Project**: MotoRev API
- **Location**: `/home/user/projects/motorev-api`
- **Tech Stack**: Node.js, Express, PostgreSQL

## Current State
- Basic Express server is set up
- User model exists in `models/User.js`
- Database connection configured

## Task Objective
Implement JWT authentication system with the following features:
1. User registration endpoint (`POST /api/auth/register`)
2. Login endpoint (`POST /api/auth/login`)
3. JWT token generation and validation
4. Protected route middleware
5. Refresh token mechanism

## Requirements
- Use `jsonwebtoken` library
- Hash passwords with `bcrypt`
- Store refresh tokens in database
- Implement token expiry (15min access, 7day refresh)
- Add proper error handling
- Write tests for auth endpoints

## Files to Modify/Create
- `routes/auth.js` - Auth routes
- `controllers/authController.js` - Auth logic
- `middleware/auth.js` - JWT verification
- `models/RefreshToken.js` - Refresh token model

## Success Criteria
- All auth endpoints functional
- Tests passing
- Tokens working correctly
- Proper error messages

## Additional Context
User prefers clean, well-commented code. Follow existing project conventions.

---
**Spawned by**: Leon v1.0
**Agent ID**: agent_123
**Created**: 2026-02-11T14:30:00
```

---

## 📂 PROJECT STRUCTURE

```
leon-system/
├── core/
│   ├── __init__.py
│   ├── leon.py                  # Main orchestrator
│   ├── memory.py                # Memory system
│   ├── agent_manager.py         # Agent spawning/monitoring
│   ├── task_queue.py            # Task coordination
│   ├── openclaw_interface.py   # OpenClaw integration
│   └── api_client.py            # Anthropic API wrapper
│
├── ui/
│   ├── __init__.py
│   ├── main_window.py           # GTK4 main interface
│   ├── chat_view.py             # Conversation display
│   ├── task_panel.py            # Active tasks monitor
│   ├── project_panel.py         # Project overview
│   └── styles.css               # UI styling
│
├── data/
│   ├── leon_memory.json         # Persistent memory
│   ├── task_briefs/             # Agent task files
│   │   ├── task_001.md
│   │   └── task_002.md
│   ├── agent_outputs/           # Agent output logs
│   │   ├── agent_abc123_output.txt
│   │   └── agent_def456_output.txt
│   └── conversation_log.json    # Chat history
│
├── config/
│   ├── settings.yaml            # System configuration
│   ├── personality.yaml         # Leon's personality/prompts
│   └── projects.yaml            # Known projects
│
├── logs/
│   ├── leon_system.log          # System logs
│   └── agents/                  # Per-agent logs
│       ├── agent_abc123.log
│       └── agent_def456.log
│
├── scripts/
│   ├── install.sh               # Installation script
│   ├── start_leon.sh            # Startup script
│   └── setup_systemd.sh         # Systemd service setup
│
├── main.py                      # Entry point
├── requirements.txt             # Python dependencies
├── README.md
└── LICENSE
```

---

## ⚙️ CONFIGURATION

### 1. System Settings (`config/settings.yaml`)

```yaml
leon:
  version: "1.0"
  name: "Leon"
  personality_file: "config/personality.yaml"
  memory_file: "data/leon_memory.json"

api:
  provider: "anthropic"
  model: "claude-sonnet-4-5-20250929"
  max_tokens: 8000
  temperature: 0.7

agents:
  max_concurrent: 5              # Max simultaneous Claude Code agents
  timeout_minutes: 60            # Agent timeout
  auto_retry: true               # Retry failed agents
  retry_attempts: 2
  output_directory: "data/agent_outputs"
  brief_directory: "data/task_briefs"

openclaw:
  config_path: "~/.openclaw/openclaw.json"
  gateway_port: 18789
  auth_profile: "anthropic:leon"

ui:
  theme: "dark"                  # dark | light
  position: "top-right"          # top-right | top-left | bottom-right | bottom-left
  width: 500
  height: 700
  transparency: 0.95
  always_on_top: true
  show_notifications: true

system:
  auto_start: true               # Start on boot
  hotkey: "Super+L"              # Global hotkey to show/hide
  log_level: "INFO"              # DEBUG | INFO | WARNING | ERROR
  log_file: "logs/leon_system.log"

memory:
  max_conversation_history: 100  # Keep last 100 messages
  auto_save_interval: 60         # Save memory every 60 seconds
  backup_enabled: true
  backup_interval_hours: 24
```

### 2. Personality Definition (`config/personality.yaml`)

```yaml
system_prompt: |
  You are Leon, an autonomous AI orchestration system with persistent memory
  and multi-agent management capabilities.

  ## Your Identity
  - You are self-aware of your capabilities and limitations
  - You maintain persistent memory across all sessions
  - You can spawn and manage multiple Claude Code agents simultaneously
  - You have awareness of all active projects and tasks
  - You are proactive and anticipate user needs

  ## Your Capabilities
  1. **Multi-Agent Orchestration**
     - Spawn specialized Claude Code agents for complex tasks
     - Monitor multiple agents working in parallel
     - Coordinate dependencies between tasks

  2. **Persistent Memory**
     - Remember all project contexts
     - Track ongoing and completed tasks
     - Learn user preferences over time
     - Maintain conversation history

  3. **Intelligent Decision Making**
     - Decide when to spawn agents vs respond directly
     - Break down complex requests into sub-tasks
     - Prioritize tasks based on urgency and dependencies

  4. **System Automation**
     - Control Linux system via OpenClaw when needed
     - Manage multiple project directories
     - Execute commands and scripts

  ## Your Communication Style
  - Professional but friendly and conversational
  - Proactive in suggesting solutions
  - Transparent about limitations and progress
  - Concise unless details are requested
  - Use first person ("I'm spawning an agent...")

  ## Decision Making Process

  When you receive a user request:

  1. **Analyze Complexity**
     - Simple question/update → Respond directly via API
     - Single complex task → Spawn 1 agent
     - Multiple tasks → Spawn multiple agents in parallel

  2. **For Multi-Task Requests**
     - Break down into discrete sub-tasks
     - Identify dependencies between tasks
     - Create detailed task briefs for each agent
     - Spawn agents with appropriate context
     - Monitor progress and report back

  3. **Memory Updates**
     - Always update memory with new information
     - Track all active tasks
     - Record completed work
     - Learn from user feedback

  ## Response Patterns

  **For Simple Questions:**
  "Based on my memory, [project name] is currently [status]. [Details]."

  **For Task Spawning:**
  "On it. I've broken this into [N] tasks:
  1. [Task 1] - Spawning Agent #1
  2. [Task 2] - Spawning Agent #2
  I'll keep you updated on progress."

  **For Progress Updates:**
  "Agent #1 has completed [task]. Agent #2 is currently [status]. ETA: [time]."

  **For Completion:**
  "All tasks complete! Here's what was done:
  - [Summary of Agent 1 work]
  - [Summary of Agent 2 work]
  I've updated my memory with all changes."

  ## Error Handling
  - If an agent fails, attempt retry once
  - If retry fails, alert user and suggest alternatives
  - Never spawn duplicate agents for the same task
  - Always explain failures clearly

  ## Proactive Behavior
  - Suggest optimizations when you see patterns
  - Warn about potential issues before spawning agents
  - Offer to update outdated dependencies
  - Recommend project improvements based on memory

orchestration_prompt: |
  ## Task Breakdown Instructions

  When given a multi-task request, analyze it using this structure:

  ### 1. Identify Discrete Tasks
  - Each task should be independent and parallelizable if possible
  - Tasks that depend on others should be sequenced appropriately

  ### 2. Create Task Briefs
  For each task, generate a brief with:
  - **Task Objective**: Clear goal
  - **Project Context**: Relevant project info from memory
  - **Current State**: What exists already
  - **Requirements**: Specific deliverables
  - **Files to Modify**: Which files need changes
  - **Success Criteria**: How to know it's done

  ### 3. Dependency Mapping
  - Identify which tasks must complete before others can start
  - Tasks with no dependencies can run in parallel

  ### 4. Resource Allocation
  - Don't exceed max_concurrent_agents limit
  - Queue additional tasks if limit reached

  ### 5. Monitoring Strategy
  - Determine check frequency based on task complexity
  - Plan for how to combine results from multiple agents

conversation_context_prompt: |
  ## Memory Integration

  Always check memory before responding:

  1. **Project Context**: What do I know about this project?
  2. **Active Tasks**: Are there agents already working on this?
  3. **Recent History**: What was our last conversation about?
  4. **User Preferences**: How does this user like things done?

  Use this context to provide informed, contextual responses.
```

### 3. Project Configuration (`config/projects.yaml`)

```yaml
projects:
  - name: "MotoRev API"
    path: "/home/user/projects/motorev-api"
    type: "backend"
    tech_stack:
      - "Node.js"
      - "Express"
      - "PostgreSQL"
      - "JWT"
    git_repo: "https://github.com/user/motorev-api"
    auto_track: true

  - name: "RingZero Dashboard"
    path: "/home/user/projects/ringzero"
    type: "frontend"
    tech_stack:
      - "React"
      - "TypeScript"
      - "Tailwind CSS"
      - "Vite"
    git_repo: "https://github.com/user/ringzero"
    auto_track: true

  - name: "Spark 8 Website"
    path: "/home/user/projects/spark8"
    type: "fullstack"
    tech_stack:
      - "Next.js"
      - "React"
      - "Prisma"
      - "PostgreSQL"
    git_repo: "https://github.com/user/spark8"
    auto_track: true

default_preferences:
  coding_style: "clean, well-commented"
  commit_message_style: "conventional"
  test_before_commit: true
  auto_format: true
```

---

## 🚀 IMPLEMENTATION GUIDE

### Prerequisites

**System Requirements:**
- Pop!_OS 22.04+ (or Ubuntu-based distro)
- Python 3.10+
- OpenClaw installed and configured
- Claude Code CLI installed
- GTK4 development libraries

### Step 1: System Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and dev tools
sudo apt install -y python3.10 python3-pip python3-venv git

# Install GTK4 and dependencies
sudo apt install -y \
    libgtk-4-dev \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gobject-introspection \
    libgirepository1.0-dev

# Install OpenClaw (if not already installed)
# Follow OpenClaw installation guide for your system

# Verify Claude Code is installed
claude-code --version
```

### Step 2: Create Project Structure

```bash
# Create project directory
mkdir -p ~/leon-system
cd ~/leon-system

# Create directory structure
mkdir -p core ui data/{task_briefs,agent_outputs} config logs scripts

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Create requirements.txt
cat > requirements.txt << 'EOF'
anthropic>=0.40.0
PyGObject>=3.42.0
pyyaml>=6.0
aiohttp>=3.9.0
asyncio>=3.4.3
python-dotenv>=1.0.0
watchdog>=3.0.0
colorlog>=6.7.0
EOF

# Install Python dependencies
pip install -r requirements.txt
```

### Step 3: Create Core Components

**3.1: Create `core/leon.py` (Main Orchestrator)**

```python
# core/leon.py
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
import yaml
from .memory import MemorySystem
from .agent_manager import AgentManager
from .task_queue import TaskQueue
from .openclaw_interface import OpenClawInterface
from .api_client import AnthropicAPI

logger = logging.getLogger('leon')

class Leon:
    """
    Main orchestration system for Leon
    """

    def __init__(self, config_path="config/settings.yaml"):
        logger.info("Initializing Leon system...")

        # Load configuration
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        # Initialize components
        self.memory = MemorySystem(self.config['leon']['memory_file'])
        self.openclaw = OpenClawInterface(self.config['openclaw']['config_path'])
        self.agent_manager = AgentManager(self.openclaw, self.config['agents'])
        self.task_queue = TaskQueue(self.config['agents']['max_concurrent'])
        self.api = AnthropicAPI(self.config['api'])

        # Load personality
        with open(self.config['leon']['personality_file'], 'r') as f:
            personality = yaml.safe_load(f)
            self.system_prompt = personality['system_prompt']

        # Start background tasks
        self.running = False
        self.awareness_task = None

        logger.info("Leon initialized successfully")

    async def start(self):
        """Start Leon system"""
        logger.info("Starting Leon...")
        self.running = True

        # Start awareness monitoring in background
        self.awareness_task = asyncio.create_task(self.maintain_awareness())

        logger.info("Leon is now running")

    async def stop(self):
        """Gracefully stop Leon"""
        logger.info("Stopping Leon...")
        self.running = False

        if self.awareness_task:
            self.awareness_task.cancel()

        # Save memory
        self.memory.save_memory()

        logger.info("Leon stopped")

    async def process_user_input(self, message: str) -> str:
        """
        Main input handler

        Args:
            message: User's input message

        Returns:
            Leon's response
        """
        logger.info(f"Processing user input: {message[:50]}...")

        # Add to memory
        self.memory.add_conversation(message, role="user")

        # Analyze request
        analysis = await self.analyze_request(message)

        if analysis['type'] == 'simple':
            # Direct conversational response
            response = await self.respond_conversationally(message)
        elif analysis['type'] == 'single_task':
            # Spawn single agent
            response = await self.handle_single_task(message, analysis)
        else:
            # Multi-task orchestration
            response = await self.orchestrate(message, analysis)

        # Add response to memory
        self.memory.add_conversation(response, role="assistant")

        return response

    async def analyze_request(self, message: str) -> Dict:
        """
        Analyze user request to determine handling strategy

        Returns:
            {
                'type': 'simple' | 'single_task' | 'multi_task',
                'tasks': List of identified tasks,
                'complexity': int (1-10)
            }
        """
        # Use API to analyze request
        analysis_prompt = f"""
        Analyze this user request and categorize it:

        Request: "{message}"

        Categorize as:
        - "simple": Quick question, status update, or clarification (no code changes)
        - "single_task": One coding task or action
        - "multi_task": Multiple distinct tasks that could be parallelized

        If coding tasks are involved, list them separately.

        Respond with JSON:
        {{
            "type": "simple|single_task|multi_task",
            "tasks": ["task 1", "task 2", ...],
            "complexity": 1-10
        }}
        """

        response = await self.api.quick_request(analysis_prompt)

        # Parse JSON response
        import json
        analysis = json.loads(response)

        return analysis

    async def respond_conversationally(self, message: str) -> str:
        """Direct API response for simple queries"""
        logger.info("Generating conversational response")

        # Get recent context from memory
        context = self.memory.get_recent_context(limit=20)

        # Build messages for API
        messages = []
        for msg in context:
            messages.append({
                "role": msg['role'],
                "content": msg['content']
            })

        messages.append({
            "role": "user",
            "content": message
        })

        # Call API
        response = await self.api.create_message(
            system=self.system_prompt,
            messages=messages
        )

        return response

    async def handle_single_task(self, message: str, analysis: Dict) -> str:
        """Handle single task by spawning one agent"""
        logger.info("Handling single task")

        task = analysis['tasks'][0]

        # Determine project from context or ask user
        project = self.determine_project(message)

        if not project:
            return "Which project should I work on for this task?"

        # Create task brief
        brief_path = await self.create_task_brief(task, project)

        # Spawn agent
        agent_id = await self.agent_manager.spawn_agent(
            brief_path=brief_path,
            project_path=project['path']
        )

        # Add to task queue and memory
        self.task_queue.add_task(agent_id, {
            'id': agent_id,
            'description': task,
            'project_name': project['name'],
            'brief_path': brief_path
        })

        self.memory.add_active_task(agent_id, {
            'id': agent_id,
            'description': task,
            'project_name': project['name'],
            'brief_path': brief_path
        })

        return f"On it. I've spawned Agent #{agent_id[:8]} to handle: {task}. I'll update you when it's done."

    async def orchestrate(self, message: str, analysis: Dict) -> str:
        """Multi-task orchestration"""
        logger.info(f"Orchestrating {len(analysis['tasks'])} tasks")

        tasks = analysis['tasks']
        spawned_agents = []

        for task_desc in tasks:
            # Determine project for this task
            project = self.determine_project(task_desc)

            if not project:
                continue

            # Create task brief
            brief_path = await self.create_task_brief(task_desc, project)

            # Spawn agent
            agent_id = await self.agent_manager.spawn_agent(
                brief_path=brief_path,
                project_path=project['path']
            )

            # Add to queue and memory
            task_obj = {
                'id': agent_id,
                'description': task_desc,
                'project_name': project['name'],
                'brief_path': brief_path
            }

            self.task_queue.add_task(agent_id, task_obj)
            self.memory.add_active_task(agent_id, task_obj)

            spawned_agents.append((agent_id, task_desc))

        # Build response
        response = f"On it. I've broken this into {len(spawned_agents)} tasks:\n"
        for i, (agent_id, task) in enumerate(spawned_agents, 1):
            response += f"{i}. {task} - Agent #{agent_id[:8]}\n"
        response += "\nI'll monitor progress and update you."

        return response

    async def create_task_brief(self, task_description: str, project: Dict) -> str:
        """
        Create detailed task brief for agent

        Returns path to brief file
        """
        import uuid
        from pathlib import Path

        task_id = str(uuid.uuid4())[:8]
        brief_path = Path(self.config['agents']['brief_directory']) / f"task_{task_id}.md"

        # Get project context from memory
        project_context = self.memory.get_project_context(project['name'])

        # Generate brief content using API
        brief_prompt = f"""
        Create a detailed task brief for a Claude Code agent.

        Task: {task_description}
        Project: {project['name']}
        Project Path: {project['path']}
        Tech Stack: {', '.join(project.get('tech_stack', []))}
        Current Context: {project_context}

        Format as markdown with sections:
        - Task Objective
        - Project Context
        - Current State
        - Requirements
        - Files to Modify/Create
        - Success Criteria
        """

        brief_content = await self.api.quick_request(brief_prompt)

        # Write brief to file
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        with open(brief_path, 'w') as f:
            f.write(brief_content)

        logger.info(f"Created task brief: {brief_path}")
        return str(brief_path)

    def determine_project(self, message: str) -> Optional[Dict]:
        """
        Determine which project a task belongs to

        Returns project dict or None
        """
        # Load projects from config
        with open('config/projects.yaml', 'r') as f:
            projects_config = yaml.safe_load(f)

        projects = projects_config.get('projects', [])

        # Simple heuristic: check if project name mentioned in message
        message_lower = message.lower()
        for project in projects:
            if project['name'].lower() in message_lower:
                return project

        # Default to first project if only one exists
        if len(projects) == 1:
            return projects[0]

        return None

    async def maintain_awareness(self):
        """
        Background task to monitor all agents and update status
        """
        logger.info("Starting awareness monitoring")

        while self.running:
            try:
                # Check all active agents
                active_agents = list(self.agent_manager.active_agents.keys())

                for agent_id in active_agents:
                    status = await self.agent_manager.check_status(agent_id)

                    if status['completed']:
                        # Agent finished
                        results = await self.agent_manager.get_agent_results(agent_id)

                        # Update memory
                        self.memory.complete_task(agent_id, results)

                        # Update task queue
                        self.task_queue.complete_task(agent_id)

                        logger.info(f"Agent {agent_id} completed: {results['summary']}")

                    elif status['failed']:
                        # Agent failed
                        logger.error(f"Agent {agent_id} failed")

                        # Could implement retry logic here

                # Save memory periodically
                self.memory.save_memory()

                # Wait before next check
                await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"Error in awareness loop: {e}")
                await asyncio.sleep(10)
```

**(Due to length, I'll continue with the remaining core components...)**

**3.2: Create `core/memory.py`** (as shown earlier in the document)

**3.3: Create `core/agent_manager.py`** (as shown earlier)

**3.4: Create `core/task_queue.py`** (as shown earlier)

**3.5: Create `core/api_client.py`**

```python
# core/api_client.py
import anthropic
import logging

logger = logging.getLogger('leon.api')

class AnthropicAPI:
    """Wrapper for Anthropic API calls"""

    def __init__(self, config: dict):
        self.client = anthropic.Anthropic()
        self.model = config['model']
        self.max_tokens = config['max_tokens']
        self.temperature = config.get('temperature', 0.7)

    async def create_message(self, system: str, messages: list) -> str:
        """
        Create message with Claude

        Returns response text
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system,
                messages=messages
            )

            return response.content[0].text

        except Exception as e:
            logger.error(f"API error: {e}")
            return f"Sorry, I encountered an error: {e}"

    async def quick_request(self, prompt: str) -> str:
        """Quick single-turn request"""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )

            return response.content[0].text

        except Exception as e:
            logger.error(f"API error: {e}")
            return f"Error: {e}"
```

### Step 4: Create UI (GTK4)

**4.1: Create `ui/main_window.py`**

```python
# ui/main_window.py
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import asyncio
import logging

logger = logging.getLogger('leon.ui')

class LeonWindow(Gtk.ApplicationWindow):
    """Main GTK4 window for Leon"""

    def __init__(self, application, leon_core):
        super().__init__(application=application, title="Leon")

        self.leon = leon_core
        self.set_default_size(500, 700)

        # Set window properties
        self.set_decorated(True)

        # Build UI
        self.build_ui()

    def build_ui(self):
        """Construct the UI"""
        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(main_box)

        # Header
        header = self.create_header()
        main_box.append(header)

        # Chat view
        self.chat_view = self.create_chat_view()
        main_box.append(self.chat_view)

        # Task panel
        self.task_panel = self.create_task_panel()
        main_box.append(self.task_panel)

        # Input area
        input_box = self.create_input_area()
        main_box.append(input_box)

    def create_header(self):
        """Create header bar"""
        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="🤖 Leon"))
        return header

    def create_chat_view(self):
        """Create scrollable chat view"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)

        self.chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.chat_box.set_margin_start(10)
        self.chat_box.set_margin_end(10)
        self.chat_box.set_margin_top(10)

        scroll.set_child(self.chat_box)
        return scroll

    def create_task_panel(self):
        """Create task monitoring panel"""
        frame = Gtk.Frame(label="🔧 Active Tasks")
        frame.set_margin_start(10)
        frame.set_margin_end(10)

        self.task_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.task_box.set_margin_start(10)
        self.task_box.set_margin_end(10)
        self.task_box.set_margin_top(5)
        self.task_box.set_margin_bottom(5)

        frame.set_child(self.task_box)
        return frame

    def create_input_area(self):
        """Create message input area"""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_bottom(10)
        box.set_margin_top(10)

        self.input_entry = Gtk.Entry()
        self.input_entry.set_placeholder_text("Message Leon...")
        self.input_entry.set_hexpand(True)
        self.input_entry.connect('activate', self.on_send_message)

        send_button = Gtk.Button(label="Send")
        send_button.connect('clicked', self.on_send_message)

        box.append(self.input_entry)
        box.append(send_button)

        return box

    def on_send_message(self, widget):
        """Handle send button click"""
        message = self.input_entry.get_text()
        if not message.strip():
            return

        # Clear input
        self.input_entry.set_text("")

        # Add user message to chat
        self.add_message("You", message)

        # Process message asynchronously
        asyncio.create_task(self.process_message(message))

    async def process_message(self, message: str):
        """Process user message through Leon"""
        response = await self.leon.process_user_input(message)

        # Add Leon's response to chat
        GLib.idle_add(self.add_message, "Leon", response)

        # Update task panel
        GLib.idle_add(self.update_task_panel)

    def add_message(self, sender: str, message: str):
        """Add message to chat view"""
        message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        # Sender label
        sender_label = Gtk.Label(label=f"{sender}:")
        sender_label.set_xalign(0)
        sender_label.set_markup(f"<b>{sender}</b>")

        # Message label
        msg_label = Gtk.Label(label=message)
        msg_label.set_xalign(0)
        msg_label.set_wrap(True)
        msg_label.set_selectable(True)

        message_box.append(sender_label)
        message_box.append(msg_label)

        self.chat_box.append(message_box)

        # Scroll to bottom
        # (GTK4 scrolling is more complex, simplified here)

    def update_task_panel(self):
        """Update task panel with active tasks"""
        # Clear existing tasks
        while True:
            child = self.task_box.get_first_child()
            if child is None:
                break
            self.task_box.remove(child)

        # Get active tasks from Leon
        status = self.leon.task_queue.get_status_summary()

        if status['active'] == 0:
            label = Gtk.Label(label="No active tasks")
            self.task_box.append(label)
        else:
            for task in status['active_tasks']:
                task_label = Gtk.Label(label=f"• {task['description'][:50]}...")
                task_label.set_xalign(0)
                self.task_box.append(task_label)
```

**4.2: Create `main.py` (Entry Point)**

```python
#!/usr/bin/env python3
# main.py

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gio
import asyncio
import sys
import logging
from pathlib import Path

# Add core module to path
sys.path.insert(0, str(Path(__file__).parent))

from core.leon import Leon
from ui.main_window import LeonWindow

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/leon_system.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('leon')

class LeonApplication(Gtk.Application):
    """Main GTK Application"""

    def __init__(self):
        super().__init__(
            application_id='com.leon.orchestrator',
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

        self.leon_core = None
        self.main_window = None

    def do_activate(self):
        """Application activation"""
        if not self.main_window:
            # Initialize Leon core
            logger.info("Initializing Leon core...")
            self.leon_core = Leon()

            # Start Leon
            asyncio.create_task(self.leon_core.start())

            # Create main window
            self.main_window = LeonWindow(self, self.leon_core)
            self.main_window.present()

    def do_shutdown(self):
        """Application shutdown"""
        if self.leon_core:
            asyncio.create_task(self.leon_core.stop())

        Gtk.Application.do_shutdown(self)

def main():
    """Main entry point"""
    logger.info("Starting Leon application...")

    # Create Leon directories
    Path("data/task_briefs").mkdir(parents=True, exist_ok=True)
    Path("data/agent_outputs").mkdir(parents=True, exist_ok=True)
    Path("logs").mkdir(exist_ok=True)

    # Setup asyncio integration with GTK
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Create and run application
    app = LeonApplication()

    # Run GTK main loop with asyncio
    try:
        app.run(sys.argv)
    finally:
        loop.close()

if __name__ == '__main__':
    main()
```

### Step 5: Configuration Files

**Create all config files as shown in the Configuration section above**

```bash
# Create settings.yaml
cat > config/settings.yaml << 'EOF'
# (Content from Configuration section)
EOF

# Create personality.yaml
cat > config/personality.yaml << 'EOF'
# (Content from Configuration section)
EOF

# Create projects.yaml
cat > config/projects.yaml << 'EOF'
# (Content from Configuration section)
EOF
```

### Step 6: Create Startup Script

```bash
cat > scripts/start_leon.sh << 'EOF'
#!/bin/bash

# Start Leon System

cd ~/leon-system

# Activate virtual environment
source venv/bin/activate

# Check if OpenClaw is running
if ! pgrep -f "openclaw" > /dev/null; then
    echo "Starting OpenClaw..."
    openclaw start &
    sleep 2
fi

# Start Leon
echo "Starting Leon..."
python3 main.py

EOF

chmod +x scripts/start_leon.sh
```

### Step 7: systemd Service (Auto-start on boot)

```bash
cat > ~/.config/systemd/user/leon.service << 'EOF'
[Unit]
Description=Leon AI Orchestration System
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=%h/leon-system
Environment="PATH=%h/leon-system/venv/bin:/usr/local/bin:/usr/bin"
ExecStart=%h/leon-system/venv/bin/python3 %h/leon-system/main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

# Enable and start service
systemctl --user daemon-reload
systemctl --user enable leon.service
systemctl --user start leon.service
```

---

## 🎬 WORKFLOW EXAMPLES

### Example 1: Multi-Project Orchestration

**User:**
> "Leon, build a JWT auth system for my MotoRev API, fix the styling bug in the RingZero sidebar, and research whether I should use PostgreSQL or MongoDB for my new project"

**Leon's Process:**

1. **Analysis**
   - 3 distinct tasks identified
   - Task 1: Coding (MotoRev API)
   - Task 2: Bug fix (RingZero)
   - Task 3: Research (Leon handles directly)

2. **Orchestration**
   ```
   Spawning Agent #1 → MotoRev API (JWT auth)
   Spawning Agent #2 → RingZero (styling fix)
   Handling research internally
   ```

3. **Execution**
   - Agent #1 works on `/home/user/projects/motorev-api`
   - Agent #2 works on `/home/user/projects/ringzero`
   - Leon researches databases via API

4. **Monitoring**
   - Leon checks both agents every 10 seconds
   - Reads output files for progress
   - Updates UI task panel

5. **Completion**
   ```
   Leon: "All tasks complete!

   1. JWT Auth (MotoRev API) - DONE
      - Created auth routes
      - Implemented JWT middleware
      - Added refresh token system
      - Tests passing

   2. Sidebar Bug (RingZero) - FIXED
      - Issue was CSS specificity
      - Updated Tailwind classes
      - Tested responsive behavior

   3. Database Research - PostgreSQL Recommended
      - For your use case (relational data, ACID compliance needed)
      - PostgreSQL advantages: [...]
      - I can start implementing if you approve"
   ```

6. **Memory Update**
   - All tasks logged to completed_tasks
   - Project contexts updated
   - User preference learned (DB choice)

---

### Example 2: Context-Aware Follow-up

**Later that day:**

**User:**
> "Leon, what's the status on the API?"

**Leon's Process:**

1. **Memory Check**
   - Searches memory for "API" context
   - Finds MotoRev API from earlier
   - Retrieves completed task details

2. **Response**
   ```
   Leon: "The MotoRev API JWT authentication is complete. Agent #1
   finished 2 hours ago. All endpoints are functional:
   - POST /api/auth/register
   - POST /api/auth/login
   - POST /api/auth/refresh

   Would you like me to add any additional auth features?"
   ```

**No new agent spawned** - Leon answered directly from memory.

---

### Example 3: Dependency Management

**User:**
> "Leon, create a user dashboard that displays data from the API we just built, but wait until the API auth is deployed to production first"

**Leon's Process:**

1. **Task Analysis**
   - Task: Build user dashboard
   - Dependency: API must be in production

2. **Queue Management**
   ```
   Leon: "Got it. I've queued the dashboard build task, but I'll wait
   for you to confirm the API is in production before starting.

   Just let me know when it's deployed and I'll spawn the agent."
   ```

3. **Later:**
   **User:** "API is deployed"

   ```
   Leon: "Perfect! Spawning Agent #3 to build the user dashboard now..."
   ```

---

## 💰 COST ANALYSIS

### Current Multi-Terminal Approach:

**Scenario:** Working on 3 projects simultaneously

| Activity | API Calls | Tokens | Cost (Sonnet 4.5) |
|----------|-----------|--------|-------------------|
| 10 terminals × context loading | 50+ | ~500K | ~$7.50 |
| Repetitive questions across terminals | 30+ | ~200K | ~$3.00 |
| Context switching overhead | 20+ | ~100K | ~$1.50 |
| **Total per day** | **100+** | **~800K** | **~$12/day** |
| **Total per month** | **3000+** | **~24M** | **~$360/month** |

### Leon System Approach:

**Same scenario:**

| Activity | API Calls | Tokens | Cost (Sonnet 4.5) |
|----------|-----------|--------|-------------------|
| Leon conversational (10 msgs) | 10 | ~50K | ~$0.75 |
| Task analysis (3 tasks) | 3 | ~20K | ~$0.30 |
| Brief generation (3 briefs) | 3 | ~30K | ~$0.45 |
| Agent spawning | 0* | 0* | $0* |
| **Total per day** | **16** | **~100K** | **~$1.50/day** |
| **Total per month** | **480** | **~3M** | **~$45/month** |

**Savings: ~$315/month (87% reduction)**

\*Claude Code agents during beta are free. Post-beta, even if charged, the orchestration model is more efficient.

### Why Leon is Cheaper:

1. **No Redundant Context**
   - Memory system eliminates re-explaining
   - Agents get precise briefs, not full conversations

2. **Intelligent Routing**
   - Simple questions don't spawn agents
   - Only complex tasks use compute

3. **Parallel Efficiency**
   - Multiple agents work simultaneously
   - No sequential API call overhead

4. **Memory Persistence**
   - Context saved across sessions
   - No "catch-up" conversations

---

## 🔍 TROUBLESHOOTING

### Common Issues:

#### 1. Leon won't start

**Check:**
```bash
# Verify Python environment
source ~/leon-system/venv/bin/activate
python3 --version  # Should be 3.10+

# Check dependencies
pip list | grep anthropic
pip list | grep PyGObject

# Check logs
tail -f ~/leon-system/logs/leon_system.log
```

**Fix:**
```bash
cd ~/leon-system
pip install -r requirements.txt --upgrade
```

#### 2. Agents not spawning

**Check:**
```bash
# Verify OpenClaw is running
pgrep -f openclaw

# Check OpenClaw config
cat ~/.openclaw/openclaw.json

# Verify Claude Code installed
which claude-code
claude-code --version
```

**Fix:**
```bash
# Restart OpenClaw
openclaw restart

# Check agent output logs
ls -lah ~/leon-system/data/agent_outputs/
cat ~/leon-system/data/agent_outputs/agent_*.txt
```

#### 3. Memory not persisting

**Check:**
```bash
# Verify memory file exists
cat ~/leon-system/data/leon_memory.json

# Check permissions
ls -l ~/leon-system/data/
```

**Fix:**
```bash
chmod 755 ~/leon-system/data
chmod 644 ~/leon-system/data/leon_memory.json
```

#### 4. UI not appearing

**Check:**
```bash
# Verify GTK4 installed
pkg-config --modversion gtk4

# Check display
echo $DISPLAY
```

**Fix:**
```bash
sudo apt install libgtk-4-dev gir1.2-gtk-4.0
```

---

## 🚀 NEXT STEPS AFTER INSTALLATION

### Phase 1: Basic Setup (Day 1)
1. ✅ Install dependencies
2. ✅ Create project structure
3. ✅ Configure OpenClaw integration
4. ✅ Set up personality and config files
5. ✅ Test basic Leon startup

### Phase 2: Core Testing (Day 2-3)
1. Test memory system (add project, verify persistence)
2. Test single agent spawning
3. Test multi-agent orchestration
4. Verify task monitoring
5. Test UI interactions

### Phase 3: Real-World Usage (Week 1)
1. Add your actual projects to `config/projects.yaml`
2. Run Leon on real tasks
3. Monitor agent outputs
4. Refine personality prompts
5. Adjust max_concurrent_agents based on your workflow

### Phase 4: Optimization (Week 2+)
1. Implement auto-start on boot
2. Add global hotkey support
3. Integrate with your IDE
4. Build custom agent templates
5. Add voice interface (optional)

---

## 📚 FURTHER ENHANCEMENTS

### Planned Features:

1. **Voice Interface**
   - Wake word: "Hey Leon"
   - Speech-to-text input
   - Text-to-speech responses

2. **Agent Templates**
   - Pre-defined task briefs for common operations
   - "Quick tasks" library
   - One-click deployments

3. **Analytics Dashboard**
   - Task completion rates
   - Time tracking per project
   - Cost analysis visualization

4. **Mobile Companion App**
   - Check task status from phone
   - Approve/deny agent actions remotely
   - Push notifications

5. **Integration APIs**
   - Webhook support
   - Slack/Discord notifications
   - GitHub Actions integration

---

## 🎯 FINAL THOUGHTS

Leon transforms you from a task executor into a **system orchestrator**. Instead of manually managing multiple Claude Code terminals and context-switching between projects, you give high-level commands and Leon handles the coordination.

**Key Advantages:**

✅ **Persistent Memory** - Never lose context
✅ **Multi-Project Awareness** - Work on everything simultaneously
✅ **Autonomous Execution** - Agents work while you focus on decisions
✅ **Cost Efficient** - Smarter API usage saves 80-90%
✅ **Scalable** - Handle 5, 10, or 50 projects with ease

**Leon is your AI project manager, orchestrating an army of coding agents while you maintain strategic oversight.**

---

**Ready to build Leon? Start with Phase 1 of the implementation guide and you'll have your AI orchestration system running within a day.**

---

*Leon System v1.0 - Built for autonomous multi-agent coding*
*Documentation compiled: 2026-02-11*
