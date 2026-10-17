"""Shared test fixtures that use the pinned upstream checkout."""

from pathlib import Path

import pytest

from auditors_paradox.adapters.collusionlab import verify_upstream
from auditors_paradox.config import StudyConfig


@pytest.fixture(scope="session")
def root():
    """The project root, we check the upstream pin once before any test uses it."""
    path = Path(__file__).resolve().parents[1]
    verify_upstream(path)
    return path


@pytest.fixture
def config():
    """A short six round scripted config that every test starts from."""
    return StudyConfig.model_validate(
        {
            "rounds": 6,
            "analysis_last_rounds": 3,
            "feedback": {"schedule_rounds": [2, 4, 6]},
            "auditor": {"window": 2},
            "agent": {"scripted_policy": "coordinated"},
        }
    )


def changed(config, **values):
    """Validate a config again after changing some of its fields."""
    return StudyConfig.model_validate({**config.model_dump(mode="json"), **values})
