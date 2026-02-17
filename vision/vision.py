"""
Leon Vision — Real-time webcam awareness system.

Continuously watches through the webcam and maintains situational awareness.
Uses Claude Vision API for periodic frame analysis with a persistent context
of what's happening in the environment.

NOT screenshot-based — this is a continuous streaming loop.
"""

import asyncio
import base64
import io
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("leon.vision")


class VisionSystem:
    """
    Real-time webcam vision with continuous AI awareness.

    Captures frames continuously, analyzes periodically via Claude Vision,
    and maintains a running understanding of the environment.
    """

    def __init__(self, api_client=None, analysis_interval: float = 3.0,
                 camera_index: int = 0, resolution: tuple = (1280, 720)):
        self.api_client = api_client
        self.analysis_interval = analysis_interval
        self.camera_index = camera_index
        self.resolution = resolution

        # State
        self._running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._analysis_task: Optional[asyncio.Task] = None
        self._current_frame = None
        self._frame_lock = threading.Lock()

        # Awareness context — rolling memory of what vision has seen
        self.awareness: deque = deque(maxlen=50)
        self.current_scene: str = "No visual input yet"
        self.detected_objects: list = []
        self.detected_people: int = 0
        self.owner_present: bool = False
        self.environment: str = "unknown"
        self.last_analysis_time: float = 0

        # Event callbacks
        self._on_scene_change: Optional[Callable] = None
        self._on_person_detected: Optional[Callable] = None
        self._on_alert: Optional[Callable] = None

        # Persistent context for the AI — what it "remembers" seeing
        self._visual_context: list = []
        self._max_context = 10  # Keep last 10 analyses for context

        logger.info("Vision system initialized")

    def on_scene_change(self, callback: Callable):
        self._on_scene_change = callback

    def on_person_detected(self, callback: Callable):
        self._on_person_detected = callback

    def on_alert(self, callback: Callable):
        self._on_alert = callback

    def start(self):
        """Start the vision system — begins continuous capture and analysis."""
        if self._running:
            return

        self._running = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        logger.info("Vision system started — watching continuously")

    def stop(self):
        """Stop the vision system."""
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=3)
        logger.info("Vision system stopped")

    def _capture_loop(self):
        """Continuous frame capture in background thread."""
        try:
            import cv2
        except ImportError:
            logger.error("OpenCV not installed — run: pip install opencv-python")
            self._running = False
            return

        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            logger.error(f"Cannot open camera {self.camera_index}")
            self._running = False
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])

        logger.info(f"Camera opened: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
                     f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")

        while self._running:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Frame capture failed, retrying...")
                time.sleep(0.1)
                continue

            with self._frame_lock:
                self._current_frame = frame

            # Small sleep to avoid hammering CPU — ~30fps capture
            time.sleep(0.033)

        cap.release()
        logger.info("Camera released")

    def _get_frame_base64(self, quality: int = 70) -> Optional[str]:
        """Get current frame as base64 JPEG."""
        try:
            import cv2
        except ImportError:
            return None

        with self._frame_lock:
            if self._current_frame is None:
                return None
            frame = self._current_frame.copy()

        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return base64.b64encode(buffer).decode("utf-8")

    async def run_analysis_loop(self):
        """Continuous analysis loop — call this from async context."""
        logger.info("Vision analysis loop started")

        while self._running:
            try:
                await self._analyze_current_frame()
            except Exception as e:
                logger.error(f"Vision analysis error: {e}")

            await asyncio.sleep(self.analysis_interval)

    async def _analyze_current_frame(self):
        """Analyze the current frame with Claude Vision."""
        frame_b64 = self._get_frame_base64()
        if not frame_b64 or not self.api_client:
            return

        # Build context from recent observations
        context_summary = ""
        if self._visual_context:
            recent = self._visual_context[-3:]
            context_summary = "Recent observations:\n" + "\n".join(
                f"- [{c['time']}] {c['summary']}" for c in recent
            )

        prompt = f"""You are Leon's vision system providing real-time awareness.
Analyze this webcam frame and report what you see.

{context_summary}

Respond in JSON:
{{
  "scene": "brief description of the overall scene",
  "people_count": 0,
  "owner_present": false,
  "objects": ["list", "of", "notable", "objects"],
  "activity": "what's happening right now",
  "changes": "what changed since last observation (or 'first observation')",
  "environment": "office/bedroom/kitchen/outdoor/etc",
  "alerts": ["any concerns or notable events"]
}}

Be concise. Focus on changes from previous observations."""

        try:
            from anthropic import Anthropic
            client = Anthropic()

            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": frame_b64,
                            }
                        },
                        {"type": "text", "text": prompt}
                    ]
                }]
            )

            result_text = response.content[0].text
            # Extract JSON from response
            start = result_text.find("{")
            end = result_text.rfind("}") + 1
            if start >= 0 and end > start:
                analysis = json.loads(result_text[start:end])
            else:
                logger.warning("Vision: no JSON in response")
                return

            # Update state
            prev_scene = self.current_scene
            prev_people = self.detected_people

            self.current_scene = analysis.get("scene", self.current_scene)
            self.detected_objects = analysis.get("objects", [])
            self.detected_people = analysis.get("people_count", 0)
            self.owner_present = analysis.get("owner_present", False)
            self.environment = analysis.get("environment", self.environment)
            self.last_analysis_time = time.time()

            # Store in rolling context
            self._visual_context.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "summary": analysis.get("activity", self.current_scene),
            })
            if len(self._visual_context) > self._max_context:
                self._visual_context.pop(0)

            # Store in awareness history
            self.awareness.append({
                "timestamp": datetime.now().isoformat(),
                "scene": self.current_scene,
                "people": self.detected_people,
                "activity": analysis.get("activity", ""),
            })

            # Fire callbacks
            if self._on_scene_change and self.current_scene != prev_scene:
                self._on_scene_change(self.current_scene)

            if self._on_person_detected and self.detected_people > prev_people:
                self._on_person_detected(self.detected_people)

            alerts = analysis.get("alerts", [])
            if self._on_alert and alerts:
                for alert in alerts:
                    self._on_alert(alert)

            logger.debug(f"Vision: {self.current_scene} | "
                         f"People: {self.detected_people} | "
                         f"Objects: {len(self.detected_objects)}")

        except Exception as e:
            logger.error(f"Vision API call failed: {e}")

    def get_status(self) -> dict:
        """Get current vision status for dashboard/API."""
        return {
            "active": self._running,
            "scene": self.current_scene,
            "people_count": self.detected_people,
            "owner_present": self.owner_present,
            "objects": self.detected_objects,
            "environment": self.environment,
            "last_analysis": self.last_analysis_time,
            "has_camera": self._current_frame is not None,
        }

    def describe_scene(self) -> str:
        """Get a natural language description of what Leon sees."""
        if not self._running:
            return "Vision system is not active."
        if self._current_frame is None:
            return "Camera is starting up..."

        parts = [f"I can see: {self.current_scene}."]
        if self.detected_people > 0:
            parts.append(f"There {'is' if self.detected_people == 1 else 'are'} "
                         f"{self.detected_people} {'person' if self.detected_people == 1 else 'people'} "
                         f"{'here' if self.owner_present else 'in view'}.")
        if self.detected_objects:
            parts.append(f"Notable objects: {', '.join(self.detected_objects[:5])}.")
        return " ".join(parts)
