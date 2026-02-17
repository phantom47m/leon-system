"""
Leon Voice System — Deepgram Nova-2 STT + ElevenLabs TTS

Real-time streaming speech recognition with wake word detection
and natural voice responses.

Flow:
  Microphone -> Deepgram Nova-2 (streaming STT) -> Leon Brain -> ElevenLabs (TTS) -> Speaker
"""

import asyncio
import io
import json
import logging
import os
import queue
import re
import threading
from typing import Callable, Optional

logger = logging.getLogger("leon.voice")

# Common mishears for "hey leon"
WAKE_PATTERNS = [
    re.compile(r"^hey\s+leon\b", re.IGNORECASE),
    re.compile(r"^hey\s+leo\b", re.IGNORECASE),
    re.compile(r"^a\s+leon\b", re.IGNORECASE),
    re.compile(r"^hey\s+le+on\b", re.IGNORECASE),
]

DEEPGRAM_MAX_RECONNECTS = 5
DEEPGRAM_RECONNECT_DELAY = 2.0
SLEEP_TIMEOUT_SECONDS = 30.0


class VoiceSystem:
    """
    Full voice I/O for Leon.

    - Wake word: "Hey Leon" (detected via Deepgram keyword boosting)
    - STT: Deepgram Nova-2 streaming (real-time, 99%+ accuracy)
    - TTS: ElevenLabs (natural custom voice)
    """

    def __init__(self, on_command: Optional[Callable] = None):
        """
        Args:
            on_command: Callback function when a voice command is received.
                        Signature: async def on_command(text: str) -> str
        """
        self.on_command = on_command
        self.wake_word = "hey leon"
        self.is_listening = False
        self.is_awake = False  # True after wake word detected, waiting for command
        self._audio_queue = queue.Queue()
        self._sleep_timer: Optional[asyncio.Task] = None

        # Deepgram config
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY", "")

        # ElevenLabs config
        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "")
        self.voice_id = os.getenv("LEON_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # Default: Adam
        self.tts_model = "eleven_turbo_v2_5"

        # Audio config
        self.sample_rate = 16000
        self.channels = 1
        self.chunk_size = 4096

        logger.info("Voice system initialized")

    # ================================================================
    # MAIN LOOP
    # ================================================================

    async def start(self):
        """Start the full voice pipeline — always listening."""
        if not self.deepgram_api_key:
            logger.warning("DEEPGRAM_API_KEY not set — voice system disabled")
            return

        logger.info("Voice system starting — say 'Hey Leon' to activate")
        self.is_listening = True

        # Start microphone capture in background thread
        mic_thread = threading.Thread(target=self._capture_microphone, daemon=True)
        mic_thread.start()

        # Start Deepgram streaming with reconnection
        await self._stream_to_deepgram_with_reconnect()

    async def stop(self):
        """Stop the voice system."""
        self.is_listening = False
        self._cancel_sleep_timer()
        logger.info("Voice system stopped")

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
                    logger.error(f"Mic read error: {e}")

            stream.stop_stream()
            stream.close()
            pa.terminate()

        except ImportError:
            logger.error("pyaudio not installed — run: pip install pyaudio")
        except Exception as e:
            logger.error(f"Microphone error: {e}")

    # ================================================================
    # DEEPGRAM STREAMING STT (with reconnection)
    # ================================================================

    async def _stream_to_deepgram_with_reconnect(self):
        """Wrapper that reconnects to Deepgram on connection failures."""
        reconnect_count = 0
        while self.is_listening and reconnect_count < DEEPGRAM_MAX_RECONNECTS:
            try:
                await self._stream_to_deepgram()
                # If we get here, streaming ended normally (is_listening=False)
                break
            except Exception as e:
                reconnect_count += 1
                if reconnect_count >= DEEPGRAM_MAX_RECONNECTS:
                    logger.error(f"Deepgram: max reconnect attempts reached ({DEEPGRAM_MAX_RECONNECTS}), giving up")
                    break
                logger.warning(f"Deepgram connection error (attempt {reconnect_count}): {e}")
                logger.info(f"Reconnecting to Deepgram in {DEEPGRAM_RECONNECT_DELAY}s...")
                await asyncio.sleep(DEEPGRAM_RECONNECT_DELAY)

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

        # Handle transcription results
        async def on_message(self_dg, result, **kwargs):
            transcript = result.channel.alternatives[0].transcript
            if not transcript.strip():
                return

            is_final = result.is_final

            if is_final:
                await self._handle_transcription(transcript.strip())

        async def on_error(self_dg, error, **kwargs):
            logger.error(f"Deepgram error: {error}")

        connection.on(LiveTranscriptionEvents.Transcript, on_message)
        connection.on(LiveTranscriptionEvents.Error, on_error)

        # Configure Deepgram
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
            # Keyword boosting for wake word
            keywords=[f"{self.wake_word}:2.0"],
        )

        if await connection.start(options) is False:
            logger.error("Failed to connect to Deepgram")
            return

        logger.info("Connected to Deepgram Nova-2 — streaming audio")

        # Send audio chunks to Deepgram
        while self.is_listening:
            try:
                data = self._audio_queue.get(timeout=0.1)
                await connection.send(data)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Send error: {e}")
                raise  # Let reconnect wrapper handle it

        await connection.finish()

    # ================================================================
    # TRANSCRIPTION HANDLING
    # ================================================================

    async def _handle_transcription(self, text: str):
        """Process transcribed text — check for wake word or command."""
        text_lower = text.lower().strip()
        logger.debug(f"Heard: {text}")

        if not self.is_awake:
            # Check for wake word using fuzzy matching
            if self._matches_wake_word(text_lower):
                self.is_awake = True
                self._start_sleep_timer()
                logger.info("Wake word detected!")
                await self.speak("Yeah?")

                # Extract any command that came after the wake word
                after_wake = self._strip_wake_word(text_lower)
                if after_wake and len(after_wake) > 3:
                    await self._process_command(after_wake)
        else:
            # We're awake — reset sleep timer and process command
            self._start_sleep_timer()
            await self._process_command(text)

    def _matches_wake_word(self, text_lower: str) -> bool:
        """Check if text starts with a wake word variant."""
        for pattern in WAKE_PATTERNS:
            if pattern.search(text_lower):
                return True
        return False

    def _strip_wake_word(self, text_lower: str) -> str:
        """Remove the wake word from the beginning of text."""
        for pattern in WAKE_PATTERNS:
            match = pattern.search(text_lower)
            if match:
                return text_lower[match.end():].strip()
        return text_lower

    async def _process_command(self, command: str):
        """Process a voice command through Leon's brain."""
        logger.info(f"Command: {command}")
        self.is_awake = False  # Go back to listening for wake word after processing
        self._cancel_sleep_timer()

        if self.on_command:
            # Send to Leon's brain and get response
            response = await self.on_command(command)
            if response:
                await self.speak(response)
        else:
            logger.warning("No command handler registered")

    # ================================================================
    # SLEEP TIMEOUT
    # ================================================================

    def _start_sleep_timer(self):
        """Start/reset the inactivity timer. Goes back to sleep after timeout."""
        self._cancel_sleep_timer()
        self._sleep_timer = asyncio.ensure_future(self._sleep_after_timeout())

    def _cancel_sleep_timer(self):
        """Cancel the current sleep timer if running."""
        if self._sleep_timer and not self._sleep_timer.done():
            self._sleep_timer.cancel()
            self._sleep_timer = None

    async def _sleep_after_timeout(self):
        """Wait for inactivity timeout then go back to sleep."""
        try:
            await asyncio.sleep(SLEEP_TIMEOUT_SECONDS)
            if self.is_awake:
                self.is_awake = False
                logger.info(f"No speech for {SLEEP_TIMEOUT_SECONDS}s — going back to sleep")
        except asyncio.CancelledError:
            pass

    # ================================================================
    # ELEVENLABS TTS
    # ================================================================

    async def speak(self, text: str):
        """
        Convert text to speech using ElevenLabs and play it.
        Falls back to local TTS if ElevenLabs is unavailable.
        """
        if not text.strip():
            return

        logger.info(f"Speaking: {text[:60]}...")

        if self.elevenlabs_api_key:
            await self._speak_elevenlabs(text)
        else:
            await self._speak_local(text)

    async def _speak_elevenlabs(self, text: str):
        """High-quality TTS via ElevenLabs API with streaming playback."""
        try:
            import aiohttp
            import sounddevice as sd
            import soundfile as sf

            url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream"
            headers = {
                "xi-api-key": self.elevenlabs_api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "text": text,
                "model_id": self.tts_model,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.8,
                    "style": 0.3,
                    "use_speaker_boost": True,
                },
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        audio_bytes = await resp.read()

                        # Play audio
                        audio_data, samplerate = sf.read(io.BytesIO(audio_bytes))
                        sd.play(audio_data, samplerate)
                        sd.wait()
                    else:
                        error = await resp.text()
                        logger.error(f"ElevenLabs error {resp.status}: {error}")
                        await self._speak_local(text)

        except ImportError as e:
            logger.warning(f"Audio library missing ({e}) — using local TTS")
            await self._speak_local(text)
        except Exception as e:
            logger.error(f"ElevenLabs TTS error: {e}")
            await self._speak_local(text)

    async def _speak_local(self, text: str):
        """Fallback: local TTS using pyttsx3 (no API needed, robotic but works)."""
        try:
            import pyttsx3

            def _speak():
                engine = pyttsx3.init()
                # Try to find a decent voice
                voices = engine.getProperty("voices")
                for v in voices:
                    if "english" in v.name.lower() and "male" in v.name.lower():
                        engine.setProperty("voice", v.id)
                        break
                engine.setProperty("rate", 175)
                engine.say(text)
                engine.runAndWait()

            # Run in thread to avoid blocking
            await asyncio.get_event_loop().run_in_executor(None, _speak)

        except ImportError:
            logger.warning("pyttsx3 not installed — printing instead")
            print(f"\nLeon: {text}\n")
        except Exception as e:
            logger.error(f"Local TTS error: {e}")
            print(f"\nLeon: {text}\n")

    # ================================================================
    # UTILITIES
    # ================================================================

    def set_wake_word(self, word: str):
        """Change the wake word."""
        self.wake_word = word.lower()
        logger.info(f"Wake word changed to: '{self.wake_word}'")

    def set_voice(self, voice_id: str):
        """Change the ElevenLabs voice."""
        self.voice_id = voice_id
        logger.info(f"Voice changed to: {voice_id}")
