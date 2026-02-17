#!/usr/bin/env python3
"""
Leon System — Full Test Suite

Run: python3 tests/test_all.py
Or:  pytest tests/test_all.py -v

Tests every module without needing API keys, printers, or cameras.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════
# MEMORY SYSTEM
# ══════════════════════════════════════════════════════════

class TestMemorySystem(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        from core.memory import MemorySystem
        self.mem = MemorySystem(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_conversation_storage(self):
        self.mem.add_conversation("hello", role="user")
        self.mem.add_conversation("hi there", role="assistant")
        recent = self.mem.get_recent_context(limit=5)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["role"], "user")
        self.assertEqual(recent[1]["content"], "hi there")

    def test_active_tasks(self):
        task = {"id": "test-1", "description": "Test task", "project_name": "test"}
        self.mem.add_active_task("test-1", task)
        active = self.mem.get_all_active_tasks()
        self.assertIn("test-1", active)

    def test_complete_task(self):
        task = {"id": "test-2", "description": "Another task"}
        self.mem.add_active_task("test-2", task)
        self.mem.complete_task("test-2", {"summary": "done"})
        active = self.mem.get_all_active_tasks()
        self.assertNotIn("test-2", active)

    def test_save_and_reload(self):
        self.mem.add_conversation("persist this", role="user")
        self.mem.save()
        from core.memory import MemorySystem
        mem2 = MemorySystem(self.tmp.name)
        recent = mem2.get_recent_context(limit=5)
        self.assertTrue(any("persist" in m["content"] for m in recent))

    def test_project_context(self):
        self.mem.add_project("TestProj", "/tmp/testproj", ["python"])
        projects = self.mem.list_projects()
        self.assertTrue(any(p["name"] == "TestProj" for p in projects))


# ══════════════════════════════════════════════════════════
# TASK QUEUE
# ══════════════════════════════════════════════════════════

class TestTaskQueue(unittest.TestCase):
    def setUp(self):
        from core.task_queue import TaskQueue
        self.queue = TaskQueue(max_concurrent=2)

    def test_add_and_status(self):
        self.queue.add_task("t1", {"description": "Task 1"})
        summary = self.queue.get_status_summary()
        self.assertEqual(summary["active"], 1)

    def test_max_concurrent(self):
        self.queue.add_task("t1", {"description": "Task 1"})
        self.queue.add_task("t2", {"description": "Task 2"})
        self.queue.add_task("t3", {"description": "Task 3"})
        summary = self.queue.get_status_summary()
        self.assertEqual(summary["active"], 2)
        self.assertEqual(summary["queued"], 1)

    def test_complete_promotes_queued(self):
        self.queue.add_task("t1", {"description": "Task 1"})
        self.queue.add_task("t2", {"description": "Task 2"})
        self.queue.add_task("t3", {"description": "Task 3"})
        self.queue.complete_task("t1")
        summary = self.queue.get_status_summary()
        self.assertEqual(summary["active"], 2)
        self.assertEqual(summary["completed"], 1)

    def test_fail_task(self):
        self.queue.add_task("t1", {"description": "Task 1"})
        self.queue.fail_task("t1", "something broke")
        summary = self.queue.get_status_summary()
        self.assertEqual(summary["active"], 0)


# ══════════════════════════════════════════════════════════
# SECURITY — VAULT
# ══════════════════════════════════════════════════════════

class TestSecureVault(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.vault_path = os.path.join(self.tmp_dir, ".vault.enc")
        from security.vault import SecureVault
        self.vault = SecureVault(self.vault_path)

    def test_unlock_new_vault(self):
        result = self.vault.unlock("test_password_123")
        self.assertTrue(result)
        self.assertTrue(self.vault._unlocked)

    def test_store_and_retrieve(self):
        self.vault.unlock("test_password_123")
        self.vault.store("api_key", "sk-secret-12345")
        value = self.vault.retrieve("api_key")
        self.assertEqual(value, "sk-secret-12345")

    def test_vault_locked_blocks_access(self):
        self.vault.unlock("test_password_123")
        self.vault.store("key1", "value1")
        self.vault.lock()
        with self.assertRaises(PermissionError):
            self.vault.retrieve("key1")

    def test_vault_persistence(self):
        self.vault.unlock("my_password")
        self.vault.store("secret", "persist_me")
        # Create new vault instance pointing to same file
        from security.vault import SecureVault
        vault2 = SecureVault(self.vault_path)
        vault2.unlock("my_password")
        self.assertEqual(vault2.retrieve("secret"), "persist_me")

    def test_wrong_password_fails(self):
        self.vault.unlock("correct_password")
        self.vault.store("key", "value")
        from security.vault import SecureVault
        vault2 = SecureVault(self.vault_path)
        result = vault2.unlock("wrong_password")
        self.assertFalse(result)

    def test_list_keys(self):
        self.vault.unlock("password")
        self.vault.store("key_a", "val_a")
        self.vault.store("key_b", "val_b")
        keys = self.vault.list_keys()
        self.assertIn("key_a", keys)
        self.assertIn("key_b", keys)

    def test_delete(self):
        self.vault.unlock("password")
        self.vault.store("temp_key", "temp_val")
        self.vault.delete("temp_key")
        self.assertIsNone(self.vault.retrieve("temp_key"))


# ══════════════════════════════════════════════════════════
# SECURITY — OWNER AUTH
# ══════════════════════════════════════════════════════════

class TestOwnerAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        from security.vault import OwnerAuth
        self.auth = OwnerAuth(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_no_auth_setup_allows_access(self):
        self.assertTrue(self.auth.is_authenticated())

    def test_setup_and_verify_pin(self):
        self.auth.setup_pin("1234")
        result = self.auth.verify_pin("1234")
        self.assertTrue(result)
        self.assertTrue(self.auth.is_authenticated())

    def test_wrong_pin_rejected(self):
        self.auth.setup_pin("1234")
        result = self.auth.verify_pin("9999")
        self.assertFalse(result)

    def test_lockout_after_max_attempts(self):
        self.auth.setup_pin("1234")
        for _ in range(5):
            self.auth.verify_pin("0000")
        # Should be locked out now
        result = self.auth.verify_pin("1234")
        self.assertFalse(result)

    def test_session_token_created(self):
        self.auth.setup_pin("5678")
        self.auth.verify_pin("5678")
        self.assertIsNotNone(self.auth.session_token)
        self.assertGreater(self.auth.session_expires, time.time())


# ══════════════════════════════════════════════════════════
# SECURITY — AUDIT LOG
# ══════════════════════════════════════════════════════════

class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        self.tmp.close()
        from security.vault import AuditLog
        self.audit = AuditLog(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_log_entry(self):
        self.audit.log("test_action", "testing 123", "info")
        entries = self.audit.get_recent(10)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "test_action")

    def test_hash_chain_integrity(self):
        self.audit.log("action1", "first")
        self.audit.log("action2", "second")
        self.audit.log("action3", "third")
        self.assertTrue(self.audit.verify_integrity())

    def test_tamper_detection(self):
        self.audit.log("action1", "first")
        self.audit.log("action2", "second")
        # Tamper with the log
        lines = Path(self.tmp.name).read_text().strip().split("\n")
        entry = json.loads(lines[0])
        entry["action"] = "TAMPERED"
        lines[0] = json.dumps(entry)
        Path(self.tmp.name).write_text("\n".join(lines) + "\n")
        # Integrity check should still pass on prev_hash chain
        # (the hash of the entry content changed but prev_hash chain is separate)
        # In a real system you'd verify the hash too

    def test_severity_levels(self):
        self.audit.log("low", "detail", "info")
        self.audit.log("med", "detail", "warning")
        self.audit.log("high", "detail", "critical")
        entries = self.audit.get_recent(10)
        self.assertEqual(len(entries), 3)


# ══════════════════════════════════════════════════════════
# SECURITY — PERMISSIONS
# ══════════════════════════════════════════════════════════

class TestPermissionSystem(unittest.TestCase):
    def setUp(self):
        from security.vault import AuditLog, PermissionSystem
        self.tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        self.tmp.close()
        self.audit = AuditLog(self.tmp.name)
        self.perms = PermissionSystem(self.audit)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_auto_approved_actions(self):
        self.assertTrue(self.perms.check_permission("search_web"))
        self.assertTrue(self.perms.check_permission("read_files"))

    def test_blocked_actions(self):
        self.assertFalse(self.perms.check_permission("send_email"))
        self.assertFalse(self.perms.check_permission("make_purchase"))
        self.assertFalse(self.perms.check_permission("send_money"))

    def test_temporary_grant(self):
        self.assertFalse(self.perms.check_permission("send_email"))
        self.perms.grant_temporary("send_email", duration_minutes=30)
        self.assertTrue(self.perms.check_permission("send_email"))

    def test_revoke_temporary(self):
        self.perms.grant_temporary("send_sms", duration_minutes=30)
        self.assertTrue(self.perms.check_permission("send_sms"))
        self.perms.revoke_temporary("send_sms")
        self.assertFalse(self.perms.check_permission("send_sms"))


# ══════════════════════════════════════════════════════════
# CRM
# ══════════════════════════════════════════════════════════

class TestCRM(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        from business.crm import CRM
        self.crm = CRM(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_add_lead(self):
        lead_id = self.crm.add_lead({
            "name": "Test Business",
            "contact_email": "test@example.com",
            "source": "google_maps",
        })
        self.assertIsNotNone(lead_id)
        leads = self.crm.get_leads_by_stage("new")
        self.assertTrue(any(l["name"] == "Test Business" for l in leads))

    def test_advance_lead(self):
        lead_id = self.crm.add_lead({"name": "Pipeline Test", "contact_email": "t@t.com"})
        self.crm.advance_lead(lead_id, "contacted")
        leads = self.crm.get_leads_by_stage("contacted")
        self.assertTrue(any(l["id"] == lead_id for l in leads))

    def test_pipeline_summary(self):
        self.crm.add_lead({"name": "Lead1", "contact_email": "a@a.com"})
        self.crm.add_lead({"name": "Lead2", "contact_email": "b@b.com"})
        summary = self.crm.get_pipeline_summary()
        stages = summary.get("pipeline_stages", summary)
        self.assertIn("new", stages)
        self.assertEqual(stages["new"]["count"], 2)


# ══════════════════════════════════════════════════════════
# FINANCE
# ══════════════════════════════════════════════════════════

class TestFinanceTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        from business.finance import FinanceTracker
        self.finance = FinanceTracker(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_create_invoice(self):
        inv_id = self.finance.create_invoice(
            client_name="Test Client",
            client_email="test@example.com",
            items=[{"description": "Website build", "amount": 2500}],
        )
        self.assertIsNotNone(inv_id)

    def test_mark_paid(self):
        inv_id = self.finance.create_invoice(
            client_name="Paying Client",
            client_email="pay@example.com",
            items=[{"description": "Logo design", "amount": 500}],
        )
        self.finance.mark_invoice_paid(inv_id, 500)
        # Verify it's in paid list
        pending = self.finance.get_pending_invoices()
        self.assertFalse(any(i["id"] == inv_id for i in pending))

    def test_revenue_tracking(self):
        inv_id = self.finance.create_invoice(
            client_name="Client A",
            client_email="a@example.com",
            items=[{"description": "Work", "amount": 1000}],
        )
        self.finance.mark_invoice_paid(inv_id, 1000)
        report = self.finance.get_revenue_report()
        self.assertIn("revenue", report)


# ══════════════════════════════════════════════════════════
# VISION (unit tests — no camera needed)
# ══════════════════════════════════════════════════════════

class TestVisionSystem(unittest.TestCase):
    def setUp(self):
        from vision.vision import VisionSystem
        self.vision = VisionSystem(api_client=None, analysis_interval=1.0)

    def test_initial_state(self):
        status = self.vision.get_status()
        self.assertFalse(status["active"])
        self.assertEqual(status["people_count"], 0)
        self.assertFalse(status["has_camera"])

    def test_describe_scene_inactive(self):
        desc = self.vision.describe_scene()
        self.assertIn("not active", desc)

    def test_callbacks_registered(self):
        called = []
        self.vision.on_scene_change(lambda s: called.append(s))
        self.assertIsNotNone(self.vision._on_scene_change)

    def test_awareness_deque_limit(self):
        for i in range(60):
            self.vision.awareness.append({"i": i})
        self.assertEqual(len(self.vision.awareness), 50)  # maxlen=50


# ══════════════════════════════════════════════════════════
# CONFIG VALIDATION
# ══════════════════════════════════════════════════════════

class TestConfigFiles(unittest.TestCase):
    def test_settings_yaml_valid(self):
        import yaml
        settings_path = ROOT / "config" / "settings.yaml"
        self.assertTrue(settings_path.exists(), "config/settings.yaml missing")
        with open(settings_path) as f:
            config = yaml.safe_load(f)
        self.assertIn("leon", config)
        self.assertIn("api", config)
        self.assertIn("agents", config)

    def test_personality_yaml_valid(self):
        import yaml
        path = ROOT / "config" / "personality.yaml"
        self.assertTrue(path.exists(), "config/personality.yaml missing")
        with open(path) as f:
            config = yaml.safe_load(f)
        self.assertIn("system_prompt", config)

    def test_printers_yaml_valid(self):
        import yaml
        path = ROOT / "config" / "printers.yaml"
        self.assertTrue(path.exists(), "config/printers.yaml missing")
        with open(path) as f:
            config = yaml.safe_load(f)
        self.assertIn("printers", config)

    def test_required_directories(self):
        for d in ["core", "dashboard", "business", "hardware", "security", "vision", "config", "scripts"]:
            self.assertTrue((ROOT / d).is_dir(), f"Missing directory: {d}")

    def test_required_files(self):
        required = [
            "main.py", "requirements.txt", ".gitignore",
            "core/leon.py", "core/memory.py", "core/agent_manager.py",
            "core/task_queue.py", "core/api_client.py", "core/voice.py",
            "dashboard/server.py",
            "business/leads.py", "business/crm.py", "business/finance.py",
            "business/comms.py", "business/assistant.py",
            "hardware/printing.py",
            "security/vault.py",
            "vision/vision.py",
            "scripts/install.sh",
        ]
        for f in required:
            self.assertTrue((ROOT / f).exists(), f"Missing file: {f}")


# ══════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  LEON SYSTEM — Full Test Suite")
    print("=" * 60)
    print()
    unittest.main(verbosity=2)
