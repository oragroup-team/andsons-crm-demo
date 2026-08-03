"""Puts ./vendor (packages installed via `pip install -r requirements.txt
--target=vendor`) at the front of sys.path.

We intentionally avoid a venv for this demo — dependencies are downloaded
straight into backend/vendor instead, so the project is self-contained
without touching the machine's global Python packages. Import this module
before any third-party import in every entry-point script.
"""
import os
import sys

_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)
