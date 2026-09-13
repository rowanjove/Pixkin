import base64
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image, ImageDraw

from core.pet_generation_run import PetGenerationRunStore
from core.pet_generator import PetGenerationWorker
from core.providers.image.base import (
    ImageProvider,
    ImageProviderCapabilities,
    ImageProviderHealth,
    classify_image_error,
)
from core.providers.image.openai_compatible import (
    OpenAICompatibleImageProvider,
)
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionResource,
    PermissionState,
)


class FakeImageProvider(ImageProvider):
    def __init__(self, image_bytes: bytes):
        self.image_bytes = image_bytes
        self.requests = []
        self.closed = False

    @property
    def endpoint(self):
        return "https://images.example.test/v1"

    @property
    def model(self):
        return "fake-image"

    @property
    def cache_key(self):
        return "fake-provider-v1"

    @property
    def capabilities(self):
        return ImageProviderCapabilities(True, True)

    def validate(self):
        return None

    def health_check(self):
        return ImageProviderHealth("verified", "fake provider ready")

    def edit(self, reference_paths, prompt):
        self.requests.append((list(reference_paths), prompt))
        return self.image_bytes

    def close(self):
        self.closed = True


class ImageProviderTests(unittest.TestCase):
    @staticmethod
    def _image_bytes() -> bytes:
        image = Image.new("RGB", (512, 512), (0, 255, 0))
        ImageDraw.Draw(image).ellipse(
            (150, 80, 360, 430),
            fill=(130, 70, 190),
            outline=(25, 30, 50),
            width=12,
        )
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()

    def test_openai_compatible_provider_edits_and_decodes_response(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            reference.write_bytes(self._image_bytes())
            encoded = base64.b64encode(b"generated-image").decode("ascii")
            edit = MagicMock(return_value=SimpleNamespace(
                data=[SimpleNamespace(b64_json=encoded)]
            ))
            retrieve = MagicMock()
            client = SimpleNamespace(
                images=SimpleNamespace(edit=edit),
                models=SimpleNamespace(retrieve=retrieve),
                close=MagicMock(),
            )
            provider = OpenAICompatibleImageProvider(
                api_key="test",
                base_url="https://images.example.test/v1",
                model="custom-image",
                quality="high",
                client=client,
            )

            health = provider.health_check()
            result = provider.edit([reference], "draw a pet")

            self.assertEqual(health.status, "verified")
            self.assertEqual(result, b"generated-image")
            retrieve.assert_called_once_with(
                "custom-image",
                timeout=15.0,
            )
            request = edit.call_args.kwargs
            self.assertEqual(request["model"], "custom-image")
            self.assertEqual(request["quality"], "high")
            self.assertEqual(request["response_format"], "b64_json")
            self.assertTrue(request["image"][0].closed)
            provider.close()
            client.close.assert_called_once_with()

    def test_compatible_provider_allows_missing_model_lookup(self):
        client = SimpleNamespace(
            images=SimpleNamespace(edit=lambda **_kwargs: None),
            models=SimpleNamespace(),
        )
        provider = OpenAICompatibleImageProvider(
            api_key="test",
            base_url="https://images.example.test/v1",
            model="custom-image",
            quality="low",
            client=client,
        )

        health = provider.health_check()

        self.assertEqual(health.status, "unverified")
        self.assertFalse(provider.capabilities.model_lookup)

    def test_provider_rejects_insecure_or_credentialed_endpoints(self):
        client = SimpleNamespace(
            images=SimpleNamespace(edit=lambda **_kwargs: None),
            models=SimpleNamespace(),
        )
        for endpoint in (
            "http://images.example.test/v1",
            "https://user:secret@images.example.test/v1",
            "https://images.example.test/v1?token=leak",
            "https://images.example.test/v1#fragment",
        ):
            with self.subTest(endpoint=endpoint):
                provider = OpenAICompatibleImageProvider(
                    api_key="test",
                    base_url=endpoint,
                    model="custom-image",
                    quality="low",
                    client=client,
                )
                with self.assertRaises(ValueError):
                    provider.validate()

    def test_official_model_lookup_failure_is_blocking(self):
        error = RuntimeError("missing model")
        error.status_code = 404
        client = SimpleNamespace(
            images=SimpleNamespace(edit=lambda **_kwargs: None),
            models=SimpleNamespace(
                retrieve=MagicMock(side_effect=error)
            ),
        )
        provider = OpenAICompatibleImageProvider(
            api_key="test",
            base_url="https://api.openai.com/v1",
            model="missing",
            quality="medium",
            client=client,
        )

        with self.assertRaisesRegex(RuntimeError, "接口或模型不存在"):
            provider.health_check()

    def test_invalid_base64_response_is_normalized(self):
        client = SimpleNamespace(
            images=SimpleNamespace(edit=MagicMock(
                return_value=SimpleNamespace(
                    data=[SimpleNamespace(b64_json="not-base64")]
                )
            )),
            models=SimpleNamespace(),
        )
        provider = OpenAICompatibleImageProvider(
            api_key="test",
            base_url="https://images.example.test/v1",
            model="custom-image",
            quality="low",
            client=client,
        )

        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            reference.write_bytes(b"fixture")
            with self.assertRaisesRegex(RuntimeError, "图片数据损坏"):
                provider.edit([reference], "prompt")

        details = classify_image_error(
            RuntimeError("图像服务返回的图片数据损坏。")
        )
        self.assertEqual(details.category, "invalid_response")
        self.assertTrue(details.retriable)

    def test_oversized_base64_response_is_rejected_before_decode(self):
        client = SimpleNamespace(
            images=SimpleNamespace(edit=MagicMock(
                return_value=SimpleNamespace(
                    data=[SimpleNamespace(
                        b64_json="A" * (
                            4 * (
                                (OpenAICompatibleImageProvider.MAX_IMAGE_BYTES + 2)
                                // 3
                            )
                            + 1
                        )
                    )]
                )
            )),
            models=SimpleNamespace(),
        )
        provider = OpenAICompatibleImageProvider(
            api_key="test",
            base_url="https://images.example.test/v1",
            model="custom-image",
            quality="low",
            client=client,
        )

        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            reference.write_bytes(b"fixture")
            with self.assertRaisesRegex(RuntimeError, "超过 20 MB 上限"):
                provider.edit([reference], "prompt")

    def test_worker_runs_with_provider_contract_without_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            reference = base / "reference.png"
            reference.write_bytes(self._image_bytes())
            store = PetGenerationRunStore(base / "runs")
            provider = FakeImageProvider(self._image_bytes())
            permissions = ContextPermissionService()
            permissions.set_state(
                PermissionResource.NETWORK,
                PermissionState.ALLOW_SESSION,
                session_only=True,
            )
            worker = PetGenerationWorker(
                api_key="unused",
                base_url=provider.endpoint,
                model=provider.model,
                quality="low",
                pet_name="Nova",
                personality="",
                style_notes="",
                reference_paths=[reference],
                generation_mode="basic",
                run_store=store,
                image_provider=provider,
                permission_service=permissions,
            )

            worker.run()

            record = store.list_runs()[0]
            self.assertEqual(record["stage"], "canonical_review")
            self.assertEqual(len(provider.requests), 1)
            self.assertTrue(provider.closed)
            self.assertEqual(
                record["request"]["capability_preflight"]["status"],
                "verified",
            )
