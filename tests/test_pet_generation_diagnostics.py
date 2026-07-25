import json
import tempfile
import unittest
from pathlib import Path

from core.pet_generation_diagnostics import PetGenerationDiagnostics


class PetGenerationDiagnosticsTests(unittest.TestCase):
    @staticmethod
    def _task(status, attempts=0, candidates=None):
        return {
            "status": status,
            "attempts": attempts,
            "candidates": candidates or [],
            "error": None,
        }

    def test_eta_uses_observed_retry_cost_and_excludes_safe_mirrors(self):
        record = {
            "id": "eta-run",
            "status": "needs_review",
            "stage": "core_review",
            "request": {
                "allow_horizontal_mirror": True,
                "max_api_calls": 10,
            },
            "tasks": {
                "canonical": self._task("complete", 1),
                "run_right": self._task("complete", 2),
                "run_left": self._task("pending"),
                "idle": self._task("pending"),
            },
            "metrics": {
                "api_calls": [
                    {
                        "outcome": "success",
                        "duration_ms": 1000,
                        "error_category": None,
                    },
                    {
                        "outcome": "failed",
                        "duration_ms": 500,
                        "error_category": "rate_limit",
                    },
                    {
                        "outcome": "success",
                        "duration_ms": 1500,
                        "error_category": None,
                    },
                ]
            },
            "artifacts": {},
            "error": None,
        }

        report = PetGenerationDiagnostics.summarize(record)

        self.assertEqual(report["health"], "review")
        self.assertTrue(report["waiting_for_review"])
        self.assertEqual(report["api"]["remaining_calls"], 1)
        self.assertEqual(
            report["api"]["average_success_cycle_ms"],
            1500,
        )
        self.assertEqual(
            report["api"]["estimated_remaining_ms"],
            1500,
        )
        self.assertEqual(
            report["errors"]["categories"],
            {"rate_limit": 1},
        )
        self.assertIn(
            "批准后约 2 秒",
            PetGenerationDiagnostics.compact_text(report),
        )

    def test_health_blocks_when_budget_cannot_cover_remaining_calls(self):
        record = {
            "id": "blocked-run",
            "status": "pending",
            "stage": "action_generation",
            "request": {"max_api_calls": 1},
            "tasks": {
                "canonical": self._task("failed", 1),
                "idle": self._task("pending"),
            },
            "metrics": {"api_calls": []},
            "artifacts": {},
            "error": "budget",
        }

        report = PetGenerationDiagnostics.summarize(record)

        self.assertEqual(report["health"], "blocked")
        self.assertEqual(report["api"]["budget_remaining"], 0)
        self.assertEqual(report["api"]["remaining_calls"], 2)
        self.assertIsNone(report["api"]["estimated_remaining_ms"])

    def test_diagnostic_report_is_written_as_json(self):
        record = {
            "id": "write-run",
            "status": "failed",
            "stage": "failed",
            "request": {},
            "tasks": {
                "canonical": self._task("failed", 1),
            },
            "metrics": {"api_calls": []},
            "artifacts": {},
            "error": "authentication failed",
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "qa" / "diagnostic.json"

            report = PetGenerationDiagnostics.write(
                record,
                destination,
            )

            parsed = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(parsed["run_id"], "write-run")
            self.assertEqual(parsed["health"], "critical")
            self.assertEqual(parsed, report)
            self.assertEqual(
                list(destination.parent.glob(
                    ".generation-diagnostic-*.tmp"
                )),
                [],
            )


if __name__ == "__main__":
    unittest.main()
