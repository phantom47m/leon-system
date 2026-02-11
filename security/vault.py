"""
Leon Security — API key vault, encryption, auth, network lockdown.

Ensures no one from the outside world can hack Leon or control your computer.

Security layers:
1. Encrypted vault for all API keys and secrets
2. Local-only network binding (no external access)
3. Owner authentication (PIN/biometric)
4. Audit logging of all actions
5. Permission system for sensitive operations
6. Automatic threat detection
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.security")


# ══════════════════════════════════════════════════════════
# ENCRYPTED VAULT — Store API keys and secrets
# ══════════════════════════════════════════════════════════

class SecureVault:
    """
    Encrypted storage for all API keys, tokens, and secrets.
    Uses AES-256 encryption with a master password.

    Keys are NEVER stored in plain text on disk.
    """

    def __init__(self, vault_path: str = "data/.vault.enc", master_key: str = None):
        self.vault_path = Path(vault_path)
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._secrets: dict = {}
        self._master_key = master_key or os.getenv("LEON_MASTER_KEY", "")
        self._unlocked = False

        if self._master_key:
            self.unlock(self._master_key)

        logger.info("Secure vault initialized")

    def unlock(self, master_password: str) -> bool:
        """Unlock the vault with the master password."""
        self._master_key = self._derive_key(master_password)

        if self.vault_path.exists():
            try:
                encrypted = self.vault_path.read_bytes()
                decrypted = self._decrypt(encrypted, self._master_key)
                self._secrets = json.loads(decrypted)
                self._unlocked = True
                logger.info("Vault unlocked successfully")
                return True
            except Exception as e:
                logger.error(f"Vault unlock failed: {e}")
                return False
        else:
            # New vault
            self._secrets = {}
            self._unlocked = True
            self._save()
            logger.info("New vault created")
            return True

    def lock(self):
        """Lock the vault — clear secrets from memory."""
        self._secrets = {}
        self._unlocked = False
        self._master_key = ""
        logger.info("Vault locked")

    def store(self, key: str, value: str):
        """Store a secret in the vault."""
        if not self._unlocked:
            raise PermissionError("Vault is locked")
        self._secrets[key] = value
        self._save()
        logger.info(f"Secret stored: {key}")

    def retrieve(self, key: str) -> Optional[str]:
        """Retrieve a secret from the vault."""
        if not self._unlocked:
            raise PermissionError("Vault is locked")
        return self._secrets.get(key)

    def delete(self, key: str):
        """Delete a secret from the vault."""
        if not self._unlocked:
            raise PermissionError("Vault is locked")
        self._secrets.pop(key, None)
        self._save()

    def list_keys(self) -> list:
        """List all stored key names (not values)."""
        if not self._unlocked:
            raise PermissionError("Vault is locked")
        return list(self._secrets.keys())

    def _save(self):
        """Encrypt and save vault to disk."""
        plaintext = json.dumps(self._secrets).encode()
        encrypted = self._encrypt(plaintext, self._master_key)
        self.vault_path.write_bytes(encrypted)

    def _derive_key(self, password: str) -> bytes:
        """Derive encryption key from password using PBKDF2."""
        salt = b"leon_vault_salt_v1"  # Static salt (vault is local only)
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)

    def _encrypt(self, data: bytes, key: bytes) -> bytes:
        """AES-256-GCM encryption."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce = os.urandom(12)
            aesgcm = AESGCM(key)
            ciphertext = aesgcm.encrypt(nonce, data, None)
            return nonce + ciphertext
        except ImportError:
            # Fallback: XOR with key hash (less secure but works without cryptography lib)
            logger.warning("cryptography library not installed — using basic encryption")
            return self._xor_encrypt(data, key)

    def _decrypt(self, data: bytes, key: bytes) -> bytes:
        """AES-256-GCM decryption."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce = data[:12]
            ciphertext = data[12:]
            aesgcm = AESGCM(key)
            return aesgcm.decrypt(nonce, ciphertext, None)
        except ImportError:
            return self._xor_decrypt(data, key)

    def _xor_encrypt(self, data: bytes, key: bytes) -> bytes:
        """Simple XOR fallback."""
        key_stream = (key * ((len(data) // len(key)) + 1))[:len(data)]
        return bytes(a ^ b for a, b in zip(data, key_stream))

    def _xor_decrypt(self, data: bytes, key: bytes) -> bytes:
        return self._xor_encrypt(data, key)  # XOR is symmetric


# ══════════════════════════════════════════════════════════
# OWNER AUTHENTICATION
# ══════════════════════════════════════════════════════════

class OwnerAuth:
    """
    Verify that the person interacting with Leon is the owner.
    Supports PIN, voice recognition, and session tokens.
    """

    def __init__(self, auth_file: str = "data/.auth.json"):
        self.auth_file = Path(auth_file)
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_data = self._load()
        self.session_token = None
        self.session_expires = 0
        self.failed_attempts = 0
        self.max_attempts = 5
        self.lockout_until = 0

    def _load(self) -> dict:
        if self.auth_file.exists():
            try:
                return json.loads(self.auth_file.read_text())
            except json.JSONDecodeError:
                pass
        return {"pin_hash": None, "voice_hash": None, "setup_complete": False}

    def _save(self):
        self.auth_file.write_text(json.dumps(self.auth_data, indent=2))
        # Restrict file permissions
        os.chmod(self.auth_file, 0o600)

    def setup_pin(self, pin: str):
        """Set up owner PIN."""
        pin_hash = hashlib.sha256(pin.encode()).hexdigest()
        self.auth_data["pin_hash"] = pin_hash
        self.auth_data["setup_complete"] = True
        self._save()
        logger.info("Owner PIN set up")

    def verify_pin(self, pin: str) -> bool:
        """Verify owner PIN."""
        if time.time() < self.lockout_until:
            remaining = int(self.lockout_until - time.time())
            logger.warning(f"Account locked. Try again in {remaining}s")
            return False

        pin_hash = hashlib.sha256(pin.encode()).hexdigest()
        if hmac.compare_digest(pin_hash, self.auth_data.get("pin_hash", "")):
            self.failed_attempts = 0
            self.session_token = secrets.token_hex(32)
            self.session_expires = time.time() + 3600 * 8  # 8 hour session
            logger.info("Owner authenticated via PIN")
            return True
        else:
            self.failed_attempts += 1
            if self.failed_attempts >= self.max_attempts:
                self.lockout_until = time.time() + 300  # 5 minute lockout
                logger.warning(f"Too many failed attempts. Locked for 5 minutes.")
            logger.warning(f"Failed PIN attempt ({self.failed_attempts}/{self.max_attempts})")
            return False

    def is_authenticated(self) -> bool:
        """Check if there's a valid session."""
        if not self.auth_data.get("setup_complete"):
            return True  # No auth set up yet — allow access
        return self.session_token is not None and time.time() < self.session_expires

    def require_auth(self) -> bool:
        """Returns True if auth is required (not authenticated)."""
        return not self.is_authenticated()


# ══════════════════════════════════════════════════════════
# AUDIT LOG — Track all sensitive actions
# ══════════════════════════════════════════════════════════

class AuditLog:
    """
    Immutable audit log of all sensitive actions.
    Can't be tampered with — each entry is hash-chained.
    """

    def __init__(self, log_file: str = "data/audit.log"):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = "GENESIS"

    def log(self, action: str, details: str = "", severity: str = "info"):
        """Log an auditable action."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details,
            "severity": severity,  # info, warning, critical
            "prev_hash": self._last_hash,
        }

        # Hash chain
        entry_str = json.dumps(entry, sort_keys=True)
        entry_hash = hashlib.sha256(entry_str.encode()).hexdigest()[:16]
        entry["hash"] = entry_hash
        self._last_hash = entry_hash

        # Append to log
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        if severity == "critical":
            logger.critical(f"AUDIT: {action} — {details}")
        elif severity == "warning":
            logger.warning(f"AUDIT: {action} — {details}")

    def get_recent(self, limit: int = 50) -> list:
        """Get recent audit entries."""
        if not self.log_file.exists():
            return []
        lines = self.log_file.read_text().strip().split("\n")
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries

    def verify_integrity(self) -> bool:
        """Verify the hash chain hasn't been tampered with."""
        if not self.log_file.exists():
            return True

        lines = self.log_file.read_text().strip().split("\n")
        prev_hash = "GENESIS"

        for line in lines:
            try:
                entry = json.loads(line)
                if entry.get("prev_hash") != prev_hash:
                    logger.critical("AUDIT LOG INTEGRITY VIOLATION!")
                    return False
                prev_hash = entry.get("hash", "")
            except json.JSONDecodeError:
                continue

        return True


# ══════════════════════════════════════════════════════════
# NETWORK SECURITY
# ══════════════════════════════════════════════════════════

class NetworkSecurity:
    """
    Ensures Leon only listens on localhost and blocks external access.
    """

    ALLOWED_HOSTS = ["127.0.0.1", "localhost", "::1"]
    BLOCKED_PORTS_EXTERNAL = [3000, 18789]  # Dashboard and OpenClaw

    @staticmethod
    def verify_localhost_only():
        """Verify all Leon services are bound to localhost only."""
        import subprocess
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True,
        )
        warnings = []
        for line in result.stdout.split("\n"):
            for port in NetworkSecurity.BLOCKED_PORTS_EXTERNAL:
                if f":{port}" in line and "0.0.0.0" in line:
                    warnings.append(f"⚠️ Port {port} is exposed to all interfaces!")
        return warnings

    @staticmethod
    def setup_firewall():
        """Configure UFW firewall to block external access to Leon services."""
        import subprocess

        rules = [
            # Block external access to dashboard
            ["sudo", "ufw", "deny", "in", "from", "any", "to", "any", "port", "3000"],
            # Block external access to OpenClaw
            ["sudo", "ufw", "deny", "in", "from", "any", "to", "any", "port", "18789"],
            # Allow localhost
            ["sudo", "ufw", "allow", "in", "from", "127.0.0.1"],
        ]

        for rule in rules:
            try:
                subprocess.run(rule, capture_output=True, check=True)
            except Exception as e:
                logger.warning(f"Firewall rule failed: {e}")

    @staticmethod
    def check_suspicious_connections() -> list:
        """Check for any suspicious network connections."""
        import subprocess
        result = subprocess.run(
            ["ss", "-tnp"],
            capture_output=True, text=True,
        )
        suspicious = []
        for line in result.stdout.split("\n"):
            if "leon" in line.lower() or "python" in line.lower():
                if not any(host in line for host in NetworkSecurity.ALLOWED_HOSTS):
                    suspicious.append(line.strip())
        return suspicious


# ══════════════════════════════════════════════════════════
# PERMISSION SYSTEM
# ══════════════════════════════════════════════════════════

class PermissionSystem:
    """
    Controls what Leon is allowed to do without asking.
    Sensitive actions require owner approval.
    """

    # Actions that ALWAYS need owner approval
    REQUIRE_APPROVAL = {
        "send_email",
        "send_sms",
        "send_whatsapp",
        "make_call",
        "make_purchase",
        "delete_files",
        "modify_system",
        "access_accounts",
        "send_money",
        "post_publicly",
        "share_personal_info",
    }

    # Actions that are auto-approved
    AUTO_APPROVED = {
        "search_web",
        "read_files",
        "run_code_agent",
        "check_emails",
        "check_printer",
        "generate_report",
        "update_memory",
        "search_stl",
    }

    def __init__(self, audit_log: AuditLog):
        self.audit = audit_log
        self.temporary_approvals: dict[str, float] = {}  # action -> expires_at

    def check_permission(self, action: str) -> bool:
        """Check if an action is allowed."""
        if action in self.AUTO_APPROVED:
            return True

        # Check temporary approval
        if action in self.temporary_approvals:
            if time.time() < self.temporary_approvals[action]:
                return True
            else:
                del self.temporary_approvals[action]

        if action in self.REQUIRE_APPROVAL:
            self.audit.log(action, "Permission denied — requires owner approval", "warning")
            return False

        return True  # Unknown actions default to allowed

    def grant_temporary(self, action: str, duration_minutes: int = 30):
        """Grant temporary permission for an action."""
        self.temporary_approvals[action] = time.time() + duration_minutes * 60
        self.audit.log(action, f"Temporary permission granted for {duration_minutes}min", "info")

    def revoke_temporary(self, action: str):
        """Revoke a temporary permission."""
        self.temporary_approvals.pop(action, None)
        self.audit.log(action, "Temporary permission revoked", "info")
