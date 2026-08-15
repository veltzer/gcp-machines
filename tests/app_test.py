"""
Tests for the machines web app.

google.auth and the compute API client are mocked out so these tests run
without any GCP credentials.
"""

import importlib
import os
import sys
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


AGGREGATED_RESPONSE = {
    "items": {
        "zones/us-central1-a": {
            "instances": [
                {
                    "name": "machine-keren",
                    "status": "RUNNING",
                    "zone": "projects/test-project/zones/us-central1-a",
                    "labels": {"owner": "keren"},
                    "networkInterfaces": [
                        {"accessConfigs": [{"natIP": "1.2.3.4"}]},
                    ],
                },
                {
                    # no labels and no external access config:
                    # must not crash the page
                    "name": "machine-bare",
                    "status": "SUSPENDED",
                    "zone": "projects/test-project/zones/us-central1-a",
                    "networkInterfaces": [{}],
                },
                {
                    # a stopped machine: shown as STOPPED, not TERMINATED
                    "name": "machine-raz",
                    "status": "TERMINATED",
                    "zone": "projects/test-project/zones/us-central1-a",
                    "labels": {"owner": "raz"},
                    "networkInterfaces": [{}],
                },
            ],
        },
        "zones/us-east1-c": {
            "warning": {"code": "NO_RESULTS_ON_PAGE"},
        },
    },
}


def make_fake_compute():
    fake = unittest.mock.Mock()
    instances = fake.instances.return_value
    list_request = unittest.mock.Mock()
    list_request.execute.return_value = AGGREGATED_RESPONSE
    instances.aggregatedList.return_value = list_request
    instances.aggregatedList_next.return_value = None
    return fake


def load_app(environ=None):
    """
    Import a fresh copy of the app with GCP mocked out.
    Returns the module and the fake compute client.
    """
    fake_compute = make_fake_compute()
    fake_credentials = unittest.mock.Mock()
    with unittest.mock.patch.dict(os.environ, environ or {}, clear=True), \
            unittest.mock.patch("google.auth.default", return_value=(fake_credentials, "test-project")), \
            unittest.mock.patch("google.cloud.datastore.Client", return_value=unittest.mock.MagicMock()), \
            unittest.mock.patch("googleapiclient.discovery.build", return_value=fake_compute):
        sys.modules.pop("src.main", None)
        module = importlib.import_module("src.main")
    module.app.config["TESTING"] = True
    return module, fake_compute


class FakeStudent(dict):
    """A Datastore student entity: a dict with a key carrying the email."""

    def __init__(self, email, owner):
        super().__init__(owner=owner)
        self.key = unittest.mock.Mock()
        self.key.name = email


def set_mapping(module, mapping):
    """Makes the app's fake Datastore client return the given email -> owner mapping."""
    module.datastore_client.query.return_value.fetch.return_value = [
        FakeStudent(email, owner) for email, owner in mapping.items()
    ]


def set_instance_status(fake_compute, status, owner=None):
    instances = fake_compute.instances.return_value
    instance = {"status": status}
    if owner is not None:
        instance["labels"] = {"owner": owner}
    instances.get.return_value.execute.return_value = instance
    return instances


def test_root_lists_machines_and_survives_missing_fields():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "keren" in body
    assert "1.2.3.4" in body
    # the machine without labels / external IP degrades gracefully
    assert "unknown" in body
    assert "N/A" in body
    # stopped machines get the friendly label
    assert "STOPPED" in body
    assert "TERMINATED" not in body


def test_process_rejects_get():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    response = client.get("/process?name=machine-keren&zone=us-central1-a&status=RUNNING")
    assert response.status_code == 405


def test_process_resumes_suspended_machine():
    module, fake_compute = load_app()
    instances = set_instance_status(fake_compute, "SUSPENDED")
    client = module.app.test_client()
    response = client.post("/process", data={"name": "machine-bare", "zone": "us-central1-a"})
    assert response.status_code == 200
    instances.resume.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-bare")
    instances.suspend.assert_not_called()
    instances.start.assert_not_called()


def test_process_suspends_running_machine():
    module, fake_compute = load_app()
    instances = set_instance_status(fake_compute, "RUNNING")
    client = module.app.test_client()
    response = client.post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 200
    instances.suspend.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-keren")
    instances.resume.assert_not_called()


def test_process_starts_terminated_machine():
    module, fake_compute = load_app()
    instances = set_instance_status(fake_compute, "TERMINATED")
    client = module.app.test_client()
    response = client.post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 200
    instances.start.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-keren")


def test_process_refuses_machine_in_transition():
    module, fake_compute = load_app()
    instances = set_instance_status(fake_compute, "STOPPING")
    client = module.app.test_client()
    response = client.post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 409
    instances.resume.assert_not_called()
    instances.suspend.assert_not_called()
    instances.start.assert_not_called()


def test_process_requires_name_and_zone():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    response = client.post("/process", data={"name": "machine-keren"})
    assert response.status_code == 400


def test_root_shows_iap_user():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    response = client.get("/", headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:keren@gmail.com"})
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Signed in as keren@gmail.com" in body
    # the logout link clears the IAP session cookie
    assert "gcp-iap-mode=CLEAR_LOGIN_COOKIE" in body


KEREN = {"X-Goog-Authenticated-User-Email": "accounts.google.com:keren@gmail.com"}


def test_student_sees_only_their_machine():
    module, _fake_compute = load_app()
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = module.app.test_client()
    body = client.get("/", headers=KEREN).get_data(as_text=True)
    assert "1.2.3.4" in body
    # the other machines (owner unknown / raz) are not shown
    assert "unknown" not in body
    assert "raz" not in body


def test_student_without_machine_sees_none():
    module, _fake_compute = load_app()
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = module.app.test_client()
    body = client.get(
        "/",
        headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:stranger@gmail.com"},
    ).get_data(as_text=True)
    assert "No machine is assigned to you" in body
    assert "1.2.3.4" not in body


def test_admin_sees_all_machines():
    module, _fake_compute = load_app({"ADMIN_EMAILS": "admin@gmail.com"})
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = module.app.test_client()
    body = client.get(
        "/",
        headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:admin@gmail.com"},
    ).get_data(as_text=True)
    assert "keren" in body
    assert "raz" in body
    assert "unknown" in body


def test_student_can_toggle_own_machine():
    module, fake_compute = load_app()
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "RUNNING", owner="keren")
    client = module.app.test_client()
    response = client.post(
        "/process",
        data={"name": "machine-keren", "zone": "us-central1-a"},
        headers=KEREN,
    )
    assert response.status_code == 200
    instances.suspend.assert_called_once()


def test_student_cannot_toggle_others_machine():
    module, fake_compute = load_app()
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "RUNNING", owner="raz")
    client = module.app.test_client()
    response = client.post(
        "/process",
        data={"name": "machine-raz", "zone": "us-central1-a"},
        headers=KEREN,
    )
    assert response.status_code == 403
    instances.suspend.assert_not_called()
    instances.resume.assert_not_called()
    instances.start.assert_not_called()


def test_student_cannot_toggle_unowned_machine():
    module, fake_compute = load_app()
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "SUSPENDED")
    client = module.app.test_client()
    response = client.post(
        "/process",
        data={"name": "machine-bare", "zone": "us-central1-a"},
        headers=KEREN,
    )
    assert response.status_code == 403
    instances.resume.assert_not_called()


def test_token_not_required_by_default():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    assert client.get("/").status_code == 200


def test_token_required_when_configured():
    module, _fake_compute = load_app({"ACCESS_TOKEN": "sesame"})
    client = module.app.test_client()
    assert client.get("/").status_code == 403
    assert client.get("/?token=wrong").status_code == 403
    assert client.get("/?token=sesame").status_code == 200
    # the token was remembered in a cookie, later requests need no url token
    assert client.get("/").status_code == 200
