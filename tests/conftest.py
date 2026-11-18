import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _allow_loopback(socket_enabled):
    """The fake gateway listens on 127.0.0.1."""
    yield
