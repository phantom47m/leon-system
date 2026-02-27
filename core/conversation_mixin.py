"""
Conversation processing mixin — request analysis, conversational response
generation, memory extraction, and permission checks.

Extracted from leon.py (Issue #19) to reduce the main module's size and
centralise all conversation-processing concerns.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger("leon")


# ── Conversational fast path ─────────────────────────────────────────────────
# Short, unambiguous conversational messages that never need LLM classification
# or system skill routing.  Skipping both _analyze_request and the LLM router
# inside _route_special_commands saves 2 LLM calls (~2s latency) per trivial
# interaction.  Mirrors the voice path (process_voice_input) which already
# skips classification for all messages.
#
# High precision required: a false positive means a real command gets answered
# conversationally instead of executed.  When in doubt, fall through.

_TRIVIAL_EXACT = frozenset({
    # Greetings
    "hi", "hey", "hello", "yo", "howdy", "sup", "heyo",
    # Acknowledgments
    "ok", "okay", "sure", "bet", "cool", "nice", "great", "awesome",
    "perfect", "sweet", "dope", "alright", "right",
    # Thanks
    "thanks", "thx", "ty", "thank you",
    # Yes / no
    "yes", "no", "yep", "nope", "yeah", "nah",
    # Reactions
    "lol", "lmao", "haha", "hahaha", "wow", "oh", "hmm", "mhm",
    # Farewell
    "bye", "goodbye", "later", "peace", "gn",
    # Misc
    "gotcha", "got it", "understood", "roger",
    "good morning", "good evening", "good afternoon", "good night",
    "never mind", "nevermind", "nvm", "forget it",
})

_TRIVIAL_CHAT_RE = re.compile(
    r"^(?:"
    r"how\s+are\s+you"
    r"|what'?s\s+up"
    r"|what'?s\s+good"
    r"|how'?s\s+it\s+going"
    r"|tell\s+me\s+(?:a\s+joke|something\s+(?:funny|interesting))"
    r"|who\s+are\s+you"
    r"|what\s+are\s+you"
    r"|what'?s\s+your\s+name"
    r"|are\s+you\s+(?:there|awake|alive|ready)"
    r"|thank\s+you(?:\s+so\s+much)?"
    r"|appreciate\s+it"
    r"|good\s+talk"
    r"|see\s+ya"
    r")\s*$",
    re.IGNORECASE,
)

_TRIVIAL_SUFFIX_RE = re.compile(r"\s+(?:leon|bro|man|dude|buddy)\s*$", re.IGNORECASE)
_TRIVIAL_PUNCT_RE = re.compile(r"[.!?,;:'\"]+$")


def _is_trivial_conversation(msg: str) -> bool:
    """Return True if *msg* is a trivially conversational message.

    These messages never need LLM classification or system skill routing —
    they always end up at _respond_conversationally.  Skipping the classify
    and route steps saves 2 LLM calls (~2 s latency).
    """
    cleaned = _TRIVIAL_PUNCT_RE.sub("", msg.strip()).strip()
    cleaned = _TRIVIAL_SUFFIX_RE.sub("", cleaned).lower()
    if cleaned in _TRIVIAL_EXACT:
        return True
    if _TRIVIAL_CHAT_RE.match(cleaned):
        return True
    return False


class ConversationMixin:
    """Methods for processing user conversations and generating responses.

    Relies on attributes set during ``Leon.__init__``:
        self.api                (AnthropicAPI)
        self.memory             (MemorySystem)
        self.config             (dict)
        self.system_prompt      (str)
        self.ai_name            (str)
        self.owner_name         (str)
        self.vision             (optional)
        self.permissions        (optional)
    """

    # ------------------------------------------------------------------
    # Request analysis
    # ------------------------------------------------------------------

    async def _analyze_request(self, message: str) -> Optional[dict]:
        """Use the API to classify and decompose the user's request."""
        # Build context from memory
        active_tasks = self.memory.get_all_active_tasks()
        projects = self.memory.list_projects()
        project_names = [p['name'] for p in projects] if projects else []

        prompt = f"""Analyze this user request and classify it.

User message: "{message}"

Current active tasks: {json.dumps(list(active_tasks.values()), default=str) if active_tasks else "None"}
Known projects: {json.dumps(project_names) if project_names else "None"}

Respond with ONLY valid JSON (no markdown fences):
{{
  "type": "simple" | "device_control" | "single_task" | "multi_task" | "plan",
  "tasks": ["description of each discrete task"],
  "projects": ["project name for each task or 'unknown'"],
  "complexity": 1-10,
  "plan_goal": "concise goal if type is plan, else null",
  "plan_project": "project name if type is plan, else null"
}}

Rules:
- "simple" = status question, quick answer, clarification, greeting, asking what you did
- "device_control" = request to control a physical device — lights, switches, thermostat, TV, fan, speaker volume, smart plug. Even if garbled or misspelled. Do NOT classify as single_task — it should never spawn a coding agent.
- "single_task" = one focused coding/research task
- "multi_task" = 2+ distinct tasks that can be parallelized
- "plan" = user wants a large, multi-hour autonomous build — they want you to take over a whole project or goal and execute it fully without interruption. Detect this from intent, not exact phrases. Examples that should classify as "plan": "go ham on X", "just go build it", "make it production ready", "work through the whole thing", "do everything needed to launch X", "take it from here and run with it", "build out the whole feature", "overhaul X completely", "just fix everything wrong with it"

For "plan" type, set plan_goal to a precise one-line description of what should be achieved, and plan_project to the most relevant known project name (or 'unknown')."""

        result = await self.api.analyze_json(prompt)
        if result:
            logger.info(f"Analysis: type={result.get('type')}, tasks={len(result.get('tasks', []))}")
        return result

    # ------------------------------------------------------------------
    # Conversational response
    # ------------------------------------------------------------------

    async def _respond_conversationally(self, message: str) -> str:
        """Direct API response for simple queries - no agent needed."""
        logger.info("Responding conversationally")

        # Build context
        recent = self.memory.get_recent_context(limit=20)
        active = self.memory.get_all_active_tasks()
        projects = self.memory.list_projects()

        # Inject memory context into system prompt
        vision_desc = self.vision.describe_scene() if self.vision and self.vision._running else "Vision inactive"

        learned = self.memory.memory.get("learned_context", {})
        learned_str = "\n".join(f"  {k}: {v}" for k, v in learned.items()) if learned else "None"

        now = datetime.now()
        context_block = f"""
## Current Time
{now.strftime("%A, %B %d, %Y — %I:%M %p %Z")} (user is in Florida, Eastern Time)

## Current State
Active tasks: {json.dumps(list(active.values()), default=str) if active else "None"}
Known projects: {json.dumps([{{'name': p['name'], 'status': p.get('status')}} for p in projects], default=str) if projects else "None"}
Vision: {vision_desc}

## What I know about the user
{learned_str}

## HARD RULES — Never break these in any response
- NEVER present a numbered list of options asking the user to choose. Pick and act.
- NEVER say "which way?", "what would you prefer?", "should I X or Y?" — decide yourself.
- If agent state looks stale or you can't verify it, assume it finished. Do not mention it. Focus on what the user wants right now.
- When ambiguous: pick the most reasonable interpretation and execute. Say what you're doing, not what you could do.
- The only question allowed is "Anything else?" after completing something.
"""

        messages = [{"role": m["role"], "content": m["content"]} for m in recent]

        return await self.api.create_message(
            system=self.system_prompt + context_block,
            messages=messages,
        )

    # ------------------------------------------------------------------
    # Memory extraction
    # ------------------------------------------------------------------

    async def _extract_memory(self, user_msg: str, response: str):
        """Background task: extract and persist any notable facts from this exchange."""
        msg_lower = user_msg.lower()

        # Explicit "remember" triggers — high confidence, store immediately
        remember_triggers = ["remember that", "remember my", "my name is", "i am ",
                             "i prefer ", "i like ", "i hate ", "i always ", "i never ",
                             "my favorite", "my email is", "my phone", "note that",
                             "keep in mind", "don't forget"]
        if any(t in msg_lower for t in remember_triggers):
            prompt = (
                f"The user said: \"{user_msg}\"\n\n"
                "Extract the key fact to remember as a short JSON object:\n"
                "{\"key\": \"short_key\", \"value\": \"fact to remember\"}\n"
                "Only respond with JSON. If nothing is worth remembering, return {}."
            )
            result = await self.api.analyze_json(prompt)
            if result and result.get("key") and result.get("value"):
                self.memory.learn(result["key"], result["value"])
                logger.info(f"Self-memory: learned '{result['key']}' = '{result['value']}'")

    # ------------------------------------------------------------------
    # Permission checks
    # ------------------------------------------------------------------

    def _check_sensitive_permissions(self, message: str) -> Optional[str]:
        """Check if the message requests a sensitive action that needs approval."""
        msg = message.lower()

        # Map keywords to permission actions
        # Note: delete_files removed — agents run with --dangerously-skip-permissions already
        checks = [
            (["purchase", "buy", "order", "checkout"], "make_purchase"),
            (["transfer money", "send money", "pay ", "wire "], "send_money"),
            (["post publicly", "tweet", "publish"], "post_publicly"),
        ]

        for keywords, action in checks:
            if any(kw in msg for kw in keywords):
                if not self.permissions.check_permission(action):
                    return (
                        f"I'll need your go-ahead for that. "
                        f"Run `/approve {action}` to unlock it."
                    )
        return None
