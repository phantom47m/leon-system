"""
Leon Discord Voice Handler.

When the owner joins the 🎤 Talk to Leon voice channel:
  1. Leon auto-joins and starts listening
  2. Speech is captured per-user via discord-ext-voice-recv
  3. When the user mutes themselves, silence detection stops processing
  4. When speech ends (silence timeout), audio → Groq Whisper → transcript
  5. Transcript posted to #chat as  🎤 **You:** <text>
  6. Transcript sent to Leon API → response
  7. Response posted to #chat as   🤖 **Leon:** <text>
  8. ElevenLabs TTS (pcm_48000) → played in voice channel
  9. When user leaves, Leon disconnects

Everything typed in #chat while a voice session is active also flows through
the same Leon→response→TTS pipeline so the conversation log stays unified.
"""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import aiohttp
import discord
import discord.ext.voice_recv as voice_recv
import yaml

logger = logging.getLogger("leon.discord.voice")

# ── Audio constants ────────────────────────────────────────────────────────────
SAMPLE_RATE  = 48_000   # Discord sends 48 kHz
CHANNELS     = 2        # Stereo
SAMPLE_WIDTH = 2        # 16-bit signed little-endian
FRAME_BYTES  = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH // 50   # 20 ms per Discord frame

# Voice Activity Detection
SILENCE_RMS          = 150     # amplitude below this = silence  (lowered for PC mics)
SILENCE_TIMEOUT_S    = 0.7     # seconds of silence → speech segment complete (was 1.5)
MIN_SPEECH_S         = 0.15    # discard segments shorter than this (was 0.4)
MAX_SPEECH_S         = 30.0    # hard cap to prevent runaway buffers

MIN_SPEECH_BYTES = int(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * MIN_SPEECH_S)
MAX_SPEECH_BYTES = int(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * MAX_SPEECH_S)

WHISPER_MODEL = "whisper-large-v3-turbo"
WHISPER_LANGUAGE = "en"   # skip auto-detection, saves ~30% latency
# Prompt primes Whisper for Leon's context — dramatically improves accuracy
WHISPER_PROMPT = "Talking to Leon, an AI assistant. Commands include opening apps, browsing, system control, and general questions."
VOICE_CHANNEL_NAME = "🎤 Talk to Leon"
LIBOPUS_PATH = "/usr/lib/x86_64-linux-gnu/libopus.so.0"


# ── Audio helpers ──────────────────────────────────────────────────────────────

def _rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    n = len(pcm) // 2
    samples = struct.unpack_from(f"<{n}h", pcm)
    return (sum(s * s for s in samples) / n) ** 0.5


def _stereo_to_mono_wav(pcm: bytes) -> bytes:
    """Convert stereo 48 kHz PCM to a mono WAV blob for Whisper."""
    n = len(pcm) // 2
    samples = struct.unpack_from(f"<{n}h", pcm)
    # Average L+R pairs
    mono = struct.pack(
        f"<{n // 2}h",
        *((samples[i] + samples[i + 1]) // 2 for i in range(0, n, 2)),
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(mono)
    return buf.getvalue()


def _mono_pcm_to_stereo(mono: bytes) -> bytes:
    """Duplicate mono 48 kHz PCM to stereo for Discord playback."""
    n = len(mono) // 2
    samples = struct.unpack_from(f"<{n}h", mono)
    stereo = struct.pack(f"<{n * 2}h", *(v for s in samples for v in (s, s)))
    return stereo


# ── Per-user speech buffer ─────────────────────────────────────────────────────

class _SpeechBuffer:
    """Accumulates PCM frames; returns a segment when silence ends speech."""

    def __init__(self) -> None:
        self._data:          bytearray = bytearray()
        self._speaking:      bool      = False
        self._last_voice_ts: float     = 0.0

    def feed(self, pcm: bytes) -> Optional[bytes]:
        """
        Feed one Discord audio frame.
        Returns completed speech bytes when a silence gap is detected, else None.
        """
        amplitude = _rms(pcm)
        now = time.monotonic()

        if amplitude > SILENCE_RMS:
            self._data.extend(pcm)
            self._last_voice_ts = now
            self._speaking = True
            if len(self._data) >= MAX_SPEECH_BYTES:
                return self._flush()
        elif self._speaking:
            if (now - self._last_voice_ts) >= SILENCE_TIMEOUT_S:
                return self._flush()

        return None

    def _flush(self) -> Optional[bytes]:
        segment = bytes(self._data)
        self._data.clear()
        self._speaking = False
        if len(segment) < MIN_SPEECH_BYTES:
            return None
        return segment


# ── Custom AudioSink ───────────────────────────────────────────────────────────

class _LeonSink(voice_recv.AudioSink):
    """
    Receives per-user raw Opus audio, decodes it ourselves, detects speech segments.

    We request Opus (wants_opus=True) so the library's PacketRouter thread never
    tries to decode — that thread crashed on every packet with OpusError.
    Instead we decode in write() with full error tolerance: skip bad packets,
    continue on the next one.

    write() runs in a background thread (PacketRouter), so we schedule
    coroutines back onto the main event loop via run_coroutine_threadsafe.
    """

    def __init__(self, on_segment: Callable, loop: asyncio.AbstractEventLoop) -> None:
        self._on_segment = on_segment
        self._loop      = loop
        self._buffers:  dict[int, _SpeechBuffer]        = {}
        self._decoders: dict[int, discord.opus.Decoder] = {}

    def write(self, user: Optional[discord.User], data: voice_recv.VoiceData) -> None:
        if user is None:
            return

        # Get raw decrypted Opus bytes from the packet
        opus_data: Optional[bytes] = data.packet.decrypted_data if data.packet else None
        if not opus_data:
            return

        # Per-user Opus → PCM decoder (tolerates bad packets without crashing)
        if user.id not in self._decoders:
            self._decoders[user.id] = discord.opus.Decoder()
            logger.debug("Voice: new decoder for user %s", user.display_name)
        try:
            pcm = self._decoders[user.id].decode(opus_data, fec=False)
        except discord.opus.OpusError as e:
            logger.debug("Voice: skipping bad Opus packet from %s: %s", user.display_name, e)
            return  # skip corrupted / comfort-noise packets

        if not pcm:
            return

        if user.id not in self._buffers:
            self._buffers[user.id] = _SpeechBuffer()
        segment = self._buffers[user.id].feed(pcm)
        if segment:
            # write() runs in router thread — schedule coroutine on the main loop
            asyncio.run_coroutine_threadsafe(
                self._on_segment(user, segment),
                self._loop,
            )

    def wants_opus(self) -> bool:
        # Request raw Opus so we control decode and can swallow individual bad packets
        # without crashing the router thread.
        return True

    def cleanup(self) -> None:
        self._buffers.clear()
        self._decoders.clear()


# ── PCM audio source for playback ─────────────────────────────────────────────

class _PCMAudioSource(discord.AudioSource):
    """Streams stereo 48 kHz 16-bit PCM into a Discord voice channel."""

    def __init__(self, pcm: bytes) -> None:
        self._pcm = pcm
        self._pos = 0

    def read(self) -> bytes:
        chunk = self._pcm[self._pos : self._pos + FRAME_BYTES]
        self._pos += FRAME_BYTES
        return chunk if len(chunk) == FRAME_BYTES else b""

    def is_opus(self) -> bool:
        return False


# ── LeonVoiceManager ──────────────────────────────────────────────────────────

class LeonVoiceManager:
    """
    Orchestrates Leon's Discord voice presence.

    - Joins 🎤 Talk to Leon when the owner enters it
    - Listens continuously; respects user mute (silence → no transcript)
    - Transcribes, asks Leon, plays TTS, logs everything to #chat
    - Leaves when the owner leaves the channel
    """

    def __init__(
        self,
        bot: discord.Client,
        config_root: str,
        leon_url: str,
        leon_token: str,
        allowed_user_ids: set[int],
    ) -> None:
        self._bot             = bot
        self._config_root     = config_root
        self._leon_url        = leon_url.rstrip("/")
        self._leon_token      = leon_token
        self._allowed         = allowed_user_ids
        self._vc: Optional[voice_recv.VoiceRecvClient] = None
        self._processing_lock = asyncio.Lock()
        self._active_user: Optional[discord.Member] = None

        cfg = self._load_config()
        self._groq_key    = cfg.get("groq_api_key", "")
        self._el_key      = cfg.get("elevenlabs_api_key", "")
        self._el_voice_id = cfg.get("elevenlabs_voice_id", "")

        logger.info(
            "Voice manager init: groq=%s, elevenlabs=%s, voice_id=%s",
            "set" if self._groq_key else "MISSING",
            "set" if self._el_key else "MISSING",
            self._el_voice_id[:8] + "..." if self._el_voice_id else "MISSING",
        )

        # Load libopus once
        if not discord.opus.is_loaded():
            try:
                discord.opus.load_opus(LIBOPUS_PATH)
                logger.info("Voice: libopus loaded")
            except Exception as e:
                logger.warning("Voice: could not load libopus: %s", e)

    def _load_config(self) -> dict:
        try:
            return yaml.safe_load(
                (Path(self._config_root) / "user_config.yaml").read_text()
            ) or {}
        except Exception:
            return {}

    # ── Voice state hook (called from bot.py on_voice_state_update) ───────────

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if self._allowed and member.id not in self._allowed:
            return

        # User left all voice channels
        if after.channel is None:
            if self._active_user and member.id == self._active_user.id:
                await self._leave()
            return

        # User joined or moved to the Leon voice channel
        if (
            after.channel is not None
            and after.channel.name == VOICE_CHANNEL_NAME
            and after.channel != getattr(before, "channel", None)
        ):
            self._active_user = member
            await self._join(after.channel, member)

    # ── Connect / disconnect ───────────────────────────────────────────────────

    async def _join(
        self,
        channel: discord.VoiceChannel,
        member: discord.Member,
    ) -> None:
        try:
            if self._vc and self._vc.is_connected():
                if self._vc.channel == channel:
                    return
                await self._vc.move_to(channel)
            else:
                self._vc = await channel.connect(cls=voice_recv.VoiceRecvClient)

            loop = asyncio.get_event_loop()
            sink = _LeonSink(self._handle_speech, loop)
            self._vc.listen(sink)

            logger.info("Voice: joined %r, listening for %s", channel.name, member.display_name)
            await self._post_to_chat(
                f"*{member.display_name} joined voice — Leon is listening. "
                f"Mute yourself to pause.*",
                role=None,
            )
        except Exception as e:
            logger.error("Voice: join failed: %s", e)

    async def _leave(self) -> None:
        try:
            if self._vc:
                self._vc.stop_listening()
                await self._vc.disconnect()
                self._vc = None
            self._active_user = None
            logger.info("Voice: disconnected")
        except Exception as e:
            logger.debug("Voice: disconnect error: %s", e)

    # ── Speech pipeline ────────────────────────────────────────────────────────

    async def _handle_speech(
        self,
        user: discord.User,
        pcm: bytes,
    ) -> None:
        """Called by sink when a speech segment completes."""
        async with self._processing_lock:
            try:
                text = await self._transcribe(pcm)
                if not text or len(text.strip()) < 2:
                    return

                logger.info("Voice [%s]: %s", user.display_name, text[:80])

                # Post user transcript immediately — don't wait for Leon's response
                await self._post_to_chat(text.strip(), role="user", name=user.display_name)

                # Ask Leon
                logger.info("Voice: asking Leon for response...")
                response = await self._ask_leon(text.strip(), str(user))
                logger.info(
                    "Voice: Leon replied (%d chars): %s",
                    len(response or ""),
                    (response or "")[:60],
                )
                if not response:
                    logger.warning("Voice: empty response from Leon — skipping TTS/chat")
                    return

                # Post Leon's reply to #chat AND start TTS simultaneously
                await asyncio.gather(
                    self._post_to_chat(response, role="leon"),
                    self._play_tts(response),
                )
                logger.info("Voice: pipeline complete for [%s]", user.display_name)

            except Exception as e:
                logger.error("Voice: pipeline error: %s", e, exc_info=True)

    async def _transcribe(self, pcm: bytes) -> str:
        if not self._groq_key:
            logger.warning("Voice: no Groq API key — transcription disabled")
            return ""
        try:
            wav = _stereo_to_mono_wav(pcm)
            async with aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
                form.add_field("model", WHISPER_MODEL)
                form.add_field("response_format", "text")
                form.add_field("language", WHISPER_LANGUAGE)
                form.add_field("prompt", WHISPER_PROMPT)
                async with session.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._groq_key}"},
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        return (await resp.text()).strip()
                    logger.warning("Whisper %d: %s", resp.status, (await resp.text())[:200])
                    return ""
        except Exception as e:
            logger.warning("Voice: transcription error: %s", e)
            return ""

    async def _ask_leon(self, text: str, author: str) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._leon_url}/api/message",
                    headers={"Authorization": f"Bearer {self._leon_token}"},
                    json={"message": text, "source": f"voice:{author}"},
                    timeout=aiohttp.ClientTimeout(total=90),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("response", "") or ""
                    body = await resp.text()
                    logger.warning(
                        "Voice: _ask_leon got status %d: %s",
                        resp.status, body[:200],
                    )
                    return ""
        except Exception as e:
            logger.warning("Voice: Leon request failed: %s", e)
            return ""

    async def _tts_edge(self, text: str) -> bytes:
        """Fallback TTS via Microsoft Edge (free, no quota). Returns stereo 48kHz PCM."""
        import edge_tts, miniaudio, array as _array
        mp3 = b""
        communicate = edge_tts.Communicate(text[:500], voice="en-US-GuyNeural")
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3 += chunk["data"]
        decoded = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16,
                                   nchannels=1, sample_rate=48000)
        mono = _array.array("h", decoded.samples)
        stereo = _array.array("h")
        for s in mono:
            stereo.append(s); stereo.append(s)
        return bytes(stereo)

    async def _play_tts(self, text: str) -> None:
        if not self._vc or not self._vc.is_connected():
            return
        try:
            stereo_pcm = None

            # Try ElevenLabs first (custom voice)
            if self._el_key and self._el_voice_id:
                logger.info("Voice: requesting TTS for %d chars...", len(text))
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/{self._el_voice_id}"
                        "?output_format=pcm_48000",
                        headers={
                            "xi-api-key": self._el_key,
                            "Content-Type": "application/json",
                        },
                        json={
                            "text": text[:500],
                            "model_id": "eleven_turbo_v2_5",
                            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                        },
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as resp:
                        if resp.status == 200:
                            mono_pcm = await resp.read()
                            stereo_pcm = _mono_pcm_to_stereo(mono_pcm)
                            logger.info("Voice: ElevenLabs TTS received %d bytes", len(mono_pcm))
                        else:
                            body = await resp.text()
                            logger.warning("ElevenLabs TTS %d — falling back to Edge TTS: %s",
                                           resp.status, body[:120])

            # Fallback: Microsoft Edge TTS (free, no quota)
            if stereo_pcm is None:
                logger.info("Voice: using Edge TTS fallback")
                stereo_pcm = await self._tts_edge(text)

            if stereo_pcm:
                if self._vc.is_playing():
                    self._vc.stop()
                    await asyncio.sleep(0.1)
                self._vc.play(_PCMAudioSource(stereo_pcm))
                logger.info("Voice: TTS playback started")
        except Exception as e:
            logger.warning("Voice: TTS playback error: %s", e, exc_info=True)

    # ── Chat logging ───────────────────────────────────────────────────────────

    async def _post_to_chat(
        self,
        text: str,
        role: Optional[str],
        name: str = "",
    ) -> None:
        """Post a line to #chat to keep the conversation transcript."""
        try:
            from integrations.discord.dashboard import get_dashboard
            db = get_dashboard()
            if not db:
                logger.warning("Voice: _post_to_chat: dashboard not initialized")
                return
            ch = db._channels.get("chat")
            if not ch:
                logger.warning(
                    "Voice: _post_to_chat: #chat not in dashboard._channels (have: %s)",
                    list(db._channels.keys()),
                )
                return
            if role is None:
                await ch.send(f"*{text}*")
            elif role == "user":
                await ch.send(f"🎤 **{name or 'You'}:** {text[:1900]}")
            else:  # leon
                await ch.send(f"🤖 **Leon:** {text[:1900]}")
        except Exception as e:
            logger.warning("Voice: post_to_chat error: %s", e, exc_info=True)


# ── Module singleton ───────────────────────────────────────────────────────────

_voice_manager: Optional[LeonVoiceManager] = None


def get_voice_manager() -> Optional[LeonVoiceManager]:
    return _voice_manager


def init_voice_manager(
    bot: discord.Client,
    config_root: str,
    leon_url: str,
    leon_token: str,
    allowed_user_ids: set[int],
) -> LeonVoiceManager:
    global _voice_manager
    _voice_manager = LeonVoiceManager(
        bot=bot,
        config_root=config_root,
        leon_url=leon_url,
        leon_token=leon_token,
        allowed_user_ids=allowed_user_ids,
    )
    return _voice_manager
