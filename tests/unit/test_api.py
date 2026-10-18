import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials

from app.main import admin, health


def test_health_is_public():
    assert health() == {"status": "ok"}


def test_admin_auth_rejects_invalid_credentials():
    with pytest.raises(HTTPException) as error:
        admin(HTTPBasicCredentials(username="wrong", password="wrong"))
    assert error.value.status_code == 401
