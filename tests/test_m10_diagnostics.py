import io
import json
import logging
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.services.crash_report_store import CrashReportStore
from core.services.diagnostic_bundle_service import (
    DiagnosticBundleError,
    DiagnosticBundleService,
)
from core.structured_logging import log_event


class CrashReportStoreTests(unittest.TestCase):
    def test_crash_summary_is_atomic_bounded_and_anonymous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "crashes"
            store = CrashReportStore(root, max_reports=2)
            for index in range(3):
                store.save_exception(
                    RuntimeError,
                    RuntimeError(
                        f"failure sk-secretvalue{index:08d}"
                    ),
                    "Traceback at C:\\Users\\Private\\project\\main.py",
                )

            reports = store.reports()

            self.assertEqual(len(reports), 2)
            raw = "".join(
                path.read_text(encoding="utf-8")
                for path in root.glob("*.json")
            )
            self.assertNotIn("sk-secretvalue", raw)
            self.assertNotIn("Users\\\\Private", raw)
            self.assertIn("<local-path>", raw)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_failed_atomic_replace_leaves_no_partial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CrashReportStore(Path(directory) / "crashes")

            with patch(
                "core.services.crash_report_store.os.replace",
                side_effect=OSError("locked"),
            ):
                with self.assertRaises(OSError):
                    store.save_exception(
                        ValueError,
                        ValueError("failed"),
                        "trace",
                    )

            self.assertEqual(store.reports(), ())
            self.assertEqual(
                list(Path(directory).rglob("*.tmp")),
                [],
            )


class DiagnosticBundleTests(unittest.TestCase):
    def test_bundle_is_whitelisted_hashed_and_inspectable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            crashes = CrashReportStore(base / "crashes")
            crashes.save_exception(
                RuntimeError,
                RuntimeError("Bearer abcdefghijklmnop"),
                "C:\\Users\\Private\\app.py",
            )
            service = DiagnosticBundleService(crashes)

            target = service.export(base / "support.zip")
            manifest = service.inspect(target)

            self.assertFalse(any(manifest["privacy"].values()))
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    service.REQUIRED_FILES,
                )
                raw = b"".join(
                    archive.read(name)
                    for name in archive.namelist()
                ).decode("utf-8")
            self.assertNotIn("abcdefghijklmnop", raw)
            self.assertNotIn("Users\\\\Private", raw)

    def test_tampered_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            service = DiagnosticBundleService(
                CrashReportStore(base / "crashes")
            )
            target = service.export(base / "support.zip")
            with zipfile.ZipFile(target) as archive:
                documents = {
                    name: archive.read(name)
                    for name in archive.namelist()
                }
            documents["crashes.json"] = b'{"tampered":true}'
            with zipfile.ZipFile(target, "w") as archive:
                for name, content in documents.items():
                    archive.writestr(name, content)

            with self.assertRaises(DiagnosticBundleError):
                service.inspect(target)


class StructuredLoggingTests(unittest.TestCase):
    def test_event_has_versioned_contract_and_redacted_fields(self):
        output = io.StringIO()
        logger = logging.Logger("structured-test")
        logger.addHandler(logging.StreamHandler(output))

        log_event(
            logger,
            logging.WARNING,
            component="chat",
            operation="request",
            error_category="authentication",
            message="request failed",
            api_key="sk-secretvalue123456",
        )

        event = json.loads(output.getvalue())
        self.assertEqual(event["event_schema"], 1)
        self.assertEqual(event["component"], "chat")
        self.assertEqual(
            event["fields"]["api_key"],
            "<redacted-secret>",
        )
