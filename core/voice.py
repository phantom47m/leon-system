"""
Leon Voice System — Groq Whisper STT + ElevenLabs TTS

Wake word detection + voice commands + natural voice responses.

Flow:
  Microphone -> Energy VAD -> Groq Whisper (whisper-large-v3-turbo) -> Leon Brain -> ElevenLabs -> Speaker

Falls back to Deepgram if DEEPGRAM_API_KEY is set (legacy).
"""

import asyncio
import hashlib
import io
import json
import random
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .safe_tasks import create_safe_task

logger = logging.getLogger("leon.voice")

# ================================================================
# WAKE WORD PATTERNS — with confidence tiers
# ================================================================
# Tier 1 (high confidence, score=1.0): Exact "leon" phrasings
# Tier 2 (medium confidence, score=0.7): Common mishears
# Tier 3 (lower confidence, score=0.5): Filler words + loose matches

_TIER_HIGH = 1.0
_TIER_MEDIUM = 0.7
_TIER_LOW = 0.5

# ================================================================
# AUTO-WAKE PATTERNS — trigger wake WITHOUT saying "hey leon"
# These are phrases clearly directed at an AI assistant.
# TV audio is usually fragments or third-person — these are first/second person.
# ================================================================
_AUTO_WAKE_PATTERNS = [
    # Direct questions to an assistant
    re.compile(r"\bcan you\b", re.IGNORECASE),
    re.compile(r"\bcould you\b", re.IGNORECASE),
    re.compile(r"\bwould you\b", re.IGNORECASE),
    re.compile(r"\bwill you\b", re.IGNORECASE),
    re.compile(r"\bare you able\b", re.IGNORECASE),
    re.compile(r"\bdo you know\b", re.IGNORECASE),
    re.compile(r"\btell me\b", re.IGNORECASE),
    re.compile(r"\bshow me\b", re.IGNORECASE),
    re.compile(r"\bhelp me\b", re.IGNORECASE),
    re.compile(r"\bi want\b", re.IGNORECASE),
    re.compile(r"\bi need\b", re.IGNORECASE),
    re.compile(r"\bremind me\b", re.IGNORECASE),
    re.compile(r"\blook up\b", re.IGNORECASE),
    re.compile(r"\bsearch for\b", re.IGNORECASE),
    re.compile(r"\bopen\s+\w+\b", re.IGNORECASE),
    re.compile(r"\bplay\s+\w+\b", re.IGNORECASE),
    re.compile(r"\bcheck\s+\w+\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s|\s+is|\s+are)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+(?:do|does|can|much|many|long)\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+(?:is|are|does|do)\b", re.IGNORECASE),
    re.compile(r"\bwhen\s+(?:is|are|does|do|will)\b", re.IGNORECASE),
    re.compile(r"\bwho\s+(?:is|are)\b", re.IGNORECASE),
    re.compile(r"\bset\s+(?:a|an|the|my)\b", re.IGNORECASE),
    re.compile(r"\bturn\s+(?:on|off)\b", re.IGNORECASE),
    re.compile(r"\bpause\b", re.IGNORECASE),
    re.compile(r"\bstop\s+\w+\b", re.IGNORECASE),
]
# Min word count for auto-wake (filters out TV fragments like "on." or "right.")
_AUTO_WAKE_MIN_WORDS = 4

WAKE_PATTERNS = [
    # --- Tier 1: Standard greetings (high confidence) ---
    (re.compile(r"\bhey\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bhi\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bhello\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\byo\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bsup\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bwhat'?s?\s+up\s+leon\b", re.IGNORECASE), _TIER_HIGH),

    # Polite / formal (high confidence)
    (re.compile(r"\bokay?\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bok\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bexcuse\s+me\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bpardon\s+(me\s+)?leon\b", re.IGNORECASE), _TIER_HIGH),

    # Attention getters (high confidence)
    (re.compile(r"\blisten\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\btalk\s+to\s+me\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bwake\s+up\s+leon\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bleon\s+wake\s+up\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bleon\s+you\s+(there|up|awake)\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bare\s+you\s+there\s+leon\b", re.IGNORECASE), _TIER_HIGH),

    # Name only at start of sentence (high confidence)
    (re.compile(r"^leon[\s,]", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"^leon$", re.IGNORECASE), _TIER_HIGH),

    # --- Tier 2: Common Whisper/Deepgram mishears (medium confidence) ---
    (re.compile(r"\bhey\s+leo\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\bhey\s+liam\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\bhey\s+neon\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\bhey\s+lee[\s-]?on\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\ba\s+leon\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\bhey\s+le+on\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\bhey\s+leanne?\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\bhey\s+lion\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\bhay\s+leon\b", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"\bhe\s+leon\b", re.IGNORECASE), _TIER_MEDIUM),
    # Whisper-specific single-word collapses of "hey leon"
    (re.compile(r"^heels?\.?$", re.IGNORECASE), _TIER_MEDIUM),
    (re.compile(r"^hey\s+lee\.?$", re.IGNORECASE), _TIER_MEDIUM),

    # --- Tier 3: Filler words + loose matches (lower confidence) ---
    (re.compile(r"\b(?:um|uh|like)\s+(?:hey\s+)?leon\b", re.IGNORECASE), _TIER_LOW),

    # --- Natural "are you there / awake" phrasings ---
    (re.compile(r"\bare\s+you\s+(awake|there|listening|ready|online)\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bhello\??\s*$", re.IGNORECASE), _TIER_LOW),
    (re.compile(r"\bcan\s+you\s+hear\s+me\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\banswer\s+me\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bwhy\s+didn.?t\s+you\s+answer\b", re.IGNORECASE), _TIER_HIGH),
    (re.compile(r"\bwake\s+up\b", re.IGNORECASE), _TIER_MEDIUM),
]

# Minimum confidence to accept a wake word match
WAKE_CONFIDENCE_THRESHOLD = 0.5

# ================================================================
# SLEEP / STOP-LISTENING PATTERNS
# ================================================================
_SLEEP_PATTERNS = [
    re.compile(r"\bstop\s+listening\b", re.IGNORECASE),
    re.compile(r"\bgo\s+to\s+sleep\b", re.IGNORECASE),
    re.compile(r"\bsleep\s+mode\b", re.IGNORECASE),
    re.compile(r"\bstop\s+talking\b", re.IGNORECASE),
    re.compile(r"\bquiet\s+(down\s+)?leon\b", re.IGNORECASE),
    re.compile(r"\bleon\s+(go\s+to\s+)?sleep\b", re.IGNORECASE),
    re.compile(r"\bthat'?s?\s+(all|enough)(\s+for\s+now)?\b", re.IGNORECASE),
    re.compile(r"\bgoodbye\s+leon\b", re.IGNORECASE),
    re.compile(r"\bbye\s+leon\b", re.IGNORECASE),
]

_SLEEP_RESPONSES = [
    "Going quiet. Just say hey Leon when you need me.",
    "Understood. I'll be here when you need me.",
    "Standing by. Say hey Leon to wake me.",
    "Roger that. Going silent.",
]

# ================================================================
# QUICK ACKNOWLEDGMENTS — spoken immediately when a command is received
# ================================================================
_QUICK_ACKS = [
    "On it.",
    "Right away.",
    "Sure thing.",
    "Got it.",
    "Of course.",
    "Consider it done.",
]

# Words that indicate the response is an internal browser/agent action description
# — these should never be spoken aloud, replace with a clean completion line
_ACTION_NOISE_PREFIXES = (
    "click", "type", "navigate", "fill", "scroll", "press", "select",
    "wait for", "waiting for", "done —", "done—", "step ",
    "completed ", "i clicked", "i typed", "i navigated", "i pressed",
    "i scrolled", "i filled", "i selected", "clicked ", "typed ",
    "navigated ", "pressed ", "scrolled ",
)
_ACTION_COMPLETIONS = [
    "Done.",
    "That's handled.",
    "All done.",
    "Done and dusted.",
    "Sorted.",
]

# ================================================================
# STARTUP GREETINGS
# ================================================================
_STARTUP_GREETINGS = [
    "All systems active and ready.",
    "Online and fully operational. Awaiting your command.",
    "Good to be back online. All systems nominal.",
    "Systems initialized. Ready to go.",
    "All subsystems green.",
    "Startup complete. Voice, intelligence, and automation — all online.",
    "I'm here. All systems are go.",
]

# ================================================================
# DEEPGRAM CONSTANTS
# ================================================================
DEEPGRAM_MAX_RECONNECTS = 10
DEEPGRAM_BACKOFF_BASE = 1.0       # Initial retry delay (seconds)
DEEPGRAM_BACKOFF_MAX = 60.0       # Maximum retry delay (seconds)
DEEPGRAM_BACKOFF_MULTIPLIER = 2.0

# ================================================================
# ELEVENLABS CONSTANTS
# ================================================================
ELEVENLABS_MAX_RETRIES = 3
ELEVENLABS_RETRY_BACKOFF_BASE = 1.0
ELEVENLABS_CONSECUTIVE_FAIL_THRESHOLD = 5  # Switch to local after this many failures

# Common short responses to cache (saves API calls)
_CACHEABLE_RESPONSES = {
    "yeah?", "on it", "done", "got it", "sure", "okay", "one moment",
    "working on it", "right away", "understood", "absolutely", "of course",
}

# Default sleep timeout — 120s of silence ends conversation mode
DEFAULT_SLEEP_TIMEOUT = 120.0


class VoiceState:
    """Enumeration of voice system states for clear logging."""
    IDLE = "idle"               # System initialized but not started
    LISTENING = "listening"     # Mic active, waiting for wake word
    AWAKE = "awake"             # Wake word heard, accepting commands
    PROCESSING = "processing"   # Processing a voice command
    SPEAKING = "speaking"       # Playing TTS audio
    SLEEPING = "sleeping"       # Timed out, going back to listening
    STOPPED = "stopped"         # System stopped
    DEGRADED = "degraded"       # Running in fallback mode
    MUTED = "muted"             # Mic hardware muted, not listening


class VoiceSystem:
    """
    Full voice I/O for Leon.

    - Wake word: "Hey Leon" (detected via regex patterns with confidence scoring)
    - STT: Deepgram Nova-2 streaming (real-time)
    - TTS: ElevenLabs (natural voice) with pyttsx3 fallback
    """

    def __init__(self, on_command: Optional[Callable] = None, config: Optional[dict] = None, name: Optional[str] = None):
        self.on_command = on_command
        self.on_vad_event: Optional[Callable] = None  # (event, text) → called for live transcription
        _name = (name or "leon").lower()
        self.wake_word = f"hey {_name}"
        # Build name-specific wake patterns for the configured AI name
        self._name_patterns = [
            (re.compile(rf"\bhey\s+{re.escape(_name)}\b", re.IGNORECASE), _TIER_HIGH),
            (re.compile(rf"^{re.escape(_name)}[\s,]", re.IGNORECASE), _TIER_HIGH),
            (re.compile(rf"^{re.escape(_name)}$", re.IGNORECASE), _TIER_HIGH),
            (re.compile(rf"\blisten\s+{re.escape(_name)}\b", re.IGNORECASE), _TIER_HIGH),
            (re.compile(rf"\bwake\s+up\s+{re.escape(_name)}\b", re.IGNORECASE), _TIER_HIGH),
            (re.compile(rf"\b{re.escape(_name)}\s+wake\s+up\b", re.IGNORECASE), _TIER_HIGH),
        ]
        self.is_listening = False
        self.is_awake = False
        self.is_muted = True   # Start muted — user must explicitly unmute
        self._voice_volume = 1.0  # TTS output gain: 1.0 = 100%, 0.5 = 50%, 2.0 = 200%
        self._audio_queue: queue.Queue = queue.Queue()
        self._sleep_timer: Optional[asyncio.Task] = None

        voice_cfg = config or {}

        # Deepgram config
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY", "")

        # ElevenLabs config
        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "")
        self.voice_id = os.getenv(
            "LEON_VOICE_ID",
            voice_cfg.get("voice_id", ""),  # set via setup wizard
        )
        self.tts_model = "eleven_turbo_v2_5"

        # Voice tuning — Jarvis spec: calm, intelligent, controlled, slightly deep
        self.tts_stability = voice_cfg.get("stability", 0.55)         # natural variation
        self.tts_similarity_boost = voice_cfg.get("similarity_boost", 0.75)  # clear but not locked
        self.tts_style = voice_cfg.get("style", 0.18)                 # mild expression
        self.tts_speed = voice_cfg.get("speed", 0.96)                 # slightly measured pace

        # Wake word config
        self.wake_words_enabled = voice_cfg.get("wake_words_enabled", True)

        # Sleep timeout — configurable from settings.yaml
        self.sleep_timeout = float(voice_cfg.get("sleep_timeout", DEFAULT_SLEEP_TIMEOUT))

        # Load varied wake responses from personality config
        self._wake_responses = ["Yeah?"]
        try:
            import yaml as _yaml
            with open("config/personality.yaml", "r") as f:
                _pers = _yaml.safe_load(f) or {}
            self._wake_responses = _pers.get("wake_responses", self._wake_responses)
        except Exception:
            pass

        # Audio config
        self.sample_rate = 16000
        self.channels = 1
        self.chunk_size = 4096

        # State tracking
        self._state = VoiceState.IDLE
        self._deepgram_healthy = True
        self._elevenlabs_consecutive_failures = 0
        self._elevenlabs_degraded = False

        # TTS audio cache (keyed by text hash -> audio bytes)
        self._tts_cache: dict[str, bytes] = {}
        self._tts_cache_dir = Path(voice_cfg.get("tts_cache_dir", "data/voice_cache"))

        # Load any persisted cache entries
        self._load_tts_cache()

        logger.info(
            "Voice system initialized — voice_id=%s, sleep_timeout=%.0fs",
            self.voice_id, self.sleep_timeout,
        )

    # ================================================================
    # STATE MANAGEMENT
    # ================================================================

    def _set_state(self, new_state: str):
        """Transition to a new state with logging."""
        old = self._state
        self._state = new_state
        if old != new_state:
            logger.info("Voice state: %s -> %s", old, new_state)

    @property
    def state(self) -> str:
        """Current voice system state (for dashboard / health API)."""
        return self._state

    @property
    def listening_state(self) -> dict:
        """Structured state info for the dashboard."""
        return {
            "state": self._state,
            "is_listening": self.is_listening,
            "is_awake": self.is_awake,
            "is_muted": self.is_muted,
            "deepgram_healthy": self._deepgram_healthy,
            "elevenlabs_degraded": self._elevenlabs_degraded,
        }

    def force_wake(self):
        """
        Manually force the voice system awake (PTT button / dashboard activate).
        Thread-safe: only sets simple flags — no async ops.
        The VAD loop will pick up is_awake=True on next iteration.
        """
        if not self.is_listening:
            return
        self.is_awake = True
        self._set_state(VoiceState.AWAKE)
        logger.info("Voice system force-woken (manual activate)")

    def mute(self):
        """Hard mute — VAD keeps running but all audio is discarded. Thread-safe."""
        self.is_muted = True
        self._set_state(VoiceState.MUTED)
        logger.info("Microphone muted")

    def unmute(self):
        """Unmute — resume normal VAD processing. Thread-safe."""
        self.is_muted = False
        self._set_state(VoiceState.AWAKE if self.is_awake else VoiceState.LISTENING)
        logger.info("Microphone unmuted")

    def set_voice_volume(self, pct: int) -> str:
        """Set Leon's TTS output volume. pct = 0–200 (100 = normal). Thread-safe."""
        pct = max(0, min(200, int(pct)))
        self._voice_volume = pct / 100.0
        logger.info("Voice volume set to %d%%", pct)
        return f"Voice volume set to {pct}%."

    # ================================================================
    # MAIN LOOP
    # ================================================================

    async def start(self):
        """Start the full voice pipeline. Uses Groq Whisper if no Deepgram key."""
        self.groq_api_key = os.getenv("GROQ_API_KEY", "")

        if not self.deepgram_api_key and not self.groq_api_key:
            logger.warning(
                "No STT key found — set GROQ_API_KEY (free) or DEEPGRAM_API_KEY in .env"
            )
            return

        logger.info("Voice system starting — say 'Hey Leon' to activate")
        self.is_listening = True
        self._set_state(VoiceState.MUTED if self.is_muted else VoiceState.LISTENING)

        if self.deepgram_api_key:
            # Legacy Deepgram streaming path
            mic_thread = threading.Thread(target=self._capture_microphone, daemon=True)
            mic_thread.start()
            await self._stream_to_deepgram_with_reconnect()
        else:
            # Groq Whisper path — energy VAD + batch transcription
            await self._run_groq_whisper_loop()

    async def stop(self):
        """Stop the voice system."""
        self.is_listening = False
        self._cancel_sleep_timer()
        self._set_state(VoiceState.STOPPED)

    # ================================================================
    # GROQ WHISPER BACKEND (energy VAD + batch transcription)
    # ================================================================

    # VAD tuning
    _VAD_SAMPLE_RATE   = 16000
    _VAD_FRAME_SAMPLES = 480        # 30ms frames
    _VAD_ENERGY_THRESH = 250        # RMS threshold — lower = more sensitive
    _VAD_SPEECH_FRAMES = 2          # consecutive loud frames to start recording
    _VAD_SILENCE_SEC   = 2.0        # seconds of silence to end utterance (longer = fewer mid-sentence cuts)
    _VAD_MIN_DURATION  = 0.3        # ignore utterances shorter than this (seconds)
    _VAD_MAX_DURATION  = 25.0       # hard cut — long enough for a full instruction

    async def _run_groq_whisper_loop(self):
        """Main loop: capture mic with VAD → Groq Whisper → wake word → command."""
        logger.info("Groq Whisper voice backend started — say 'Hey Leon' to activate")
        self._loop = asyncio.get_event_loop()
        self._transcription_queue: asyncio.Queue = asyncio.Queue()

        # VAD runs in a background thread, pushes utterances onto the queue
        vad_thread = threading.Thread(
            target=self._vad_capture_thread,
            daemon=True,
        )
        vad_thread.start()

        # Startup greeting — wait for VAD to calibrate (1.5s) then speak
        create_safe_task(self._startup_greeting(), name="voice-startup-greeting")

        # Main async loop processes transcriptions
        while self.is_listening:
            try:
                audio_bytes = await asyncio.wait_for(
                    self._transcription_queue.get(), timeout=1.0
                )
                # Transcribe in background so VAD keeps running
                create_safe_task(self._transcribe_and_handle(audio_bytes), name="voice-transcribe")
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Groq voice loop error: %s", e)

    async def _startup_greeting(self):
        """Play a startup line then go straight into awake/conversation mode."""
        await asyncio.sleep(2.0)   # Let VAD calibrate + mic stream open
        hour = time.localtime().tm_hour
        # 5am–9am: still up from the night before
        if 5 <= hour < 9:
            greeting = "Why are you still up? Get some sleep."
        else:
            greeting = random.choice(_STARTUP_GREETINGS)
        logger.info("Startup greeting: %s", greeting)
        await self.speak(greeting)
        # Start in awake/conversation mode right away — no need to say "hey leon" first
        self.is_awake = True
        self._set_state(VoiceState.AWAKE)
        self._start_sleep_timer()
        logger.info("Voice ready — conversation mode active")

    async def _fire_vad_event(self, event: str, text: str):
        """Fire a VAD event callback (transcription, recording start, etc.)."""
        if self.on_vad_event:
            try:
                await self.on_vad_event(event, text)
            except Exception:
                pass

    def _vad_capture_thread(self):
        """Background thread: mic → energy VAD → push utterances to queue."""
        try:
            import pyaudio
            import struct
        except ImportError:
            logger.error("pyaudio not installed — run: pip install pyaudio")
            return

        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._VAD_SAMPLE_RATE,
            input=True,
            frames_per_buffer=self._VAD_FRAME_SAMPLES,
        )
        logger.info("VAD mic stream opened")

        # Auto-calibrate: measure ambient noise for 1.5 seconds, set threshold to 4x ambient
        cal_frames = int(1.5 * self._VAD_SAMPLE_RATE / self._VAD_FRAME_SAMPLES)
        cal_rms_values = []
        for _ in range(cal_frames):
            try:
                d = stream.read(self._VAD_FRAME_SAMPLES, exception_on_overflow=False)
                s = struct.unpack_from(f"{self._VAD_FRAME_SAMPLES}h", d)
                cal_rms_values.append((sum(x*x for x in s)/len(s))**0.5)
            except Exception:
                pass
        if cal_rms_values:
            # Use median (more robust to spikes during calibration)
            cal_sorted = sorted(cal_rms_values)
            ambient = cal_sorted[len(cal_sorted) // 2]
            # Speech threshold: 1.5x ambient (start recording)
            # Silence threshold: 1.25x ambient (end utterance — safely above ambient,
            #   but well below speech. Avoids TV/background keeping silence_streak at 0.)
            dynamic_thresh = max(self._VAD_ENERGY_THRESH, ambient * 1.5)
            dynamic_silence_thresh = max(self._VAD_ENERGY_THRESH * 0.8, ambient * 1.25)
            logger.info("VAD calibrated: ambient_rms=%.0f → speech=%.0f silence=%.0f",
                        ambient, dynamic_thresh, dynamic_silence_thresh)
        else:
            dynamic_thresh = self._VAD_ENERGY_THRESH
            dynamic_silence_thresh = self._VAD_ENERGY_THRESH * 0.8

        silence_frames_needed = int(
            self._VAD_SILENCE_SEC * self._VAD_SAMPLE_RATE / self._VAD_FRAME_SAMPLES
        )
        max_frames = int(
            self._VAD_MAX_DURATION * self._VAD_SAMPLE_RATE / self._VAD_FRAME_SAMPLES
        )
        min_frames = int(
            self._VAD_MIN_DURATION * self._VAD_SAMPLE_RATE / self._VAD_FRAME_SAMPLES
        )

        recording = False
        loud_streak = 0
        silence_streak = 0
        frames: list[bytes] = []
        rec_frame_count = 0   # total frames since recording started (includes middle-zone)
        _diag_counter = 0
        _speak_cooldown_until = 0.0  # don't record until this timestamp (prevents echo feedback)
        _ambient_history: list[float] = []  # rolling ambient samples for re-calibration
        _AMBIENT_WINDOW = 150   # ~30s of history

        while self.is_listening:
            try:
                data = stream.read(self._VAD_FRAME_SAMPLES, exception_on_overflow=False)
            except Exception:
                continue

            # Suppress mic while Leon is speaking or briefly after (echo prevention)
            if self._state == VoiceState.SPEAKING:
                _speak_cooldown_until = time.time() + 1.5
                recording = False
                frames = []
                loud_streak = 0
                silence_streak = 0
                rec_frame_count = 0
                continue
            if time.time() < _speak_cooldown_until:
                continue

            # Hard mute — discard audio silently
            if self.is_muted:
                continue

            # RMS energy
            samples = struct.unpack_from(f"{self._VAD_FRAME_SAMPLES}h", data)
            rms = (sum(s * s for s in samples) / len(samples)) ** 0.5

            # Log RMS every ~3 seconds for diagnostics
            _diag_counter += 1
            if _diag_counter % 100 == 0:
                logger.info("VAD rms=%.0f threshold=%.0f recording=%s", rms, dynamic_thresh, recording)

            if not recording:
                # Collect ambient samples for rolling re-calibration
                _ambient_history.append(rms)
                if len(_ambient_history) > _AMBIENT_WINDOW:
                    _ambient_history.pop(0)
                # Re-calibrate every ~30s of not-recording using recent ambient
                if len(_ambient_history) == _AMBIENT_WINDOW and _diag_counter % 150 == 0:
                    sorted_h = sorted(_ambient_history)
                    new_ambient = sorted_h[len(sorted_h) // 2]
                    new_thresh = max(self._VAD_ENERGY_THRESH, new_ambient * 1.5)
                    new_sil = max(self._VAD_ENERGY_THRESH * 0.8, new_ambient * 1.25)
                    if abs(new_thresh - dynamic_thresh) > 300:
                        dynamic_thresh = new_thresh
                        dynamic_silence_thresh = new_sil
                        logger.info(
                            "VAD re-calibrated: ambient=%.0f → speech=%.0f silence=%.0f",
                            new_ambient, dynamic_thresh, dynamic_silence_thresh,
                        )

                # Waiting for speech — look for consecutive loud frames
                if rms > dynamic_thresh:
                    loud_streak += 1
                    if loud_streak >= self._VAD_SPEECH_FRAMES:
                        recording = True
                        rec_frame_count = 0
                        logger.info("VAD: speech started (rms=%.0f)", rms)
                        if self.on_vad_event:
                            asyncio.run_coroutine_threadsafe(
                                self._fire_vad_event("recording", ""),
                                self._loop,
                            )
                else:
                    loud_streak = 0
            else:
                # Recording — always capture this frame regardless of RMS zone
                frames.append(data)
                rec_frame_count += 1

                if rms > dynamic_thresh:
                    loud_streak += 1
                    silence_streak = 0
                elif rms < dynamic_silence_thresh:
                    silence_streak += 1
                    loud_streak = 0
                else:
                    # Middle zone: not loud enough to be speech, not quiet enough to be silence.
                    # Don't advance either streak — just accumulate time toward max_frames.
                    loud_streak = 0

                # End utterance: enough silence, OR hard max-duration cap
                if silence_streak >= silence_frames_needed or rec_frame_count >= max_frames:
                    if len(frames) >= min_frames:
                        audio_bytes = b"".join(frames)
                        asyncio.run_coroutine_threadsafe(
                            self._transcription_queue.put(audio_bytes),
                            self._loop,
                        )
                        logger.info("VAD: utterance queued (%d frames, %.1fs)",
                                    len(frames), rec_frame_count * self._VAD_FRAME_SAMPLES / self._VAD_SAMPLE_RATE)
                    frames = []
                    recording = False
                    silence_streak = 0
                    loud_streak = 0
                    rec_frame_count = 0

        stream.stop_stream()
        stream.close()
        pa.terminate()
        logger.info("VAD mic stream closed")

    async def _transcribe_and_handle(self, audio_bytes: bytes):
        """Convert raw PCM bytes to WAV, send to Groq Whisper, handle result."""
        import io
        import wave

        # Wrap raw PCM in WAV container
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)   # 16-bit
            wf.setframerate(self._VAD_SAMPLE_RATE)
            wf.writeframes(audio_bytes)
        wav_bytes = buf.getvalue()

        try:
            import aiohttp
            form = aiohttp.FormData()
            form.add_field("file", wav_bytes,
                           filename="audio.wav", content_type="audio/wav")
            form.add_field("model", "whisper-large-v3-turbo")
            form.add_field("response_format", "json")
            form.add_field("language", "en")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.groq_api_key}"},
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data.get("text", "").strip()
                        if text:
                            logger.info("Whisper heard: %s", text)
                            await self._fire_vad_event("transcription", text)
                            await self._handle_transcription(text)
                    elif resp.status == 429:
                        logger.warning("Groq Whisper rate limited — pausing 5s")
                        await asyncio.sleep(5)
                    else:
                        body = await resp.text()
                        logger.warning("Groq Whisper error %d: %s", resp.status, body[:100])
        except Exception as e:
            logger.error("Transcription error: %s", e)

    # ================================================================
    # MICROPHONE CAPTURE
    # ================================================================

    def _capture_microphone(self):
        """Capture audio from microphone in a background thread."""
        try:
            import pyaudio

            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
            )
            logger.info("Microphone stream opened")

            while self.is_listening:
                try:
                    data = stream.read(self.chunk_size, exception_on_overflow=False)
                    self._audio_queue.put(data)
                except Exception as e:
                    logger.error("Mic read error: %s", e)

            stream.stop_stream()
            stream.close()
            pa.terminate()
            logger.info("Microphone stream closed")

        except ImportError:
            logger.error("pyaudio not installed — run: pip install pyaudio")
        except Exception as e:
            logger.error("Microphone error: %s", e)

    # ================================================================
    # DEEPGRAM STREAMING STT (with exponential backoff reconnection)
    # ================================================================

    async def _stream_to_deepgram_with_reconnect(self):
        """Reconnect to Deepgram with exponential backoff and error categorization."""
        reconnect_count = 0

        while self.is_listening and reconnect_count < DEEPGRAM_MAX_RECONNECTS:
            try:
                await self._stream_to_deepgram()
                # Normal exit (is_listening set to False)
                break
            except Exception as e:
                reconnect_count += 1
                error_type = self._categorize_deepgram_error(e)

                if error_type == "auth":
                    logger.error(
                        "Deepgram authentication failed — check DEEPGRAM_API_KEY. "
                        "Voice STT is disabled until the key is fixed."
                    )
                    self._deepgram_healthy = False
                    self._set_state(VoiceState.DEGRADED)
                    break

                if error_type == "rate_limit":
                    delay = min(DEEPGRAM_BACKOFF_MAX, 30.0)  # Rate limits need longer waits
                    logger.warning(
                        "Deepgram rate limited — waiting %.1fs before retry (attempt %d/%d)",
                        delay, reconnect_count, DEEPGRAM_MAX_RECONNECTS,
                    )
                elif error_type == "network":
                    delay = min(
                        DEEPGRAM_BACKOFF_MAX,
                        DEEPGRAM_BACKOFF_BASE * (DEEPGRAM_BACKOFF_MULTIPLIER ** (reconnect_count - 1)),
                    )
                    logger.warning(
                        "Deepgram network error (attempt %d/%d): %s — reconnecting in %.1fs",
                        reconnect_count, DEEPGRAM_MAX_RECONNECTS, e, delay,
                    )
                else:
                    delay = min(
                        DEEPGRAM_BACKOFF_MAX,
                        DEEPGRAM_BACKOFF_BASE * (DEEPGRAM_BACKOFF_MULTIPLIER ** (reconnect_count - 1)),
                    )
                    logger.warning(
                        "Deepgram error (attempt %d/%d): %s — reconnecting in %.1fs",
                        reconnect_count, DEEPGRAM_MAX_RECONNECTS, e, delay,
                    )

                if reconnect_count >= DEEPGRAM_MAX_RECONNECTS:
                    logger.error(
                        "Deepgram: max reconnect attempts reached (%d). "
                        "Voice STT is disabled. Check network and API key, then restart Leon.",
                        DEEPGRAM_MAX_RECONNECTS,
                    )
                    self._deepgram_healthy = False
                    self._set_state(VoiceState.DEGRADED)
                    break

                await asyncio.sleep(delay)

    @staticmethod
    def _categorize_deepgram_error(error: Exception) -> str:
        """Categorize a Deepgram error for appropriate handling."""
        err_str = str(error).lower()

        # Auth errors
        if any(term in err_str for term in ("401", "403", "unauthorized", "forbidden", "invalid api key")):
            return "auth"

        # Rate limiting
        if any(term in err_str for term in ("429", "rate limit", "too many requests")):
            return "rate_limit"

        # Network errors
        if any(term in err_str for term in (
            "connection", "timeout", "dns", "refused", "reset",
            "eof", "broken pipe", "network", "1006", "1011",
        )):
            return "network"

        return "unknown"

    async def _stream_to_deepgram(self):
        """Stream audio to Deepgram Nova-2 for real-time transcription."""
        try:
            from deepgram import (
                DeepgramClient,
                LiveTranscriptionEvents,
                LiveOptions,
            )
        except ImportError:
            logger.error("deepgram-sdk not installed — run: pip install deepgram-sdk")
            return

        deepgram = DeepgramClient(self.deepgram_api_key)
        connection = deepgram.listen.asynclive.v("1")

        async def on_message(self_dg, result, **kwargs):
            transcript = result.channel.alternatives[0].transcript
            if not transcript.strip():
                return
            if result.is_final:
                await self._handle_transcription(transcript.strip())

        async def on_error(self_dg, error, **kwargs):
            logger.error("Deepgram stream error: %s", error)

        connection.on(LiveTranscriptionEvents.Transcript, on_message)
        connection.on(LiveTranscriptionEvents.Error, on_error)

        options = LiveOptions(
            model="nova-2",
            language="en-US",
            smart_format=True,
            interim_results=True,
            utterance_end_ms=1500,
            vad_events=True,
            encoding="linear16",
            sample_rate=self.sample_rate,
            channels=self.channels,
            keywords=[f"{self.wake_word}:2.0"],
        )

        if await connection.start(options) is False:
            raise ConnectionError("Failed to start Deepgram connection")

        logger.info("Connected to Deepgram Nova-2 — streaming audio")
        self._deepgram_healthy = True

        try:
            while self.is_listening:
                try:
                    data = self._audio_queue.get(timeout=0.1)
                    await connection.send(data)
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error("Deepgram send error: %s", e)
                    raise
        finally:
            await connection.finish()

    # ================================================================
    # TRANSCRIPTION HANDLING
    # ================================================================

    def _is_auto_wake_phrase(self, text_lower: str) -> bool:
        """Return True if text looks like a user command even without a wake word.

        Filters out short TV fragments and third-person speech.
        Matches direct questions/commands clearly aimed at an assistant.
        """
        words = text_lower.split()
        if len(words) < _AUTO_WAKE_MIN_WORDS:
            return False
        return any(p.search(text_lower) for p in _AUTO_WAKE_PATTERNS)

    async def _handle_transcription(self, text: str):
        """Process transcribed text — smart conversation mode.

        When awake: process everything as commands.
        When sleeping:
          1. Explicit wake word → wake + optional inline command
          2. Auto-wake: phrase looks like a direct command/question → wake silently + process
          3. Everything else → ignore (TV audio, short fragments)
        """
        text_lower = text.lower().strip()
        logger.debug("Heard: %s", text)

        # Filter out very short noise (punctuation-only, single chars)
        clean_text = text.strip().strip(".,!?-– ")
        if len(clean_text) < 2:
            return

        # --- Sleep commands: always checked, even mid-conversation ---
        if self.is_awake and any(p.search(text_lower) for p in _SLEEP_PATTERNS):
            self.is_awake = False
            self._cancel_sleep_timer()
            self._set_state(VoiceState.LISTENING)
            logger.info("Sleep command heard — going back to listening mode")
            await self.speak(random.choice(_SLEEP_RESPONSES))
            return

        if not self.is_awake:
            # --- Explicit wake word check ---
            if self.wake_words_enabled:
                confidence = self._wake_word_confidence(text_lower)
                if confidence >= WAKE_CONFIDENCE_THRESHOLD:
                    self.is_awake = True
                    self._set_state(VoiceState.AWAKE)
                    self._start_sleep_timer()
                    logger.info("Wake word detected (confidence=%.2f): %s", confidence, text)
                    await self.speak(random.choice(self._wake_responses))
                    after_wake = self._strip_wake_word(text_lower)
                    if after_wake and len(after_wake) > 3:
                        await self._process_command(after_wake)
                    return

            # --- Smart auto-wake: looks like a direct command/question ---
            if self._is_auto_wake_phrase(text_lower):
                self.is_awake = True
                self._set_state(VoiceState.AWAKE)
                self._start_sleep_timer()
                logger.info("Auto-wake triggered: %s", text)
                await self._process_command(text)
                return

            logger.debug("Sleeping — ignoring: %s", text)
        else:
            # Conversation mode — filter short noise, then process
            words = text_lower.split()
            # Single/two-word fragments are almost always TV or mic noise unless
            # they're explicit control words (stop, pause, yes, no, etc.)
            _OK_SHORT = {
                "stop", "pause", "play", "resume", "mute", "unmute",
                "yes", "no", "okay", "ok", "sure", "thanks", "done",
                "quit", "exit", "help", "status", "go", "wait",
            }
            if len(words) <= 2:
                clean = " ".join(w.strip(".,!?") for w in words)
                if not any(w in _OK_SHORT for w in clean.split()):
                    logger.debug("Awake — dropping short noise: %s", text)
                    return
            await self._process_command(text)

    def _wake_word_confidence(self, text_lower: str) -> float:
        """Return the highest confidence score across all wake word patterns.

        Returns 0.0 if no pattern matches.
        """
        best = 0.0
        # Check name-specific patterns first (covers custom AI names)
        for pattern, tier_score in self._name_patterns:
            match = pattern.search(text_lower)
            if match:
                position_bonus = 0.1 if match.start() <= 3 else 0.0
                score = min(1.0, tier_score + position_bonus)
                if score > best:
                    best = score
        # Check generic patterns (universal phrases + "leon" mishear variants)
        for pattern, tier_score in WAKE_PATTERNS:
            match = pattern.search(text_lower)
            if match:
                # Boost confidence if the match is at the start of the text
                position_bonus = 0.1 if match.start() <= 3 else 0.0
                score = min(1.0, tier_score + position_bonus)
                if score > best:
                    best = score
        return best

    def _matches_wake_word(self, text_lower: str) -> bool:
        """Check if text matches a wake word pattern (convenience wrapper)."""
        return self._wake_word_confidence(text_lower) >= WAKE_CONFIDENCE_THRESHOLD

    def _strip_wake_word(self, text_lower: str) -> str:
        """Remove the wake word from the beginning of text, returning the rest."""
        for pattern, _ in (*self._name_patterns, *WAKE_PATTERNS):
            match = pattern.search(text_lower)
            if match:
                remainder = text_lower[match.end():].strip(" ,.-")
                return remainder
        return text_lower

    def _is_action_noise(self, text: str) -> bool:
        """Return True if text looks like an internal browser/agent step description
        that should never be spoken aloud."""
        t = text.lower().strip()
        return any(t.startswith(p) for p in _ACTION_NOISE_PREFIXES)

    async def _process_command(self, command: str):
        """Process a voice command. Stays in conversation mode after responding."""
        self._set_state(VoiceState.PROCESSING)
        logger.info("Command: %s", command)
        self._cancel_sleep_timer()

        # Speak an acknowledgment immediately so user knows we heard them
        await self.speak(random.choice(_QUICK_ACKS))

        if self.on_command:
            response = await self.on_command(command)
            if response:
                # Filter internal browser/agent action descriptions
                if self._is_action_noise(response):
                    logger.debug("Filtered action noise from TTS: %s", response[:60])
                    await self.speak(random.choice(_ACTION_COMPLETIONS))
                else:
                    await self.speak(response)
        else:
            logger.warning("No command handler registered")

        # Stay awake — reset idle timer so user can reply naturally
        if self.is_listening and self.is_awake:
            self._set_state(VoiceState.AWAKE)
            self._start_sleep_timer()
        elif self.is_listening:
            self._set_state(VoiceState.LISTENING)

    # ================================================================
    # SLEEP TIMEOUT
    # ================================================================

    def _start_sleep_timer(self):
        """Start/reset the inactivity timer."""
        self._cancel_sleep_timer()
        self._sleep_timer = asyncio.ensure_future(self._sleep_after_timeout())

    def _cancel_sleep_timer(self):
        if self._sleep_timer and not self._sleep_timer.done():
            self._sleep_timer.cancel()
            self._sleep_timer = None

    async def _sleep_after_timeout(self):
        """Wait for inactivity timeout then go back to sleep."""
        try:
            await asyncio.sleep(self.sleep_timeout)
            if self.is_awake:
                self.is_awake = False
                self._set_state(VoiceState.SLEEPING)
                logger.info("No speech for %.0fs — going back to sleep", self.sleep_timeout)
                await self.speak(random.choice(_SLEEP_RESPONSES))
                if self.is_listening:
                    self._set_state(VoiceState.LISTENING)
        except asyncio.CancelledError:
            pass

    # ================================================================
    # ELEVENLABS TTS (with retry, rate-limit handling, and caching)
    # ================================================================

    async def speak(self, text: str):
        """Convert text to speech. Uses ElevenLabs with pyttsx3 fallback."""
        if not text.strip():
            return

        self._set_state(VoiceState.SPEAKING)
        logger.info("Speaking: %s", text[:80])

        if self.elevenlabs_api_key and self.voice_id and not self._elevenlabs_degraded:
            await self._speak_elevenlabs(text)
        else:
            await self._speak_local(text)

        # Return to previous listening state
        if self.is_listening:
            self._set_state(VoiceState.AWAKE if self.is_awake else VoiceState.LISTENING)

    async def generate_audio(self, text: str) -> Optional[bytes]:
        """
        Generate ElevenLabs audio bytes without playing them.
        Returns raw MP3 bytes on success, None on failure.
        Used for sending voice notes to WhatsApp.
        """
        if not text.strip() or not self.elevenlabs_api_key or self._elevenlabs_degraded:
            return None

        # Check cache first
        cache_key = self._tts_cache_key(text)
        cached = self._tts_cache.get(cache_key)
        if cached and self._validate_audio(cached):
            return cached

        try:
            import aiohttp as _aiohttp
        except ImportError:
            return None

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        headers = {
            "xi-api-key": self.elevenlabs_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": self.tts_model,
            "voice_settings": {
                "stability": self.tts_stability,
                "similarity_boost": self.tts_similarity_boost,
                "style": self.tts_style,
                "use_speaker_boost": True,
                "speed": getattr(self, "tts_speed", 0.96),
            },
        }

        try:
            async with _aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=_aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        audio_bytes = await resp.read()
                        if self._validate_audio(audio_bytes):
                            logger.debug("Generated audio for WhatsApp voice note (%d bytes)", len(audio_bytes))
                            return audio_bytes
                    else:
                        logger.warning("ElevenLabs generate_audio: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("generate_audio failed: %s", e)

        return None

    async def _speak_elevenlabs(self, text: str):
        """TTS via ElevenLabs with retry on 5xx, 429 handling, and caching."""
        # Check cache first for short common responses
        cache_key = self._tts_cache_key(text)
        cached = self._tts_cache.get(cache_key)
        if cached:
            logger.debug("TTS cache hit: %s", text[:40])
            if await self._play_audio_bytes(cached):
                return
            # Cache entry was bad — remove it and fall through to API
            self._tts_cache.pop(cache_key, None)

        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp not installed — using local TTS")
            await self._speak_local(text)
            return

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream"
        headers = {
            "xi-api-key": self.elevenlabs_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": self.tts_model,
            "voice_settings": {
                "stability": self.tts_stability,
                "similarity_boost": self.tts_similarity_boost,
                "style": self.tts_style,
                "use_speaker_boost": True,
                "speed": getattr(self, "tts_speed", 0.96),
            },
        }

        last_error = None
        for attempt in range(1, ELEVENLABS_MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        if resp.status == 200:
                            audio_bytes = await resp.read()

                            # Validate audio before playback
                            if not self._validate_audio(audio_bytes):
                                logger.error("ElevenLabs returned invalid audio data (%d bytes)", len(audio_bytes))
                                await self._speak_local(text)
                                return

                            if await self._play_audio_bytes(audio_bytes):
                                # Success — reset failure counter
                                self._elevenlabs_consecutive_failures = 0

                                # Cache if it's a short common response
                                if text.strip().lower().rstrip("?.!,") in _CACHEABLE_RESPONSES:
                                    self._tts_cache[cache_key] = audio_bytes
                                    self._persist_tts_cache_entry(cache_key, audio_bytes)
                                return

                        elif resp.status == 429:
                            # Rate limited — respect Retry-After header
                            retry_after = resp.headers.get("Retry-After")
                            wait = float(retry_after) if retry_after else 5.0
                            wait = min(wait, 30.0)
                            logger.warning(
                                "ElevenLabs rate limited (429) — waiting %.1fs (attempt %d/%d)",
                                wait, attempt, ELEVENLABS_MAX_RETRIES,
                            )
                            await asyncio.sleep(wait)
                            continue

                        elif resp.status == 401:
                            error_text = await resp.text()
                            logger.error(
                                "ElevenLabs auth error (401): %s — check ELEVENLABS_API_KEY",
                                error_text[:200],
                            )
                            self._record_elevenlabs_failure()
                            await self._speak_local(text)
                            return

                        elif 500 <= resp.status < 600:
                            error_text = await resp.text()
                            delay = ELEVENLABS_RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                            logger.warning(
                                "ElevenLabs server error %d (attempt %d/%d): %s — retrying in %.1fs",
                                resp.status, attempt, ELEVENLABS_MAX_RETRIES,
                                error_text[:120], delay,
                            )
                            last_error = f"HTTP {resp.status}"
                            await asyncio.sleep(delay)
                            continue

                        else:
                            error_text = await resp.text()
                            logger.error("ElevenLabs error %d: %s", resp.status, error_text[:200])
                            self._record_elevenlabs_failure()
                            await self._speak_local(text)
                            return

            except aiohttp.ClientError as e:
                delay = ELEVENLABS_RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "ElevenLabs network error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt, ELEVENLABS_MAX_RETRIES, e, delay,
                )
                last_error = str(e)
                await asyncio.sleep(delay)
                continue

        # Exhausted retries
        logger.error("ElevenLabs: all %d retries failed (last: %s)", ELEVENLABS_MAX_RETRIES, last_error)
        self._record_elevenlabs_failure()
        await self._speak_local(text)

    def _record_elevenlabs_failure(self):
        """Track consecutive ElevenLabs failures and degrade if needed."""
        self._elevenlabs_consecutive_failures += 1
        if (
            self._elevenlabs_consecutive_failures >= ELEVENLABS_CONSECUTIVE_FAIL_THRESHOLD
            and not self._elevenlabs_degraded
        ):
            self._elevenlabs_degraded = True
            logger.warning(
                "ElevenLabs: %d consecutive failures — switching to local TTS. "
                "ElevenLabs will be retried on next restart, or call voice.reset_elevenlabs().",
                self._elevenlabs_consecutive_failures,
            )

    def reset_elevenlabs(self):
        """Re-enable ElevenLabs after degraded mode. Call this to retry."""
        self._elevenlabs_degraded = False
        self._elevenlabs_consecutive_failures = 0
        logger.info("ElevenLabs re-enabled — will use cloud TTS on next speak()")

    @staticmethod
    def _validate_audio(audio_bytes: bytes) -> bool:
        """Basic validation that audio_bytes looks like playable audio."""
        if not audio_bytes or len(audio_bytes) < 100:
            return False
        # Check for common audio magic bytes (MP3, OGG, WAV, FLAC)
        header = audio_bytes[:4]
        valid_headers = [
            b'\xff\xfb',  # MP3 (MPEG frame sync)
            b'\xff\xf3',  # MP3 (MPEG frame sync variant)
            b'\xff\xf2',  # MP3
            b'ID3',       # MP3 with ID3 tag
            b'OggS',      # OGG
            b'RIFF',      # WAV
            b'fLaC',      # FLAC
        ]
        return any(header[:len(h)] == h for h in valid_headers)

    async def _play_audio_bytes(self, audio_bytes: bytes) -> bool:
        """Play audio bytes through speakers via paplay (PulseAudio). Returns True on success."""
        import subprocess
        import tempfile
        import os

        # Write to temp file and play via paplay — most reliable on PipeWire/PulseAudio
        tmp_path = None
        try:
            import soundfile as sf
            audio_data, samplerate = sf.read(io.BytesIO(audio_bytes))

            # Convert to 16-bit signed PCM for paplay, applying voice volume gain
            import numpy as np
            pcm = np.clip(audio_data * 32767 * self._voice_volume, -32768, 32767).astype(np.int16)
            channels = 1 if pcm.ndim == 1 else pcm.shape[1]
            raw_bytes = pcm.tobytes()

            fmt = f"s{pcm.dtype.itemsize * 8}le"
            cmd = [
                "paplay", "--raw",
                f"--rate={samplerate}",
                f"--format={fmt}",
                f"--channels={channels}",
            ]
            logger.info("Playing audio: %dHz %dch %d bytes via paplay", samplerate, channels, len(raw_bytes))
            result = subprocess.run(cmd, input=raw_bytes, timeout=60)
            if result.returncode == 0:
                logger.info("Audio playback complete")
                return True
            logger.error("paplay exited %d", result.returncode)
        except Exception as e:
            logger.error("Audio playback error: %s", e)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # Fallback: sounddevice
        try:
            import sounddevice as sd, soundfile as sf
            audio_data, samplerate = sf.read(io.BytesIO(audio_bytes))
            logger.info("Fallback: playing via sounddevice")
            sd.play(audio_data, samplerate)
            sd.wait()
            return True
        except Exception as e:
            logger.error("sounddevice fallback error: %s", e)
            return False

    async def _speak_local(self, text: str):
        """Fallback TTS chain: pyttsx3 → espeak-ng → print."""
        # 1. Try pyttsx3
        try:
            import pyttsx3

            def _speak():
                engine = pyttsx3.init()
                voices = engine.getProperty("voices")
                for v in voices:
                    if "english" in v.name.lower() and "male" in v.name.lower():
                        engine.setProperty("voice", v.id)
                        break
                engine.setProperty("rate", 175)
                engine.say(text)
                engine.runAndWait()

            await asyncio.get_event_loop().run_in_executor(None, _speak)
            return
        except ImportError:
            pass
        except Exception as e:
            logger.warning("pyttsx3 failed: %s — trying espeak-ng", e)

        # 2. Try espeak-ng directly (installed by scripts/install.sh)
        import shutil as _shutil
        if _shutil.which("espeak-ng"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "espeak-ng", "-s", "160", "-v", "en+m3", text,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=30)
                return
            except asyncio.TimeoutError:
                logger.warning("espeak-ng timed out — killing subprocess")
                proc.kill()
                await proc.wait()
            except Exception as e:
                logger.warning("espeak-ng failed: %s", e)

        # 3. Last resort: print to console
        print(f"\n[{self.wake_word.split()[-1].upper()}]: {text}\n")

    # ================================================================
    # TTS CACHE
    # ================================================================

    @staticmethod
    def _tts_cache_key(text: str) -> str:
        """Deterministic cache key for a TTS text."""
        normalized = text.strip().lower()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _load_tts_cache(self):
        """Load cached TTS audio from disk."""
        if not self._tts_cache_dir.exists():
            return
        loaded = 0
        for f in self._tts_cache_dir.glob("*.mp3"):
            try:
                self._tts_cache[f.stem] = f.read_bytes()
                loaded += 1
            except Exception:
                pass
        if loaded:
            logger.debug("Loaded %d cached TTS entries", loaded)

    def _persist_tts_cache_entry(self, key: str, audio_bytes: bytes):
        """Save a TTS cache entry to disk."""
        try:
            self._tts_cache_dir.mkdir(parents=True, exist_ok=True)
            (self._tts_cache_dir / f"{key}.mp3").write_bytes(audio_bytes)
        except Exception as e:
            logger.debug("Failed to persist TTS cache entry: %s", e)

    # ================================================================
    # UTILITIES
    # ================================================================

    def set_wake_word(self, word: str):
        """Change the wake word."""
        self.wake_word = word.lower()
        logger.info("Wake word changed to: '%s'", self.wake_word)

    def set_voice(self, voice_id: str):
        """Change the ElevenLabs voice."""
        self.voice_id = voice_id
        logger.info("Voice changed to: %s", voice_id)
