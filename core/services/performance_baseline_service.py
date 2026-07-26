"""Performance baseline schema and regression comparison."""

import json
import os
import tempfile
from pathlib import Path


class PerformanceBaselineError(ValueError):
    pass


class PerformanceBaselineService:
    SCHEMA_VERSION = 1

    @classmethod
    def compare(
        cls,
        current: dict,
        baseline: dict,
        *,
        threshold_percent: float = 10.0,
    ) -> list[dict]:
        cls.validate(current)
        cls.validate(baseline)
        regressions = []
        for name, current_metric in current["metrics"].items():
            previous = baseline["metrics"].get(name)
            if not isinstance(previous, dict):
                continue
            if not current_metric.get("lower_is_better", True):
                continue
            old_value = float(previous["value"])
            new_value = float(current_metric["value"])
            if old_value <= 0:
                continue
            change = ((new_value - old_value) / old_value) * 100
            if change > threshold_percent:
                regressions.append(
                    {
                        "metric": name,
                        "baseline": old_value,
                        "current": new_value,
                        "change_percent": round(change, 2),
                    }
                )
        return regressions

    @classmethod
    def validate(cls, baseline: dict) -> None:
        if baseline.get("schema_version") != cls.SCHEMA_VERSION:
            raise PerformanceBaselineError(
                "性能基线版本不兼容"
            )
        metrics = baseline.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            raise PerformanceBaselineError("性能基线缺少指标")
        for name, metric in metrics.items():
            if (
                not isinstance(name, str)
                or not isinstance(metric, dict)
                or isinstance(metric.get("value"), bool)
                or not isinstance(metric.get("value"), (int, float))
                or not isinstance(metric.get("unit"), str)
            ):
                raise PerformanceBaselineError(
                    f"性能指标无效：{name}"
                )

    @classmethod
    def write(cls, destination: str | Path, baseline: dict) -> Path:
        cls.validate(baseline)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    baseline,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return target

    @classmethod
    def read(cls, source: str | Path) -> dict:
        try:
            baseline = json.loads(
                Path(source).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise PerformanceBaselineError(
                "性能基线无法读取"
            ) from exc
        cls.validate(baseline)
        return baseline
