# The committed deploy payload must satisfy the entry contract.
#
# The deployment smoke check posts deploy/invoke_payload.json verbatim, and
# every assertion around that post can pass while the AGENT refuses the
# request: the HTTP call is well formed, the status is 200, the body is valid
# JSON — and the agent answers "error". The smoke job tolerates failure, so the
# pipeline stays green and the only record is one line in an artifact nobody
# opens.
#
# So the payload is pinned here, in two directions:
#
#   1. it is byte-equal to the fixture the integration tests assert against, so
#      the payload and the contract cannot drift apart;
#   2. posting it through the real entry point actually SUCCEEDS, and posting a
#      payload the contract rejects actually fails — one direction alone cannot
#      tell a real check from a broken one.

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_invoke_contract import _BASE_REQUEST

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAYLOAD_PATH = _REPO_ROOT / "deploy" / "invoke_payload.json"

TOKEN = "integration-test-token"
os.environ["INVOKE_AUTH_TOKEN"] = TOKEN
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    from src.api.server import app

    return TestClient(app)


def test_the_payload_file_exists_and_is_valid_json():
    assert _PAYLOAD_PATH.exists(), f"{_PAYLOAD_PATH} is required by the deployment smoke check"
    json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))


def test_the_payload_is_the_repository_fixture_not_a_value_invented_for_the_deploy():
    committed = json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))
    assert committed == _BASE_REQUEST, (
        "deploy/invoke_payload.json has drifted from the fixture the integration "
        "tests assert against. Both must be the same request."
    )


def test_posting_the_committed_payload_succeeds(client):
    """The agent — not just the HTTP call — must answer successfully."""
    committed = json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))
    response = client.post("/invoke", json=committed, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success", body
    entry = json.loads(body["output"])
    assert entry["product_name"], "the smoke payload must produce a real extraction"
    assert entry["document_source"] == "caller"


def test_a_payload_the_contract_rejects_really_does_fail(client):
    """Probing only the good case cannot distinguish a fix from a broken probe."""
    bad = {"input": "smoke", "input_context": {"document_type": "DOCX"}}
    response = client.post("/invoke", json=bad, headers=AUTH)
    assert response.status_code == 400


def test_the_payload_names_no_credential_and_only_reserved_hosts(client):
    """The payload is committed and published with the repository.

    Every host it names must be a reserved documentation domain, so the file
    carries no credential and points at nothing real.
    """
    import re

    from src.services.screens import credential_findings

    raw = _PAYLOAD_PATH.read_text(encoding="utf-8")
    assert credential_findings(raw) == []

    hosts = re.findall(r"https?://([^/\s\"]+)", raw)
    assert hosts, "the payload should exercise the document-reference field"
    for host in hosts:
        assert (
            host.endswith(".example") or host == "example.com" or host.endswith(".example.com")
        ), f"{host} is not a reserved documentation domain"
