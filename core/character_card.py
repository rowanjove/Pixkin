"""SillyTavern Character Card v1/v2 interoperability.

The codec is intentionally data-only: importing a card never executes code or
downloads assets. Unknown extension keys are retained for round-tripping.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class CharacterCardError(ValueError):
    pass


MAX_CARD_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 100_000


@dataclass(frozen=True)
class CharacterCard:
    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    creator_notes: str = ""
    alternate_greetings: tuple[str, ...] = ()
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise CharacterCardError("角色卡 name 不能为空")
        for value in (
            self.description,
            self.personality,
            self.scenario,
            self.first_mes,
            self.mes_example,
            self.system_prompt,
            self.creator_notes,
        ):
            if len(str(value)) > MAX_TEXT_CHARS:
                raise CharacterCardError("角色卡文本过长")
        if len(self.alternate_greetings) > 20:
            raise CharacterCardError("alternate_greetings 不能超过 20 条")

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "CharacterCard":
        if not isinstance(document, Mapping):
            raise CharacterCardError("角色卡必须是 JSON 对象")
        spec = str(document.get("spec", ""))
        if spec.startswith("chara_card_v2"):
            raw = document.get("data")
            if not isinstance(raw, Mapping):
                raise CharacterCardError("v2 角色卡缺少 data 对象")
            source = raw
            known = {
                "name", "description", "personality", "scenario", "first_mes",
                "mes_example", "system_prompt", "creator_notes",
                "alternate_greetings", "extensions",
            }
            raw_extensions = raw.get("extensions") or {}
            if not isinstance(raw_extensions, Mapping):
                raise CharacterCardError("v2 角色卡 extensions 必须是对象")
            extensions = dict(raw_extensions)
            extensions.update({k: v for k, v in raw.items() if k not in known})
        else:
            source = document
            known = {
                "name", "description", "personality", "scenario", "first_mes",
                "mes_example", "system_prompt", "creator_notes",
                "alternate_greetings", "extensions",
            }
            raw_extensions = document.get("extensions") or {}
            if not isinstance(raw_extensions, Mapping):
                raise CharacterCardError("角色卡 extensions 必须是对象")
            extensions = dict(raw_extensions)
            extensions.update({k: v for k, v in document.items() if k not in known})

        greetings = source.get("alternate_greetings") or []
        if isinstance(greetings, str):
            greetings = [greetings]
        if not isinstance(greetings, (list, tuple)):
            raise CharacterCardError("alternate_greetings 格式无效")
        return cls(
            name=str(source.get("name") or "").strip(),
            description=str(source.get("description") or ""),
            personality=str(source.get("personality") or ""),
            scenario=str(source.get("scenario") or ""),
            first_mes=str(source.get("first_mes") or ""),
            mes_example=str(source.get("mes_example") or ""),
            system_prompt=str(source.get("system_prompt") or ""),
            creator_notes=str(source.get("creator_notes") or ""),
            alternate_greetings=tuple(str(item) for item in greetings if str(item).strip()),
            extensions=extensions,
        )

    @classmethod
    def from_json(cls, value: str | bytes) -> "CharacterCard":
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if len(raw) > MAX_CARD_BYTES:
            raise CharacterCardError("角色卡文件超过 2 MiB")
        try:
            document = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CharacterCardError("角色卡 JSON 无法解析") from exc
        return cls.from_dict(document)

    @classmethod
    def from_file(cls, path: str | Path) -> "CharacterCard":
        source = Path(path)
        try:
            if source.stat().st_size > MAX_CARD_BYTES:
                raise CharacterCardError("角色卡文件超过 2 MiB")
        except OSError as exc:
            raise CharacterCardError("角色卡文件无法读取") from exc
        raw = source.read_bytes()
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            try:
                from PIL import Image

                with Image.open(source) as image:
                    encoded = image.info.get("chara")
                if not encoded:
                    raise CharacterCardError("PNG 未包含 chara 角色卡元数据")
                if isinstance(encoded, str):
                    encoded = encoded.encode("ascii")
                return cls.from_json(base64.b64decode(encoded, validate=True))
            except CharacterCardError:
                raise
            except (binascii.Error, UnicodeError, TypeError, ValueError) as exc:
                raise CharacterCardError("PNG 角色卡元数据无法读取") from exc
        return cls.from_json(raw)

    def to_v2_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_mes,
            "mes_example": self.mes_example,
            "system_prompt": self.system_prompt,
            "creator_notes": self.creator_notes,
            "alternate_greetings": list(self.alternate_greetings),
            "extensions": dict(self.extensions),
        }
        return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_v2_dict(), ensure_ascii=False, indent=indent) + "\n"

    def to_pixkin_persona(self) -> dict[str, Any]:
        """Map card fields into the provider-neutral Pixkin persona model."""
        persona: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_message": self.first_mes,
            "message_examples": self.mes_example,
        }
        if self.system_prompt:
            persona["system_prompt"] = self.system_prompt
        if self.alternate_greetings:
            persona["alternate_greetings"] = list(self.alternate_greetings)
        return persona

    @classmethod
    def from_pixkin_persona(cls, persona: Mapping[str, Any]) -> "CharacterCard":
        if not isinstance(persona, Mapping):
            raise CharacterCardError("Pixkin persona 必须是对象")
        return cls(
            name=str(persona.get("name") or "Pixkin Character").strip(),
            description=str(persona.get("description") or ""),
            personality=str(persona.get("personality") or ""),
            scenario=str(persona.get("scenario") or ""),
            first_mes=str(persona.get("first_message") or persona.get("first_mes") or ""),
            mes_example=str(persona.get("message_examples") or persona.get("mes_example") or ""),
            system_prompt=str(persona.get("system_prompt") or ""),
            alternate_greetings=tuple(str(item) for item in (persona.get("alternate_greetings") or [])),
        )


__all__ = ["CharacterCard", "CharacterCardError"]
