from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def suite_basic_dir() -> Path:
    return FIXTURES_DIR / "suite_basic"


@pytest.fixture
def suite_regression_dir() -> Path:
    return FIXTURES_DIR / "suite_regression"
