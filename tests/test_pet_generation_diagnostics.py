import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.pet_generation_diagnostics import (
    IssueBundleError,
    PetGenerationDiagnostics,
)


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

    def test_issue_bundle_is_whitelisted_and_anonymous(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "run"
            reports = workspace / "qa"
            reports.mkdir(parents=True)
            qa_report = reports / "qa-report.json"
            qa_report.write_text(
                json.dumps({
                    "path": str(workspace / "images" / "secret.png"),
                    "message": "Bearer secret-token-123456",
                    "endpoint":
                        "https://user:pass@example.test/v1?api_key=hidden",
                }),
                encoding="utf-8",
            )
            diagnostic_report = reports / "diagnostic.json"
            diagnostic_report.write_text(
                json.dumps({
                    "api_key": "sk-supersecret123456",
                    "pet_name": "Private Pet",
                    "note": str(workspace / "references" / "face.png"),
                }),
                encoding="utf-8",
            )
            outside = base / "outside.json"
            outside.write_text('{"leak": "must-not-ship"}', encoding="utf-8")
            record = {
                "id": "private-pet-run",
                "status": "failed",
                "stage": "failed",
                "request": {
                    "mode": "standard",
                    "pet_name": "Private Pet",
                    "personality": "private personality",
                    "style_notes": "private style",
                    "api_key": "sk-supersecret123456",
                    "reference_paths": [
                        str(workspace / "references" / "face.png")
                    ],
                    "image_base_url":
                        "https://example.test/v1?token=hidden",
                    "image_model": "test-image",
                    "image_quality": "low",
                    "max_api_calls": 23,
                    "capability_preflight": {
                        "status": "unverified",
                        "endpoint":
                            "https://user:pass@example.test/v1?key=hidden",
                        "note": str(workspace / "private" / "trace.txt"),
                    },
                },
                "tasks": {
                    "canonical": {
                        **self._task("failed", 1),
                        "error": (
                            f"failed at {workspace / 'private.png'} "
                            "with sk-anothersecret123"
                        ),
                    },
                },
                "metrics": {
                    "api_calls": [{
                        "task_id": "canonical",
                        "outcome": "failed",
                        "duration_ms": 100,
                        "error_category": "authentication",
                    }]
                },
                "artifacts": {
                    "qa_report": str(qa_report),
                    "generation_diagnostic_report":
                        str(diagnostic_report),
                    "static_qa_report": str(outside),
                    "preview": str(workspace / "images" / "private.png"),
                },
                "error": "Private Pet sk-supersecret123456",
            }
            destination = base / "issue.zip"

            result = PetGenerationDiagnostics.build_issue_bundle(
                record,
                workspace=workspace,
                destination=destination,
            )

            self.assertTrue(destination.is_file())
            self.assertTrue(
                result["anonymous_run_id"].startswith("anonymous-")
            )
            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
                self.assertEqual(
                    names,
                    {
                        "manifest.json",
                        "diagnostic.json",
                        "run-summary.json",
                        "reports/qa_report.json",
                    },
                )
                combined = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in names
                )
                manifest = json.loads(
                    archive.read("manifest.json")
                )
            for secret in (
                "Private Pet",
                "private personality",
                "private style",
                "sk-supersecret123456",
                "sk-anothersecret123",
                "secret-token-123456",
                "must-not-ship",
                str(workspace),
                "api_key=hidden",
                "token=hidden",
            ):
                self.assertNotIn(secret, combined)
            self.assertFalse(any(
                name.lower().endswith((".png", ".jpg", ".webp"))
                for name in names
            ))
            self.assertFalse(
                manifest["privacy"]["images_included"]
            )
            self.assertFalse(
                manifest["privacy"]["api_keys_included"]
            )
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(
                set(manifest["files"]),
                names - {"manifest.json"},
            )

            inspection = (
                PetGenerationDiagnostics.inspect_issue_bundle(destination)
            )
            self.assertTrue(inspection["valid"])
            self.assertEqual(
                inspection["anonymous_run_id"],
                result["anonymous_run_id"],
            )
            self.assertEqual(set(inspection["contents"]), names)
            self.assertEqual(
                set(inspection["reports"]),
                {"reports/qa_report.json"},
            )

    def test_issue_bundle_inspection_rejects_tampered_content(self):
        record = {
            "id": "tamper-test",
            "status": "pending",
            "stage": "created",
            "request": {},
            "tasks": {"canonical": self._task("pending")},
            "metrics": {"api_calls": []},
            "artifacts": {},
            "error": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "issue.zip"
            PetGenerationDiagnostics.build_issue_bundle(
                record,
                workspace=base,
                destination=source,
            )
            with zipfile.ZipFile(source) as archive:
                documents = {
                    name: archive.read(name)
                    for name in archive.namelist()
                }
            documents["diagnostic.json"] = documents[
                "diagnostic.json"
            ].replace(b'"healthy"', b'"altered"', 1)
            tampered = base / "tampered.zip"
            with zipfile.ZipFile(tampered, "w") as archive:
                for name, payload in documents.items():
                    archive.writestr(name, payload)

            with self.assertRaisesRegex(
                IssueBundleError,
                "哈希校验失败",
            ):
                PetGenerationDiagnostics.inspect_issue_bundle(tampered)

    def test_issue_bundle_inspection_rejects_non_whitelisted_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("manifest.json", "{}")
                archive.writestr("diagnostic.json", "{}")
                archive.writestr("run-summary.json", "{}")
                archive.writestr("../private.png", b"not-an-image")

            with self.assertRaisesRegex(
                IssueBundleError,
                "不受支持",
            ):
                PetGenerationDiagnostics.inspect_issue_bundle(source)

    def test_issue_bundle_inspection_rejects_malformed_manifest_lists(self):
        record = {
            "id": "malformed-manifest",
            "status": "pending",
            "stage": "created",
            "request": {},
            "tasks": {"canonical": self._task("pending")},
            "metrics": {"api_calls": []},
            "artifacts": {},
            "error": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source.zip"
            malformed = base / "malformed.zip"
            PetGenerationDiagnostics.build_issue_bundle(
                record,
                workspace=base,
                destination=source,
            )
            with zipfile.ZipFile(source) as archive:
                documents = {
                    name: archive.read(name)
                    for name in archive.namelist()
                }
            manifest = json.loads(documents["manifest.json"])
            manifest["contents"] = 7
            documents["manifest.json"] = json.dumps(manifest).encode()
            with zipfile.ZipFile(malformed, "w") as archive:
                for name, payload in documents.items():
                    archive.writestr(name, payload)

            with self.assertRaisesRegex(
                IssueBundleError,
                "清单与实际文件不一致",
            ):
                PetGenerationDiagnostics.inspect_issue_bundle(malformed)

    def test_technical_summary_contains_no_run_identifier(self):
        report = PetGenerationDiagnostics.summarize({
            "id": "private-character-name",
            "status": "pending",
            "stage": "action_generation",
            "request": {"max_api_calls": 8},
            "tasks": {"canonical": self._task("pending")},
            "metrics": {"api_calls": []},
            "artifacts": {},
            "error": None,
        })

        summary = PetGenerationDiagnostics.technical_summary(report)

        self.assertIn("Pixkin 伙伴工坊技术摘要", summary)
        self.assertNotIn("private-character-name", summary)


    def test_github_issue_markdown_is_copy_ready_and_anonymous(self):
        report = PetGenerationDiagnostics.summarize({
            "id": "private-run-id",
            "status": "failed",
            "stage": "failed",
            "request": {"max_api_calls": 8},
            "tasks": {"canonical": self._task("failed", 1)},
            "metrics": {"api_calls": []},
            "artifacts": {},
            "error": "private raw error",
        })

        markdown = PetGenerationDiagnostics.github_issue_markdown(
            report,
            bundle_filename=r"C:\private\Pixkin-Issue.zip",
        )

        self.assertIn("## 问题概述", markdown)
        self.assertIn("## 复现步骤", markdown)
        self.assertIn("## 自动诊断", markdown)
        self.assertIn("`Pixkin-Issue.zip`", markdown)
        self.assertNotIn("private-run-id", markdown)
        self.assertNotIn("private raw error", markdown)
        self.assertNotIn(r"C:\private", markdown)


if __name__ == "__main__":
    unittest.main()
