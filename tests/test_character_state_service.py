import tempfile
import unittest
from pathlib import Path

from core.services.character_state_service import CharacterStateService


class CharacterStateServiceTests(unittest.TestCase):
    def test_events_update_bounded_state_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "character-state.json"
            service = CharacterStateService(path)
            initial = service.get("shanshan")
            service.transition("shanshan", "idle_tick", occurred_at="t1")
            engaged = service.transition("shanshan", "user_interaction", occurred_at="t2")
            self.assertGreater(engaged.affinity, initial.affinity)
            self.assertLessEqual(engaged.mood, 1.0)
            reloaded = CharacterStateService(path)
            self.assertEqual(reloaded.get("shanshan").last_interaction, "t2")

    def test_unknown_event_cannot_arbitrarily_write_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CharacterStateService(Path(directory) / "state.json")
            before = service.get("c")
            after = service.transition("c", "llm_set_energy", environment="secret")
            self.assertEqual(after.energy, before.energy)
            self.assertEqual(after.environment, "secret")

    def test_behavior_choice_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CharacterStateService(Path(directory) / "state.json")
            candidates = [("wave", 1), ("sleep", 2), ("stretch", 3)]
            self.assertEqual(
                service.choose_behavior(candidates, seed=7),
                service.choose_behavior(candidates, seed=7),
            )
            self.assertIsNone(service.choose_behavior([], seed=7))

    def test_malformed_root_is_rejected_without_attribute_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot be read safely"):
                CharacterStateService(path)


if __name__ == "__main__":
    unittest.main()
