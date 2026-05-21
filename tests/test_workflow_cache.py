import os
import unittest
import asyncio
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Force the test to run using a clean isolated environment variables if needed
os.environ["GROQ_API_KEY"] = "mock-key"
os.environ["GOOGLE_API_KEY"] = "mock-key"

from app import workflow

class TestWorkflowCache(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary directory for workflows file isolation
        self.test_dir = tempfile.mkdtemp()
        self.original_workflows_file = workflow.WORKFLOWS_FILE
        workflow.WORKFLOWS_FILE = os.path.join(self.test_dir, "test_workflows.json")
        # Reset cache
        workflow._WORKFLOWS_CACHE = {}

    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.test_dir)
        workflow.WORKFLOWS_FILE = self.original_workflows_file
        workflow._WORKFLOWS_CACHE = {}

    def test_load_empty_workflows(self):
        """Test that load_workflows returns an empty dict when no cache or file exists."""
        data = workflow.load_workflows()
        self.assertEqual(data, {})
        self.assertEqual(workflow._WORKFLOWS_CACHE, {})

    def test_cache_is_utilized(self):
        """Test that load_workflows reads from in-memory cache directly without disk reads."""
        # Manually seed cache
        workflow._WORKFLOWS_CACHE = {"run_123": {"status": "running", "title": "Cache Check"}}
        
        # Verify that it returns the cache directly even if file does not exist
        self.assertFalse(os.path.exists(workflow.WORKFLOWS_FILE))
        data = workflow.load_workflows()
        self.assertEqual(data["run_123"]["title"], "Cache Check")

    def test_save_updates_cache_instantly(self):
        """Test that save_workflows updates the cache dictionary instantly."""
        payload = {"run_456": {"status": "completed"}}
        
        # Save payload
        workflow.save_workflows(payload)
        
        # Assert memory cache reflects save immediately
        self.assertEqual(workflow._WORKFLOWS_CACHE, payload)

    def test_save_persists_to_disk_sync_fallback(self):
        """Test that save_workflows falls back to sync write and persists to disk when no loop is running."""
        payload = {"run_999": {"status": "pending"}}
        
        # Run save (should fallback to sync save since unittest runs synchronously without asyncio event loop)
        workflow.save_workflows(payload)
        
        # Verify it actually wrote the file to disk successfully
        self.assertTrue(os.path.exists(workflow.WORKFLOWS_FILE))
        
        # Reload from fresh cache-cleared load
        workflow._WORKFLOWS_CACHE = {}
        loaded = workflow.load_workflows()
        self.assertEqual(loaded["run_999"]["status"], "pending")

    def test_async_save_persistence(self):
        """Test that save_workflows schedules a background task when an event loop is running."""
        async def run_async_test():
            payload = {"run_async": {"status": "active"}}
            workflow.save_workflows(payload)
            # Yield control so loop can execute the scheduled background persistence task
            await asyncio.sleep(0.1)
            
            # Assert cache updated
            self.assertEqual(workflow._WORKFLOWS_CACHE, payload)
            # Assert file exists on disk
            self.assertTrue(os.path.exists(workflow.WORKFLOWS_FILE))

        asyncio.run(run_async_test())

if __name__ == "__main__":
    unittest.main()
