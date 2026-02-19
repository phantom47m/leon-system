"""
Leon Voice System — Deepgram Nova-2 STT + ElevenLabs TTS

Real-time streaming speech recognition with wake word detection
and natural voice responses.

Flow:
  Microphone -> Deepgram Nova-2 (streaming STT) -> Leon Brain -> ElevenLabs (TTS) -> Speaker
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

    # --- Tier 2: Common Deepgram mishears (medium confidence) ---
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

    # --- Tier 3: Filler words + loose matches (lower confidence) ---
    (re.compile(r"\b(?:um|uh|like)\s+(?:hey\s+)?leon\b", re.IGNORECASE), _TIER_LOW),
]

# Minimum confidence to accept a wake word match
WAKE_CONFIDENCE_THRESHOLD = 0.5

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

# Default sleep timeout
DEFAULT_SLEEP_TIMEOUT = 30.0


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


class VoiceSystem:
    """
    Full voice I/O for Leon.

    - Wake word: "Hey Leon" (detected via regex patterns with confidence scoring)
    - STT: Deepgram Nova-2 streaming (real-time)
    - TTS: ElevenLabs (natural voice) with pyttsx3 fallback
    """

    def __init__(self, on_command: Optional[Callable] = None, config: Optional[dict] = None):
        self.on_command = on_command
        self.wake_word = "hey leon"
        self.is_listening = False
        self.is_awake = False
        self._audio_queue: queue.Queue = queue.Queue()
        self._sleep_timer: Optional[asyncio.Task] = None

        voice_cfg = config or {}

        # Deepgram config
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY", "")

        # ElevenLabs config
        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "")
        self.voice_id = os.getenv(
            "LEON_VOICE_ID",
            voice_cfg.get("voice_id", "onwK4e9ZLuTAKqWW03F9"),
        )
        self.tts_model = "eleven_turbo_v2_5"

        # Voice tuning
        self.tts_stability = voice_cfg.get("stability", 0.6)
        self.tts_similarity_boost = voice_cfg.get("similarity_boost", 0.85)
        self.tts_style = voice_cfg.get("style", 0.2)

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
            "deepgram_healthy": self._deepgram_healthy,
            "elevenlabs_degraded": self._elevenlabs_degraded,
        }

    # ================================================================
    # MAIN LOOP
    # ================================================================

    async def start(self):
        """Start the full voice pipeline."""
        if not self.deepgram_api_key:
            logger.warning(
                "DEEPGRAM_API_KEY not set — voice system disabled. "
                "Set the env var and restart to enable voice."
            )
            return

        logger.info("Voice system starting — say 'Hey Leon' to activate")
        self.is_listening = True
        self._set_state(VoiceState.LISTENING)

        mic_thread = threading.Thread(target=self._capture_microphone, daemon=True)
        mic_thread.start()

        await self._stream_to_deepgram_with_reconnect()

    async def stop(self):
        """Stop the voice system."""
        self.is_listening = False
        self._cancel_sleep_timer()
        self._set_state(VoiceState.STOPPED)

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

    async def _handle_transcription(self, text: str):
        """Process transcribed text — check for wake word or command."""
        text_lower = text.lower().strip()
        logger.debug("Heard: %s", text)

        if not self.is_awake:
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
        else:
            self._start_sleep_timer()
            await self._process_command(text)

    def _wake_word_confidence(self, text_lower: str) -> float:
        """Return the highest confidence score across all wake word patterns.

        Returns 0.0 if no pattern matches.
        """
        best = 0.0
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
        for pattern, _ in WAKE_PATTERNS:
            match = pattern.search(text_lower)
            if match:
                remainder = text_lower[match.end():].strip(" ,.-")
                return remainder
        return text_lower

    async def _process_command(self, command: str):
        """Process a voice command through Leon's brain."""
        self._set_state(VoiceState.PROCESSING)
        logger.info("Command: %s", command)
        self.is_awake = False
        self._cancel_sleep_timer()

        if self.on_command:
            response = await self.on_command(command)
            if response:
                await self.speak(response)
        else:
            logger.warning("No command handler registered")

        if self.is_listening:
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
                # Transition back to listening after the sleep log
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

        if self.elevenlabs_api_key and not self._elevenlabs_degraded:
            await self._speak_elevenlabs(text)
        else:
            await self._speak_local(text)

        # Return to previous listening state
        if self.is_listening:
            self._set_state(VoiceState.AWAKE if self.is_awake else VoiceState.LISTENING)

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
        """Play audio bytes through speakers. Returns True on success."""
        try:
            import sounddevice as sd
            import soundfile as sf

            audio_data, samplerate = sf.read(io.BytesIO(audio_bytes))
            sd.play(audio_data, samplerate)
            sd.wait()
            return True
        except ImportError as e:
            logger.warning("Audio library missing (%s) — using local TTS", e)
            return False
        except Exception as e:
            logger.error("Audio playback error: %s", e)
            return False

    async def _speak_local(self, text: str):
        """Fallback: local TTS using pyttsx3."""
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

        except ImportError:
            logger.warning("pyttsx3 not installed — printing instead")
            print(f"\nLeon: {text}\n")
        except Exception as e:
            logger.error("Local TTS error: %s", e)
            print(f"\nLeon: {text}\n")

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
