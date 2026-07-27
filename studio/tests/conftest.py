"""Shared pytest configuration for the studio tests."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the markers the suite uses.

    ``slow`` is anything that shells out to ffmpeg and encodes a real file. Those
    tests earn their seconds — an MP4 that does not exist is the failure this
    package is most likely to ship — but they are labelled so a tight inner loop
    can skip them with ``-m 'not slow'``.
    """
    config.addinivalue_line("markers", "slow: encodes a real media file with ffmpeg")
