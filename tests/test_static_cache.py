import unittest
from fastapi.testclient import TestClient
from app import app

class TestStaticCacheControl(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_static_js_has_cache_control(self):
        """Test that static JavaScript requests include Cache-Control headers."""
        response = self.client.get("/static/js/atlas.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Cache-Control", response.headers)
        self.assertEqual(
            response.headers["Cache-Control"],
            "public, max-age=604800, must-revalidate"
        )

    def test_static_css_has_cache_control(self):
        """Test that static CSS requests include Cache-Control headers."""
        response = self.client.get("/static/css/atlas.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Cache-Control", response.headers)
        self.assertEqual(
            response.headers["Cache-Control"],
            "public, max-age=604800, must-revalidate"
        )

    def test_non_static_does_not_have_cache_control(self):
        """Test that regular HTML pages do not have static Cache-Control headers."""
        # Unauthenticated request to / should redirect to login, not return static cache headers
        response = self.client.get("/", follow_redirects=False)
        self.assertNotIn("Cache-Control", response.headers)

if __name__ == "__main__":
    unittest.main()
