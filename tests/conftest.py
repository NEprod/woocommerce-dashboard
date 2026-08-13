import os

import pytest


os.environ["DISCORD_ENABLED"] = "false"


@pytest.fixture
def quiet_log():
    """Accept the production logger signature without emitting test noise."""

    def log(message, level="INFO"):
        return None

    return log
