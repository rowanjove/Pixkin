import unittest

from core.providers.live import (
    LiveProvider,
    LiveProviderCapabilities,
    LiveProviderHealth,
    LiveStatus,
)
from core.services.live_provider_registry import (
    LiveProviderPluginError,
    LiveProviderRegistry,
)


class ContractProvider(LiveProvider):
    platform = "contract"
    api_version = 1
    credential_scope = "per_room"

    @property
    def capabilities(self):
        return LiveProviderCapabilities()

    def check(self, room):
        return LiveStatus(False)

    def health_check(self):
        return LiveProviderHealth(True, "ok")

    def close(self):
        return None


class UnsafeProvider(ContractProvider):
    @property
    def capabilities(self):
        return LiveProviderCapabilities(safe_url_validation=False)


class Point:
    def __init__(self, name, provider_type):
        self.name = name
        self.provider_type = provider_type

    def load(self):
        return self.provider_type


class LiveProviderRegistryTests(unittest.TestCase):
    def test_only_explicitly_enabled_contract_adapters_load(self):
        registry = LiveProviderRegistry()
        loaded = registry.load_enabled(
            ["contract"],
            entry_points=[
                Point("contract", ContractProvider),
                Point("ignored", ContractProvider),
            ],
        )

        self.assertEqual(set(loaded), {"contract"})

    def test_unsafe_adapter_fails_contract_before_use(self):
        registry = LiveProviderRegistry()
        with self.assertRaises(LiveProviderPluginError):
            registry.load_enabled(
                ["contract"],
                entry_points=[Point("contract", UnsafeProvider)],
            )


if __name__ == "__main__":
    unittest.main()
