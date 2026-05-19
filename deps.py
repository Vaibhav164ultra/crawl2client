"""Check required packages before running the pipeline."""

from __future__ import annotations

import importlib
import subprocess
import sys

REQUIRED = (
    ("playwright", "playwright"),
    ("pandas", "pandas"),
    ("bs4", "beautifulsoup4"),
    ("lxml", "lxml"),
    ("requests", "requests"),
)


def check_dependencies(install: bool = False) -> bool:
    """Return True if all deps present; optionally pip install missing ones."""
    missing: list[str] = []

    for module, package in REQUIRED:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)

    if not missing:
        return True

    print("\n[ERROR] Missing Python packages:")
    for pkg in missing:
        print(f"  - {pkg}")

    if install:
        print("\nInstalling missing packages...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing],
        )
        return check_dependencies(install=False)

    print("\nFix: run this in your project folder:")
    print("  pip install -r requirements.txt")
    print("  playwright install chromium\n")
    return False
