import os
import tempfile

# Set environment before app modules initialize engine and settings.
TEST_DB = tempfile.NamedTemporaryFile(prefix="firstcustomer-test-", suffix=".db", delete=False)
TEST_DB.close()
os.environ["DATABASE_URL"] = "sqlite:///" + TEST_DB.name
os.environ["DEMO_MODE"] = "true"
# Older mocked-provider tests exercise the legacy contact adapter explicitly.
# Production defaults to the search-only strategy; new tests opt into it.
os.environ["CONTACT_LOOKUP_MODE"] = "contact"
os.environ["ADMIN_USERNAME"] = "test-admin"
os.environ["ADMIN_PASSWORD"] = "unpredictable-test-password"
os.environ["CSRF_SECRET"] = "this-is-a-long-test-csrf-secret"
os.environ.pop("CALENDLY_SIGNING_KEY", None)

import pytest
from fastapi.testclient import TestClient
from app.db import Base, engine
from app.main import app, csrf_token


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        c.auth = ("test-admin", "unpredictable-test-password")
        yield c


@pytest.fixture
def token():
    return csrf_token()
