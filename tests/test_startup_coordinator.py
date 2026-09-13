import unittest

from core.runtime.startup import StartupCoordinator, StartupError, StartupStep


class StartupCoordinatorTests(unittest.TestCase):
    def test_steps_are_ordered_and_named(self):
        calls = []
        coordinator = StartupCoordinator(
            [StartupStep("config", lambda: calls.append("config")), StartupStep("ui", lambda: calls.append("ui"))]
        )
        self.assertEqual(coordinator.run(), ("config", "ui"))
        self.assertEqual(calls, ["config", "ui"])

    def test_failure_is_wrapped_and_records_completed_prefix(self):
        coordinator = StartupCoordinator(
            [StartupStep("config", lambda: None), StartupStep("db", lambda: (_ for _ in ()).throw(ValueError("bad")))]
        )
        with self.assertRaises(StartupError):
            coordinator.run()
        self.assertEqual(coordinator.completed, ["config"])


if __name__ == "__main__":
    unittest.main()
