"""Run the app locally with a simulated printer (no hardware, no labels used)."""

import os

os.environ.setdefault("SLP_BACKEND", "mock")
os.environ.setdefault("SLP_HOST", "127.0.0.1")
os.environ.setdefault("SLP_PORT", "8111")
os.environ.setdefault("SLP_DATA", "data")

from slplabel.__main__ import main  # noqa: E402

main()
