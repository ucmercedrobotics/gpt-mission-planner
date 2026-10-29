"""Tests for detecting whether SPIN and Spot are installed.

An image built without ENABLE_VERIFICATION=true has neither, and the default
config still turns verification on. The planner has to notice and skip it
instead of failing on `import spot`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from app.utils import os_utils
from app.utils.os_utils import missing_verification_tools


def test_nothing_missing_when_both_installed(monkeypatch):
    monkeypatch.setattr(os_utils.shutil, "which", lambda _: "/usr/local/bin/spin")
    monkeypatch.setattr(os_utils.importlib.util, "find_spec", lambda _: object())
    assert missing_verification_tools("spin") == []


def test_spin_not_on_path(monkeypatch):
    monkeypatch.setattr(os_utils.importlib.util, "find_spec", lambda _: object())
    assert missing_verification_tools("definitely-not-spin-binary") == ["spin"]


def test_empty_spin_path(monkeypatch):
    monkeypatch.setattr(os_utils.importlib.util, "find_spec", lambda _: object())
    assert missing_verification_tools("") == ["spin"]


def test_spot_not_importable(monkeypatch):
    monkeypatch.setattr(os_utils.shutil, "which", lambda _: "/usr/local/bin/spin")
    monkeypatch.setattr(os_utils.importlib.util, "find_spec", lambda _: None)
    assert missing_verification_tools("spin") == ["spot"]
