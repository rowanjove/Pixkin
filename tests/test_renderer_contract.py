import unittest

from core.renderers import CharacterRenderer, SpriteRenderer


class RendererContractTests(unittest.TestCase):
    def test_sprite_renderer_satisfies_interface(self):
        seen = []
        renderer = SpriteRenderer(on_state=seen.append)
        renderer.load({"id": "demo"})
        renderer.play("talking")
        renderer.set_direction("left")
        renderer.set_expression("happy")
        self.assertIsInstance(renderer, CharacterRenderer)
        self.assertEqual((renderer.state, renderer.direction, renderer.expression), ("talking", "left", "happy"))
        self.assertEqual(seen, ["talking"])
        renderer.unload()
        self.assertIsNone(renderer.character)


if __name__ == "__main__":
    unittest.main()
