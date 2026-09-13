import unittest

from core.providers.registry import ProviderDescriptor, ProviderRegistry
from core.tool_registry import ToolRegistry
from core.services.tool_permission_service import ToolPermissionLevel


class FakeProvider:
    def __init__(self, value="ok"):
        self.value = value
        self.closed = False

    def health_check(self):
        return self.value

    def close(self):
        self.closed = True


class ProviderRegistryTests(unittest.TestCase):
    def test_registry_rejects_kind_mismatch_and_duplicates(self):
        registry = ProviderRegistry("chat")
        descriptor = ProviderDescriptor("fake", "chat", {"streaming": True})
        registry.register(descriptor, FakeProvider)
        with self.assertRaises(KeyError):
            registry.register(descriptor, FakeProvider)
        with self.assertRaises(ValueError):
            registry.register(ProviderDescriptor("image", "image"), FakeProvider)

    def test_registry_creates_and_runs_health_with_cleanup(self):
        registry = ProviderRegistry("chat")
        registry.register(ProviderDescriptor("fake", "chat"), FakeProvider)
        self.assertEqual(registry.create("fake", value="healthy").value, "healthy")
        self.assertEqual(registry.health_check("fake", value="ready"), "ready")

    def test_tool_metadata_marks_external_tools_for_confirmation(self):
        registry = ToolRegistry()
        metadata = registry.get_tool_metadata("open_url")
        self.assertEqual(metadata.source, "builtin")
        self.assertEqual(metadata.permission_level, ToolPermissionLevel.EXTERNAL_ACTION)
        self.assertTrue(metadata.requires_confirmation)


if __name__ == "__main__":
    unittest.main()
