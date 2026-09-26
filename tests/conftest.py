import keyring
import keyring.backends.null
import pytest


@pytest.fixture(autouse=True)
def _no_real_credential_manager():
    """No test may read or write the real Windows Credential Manager."""
    previous = keyring.get_keyring()
    keyring.set_keyring(keyring.backends.null.Keyring())
    yield
    keyring.set_keyring(previous)
