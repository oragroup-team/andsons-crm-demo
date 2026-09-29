"""Pytest configuration for backend tests."""
import os
import sys

# Add backend/vendor and backend itself to sys.path (same as _vendor_path.py)
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDOR_DIR = os.path.join(_BACKEND_DIR, "vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Stub environment variables that might be read at import time
os.environ.setdefault("BIGQUERY_PROJECT_ID", "test-project")
os.environ.setdefault("BIGQUERY_DATASET", "test_dataset")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
