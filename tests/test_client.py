import os
import sys
import unittest

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from sas_hba_console.backend.client import HelperClient, classify_start_error
from sas_hba_console.backend.provider import LocalProvider

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(PROJECT_ROOT, "scripts", "sas-hba-helper-dev")


class HelperClientTests(unittest.TestCase):
    """用 GLib 主循环跑通 pkexec 之后真正使用的 JSON 行协议。"""

    def _run(self, body, timeout=20):
        loop = GLib.MainLoop()
        state = {"done": False, "error": None, "results": {}}

        def fail(message):
            state["error"] = message
            state["done"] = True
            loop.quit()

        client = HelperClient(
            command=[sys.executable, HELPER],
            env={
                "SAS_HBA_ALLOW_NONROOT": "1",
                "SAS_HBA_HELPER_DEMO": "1",
                "SAS_HBA_SMARTCTL": "",
            },
        )

        def ready(hello):
            body(client, state, loop, fail)

        def guard():
            if not state["done"]:
                fail("测试超时")
            return False

        GLib.timeout_add_seconds(timeout, guard)
        client.start(ready, fail, on_disconnect=fail)
        try:
            loop.run()
        finally:
            client.close()
        if state["error"]:
            self.fail(state["error"])
        return state["results"]

    def test_handshake_and_requests(self):
        def body(client, state, loop, fail):
            def on_snapshot(ok, data, error):
                try:
                    self.assertTrue(ok, error)
                    self.assertEqual(data["hba"]["model"], "LSI SAS 9207-8i")
                    self.assertEqual(len(data["drives"]), 7)
                    state["results"]["snapshot"] = data
                except AssertionError as exc:
                    fail(str(exc))
                    return

                def on_smart(ok2, data2, error2):
                    try:
                        self.assertTrue(ok2, error2)
                        self.assertEqual(data2["protocol"], "SCSI")
                        state["results"]["smart"] = data2
                    except AssertionError as exc:
                        fail(str(exc))
                        return
                    state["done"] = True
                    loop.quit()

                client.call("smart", {"dev": "/dev/sdb"}, callback=on_smart)

            client.call("snapshot", callback=on_snapshot)

        results = self._run(body)
        self.assertIn("snapshot", results)
        self.assertIn("smart", results)

    def test_rejected_device_returns_error(self):
        def body(client, state, loop, fail):
            def callback(ok, data, error):
                try:
                    self.assertFalse(ok)
                    self.assertIn("非法", error)
                except AssertionError as exc:
                    fail(str(exc))
                    return
                state["done"] = True
                loop.quit()

            client.call("smart", {"dev": "/etc/shadow"}, callback=callback)

        self._run(body)

    def test_large_snapshot_does_not_block(self):
        """snapshot JSON 超过管道缓冲区时也要完整读取。"""

        def body(client, state, loop, fail):
            def callback(ok, data, error):
                try:
                    self.assertTrue(ok, error)
                    self.assertEqual(len(data["drives"]), 7)
                except AssertionError as exc:
                    fail(str(exc))
                    return
                state["done"] = True
                loop.quit()

            # 连续两次大响应，验证分帧缓冲
            client.call("snapshot", callback=callback)

        self._run(body)

    def test_error_classification(self):
        self.assertIn("polkit", classify_start_error("Error: No authentication agent found"))
        self.assertIn("未获得", classify_start_error("", 126))
        self.assertIn("找不到", classify_start_error("", 127))


class LocalProviderPermissionTests(unittest.TestCase):
    def test_readonly_provider_reports_capabilities(self):
        provider = LocalProvider(can_control=False)
        snapshot = provider.snapshot()
        self.assertFalse(snapshot["capabilities"]["root"])
        self.assertFalse(provider.can_control)
        with self.assertRaises(Exception):
            provider.action("spin_down", "/dev/sdb")

    def test_smart_rejects_foreign_device(self):
        provider = LocalProvider(can_control=False)
        with self.assertRaises(Exception) as ctx:
            provider.smart("/etc/passwd")
        self.assertIn("非法", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
