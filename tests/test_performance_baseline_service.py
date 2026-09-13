import tempfile
import unittest
from pathlib import Path

from core.services.performance_baseline_service import (
    PerformanceBaselineError,
    PerformanceBaselineService,
)


def baseline(value: float, *, lower=True) -> dict:
    return {
        "schema_version": 1,
        "metrics": {
            "latency": {
                "value": value,
                "unit": "ms",
                "lower_is_better": lower,
            }
        },
    }


class PerformanceBaselineTests(unittest.TestCase):
    def test_regression_above_ten_percent_is_reported(self):
        regressions = PerformanceBaselineService.compare(
            baseline(111),
            baseline(100),
        )

        self.assertEqual(regressions[0]["metric"], "latency")
        self.assertEqual(regressions[0]["change_percent"], 11.0)

    def test_improvement_and_capacity_metrics_are_not_regressions(self):
        self.assertEqual(
            PerformanceBaselineService.compare(
                baseline(90),
                baseline(100),
            ),
            [],
        )
        self.assertEqual(
            PerformanceBaselineService.compare(
                baseline(40, lower=False),
                baseline(44, lower=False),
            ),
            [],
        )

    def test_atomic_roundtrip_and_schema_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "performance.json"
            value = baseline(100)

            PerformanceBaselineService.write(path, value)

            self.assertEqual(
                PerformanceBaselineService.read(path),
                value,
            )
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
        with self.assertRaises(PerformanceBaselineError):
            PerformanceBaselineService.validate(
                {"schema_version": 2, "metrics": {}}
            )
        with self.assertRaises(PerformanceBaselineError):
            PerformanceBaselineService.validate([])
