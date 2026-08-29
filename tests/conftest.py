import os

import pytest


os.environ["DISCORD_ENABLED"] = "false"
os.environ["SECRET_KEY"] = "pytest-only-explicit-secret-key-000000000000"


@pytest.fixture
def quiet_log():
    """Accept the production logger signature without emitting test noise."""

    def log(message, level="INFO"):
        return None

    return log
