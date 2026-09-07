import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app import app
from app.routers.images import generate_image_internal

client = TestClient(app)

class TestImageGeneration(unittest.IsolatedAsyncioTestCase):

    def test_image_generation_disabled(self):
        with patch("app.storage.load_prefs", return_value={"setup_completed": False, "image_gen_enabled": False}), \
             patch("app.routers.images.load_prefs", return_value={"setup_completed": False, "image_gen_enabled": False}):
            response = client.post("/api/images/generations", json={"prompt": "A cat"})
            self.assertEqual(response.status_code, 400)
            self.assertIn("disabled", response.json()["detail"])

    async def test_generate_image_internal_b64(self):
        fake_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"b64_json": fake_b64, "revised_prompt": "A cute white kitten"}]
        }

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        mock_client_cls = MagicMock(return_value=mock_client_instance)
        mock_client_cls.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_cls.__aexit__ = AsyncMock(return_value=None)

        with patch("app.routers.images.load_prefs", return_value={
            "image_gen_enabled": True,
            "image_gen_api_key": "test_key",
            "image_gen_base_url": "https://api.openai.com/v1",
            "image_gen_model": "dall-e-3"
        }), patch("httpx.AsyncClient", return_value=mock_client_cls):
            result = await generate_image_internal("A cute white kitten")
            self.assertEqual(result["prompt"], "A cute white kitten")
            self.assertEqual(result["revised_prompt"], "A cute white kitten")
            self.assertTrue(result["url"].startswith("/static/generated/"))

