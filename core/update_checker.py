"""
Update checker — polls GitHub releases API to detect new versions.

Configured via settings.yaml:
  update_check:
    enabled: true
    repo: "owner/repo-name"
    check_interval_hours: 12
"""

import logging

logger = logging.getLogger("leon.updates")


class UpdateChecker:
    """Checks GitHub releases for newer versions of this software."""

    def __init__(self, repo: str, current_version: str):
        self.repo = repo                  # "owner/repo"
        self.current_version = current_version
        self.latest_version: str = ""
        self.update_available: bool = False
        self.release_url: str = ""
        self.release_notes: str = ""
        self._notified_version: str = ""  # prevents repeat notifications

    async def check(self) -> bool:
        """Query GitHub releases API. Returns True if a newer version is available."""
        try:
            import aiohttp
        except ImportError:
            logger.debug("aiohttp not available — skipping update check")
            return False

        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"User-Agent": "leon-ai-update-checker"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 404:
                        logger.debug("No releases found for %s", self.repo)
                        return False
                    if resp.status != 200:
                        logger.debug("GitHub API returned %s for %s", resp.status, self.repo)
                        return False
                    data = await resp.json()
        except Exception as e:
            logger.debug("Update check failed: %s", e)
            return False

        tag = data.get("tag_name", "")
        self.latest_version = tag.lstrip("v")
        self.release_url = data.get("html_url", "")
        body = data.get("body") or ""
        self.release_notes = body[:400] + ("…" if len(body) > 400 else "")
        self.update_available = self._is_newer(self.latest_version, self.current_version)

        if self.update_available:
            logger.info("Update available: v%s → v%s (%s)",
                        self.current_version, self.latest_version, self.release_url)
        return self.update_available

    @staticmethod
    def _is_newer(remote: str, local: str) -> bool:
        """Return True if remote version is strictly newer than local."""
        def parse(v: str):
            return tuple(int(x) for x in v.split(".") if x.isdigit())
        try:
            return parse(remote) > parse(local)
        except Exception:
            return remote != local and bool(remote)

    def should_notify(self) -> bool:
        """True if there's an update we haven't notified about yet."""
        return self.update_available and self._notified_version != self.latest_version

    def mark_notified(self):
        """Mark the current latest version as notified so we don't spam."""
        self._notified_version = self.latest_version
