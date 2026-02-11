"""
Leon 3D Printing — Bambu Lab P1S Integration

Full 3D printing workflow:
  "Leon, find me a lighter keychain holder and print it"
  → Searches STL databases
  → Shows preview
  → Sends to printer with correct settings
  → Monitors print via camera
  → Detects spaghetti/failures
  → Alerts you when done

Bambu Lab P1S Communication:
  - MQTT for printer control and status
  - RTSP for live camera feed
  - FTP for uploading gcode/3mf files
  - Local network (LAN mode)
"""

import asyncio
import ftplib
import io
import json
import logging
import os
import re
import ssl
import struct
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.hardware.printing")


# ══════════════════════════════════════════════════════════
# PRINTER MANAGER — Multi-printer orchestration
# ══════════════════════════════════════════════════════════

class PrinterManager:
    """
    Manages all connected 3D printers.
    Currently supports Bambu Lab (P1S, P1P, X1C, A1).
    """

    def __init__(self, config_file: str = "config/printers.yaml"):
        self.config_file = Path(config_file)
        self.printers: dict[str, BambuPrinter] = {}
        self.stl_searcher = STLSearcher()
        self._load_printers()
        logger.info(f"Printer manager initialized: {len(self.printers)} printers")

    def _load_printers(self):
        """Load printer configurations."""
        if not self.config_file.exists():
            logger.warning(f"No printer config at {self.config_file}")
            return

        import yaml
        with open(self.config_file) as f:
            config = yaml.safe_load(f) or {}

        for printer_conf in config.get("printers", []):
            name = printer_conf["name"]
            self.printers[name] = BambuPrinter(
                name=name,
                ip=printer_conf["ip"],
                serial=printer_conf.get("serial", ""),
                access_code=printer_conf.get("access_code", ""),
                model=printer_conf.get("model", "P1S"),
            )

    def list_printers(self) -> list:
        """Get status of all printers."""
        return [
            {
                "name": name,
                "model": p.model,
                "ip": p.ip,
                "status": p.status,
                "current_job": p.current_job,
                "progress": p.progress,
                "filament": p.filament_type,
            }
            for name, p in self.printers.items()
        ]

    def get_printer(self, name: str) -> Optional["BambuPrinter"]:
        """Get printer by name or number."""
        # By name
        if name in self.printers:
            return self.printers[name]
        # By number ("printer 2" → second printer)
        try:
            idx = int(re.search(r"\d+", name).group()) - 1
            keys = list(self.printers.keys())
            if 0 <= idx < len(keys):
                return self.printers[keys[idx]]
        except (AttributeError, ValueError):
            pass
        return None

    async def full_print_workflow(self, query: str, printer_name: str = None, api_client=None) -> dict:
        """
        Complete workflow: search STL → preview → confirm → slice → print → monitor.

        Args:
            query: What to search for ("lighter keychain holder")
            printer_name: Which printer to use (or None to ask)
            api_client: Leon's API client for AI decisions
        """
        logger.info(f"Starting print workflow: '{query}'")

        # 1. Search for STL
        results = await self.stl_searcher.search(query)
        if not results:
            return {"status": "error", "message": f"No STL files found for '{query}'"}

        # 2. Return top results for user to choose
        return {
            "status": "results_found",
            "results": results[:5],
            "message": f"Found {len(results)} STL files for '{query}'. Here are the top 5:",
            "printers_available": self.list_printers(),
        }

    async def print_stl(self, stl_url: str, printer_name: str, settings: dict = None) -> dict:
        """
        Download STL, prepare, and send to printer.

        Args:
            stl_url: URL to download the STL file
            printer_name: Which printer to use
            settings: Print settings override
        """
        printer = self.get_printer(printer_name)
        if not printer:
            return {"status": "error", "message": f"Printer '{printer_name}' not found"}

        # Download STL
        stl_path = await self.stl_searcher.download_stl(stl_url)
        if not stl_path:
            return {"status": "error", "message": "Failed to download STL"}

        # Send to printer
        result = await printer.print_file(stl_path, settings)
        return result


# ══════════════════════════════════════════════════════════
# BAMBU LAB PRINTER — Individual printer control
# ══════════════════════════════════════════════════════════

class BambuPrinter:
    """
    Control a single Bambu Lab printer via MQTT, FTP, and RTSP.

    Bambu Lab Protocol:
    - MQTT (port 8883 TLS): Commands and status
    - FTP (port 990): File upload
    - RTSP (port 322): Camera stream
    """

    MQTT_PORT = 8883
    FTP_PORT = 990
    RTSP_PORT = 322

    def __init__(self, name: str, ip: str, serial: str, access_code: str, model: str = "P1S"):
        self.name = name
        self.ip = ip
        self.serial = serial
        self.access_code = access_code
        self.model = model

        # State
        self.status = "unknown"  # idle, printing, paused, error, offline
        self.current_job = None
        self.progress = 0
        self.temperature = {"bed": 0, "nozzle": 0}
        self.filament_type = "unknown"
        self.remaining_time = 0
        self.layer_current = 0
        self.layer_total = 0

        self._mqtt_client = None
        self._connected = False

        logger.info(f"Printer configured: {name} ({model}) at {ip}")

    # ── MQTT Connection ──────────────────────────────────

    async def connect(self):
        """Connect to printer via MQTT."""
        try:
            import paho.mqtt.client as mqtt

            self._mqtt_client = mqtt.Client()
            self._mqtt_client.username_pw_set("bblp", self.access_code)

            # TLS
            self._mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
            self._mqtt_client.tls_insecure_set(True)

            self._mqtt_client.on_connect = self._on_connect
            self._mqtt_client.on_message = self._on_message

            self._mqtt_client.connect(self.ip, self.MQTT_PORT, 60)
            self._mqtt_client.loop_start()

            logger.info(f"Connecting to {self.name} at {self.ip}...")
            return True

        except ImportError:
            logger.error("paho-mqtt not installed: pip install paho-mqtt")
            return False
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            self.status = "offline"
            return False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            self.status = "idle"
            # Subscribe to printer reports
            topic = f"device/{self.serial}/report"
            client.subscribe(topic)
            logger.info(f"Connected to {self.name}")

            # Request initial status
            self._send_command({"pushing": {"command": "pushall"}})
        else:
            logger.error(f"MQTT connect failed: rc={rc}")
            self.status = "offline"

    def _on_message(self, client, userdata, msg):
        """Handle status updates from printer."""
        try:
            data = json.loads(msg.payload.decode())
            self._parse_status(data)
        except Exception as e:
            logger.error(f"Parse error: {e}")

    def _parse_status(self, data: dict):
        """Parse Bambu Lab status message."""
        print_data = data.get("print", {})

        if "gcode_state" in print_data:
            state = print_data["gcode_state"]
            state_map = {
                "IDLE": "idle",
                "RUNNING": "printing",
                "PAUSE": "paused",
                "FINISH": "idle",
                "FAILED": "error",
            }
            self.status = state_map.get(state, state.lower())

        if "mc_percent" in print_data:
            self.progress = print_data["mc_percent"]

        if "mc_remaining_time" in print_data:
            self.remaining_time = print_data["mc_remaining_time"]

        if "bed_temper" in print_data:
            self.temperature["bed"] = print_data["bed_temper"]

        if "nozzle_temper" in print_data:
            self.temperature["nozzle"] = print_data["nozzle_temper"]

        if "layer_num" in print_data:
            self.layer_current = print_data["layer_num"]

        if "total_layer_num" in print_data:
            self.layer_total = print_data["total_layer_num"]

        if "gcode_file" in print_data:
            self.current_job = print_data["gcode_file"]

        # Filament info
        ams = data.get("print", {}).get("ams", {})
        if ams and "ams" in ams:
            trays = ams["ams"]
            if trays and len(trays) > 0:
                first_tray = trays[0].get("tray", [])
                if first_tray:
                    self.filament_type = first_tray[0].get("tray_type", "unknown")

    def _send_command(self, command: dict):
        """Send MQTT command to printer."""
        if self._mqtt_client and self._connected:
            topic = f"device/{self.serial}/request"
            self._mqtt_client.publish(topic, json.dumps(command))

    # ── Printer Actions ──────────────────────────────────

    async def print_file(self, filepath: str, settings: dict = None) -> dict:
        """
        Upload and start printing a file.

        Args:
            filepath: Path to .3mf or .gcode file
            settings: Override print settings
        """
        settings = settings or {}
        filename = Path(filepath).name

        # Upload via FTP
        uploaded = await self._upload_file(filepath, filename)
        if not uploaded:
            return {"status": "error", "message": "Failed to upload file to printer"}

        # Start print
        plate = settings.get("plate", 1)
        command = {
            "print": {
                "command": "project_file",
                "param": f"Metadata/plate_{plate}.gcode",
                "subtask_name": filename,
                "url": f"ftp://{filename}",
                "bed_type": settings.get("bed_type", "auto"),
                "timelapse": settings.get("timelapse", False),
                "bed_leveling": settings.get("bed_leveling", True),
                "flow_cali": settings.get("flow_calibration", True),
                "vibration_cali": settings.get("vibration_calibration", True),
                "use_ams": settings.get("use_ams", True),
            }
        }

        self._send_command(command)
        self.status = "printing"
        self.current_job = filename

        logger.info(f"Print started on {self.name}: {filename}")
        return {
            "status": "printing",
            "printer": self.name,
            "file": filename,
            "message": f"Print started on {self.name}!",
        }

    async def _upload_file(self, local_path: str, remote_name: str) -> bool:
        """Upload file to printer via FTP."""
        try:
            ftp = ftplib.FTP_TLS()
            ftp.connect(self.ip, self.FTP_PORT)
            ftp.login("bblp", self.access_code)
            ftp.prot_p()

            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {remote_name}", f)

            ftp.quit()
            logger.info(f"File uploaded to {self.name}: {remote_name}")
            return True

        except Exception as e:
            logger.error(f"FTP upload failed: {e}")
            return False

    def pause(self):
        """Pause current print."""
        self._send_command({"print": {"command": "pause"}})
        self.status = "paused"
        logger.info(f"Print paused on {self.name}")

    def resume(self):
        """Resume paused print."""
        self._send_command({"print": {"command": "resume"}})
        self.status = "printing"
        logger.info(f"Print resumed on {self.name}")

    def stop(self):
        """Stop/cancel current print."""
        self._send_command({"print": {"command": "stop"}})
        self.status = "idle"
        self.current_job = None
        logger.info(f"Print stopped on {self.name}")

    # ── Camera ───────────────────────────────────────────

    def get_camera_url(self) -> str:
        """Get RTSP URL for printer camera."""
        return f"rtsps://bblp:{self.access_code}@{self.ip}:{self.RTSP_PORT}/streaming/live/1"

    async def capture_frame(self) -> Optional[bytes]:
        """Capture a single frame from the printer camera."""
        try:
            import cv2

            rtsp_url = self.get_camera_url()
            # OpenCV with RTSP
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
            cap = cv2.VideoCapture(rtsp_url)

            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                if ret:
                    _, buffer = cv2.imencode(".jpg", frame)
                    return buffer.tobytes()

        except ImportError:
            logger.error("opencv-python not installed: pip install opencv-python")
        except Exception as e:
            logger.error(f"Camera capture failed: {e}")
        return None

    async def check_for_spaghetti(self, api_client) -> dict:
        """
        Use AI vision to detect print failures (spaghetti, layer shift, etc.).

        Captures a frame from the camera and sends to Claude Vision.
        """
        frame = await self.capture_frame()
        if not frame:
            return {"status": "error", "message": "Could not capture camera frame"}

        import base64
        image_b64 = base64.b64encode(frame).decode()

        # Use Claude Vision to analyze
        try:
            response = api_client.client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "You are monitoring a 3D printer. Analyze this image and determine:\n"
                                "1. Is the print in progress? (yes/no)\n"
                                "2. Are there any signs of failure? (spaghetti, layer shift, adhesion issues, stringing)\n"
                                "3. Print quality assessment (good/warning/failure)\n"
                                "4. Any recommended action?\n\n"
                                "Respond with JSON: {\"printing\": bool, \"failure_detected\": bool, "
                                "\"quality\": \"good|warning|failure\", \"issues\": [\"...\"], \"action\": \"...\"}"
                            ),
                        },
                    ],
                }],
            )

            result_text = response.content[0].text
            try:
                # Parse JSON from response
                text = result_text.strip()
                if "```" in text:
                    text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
                result = json.loads(text)
            except json.JSONDecodeError:
                result = {"raw_response": result_text}

            # Auto-pause on failure detection
            if result.get("failure_detected") and result.get("quality") == "failure":
                logger.warning(f"SPAGHETTI DETECTED on {self.name}! Auto-pausing...")
                self.pause()
                result["auto_paused"] = True

            return result

        except Exception as e:
            logger.error(f"Vision analysis failed: {e}")
            return {"status": "error", "message": str(e)}

    # ── Status ───────────────────────────────────────────

    def get_status(self) -> dict:
        """Full printer status."""
        return {
            "name": self.name,
            "model": self.model,
            "ip": self.ip,
            "status": self.status,
            "connected": self._connected,
            "current_job": self.current_job,
            "progress": self.progress,
            "temperature": self.temperature,
            "filament": self.filament_type,
            "remaining_minutes": self.remaining_time,
            "layer": f"{self.layer_current}/{self.layer_total}" if self.layer_total else "N/A",
            "camera_url": self.get_camera_url(),
        }

    async def disconnect(self):
        """Disconnect from printer."""
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
            self._connected = False
            self.status = "offline"


# ══════════════════════════════════════════════════════════
# STL SEARCHER — Find 3D models online
# ══════════════════════════════════════════════════════════

class STLSearcher:
    """
    Search multiple STL databases for 3D printable models.

    Sources:
    - Thingiverse
    - Printables (Prusa)
    - Thangs
    - MyMiniFactory
    """

    def __init__(self):
        self.download_dir = Path("data/stl_downloads")
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def search(self, query: str, limit: int = 10) -> list:
        """
        Search for STL files across multiple platforms.

        Returns list of results with preview images and download links.
        """
        logger.info(f"Searching for STL: '{query}'")

        results = []

        # Search Thangs API (most comprehensive, no auth needed)
        thangs_results = await self._search_thangs(query, limit)
        results.extend(thangs_results)

        # Search Thingiverse
        thingiverse_results = await self._search_thingiverse(query, limit)
        results.extend(thingiverse_results)

        # Search Printables
        printables_results = await self._search_printables(query, limit)
        results.extend(printables_results)

        # Deduplicate and sort by relevance
        seen = set()
        unique = []
        for r in results:
            key = r["name"].lower()[:30]
            if key not in seen:
                seen.add(key)
                unique.append(r)

        logger.info(f"Found {len(unique)} STL results for '{query}'")
        return unique[:limit]

    async def _search_thangs(self, query: str, limit: int) -> list:
        """Search Thangs.com for 3D models."""
        try:
            import aiohttp

            url = "https://thangs.com/api/search"
            params = {"q": query, "limit": limit, "type": "models"}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [
                            {
                                "name": item.get("name", "Unknown"),
                                "source": "Thangs",
                                "preview_url": item.get("thumbnailUrl", ""),
                                "download_url": item.get("downloadUrl", ""),
                                "page_url": f"https://thangs.com/model/{item.get('id', '')}",
                                "likes": item.get("likes", 0),
                                "downloads": item.get("downloadCount", 0),
                            }
                            for item in data.get("results", [])
                        ]
        except Exception as e:
            logger.debug(f"Thangs search failed: {e}")
        return []

    async def _search_thingiverse(self, query: str, limit: int) -> list:
        """Search Thingiverse for 3D models."""
        thingiverse_token = os.getenv("THINGIVERSE_TOKEN", "")
        if not thingiverse_token:
            return []

        try:
            import aiohttp

            url = "https://api.thingiverse.com/search/"
            params = {"term": query, "per_page": limit}
            headers = {"Authorization": f"Bearer {thingiverse_token}"}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [
                            {
                                "name": item.get("name", "Unknown"),
                                "source": "Thingiverse",
                                "preview_url": item.get("thumbnail", ""),
                                "download_url": f"https://www.thingiverse.com/thing:{item.get('id', '')}/zip",
                                "page_url": item.get("public_url", ""),
                                "likes": item.get("like_count", 0),
                                "downloads": item.get("download_count", 0),
                            }
                            for item in data.get("hits", [])
                        ]
        except Exception as e:
            logger.debug(f"Thingiverse search failed: {e}")
        return []

    async def _search_printables(self, query: str, limit: int) -> list:
        """Search Printables.com (Prusa) for 3D models."""
        try:
            import aiohttp

            # Printables GraphQL API
            url = "https://api.printables.com/graphql/"
            graphql_query = {
                "query": """
                    query SearchModels($query: String!, $limit: Int!) {
                        searchModels(query: $query, limit: $limit) {
                            items {
                                id
                                name
                                image { url }
                                downloadCount
                                likeCount
                            }
                        }
                    }
                """,
                "variables": {"query": query, "limit": limit},
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=graphql_query,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("data", {}).get("searchModels", {}).get("items", [])
                        return [
                            {
                                "name": item.get("name", "Unknown"),
                                "source": "Printables",
                                "preview_url": item.get("image", {}).get("url", ""),
                                "download_url": f"https://www.printables.com/model/{item.get('id', '')}",
                                "page_url": f"https://www.printables.com/model/{item.get('id', '')}",
                                "likes": item.get("likeCount", 0),
                                "downloads": item.get("downloadCount", 0),
                            }
                            for item in items
                        ]
        except Exception as e:
            logger.debug(f"Printables search failed: {e}")
        return []

    async def download_stl(self, url: str) -> Optional[str]:
        """Download an STL file and return the local path."""
        try:
            import aiohttp

            filename = f"model_{datetime.now().strftime('%Y%m%d_%H%M%S')}.stl"
            filepath = self.download_dir / filename

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        filepath.write_bytes(content)
                        logger.info(f"STL downloaded: {filepath}")
                        return str(filepath)

        except Exception as e:
            logger.error(f"STL download failed: {e}")
        return None


# ══════════════════════════════════════════════════════════
# PRINT MONITOR — Background spaghetti detection
# ══════════════════════════════════════════════════════════

class PrintMonitor:
    """
    Background print monitoring with AI-powered failure detection.
    Periodically checks camera and auto-pauses on detected failures.
    """

    def __init__(self, printer_manager: PrinterManager, api_client, notify_callback=None):
        self.printer_manager = printer_manager
        self.api = api_client
        self.notify = notify_callback
        self.check_interval = 120  # Check every 2 minutes
        self.running = False

    async def start(self):
        """Start monitoring all printers."""
        self.running = True
        logger.info("Print monitor started")

        while self.running:
            for name, printer in self.printer_manager.printers.items():
                if printer.status == "printing":
                    try:
                        result = await printer.check_for_spaghetti(self.api)

                        if result.get("failure_detected"):
                            msg = (
                                f"⚠️ PRINT FAILURE on {name}!\n"
                                f"Issues: {', '.join(result.get('issues', ['unknown']))}\n"
                                f"Action: {result.get('action', 'Check printer')}"
                            )
                            logger.warning(msg)
                            if self.notify:
                                await self.notify(msg)

                            if result.get("auto_paused"):
                                if self.notify:
                                    await self.notify(f"🛑 Auto-paused {name} to prevent damage.")

                        elif result.get("quality") == "warning":
                            if self.notify:
                                await self.notify(
                                    f"⚡ Print on {name}: minor issues detected — "
                                    f"{', '.join(result.get('issues', []))}"
                                )

                    except Exception as e:
                        logger.error(f"Monitor check failed for {name}: {e}")

            await asyncio.sleep(self.check_interval)

    async def stop(self):
        self.running = False
        logger.info("Print monitor stopped")
