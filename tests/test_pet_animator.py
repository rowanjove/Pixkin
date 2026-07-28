import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication

from core.pet_animator import PetAnimator, PetState


class PetAnimatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_lower_priority_state_cannot_interrupt_dragging(self):
        animator = PetAnimator()
        animator.set_state(PetState.DRAGGING)

        accepted = animator.request_state(PetState.THINKING)

        self.assertFalse(accepted)
        self.assertEqual(animator.current_state, PetState.DRAGGING)

    def test_transient_alert_restores_previous_edge_state(self):
        animator = PetAnimator()
        animator.set_state(PetState.EDGE_IDLE_LEFT)

        accepted = animator.request_state(
            PetState.ALERTING_IMPORTANT,
            duration_ms=1200,
            restore=True,
        )
        self.assertTrue(accepted)
        self.assertEqual(animator.current_state, PetState.ALERTING_IMPORTANT)

        animator.finish_transient()

        self.assertEqual(animator.current_state, PetState.EDGE_IDLE_LEFT)

    def test_forced_state_clears_pending_restore_context(self):
        animator = PetAnimator()
        animator.set_state(PetState.EDGE_IDLE_RIGHT)
        animator.request_state(
            PetState.ALERTING,
            duration_ms=1200,
            restore=True,
        )

        animator.set_state(PetState.DRAGGING)
        animator.finish_transient()

        self.assertEqual(animator.current_state, PetState.DRAGGING)

    def test_behavior_configuration_updates_ambient_profile(self):
        animator = PetAnimator()
        animator.configure_behavior({
            "idle_interval_seconds": [9, 18],
            "ambient_weights": {"blink": 5, "wave": 0.5},
            "cooldown_seconds": {"wave": 45},
            "speed_multiplier": 0.9,
        })

        self.assertEqual(animator.idle_interval_seconds, (9.0, 18.0))
        self.assertEqual(animator.ambient_weights[PetState.BLINK], 5.0)
        self.assertEqual(animator.cooldown_seconds[PetState.WAVE], 45.0)
        self.assertEqual(animator.speed_multiplier, 0.9)

    def test_character_animation_policy_controls_priority_and_interrupts(self):
        animator = PetAnimator()
        animator.configure_animation_policies({
            "working": SimpleNamespace(
                priority=25, interruptible=False
            ),
        })
        animator.set_state(PetState.WORKING)

        self.assertEqual(animator.priority_for(PetState.WORKING), 25)
        self.assertFalse(
            animator.request_state(PetState.ALERTING_IMPORTANT)
        )
        self.assertTrue(animator.request_state(PetState.DRAGGING))
        self.assertEqual(animator.current_state, PetState.DRAGGING)

    def test_full_character_gets_movement_ambient_actions(self):
        animator = PetAnimator()
        policies = {
            state.value: SimpleNamespace(
                priority=60, interruptible=True
            )
            for state in (
                PetState.WALK_LEFT,
                PetState.WALK_RIGHT,
                PetState.RUN_LEFT,
                PetState.RUN_RIGHT,
                PetState.JUMP,
                PetState.HAPPY,
            )
        }
        animator.configure_animation_policies(policies)
        animator.configure_behavior({
            "motion_temperament": "lively",
            "ambient_weights": {"blink": 5},
        })

        self.assertIn(PetState.WALK_LEFT, animator.ambient_weights)
        self.assertIn(PetState.WALK_RIGHT, animator.ambient_weights)
        self.assertIn(PetState.JUMP, animator.ambient_weights)
        self.assertGreater(
            animator.ambient_weights[PetState.RUN_RIGHT], 0
        )


if __name__ == "__main__":
    unittest.main()
