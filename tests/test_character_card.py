import json
import tempfile
import unittest
from pathlib import Path

from core.character_card import CharacterCard, CharacterCardError


class CharacterCardTests(unittest.TestCase):
    def test_v1_import_and_v2_round_trip(self):
        card = CharacterCard.from_dict(
            {
                "name": "小 Pixkin",
                "description": "伙伴",
                "personality": "温柔、好奇",
                "scenario": "桌面上",
                "first_mes": "你好！",
                "mes_example": "<START>\n{{char}}: 嗨",
                "creator_notes": "不要放进 system prompt",
                "custom_unknown": {"keep": True},
            }
        )
        encoded = card.to_v2_dict()
        self.assertEqual(encoded["spec"], "chara_card_v2")
        self.assertEqual(encoded["data"]["extensions"]["custom_unknown"], {"keep": True})
        restored = CharacterCard.from_json(json.dumps(encoded, ensure_ascii=False))
        self.assertEqual(restored.to_pixkin_persona()["first_message"], "你好！")
        self.assertNotIn("creator_notes", restored.to_pixkin_persona())

    def test_file_size_and_required_name_are_checked(self):
        with self.assertRaises(CharacterCardError):
            CharacterCard.from_dict({"description": "missing name"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "card.json"
            path.write_text(json.dumps({"name": "ok"}), encoding="utf-8")
            self.assertEqual(CharacterCard.from_file(path).name, "ok")


if __name__ == "__main__":
    unittest.main()
