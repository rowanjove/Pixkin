import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from core.plugins.host import PluginHost, PluginProcessError
from core.plugins.manifest import PluginManifest, PluginManifestError
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionResource,
    PermissionState,
)


PLUGIN_CODE = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        if method == "initialize":
            result = {"api_version": request["params"]["api_version"]}
        elif method == "echo":
            result = {"value": request["params"].get("value", "")}
        elif method == "shutdown":
            result = {"stopped": True}
            print(json.dumps({"jsonrpc":"2.0", "id":request["id"], "result":result}), flush=True)
            break
        else:
            result = {"method": method}
        print(json.dumps({"jsonrpc":"2.0", "id":request["id"], "result":result}), flush=True)
    """
)


class PluginHostTests(unittest.TestCase):
    def test_manifest_is_strict_and_requires_process_isolation(self):
        with self.assertRaises(PluginManifestError):
            PluginManifest.from_dict({
                "schema_version": 1,
                "id": "unsafe",
                "name": "Unsafe",
                "version": "1.0.0",
                "command": ["python"],
                "isolation": "in_process",
            })

    def test_host_starts_calls_and_stops_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "plugin.py"
            script.write_text(PLUGIN_CODE, encoding="utf-8")
            manifest_path = root / "echo.json"
            manifest_path.write_text(json.dumps({
                "schema_version": 1,
                "id": "echo",
                "name": "Echo",
                "version": "1.0.0",
                "api_version": 1,
                "command": [sys.executable, "-u", str(script)],
                "capabilities": ["tools"],
                "permissions": {},
                "isolation": "process",
            }), encoding="utf-8")
            permissions = ContextPermissionService()
            permissions.set_state(
                PermissionResource.PLUGIN,
                PermissionState.ALLOW_SESSION,
            )
            host = PluginHost(root, permission_service=permissions)
            manifest = host.discover()[0]
            process = host.start(manifest)
            self.assertEqual(process.call("echo", {"value": "ok"})["value"], "ok")
            self.assertEqual(host.running_ids(), ("echo",))
            self.assertTrue(host.stop("echo"))
            self.assertFalse(process.running)

    def test_safe_mode_never_discovers_or_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            host = PluginHost(directory, safe_mode=True)
            self.assertEqual(host.discover(), ())
            manifest = PluginManifest.from_dict({
                "schema_version": 1,
                "id": "safe",
                "name": "Safe",
                "version": "1.0.0",
                "command": [sys.executable, "-c", "pass"],
            })
            with self.assertRaises(PluginProcessError):
                host.start(manifest)

    def test_host_without_permission_service_defaults_to_deny(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = PluginManifest.from_dict(
                {
                    "schema_version": 1,
                    "id": "deny",
                    "name": "Deny",
                    "version": "1.0.0",
                    "api_version": 1,
                    "command": [sys.executable, "-c", "pass"],
                },
                path=root / "deny.json",
            )
            with self.assertRaises(PluginProcessError):
                PluginHost(root).start(manifest)


if __name__ == "__main__":
    unittest.main()
