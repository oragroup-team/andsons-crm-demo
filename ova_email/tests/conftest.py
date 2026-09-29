"""Pytest configuration for ova_email tests."""
import os
import sys

# Add ova_email and backend/vendor to sys.path (same as ova_email/app.py)
_THIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
_VENDOR_DIR = os.path.join(_BACKEND_DIR, "vendor")

sys.path.insert(0, _THIS_DIR)
if os.path.isdir(_VENDOR_DIR):
    sys.path.append(_VENDOR_DIR)

# Stub environment variables
os.environ.setdefault("ANALYTICS_SERVICE_URL", "http://localhost:8000")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
