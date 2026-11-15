"""
Tests for the machines web app.

google.auth and the compute API client are mocked out so these tests run
without any GCP credentials.
"""

import base64
import contextlib
import datetime
import importlib
import os
import sys
import threading
import unittest.mock

import pytest
from google.cloud import datastore
from googleapiclient.errors import HttpError

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
                    # started 13:07 Pacific, limited to 4 hours
                    "lastStartTimestamp": "2026-11-14T13:07:15.199-08:00",
                    "scheduling": {"maxRunDuration": {"seconds": "14400"}},
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


class FakeKey:
    """A Datastore key: kind plus name."""

    def __init__(self, kind, name):
        self.kind = kind
        self.name = name


class FakeDatastore:
    """
    Just enough of google.cloud.datastore.Client for the app: keys, get, put,
    transactions and the student roster query.
    """

    def __init__(self):
        self.entities = {}
        self.students = []

    def key(self, kind, name):
        return FakeKey(kind, name)

    def get(self, key):
        return self.entities.get((key.kind, key.name))

    def put(self, entity):
        self.entities[(entity.key.kind, entity.key.name)] = entity

    def transaction(self):
        return contextlib.nullcontext()

    def query(self, kind):
        query = unittest.mock.Mock()
        if kind == "student":
            query.fetch.return_value = self.students
        else:
            query.fetch.return_value = [e for (k, _name), e in self.entities.items() if k == kind]
        return query


class FakeVerifier:
    """
    Stands in for verification.IdentityPlatform: records the mails it was
    asked to send, and reports an address verified once a test says its
    link was followed.
    """

    def __init__(self):
        self.sent = []
        self.followed = set()
        self.fail = False

    def _fail(self, message):
        # imported here: src is importable only after the sys.path line above
        from src.verification import VerificationError  # pylint: disable=import-outside-toplevel
        raise VerificationError(f"Identity Platform: {message}")

    def send(self, email, continue_url):
        if self.fail:
            self._fail("QUOTA_EXCEEDED")
        self.sent.append((email, continue_url))

    def verified(self, email):
        if self.fail:
            self._fail("UNAVAILABLE")
        return email in self.followed


def load_app(environ=None):
    """
    Import a fresh copy of the app with GCP mocked out.
    Returns the module and the fake compute client.
    """
    fake_compute = make_fake_compute()
    fake_credentials = unittest.mock.Mock()
    # a fixed session key unless a test says otherwise, as Cloud Run would have one
    environ = {"SECRET_KEY": "test-key"} | (environ or {})
    with unittest.mock.patch.dict(os.environ, environ, clear=True), \
            unittest.mock.patch("google.auth.default", return_value=(fake_credentials, "test-project")), \
            unittest.mock.patch("google.cloud.datastore.Client", return_value=FakeDatastore()), \
            unittest.mock.patch("googleapiclient.discovery.build", return_value=fake_compute):
        sys.modules.pop("src.main", None)
        module = importlib.import_module("src.main")
    module.app.config["TESTING"] = True
    module.app.config["VERIFIER"] = FakeVerifier()
    return module, fake_compute


class FakeStudent(dict):
    """A Datastore student entity: a dict with a key carrying the email."""

    def __init__(self, email, owner):
        super().__init__(owner=owner)
        self.key = FakeKey("student", email)


def set_mapping(module, mapping):
    """Makes the app's fake Datastore return the given email -> owner roster."""
    module.datastore_client.students = [FakeStudent(email, owner) for email, owner in mapping.items()]


def add_user(module, subject, email, verified=True, owner=None):
    """Creates an account in the fake Datastore and returns it."""
    entity = datastore.Entity(key=FakeKey("user", subject))
    entity.update({"email": email, "name": email.split("@")[0], "picture": "", "verified": verified})
    if owner is not None:
        entity["owner"] = owner
    module.datastore_client.put(entity)
    return entity


def signed_in_client(module, email="admin@gmail.com", subject=None, verified=True, owner=None):
    """A test client whose session belongs to a (new) account."""
    subject = subject or f"google:{email}"
    add_user(module, subject, email, verified=verified, owner=owner)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session["subject"] = subject
    return client


def set_instance_status(fake_compute, status, owner=None):
    instances = fake_compute.instances.return_value
    instance = {"status": status}
    if owner is not None:
        instance["labels"] = {"owner": owner}
    instances.get.return_value.execute.return_value = instance
    return instances


ADMIN = {"ADMIN_EMAILS": "admin@gmail.com"}


def test_root_shows_sign_in_page_when_signed_out():
    module, _fake_compute = load_app()
    response = module.app.test_client().get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="email-form"' in body
    # no machine data leaks to a visitor
    assert "1.2.3.4" not in body


def test_root_lists_machines_and_survives_missing_fields():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module)
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
    assert "Signed in as admin@gmail.com" in body
    # the running machine shows when it turns itself off, in UTC for the browser to localize
    assert '<time datetime="2026-11-15T01:07:15Z">01:07 UTC</time>' in body
    assert body.count("turns itself off at") == 1
    # only the running machine has an IP to copy; the others' Copy IP is disabled
    assert 'data-copy="1.2.3.4">Copy IP</button>' in body
    assert body.count('disabled>Copy IP</button>') == 2


def test_process_rejects_get():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module)
    response = client.get("/process?name=machine-keren&zone=us-central1-a&status=RUNNING")
    assert response.status_code == 405


def test_process_requires_sign_in():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "RUNNING", owner="keren")
    response = module.app.test_client().post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 302
    instances.suspend.assert_not_called()


def test_process_resumes_suspended_machine():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "SUSPENDED")
    client = signed_in_client(module)
    response = client.post("/process", data={"name": "machine-bare", "zone": "us-central1-a"})
    assert response.status_code == 200
    instances.resume.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-bare")
    instances.suspend.assert_not_called()
    instances.start.assert_not_called()


def test_process_suspends_running_machine():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "RUNNING")
    client = signed_in_client(module)
    response = client.post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 200
    instances.suspend.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-keren")
    instances.resume.assert_not_called()


def test_process_starts_terminated_machine():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "TERMINATED")
    client = signed_in_client(module)
    response = client.post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 200
    instances.start.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-keren")


def test_process_refuses_machine_in_transition():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "STOPPING")
    client = signed_in_client(module)
    response = client.post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 409
    instances.resume.assert_not_called()
    instances.suspend.assert_not_called()
    instances.start.assert_not_called()


def test_process_requires_name_and_zone():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module)
    response = client.post("/process", data={"name": "machine-keren"})
    assert response.status_code == 400


def test_student_sees_only_their_machine():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = signed_in_client(module, "keren@gmail.com")
    body = client.get("/").get_data(as_text=True)
    assert "1.2.3.4" in body
    # the other machines (owner unknown / raz) are not shown
    assert "unknown" not in body
    assert "raz" not in body


def test_student_without_machine_sees_none():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = signed_in_client(module, "stranger@gmail.com")
    body = client.get("/").get_data(as_text=True)
    assert "No machine is assigned to you" in body
    assert "1.2.3.4" not in body


def test_password_account_is_not_matched_by_email():
    """Anyone can type a student's address into the password form: that must not reach their machine."""
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = signed_in_client(module, "keren@gmail.com", subject="email:keren@gmail.com", verified=False)
    body = client.get("/").get_data(as_text=True)
    assert "verify your email" in body
    assert "machine-keren" not in body
    instances = set_instance_status(fake_compute, "RUNNING", owner="keren")
    response = client.post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 403
    instances.suspend.assert_not_called()


def test_password_account_is_not_admin():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module, "admin@gmail.com", subject="email:admin@gmail.com", verified=False)
    body = client.get("/").get_data(as_text=True)
    assert "raz" not in body
    assert "verify your email" in body


def test_assigned_owner_wins_for_any_account():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module, "raz@example.com", subject="email:raz@example.com", verified=False, owner="raz")
    body = client.get("/").get_data(as_text=True)
    assert "machine-raz" in body
    assert "machine-keren" not in body


def test_admin_sees_all_machines():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    body = signed_in_client(module).get("/").get_data(as_text=True)
    assert "keren" in body
    assert "raz" in body
    assert "unknown" in body
    assert "admin" in body


def test_student_can_toggle_own_machine():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "RUNNING", owner="keren")
    client = signed_in_client(module, "keren@gmail.com")
    response = client.post("/process", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 200
    instances.suspend.assert_called_once()


def test_student_cannot_toggle_others_machine():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "RUNNING", owner="raz")
    client = signed_in_client(module, "keren@gmail.com")
    response = client.post("/process", data={"name": "machine-raz", "zone": "us-central1-a"})
    assert response.status_code == 403
    instances.suspend.assert_not_called()
    instances.resume.assert_not_called()
    instances.start.assert_not_called()


def test_student_cannot_toggle_unowned_machine():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "SUSPENDED")
    client = signed_in_client(module, "keren@gmail.com")
    response = client.post("/process", data={"name": "machine-bare", "zone": "us-central1-a"})
    assert response.status_code == 403
    instances.resume.assert_not_called()


# ─── sign-in ─────────────────────────────────────────────────────────────────

def test_register_then_sign_in_with_password():
    module, _fake_compute = load_app(ADMIN)
    client = module.app.test_client()
    response = client.post("/auth/email/register", json={"email": " New@Example.com ", "password": "longenough"})
    assert response.status_code == 200
    assert response.get_json() == {"next": "/"}
    stored = module.datastore_client.entities[("user", "email:new@example.com")]
    assert stored["verified"] is False
    assert stored["password_hash"] != "longenough"
    assert "Signed in as new@example.com" in client.get("/").get_data(as_text=True)
    client.post("/auth/logout")
    assert 'id="email-form"' in client.get("/").get_data(as_text=True)
    response = client.post("/auth/email/login", json={"email": "new@example.com", "password": "longenough"})
    assert response.status_code == 200
    assert "Signed in as new@example.com" in client.get("/").get_data(as_text=True)


def test_register_refuses_short_password_and_duplicates():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    response = client.post("/auth/email/register", json={"email": "a@example.com", "password": "short"})
    assert response.status_code == 400
    assert "error" in response.get_json()
    assert client.post("/auth/email/register", json={"email": "a@example.com", "password": "longenough"}).status_code == 200
    module.datastore_client.entities[("user", "email:a@example.com")]["verified"] = True
    response = module.app.test_client().post("/auth/email/register", json={"email": "a@example.com", "password": "different1"})
    assert response.status_code == 409


def test_password_login_failures_look_the_same():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    client.post("/auth/email/register", json={"email": "a@example.com", "password": "longenough"})
    client.post("/auth/logout")
    wrong_password = client.post("/auth/email/login", json={"email": "a@example.com", "password": "wrongwrong"})
    no_account = client.post("/auth/email/login", json={"email": "b@example.com", "password": "wrongwrong"})
    assert wrong_password.status_code == no_account.status_code == 401
    assert wrong_password.get_json() == no_account.get_json()


def test_google_login_checks_csrf_and_creates_verified_account():
    module, _fake_compute = load_app({**ADMIN, "GOOGLE_CLIENT_ID": "client-id"})
    client = module.app.test_client()
    assert client.post("/auth/login", data={"credential": "x", "g_csrf_token": "a"}).status_code == 400
    claims = {"sub": "123", "email": "Admin@gmail.com", "email_verified": True, "name": "Admin", "picture": ""}
    with unittest.mock.patch("src.auth.verify_google_token", return_value=claims):
        client.set_cookie("g_csrf_token", "tok")
        response = client.post("/auth/login", data={"credential": "x", "g_csrf_token": "tok"})
    assert response.status_code == 302
    stored = module.datastore_client.entities[("user", "google:123")]
    assert stored["verified"] is True
    assert stored["email"] == "admin@gmail.com"
    assert "badge" in client.get("/").get_data(as_text=True)


def test_dev_login_only_when_enabled_and_never_on_cloud_run():
    module, _fake_compute = load_app()
    assert module.app.test_client().post("/auth/dev-login", data={"email": "a@b.c"}).status_code == 404
    module, _fake_compute = load_app({"MACHINES_DEV_LOGIN": "1", "K_SERVICE": "machines"})
    assert module.app.test_client().post("/auth/dev-login", data={"email": "a@b.c"}).status_code == 404
    module, _fake_compute = load_app({"MACHINES_DEV_LOGIN": "1"})
    client = module.app.test_client()
    assert client.post("/auth/dev-login", data={"email": "a@b.c"}).status_code == 302
    assert "Signed in as a@b.c" in client.get("/").get_data(as_text=True)


def test_github_hidden_until_configured():
    module, _fake_compute = load_app()
    assert module.app.test_client().get("/api/config").get_json()["providers"] == {"github": False}
    assert module.app.test_client().get("/auth/oauth/github").status_code == 404
    module, _fake_compute = load_app({"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "secret"})
    response = module.app.test_client().get("/auth/oauth/github")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://github.com/login/oauth/authorize?")


def test_secret_key_required_on_cloud_run():
    with pytest.raises(RuntimeError):
        load_app({"K_SERVICE": "machines", "SECRET_KEY": ""})


def test_run_app_hostnames_redirect_to_the_custom_domain():
    module, _fake_compute = load_app({"CANONICAL_HOST": "machines.veltzer.org"})
    client = module.app.test_client()
    response = client.get("/some/path?x=1", base_url="https://machines-123.us-central1.run.app")
    assert response.status_code == 308
    assert response.headers["Location"] == "https://machines.veltzer.org/some/path?x=1"
    assert client.get("/app/health", base_url="https://machines-123.us-central1.run.app").status_code == 200
    assert client.get("/", base_url="https://machines.veltzer.org").status_code == 200


# ─── assignments ─────────────────────────────────────────────────────────────

def test_admin_sees_who_has_which_machine():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren", "raz@gmail.com": "raz"})
    add_user(module, "google:keren", "keren@gmail.com")
    add_user(module, "email:dana@example.com", "dana@example.com", verified=False)
    body = signed_in_client(module).get("/").get_data(as_text=True)
    assert 'id="students"' in body
    # machine cards name their students; the unlabeled one is unassigned
    assert "assigned to keren@gmail.com" in body
    assert "assigned to raz@gmail.com" in body
    assert "unassigned" in body
    # every account is listed, and roster students who have not signed up yet
    assert "dana@example.com" in body
    assert "not signed up" in body
    assert "Password" in body


def test_student_does_not_see_other_students():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren", "raz@gmail.com": "raz"})
    add_user(module, "google:raz", "raz@gmail.com")
    body = signed_in_client(module, "keren@gmail.com").get("/").get_data(as_text=True)
    assert "machine-keren" in body
    assert 'id="students"' not in body
    assert "raz@gmail.com" not in body
    assert "machine-raz" not in body


def test_admin_assigns_a_machine_to_a_password_account():
    module, fake_compute = load_app(ADMIN)
    add_user(module, "email:dana@example.com", "dana@example.com", verified=False)
    admin = signed_in_client(module)
    response = admin.post("/admin/assign", data={"subject": "email:dana@example.com", "owner": "raz"})
    assert response.status_code == 302
    assert module.datastore_client.entities[("user", "email:dana@example.com")]["owner"] == "raz"
    dana = module.app.test_client()
    with dana.session_transaction() as session:
        session["subject"] = "email:dana@example.com"
    body = dana.get("/").get_data(as_text=True)
    assert "machine-raz" in body
    assert "machine-keren" not in body
    instances = set_instance_status(fake_compute, "TERMINATED", owner="raz")
    assert dana.post("/process", data={"name": "machine-raz", "zone": "us-central1-a"}).status_code == 200
    instances.start.assert_called_once()
    # clearing it takes the machine away again
    admin.post("/admin/assign", data={"subject": "email:dana@example.com", "owner": ""})
    assert "owner" not in module.datastore_client.entities[("user", "email:dana@example.com")]
    assert "machine-raz" not in dana.get("/").get_data(as_text=True)


def test_assignment_wins_over_the_roster():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = signed_in_client(module, "keren@gmail.com", owner="raz")
    body = client.get("/").get_data(as_text=True)
    assert "machine-raz" in body
    assert "machine-keren" not in body


def test_only_admins_assign():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "email:dana@example.com", "dana@example.com", verified=False)
    student = signed_in_client(module, "keren@gmail.com")
    response = student.post("/admin/assign", data={"subject": "google:keren@gmail.com", "owner": "raz"})
    assert response.status_code == 403
    assert "owner" not in module.datastore_client.entities[("user", "google:keren@gmail.com")]
    anonymous = module.app.test_client().post("/admin/assign", data={"subject": "email:dana@example.com", "owner": "raz"})
    assert anonymous.status_code == 302
    assert "owner" not in module.datastore_client.entities[("user", "email:dana@example.com")]


def test_assign_refuses_unknown_machines_and_accounts():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "email:dana@example.com", "dana@example.com", verified=False)
    admin = signed_in_client(module)
    assert admin.post("/admin/assign", data={"subject": "email:dana@example.com", "owner": "nobody"}).status_code == 400
    assert admin.post("/admin/assign", data={"subject": "email:ghost@example.com", "owner": "raz"}).status_code == 404


# ─── machine keys ────────────────────────────────────────────────────────────

def set_key(fake_compute, private_key):
    """Makes the fake Secret Manager hold a machine key; returns the access mock."""
    access = fake_compute.projects.return_value.secrets.return_value.versions.return_value.access
    access.return_value.execute.return_value = {
        "payload": {"data": base64.b64encode(private_key.encode()).decode()},
    }
    return access


def test_student_downloads_own_machine_key():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    set_instance_status(fake_compute, "TERMINATED", owner="keren")
    access = set_key(fake_compute, "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
    client = signed_in_client(module, "keren@gmail.com")
    response = client.get("/key?name=machine-keren&zone=us-central1-a")
    assert response.status_code == 200
    assert response.get_data(as_text=True).startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert response.headers["Content-Disposition"] == 'attachment; filename="machine-keren.key"'
    assert response.headers["Cache-Control"] == "no-store"
    access.assert_called_with(name="projects/test-project/secrets/machine-key-machine-keren/versions/latest")


def test_student_cannot_download_others_key():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    set_instance_status(fake_compute, "RUNNING", owner="raz")
    access = set_key(fake_compute, "secret")
    access.reset_mock()
    client = signed_in_client(module, "keren@gmail.com")
    assert client.get("/key?name=machine-raz&zone=us-central1-a").status_code == 403
    access.assert_not_called()


def test_key_download_requires_sign_in_and_sane_names():
    module, fake_compute = load_app(ADMIN)
    set_instance_status(fake_compute, "RUNNING", owner="keren")
    access = set_key(fake_compute, "secret")
    access.reset_mock()
    assert module.app.test_client().get("/key?name=machine-keren&zone=us-central1-a").status_code == 302
    admin = signed_in_client(module)
    assert admin.get("/key?name=../../x&zone=us-central1-a").status_code == 400
    assert admin.get("/key?name=machine-keren&zone=us/central").status_code == 400
    access.assert_not_called()
    assert admin.get("/key?name=machine-keren&zone=us-central1-a").status_code == 200


def test_machine_without_key_says_so():
    module, fake_compute = load_app(ADMIN)
    set_instance_status(fake_compute, "RUNNING", owner="keren")
    access = fake_compute.projects.return_value.secrets.return_value.versions.return_value.access
    access.return_value.execute.side_effect = HttpError(unittest.mock.Mock(status=404, reason="Not Found"), b"")
    response = signed_in_client(module).get("/key?name=machine-keren&zone=us-central1-a")
    assert response.status_code == 404
    assert "no downloadable key" in response.get_data(as_text=True)


def test_cards_offer_key_and_ssh_command():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    body = signed_in_client(module, "keren@gmail.com").get("/").get_data(as_text=True)
    assert "Download key" in body
    assert "ssh -i machine-keren.key ubuntu@1.2.3.4" in body


def test_roster_match_of_a_password_account_is_suggested_not_given():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@proton.me": "raz"})
    add_user(module, "email:dana@proton.me", "dana@proton.me", verified=False)
    body = signed_in_client(module).get("/").get_data(as_text=True)
    assert "roster says raz once the email is verified; Save to give it now" in body
    assert "Password (email not verified yet)" in body
    assert 'data-selected="raz"' in body
    dana = module.app.test_client()
    with dana.session_transaction() as session:
        session["subject"] = "email:dana@proton.me"
    body = dana.get("/").get_data(as_text=True)
    assert "verify your email" in body
    assert "machine-raz" not in body


# ─── email verification ─────────────────────────────────────────────────────

def register(client, email="dana@proton.me", password="longenough"):
    return client.post("/auth/email/register", json={"email": email, "password": password})


def sign_in(client, email="dana@proton.me", password="longenough"):
    return client.post("/auth/email/login", json={"email": email, "password": password})


def follow_link(module, client, email="dana@proton.me"):
    """
    The owner of the address follows the last link mailed to it, on Google's
    page, then presses Continue in the browser of the given client.
    """
    verifier = module.app.config["VERIFIER"]
    url = [url for to, url in verifier.sent if to == email][-1]
    verifier.followed.add(email)
    return client.get(url.removeprefix("http://localhost"))


def test_register_mails_a_verification_link():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    assert register(client).status_code == 200
    [(to, url)] = module.app.config["VERIFIER"].sent
    assert to == "dana@proton.me"
    token = module.datastore_client.entities[("user", "email:dana@proton.me")]["verification_token"]
    assert url == f"http://localhost/auth/verify/confirm?token={token}"
    body = client.get("/").get_data(as_text=True)
    assert "We mailed a verification link to dana@proton.me." in body
    assert "Send again" in body


def test_verified_password_account_gets_its_roster_machine():
    module, _fake_compute = load_app()
    set_mapping(module, {"dana@proton.me": "raz"})
    client = module.app.test_client()
    register(client)
    assert "machine-raz" not in client.get("/").get_data(as_text=True)
    assert follow_link(module, client).status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert "Your email is verified." in body
    assert "machine-raz" in body
    assert "Send again" not in body
    assert module.datastore_client.entities[("user", "email:dana@proton.me")]["verified"] is True


def test_verified_password_account_can_be_admin():
    module, _fake_compute = load_app(ADMIN)
    client = module.app.test_client()
    register(client, "admin@gmail.com")
    follow_link(module, client, "admin@gmail.com")
    assert "All machines" in client.get("/").get_data(as_text=True)


def test_resend_is_paced():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    register(client)
    client.get("/")
    response = client.post("/auth/verify/resend")
    assert response.status_code == 302
    assert "less than a minute ago" in client.get("/").get_data(as_text=True)
    assert len(module.app.config["VERIFIER"].sent) == 1
    entity = module.datastore_client.entities[("user", "email:dana@proton.me")]
    entity["verification_sent"] -= datetime.timedelta(minutes=2)
    client.post("/auth/verify/resend")
    assert len(module.app.config["VERIFIER"].sent) == 2


def test_resend_only_for_unverified_password_accounts():
    module, _fake_compute = load_app()
    assert module.app.test_client().post("/auth/verify/resend").status_code == 302
    client = signed_in_client(module, "keren@gmail.com")
    client.post("/auth/verify/resend")
    assert module.app.config["VERIFIER"].sent == []


def test_failed_mail_still_creates_the_account():
    module, _fake_compute = load_app()
    module.app.config["VERIFIER"].fail = True
    client = module.app.test_client()
    assert register(client).status_code == 200
    body = client.get("/").get_data(as_text=True)
    assert "could not be sent" in body
    assert "Signed in as dana@proton.me" in body
    assert "verification_sent" not in module.datastore_client.entities[("user", "email:dana@proton.me")]


def test_registering_again_replaces_an_unverified_account():
    """An unverified account proves nothing, so it cannot keep an address (or an assignment) from its owner."""
    module, _fake_compute = load_app()
    register(module.app.test_client(), password="squatter1")
    module.datastore_client.entities[("user", "email:dana@proton.me")]["owner"] = "raz"
    client = module.app.test_client()
    assert register(client, password="realowner").status_code == 200
    stored = module.datastore_client.entities[("user", "email:dana@proton.me")]
    assert "owner" not in stored
    # the second registration came within the minute: no second mail, the first link still serves
    assert len(module.app.config["VERIFIER"].sent) == 1
    client.post("/auth/logout")
    assert client.post("/auth/email/login", json={"email": "dana@proton.me", "password": "squatter1"}).status_code == 401
    assert client.post("/auth/email/login", json={"email": "dana@proton.me", "password": "realowner"}).status_code == 200


def test_failed_verification_check_counts_as_not_yet():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    register(client)
    module.app.config["VERIFIER"].fail = True
    follow_link(module, client)
    assert "verify your email" in client.get("/").get_data(as_text=True)
    assert module.datastore_client.entities[("user", "email:dana@proton.me")]["verified"] is False


def test_followed_link_alone_does_not_verify():
    """Google saying the address was verified is not enough without the link's secret in the session."""
    module, _fake_compute = load_app()
    client = module.app.test_client()
    register(client)
    module.app.config["VERIFIER"].followed.add("dana@proton.me")
    assert "verify your email" in client.get("/").get_data(as_text=True)
    client.get("/auth/verify/confirm?token=guessed")
    assert "verify your email" in client.get("/").get_data(as_text=True)


def test_squatter_cannot_get_verified_by_the_owner_clicking():
    """
    Someone registers a student's address; the student gets Google's genuine
    mail and follows it. That must not verify the squatter's account.
    """
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@proton.me": "raz"})
    squatter = module.app.test_client()
    register(squatter, password="squatter1")
    owner = module.app.test_client()
    response = follow_link(module, owner)
    assert response.status_code == 302
    assert "error=Sign+in+with+your+email+and+password" in response.location
    # the owner does not know the squatter's password, so cannot finish it
    assert sign_in(owner, password="realowner").status_code == 401
    body = squatter.get("/").get_data(as_text=True)
    assert "verify your email" in body
    assert "machine-raz" not in body
    assert module.datastore_client.entities[("user", "email:dana@proton.me")]["verified"] is False


def test_link_followed_in_another_browser_counts_after_signing_in_there():
    module, _fake_compute = load_app()
    set_mapping(module, {"dana@proton.me": "raz"})
    register(module.app.test_client())
    phone = module.app.test_client()
    follow_link(module, phone)
    assert sign_in(phone).status_code == 200
    body = phone.get("/").get_data(as_text=True)
    assert "Your email is verified." in body
    assert "machine-raz" in body


def test_replacing_an_unverified_account_signs_out_its_sessions():
    """
    A student registers; before they follow the link someone registers the
    address again. The student's session must not now belong to the
    squatter's account, or following the link would verify it.
    """
    module, _fake_compute = load_app()
    student = module.app.test_client()
    register(student, password="realowner")
    assert "Signed in as dana@proton.me" in student.get("/").get_data(as_text=True)
    squatter = module.app.test_client()
    register(squatter, password="squatter1")
    assert follow_link(module, student).status_code == 302
    assert "Signed in as" not in student.get("/").get_data(as_text=True)
    assert module.datastore_client.entities[("user", "email:dana@proton.me")]["verified"] is False
    # registering again takes the address back, and the mail already in hand still serves
    register(student, password="realowner")
    assert "Your email is verified." in student.get("/").get_data(as_text=True)
    assert "verify your email" not in squatter.get("/").get_data(as_text=True)
    assert sign_in(squatter, password="squatter1").status_code == 401


def test_version_reports_deploy_stamp_and_revision():
    module, _fake_compute = load_app()
    module.app.config["BUILD_INFO"] = {"deploy_date": "2026-11-14T21:00:00Z", "git_describe": "dd8e1e0"}
    with unittest.mock.patch.dict(os.environ, {"K_REVISION": "machines-00011-abc"}):
        response = module.app.test_client().get("/app/version")
    assert response.status_code == 200
    assert response.get_json() == {
        "deploy_date": "2026-11-14T21:00:00Z",
        "git_describe": "dd8e1e0",
        "revision": "machines-00011-abc",
    }


def test_both_pages_carry_the_build_footer():
    module, _fake_compute = load_app(ADMIN)
    signed_out = module.app.test_client().get("/").get_data(as_text=True)
    signed_in = signed_in_client(module).get("/").get_data(as_text=True)
    for body in (signed_out, signed_in):
        assert '<p class="inscription" id="build"></p>' in body


@pytest.mark.parametrize(("subject", "icon", "name"), [
    ("google:1234", "google", "Google"),
    ("github:5678", "github", "GitHub"),
    ("email:keren@gmail.com", "email", "email and password"),
])
def test_top_bar_shows_how_the_user_signed_in(subject, icon, name):
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module, email="keren@gmail.com", subject=subject)
    body = client.get("/").get_data(as_text=True)
    assert f'title="Signed in with {name}"' in body
    assert f'src="/static/{icon}.svg" alt="{name}"' in body
    assert 'src="/static/signout.svg"' in body


# ─── thread safety and response headers ─────────────────────────────────────

def test_each_thread_gets_its_own_api_connection():
    module, fake_compute = load_app()
    seen = []
    thread = threading.Thread(target=lambda: seen.append(module.api_http()))
    thread.start()
    thread.join()
    assert module.api_http() is module.api_http()
    assert seen[0] is not module.api_http()
    signed_in_client(module, "keren@gmail.com").get("/")
    call = fake_compute.instances.return_value.aggregatedList.return_value.execute.call_args
    assert call.kwargs["http"] is module.api_http()


def test_pages_cannot_be_framed():
    module, _fake_compute = load_app(ADMIN)
    for response in (module.app.test_client().get("/"), signed_in_client(module).get("/")):
        csp = response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp
        assert "script-src 'self' https://accounts.google.com/gsi/client" in csp
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "Strict-Transport-Security" not in response.headers
    # the SVGs keep their inline styles: no CSP on what is not a page
    assert "Content-Security-Policy" not in module.app.test_client().get("/static/scene.svg").headers


def test_hsts_on_cloud_run():
    module, _fake_compute = load_app()
    with unittest.mock.patch.dict(os.environ, {"K_SERVICE": "machines"}):
        response = module.app.test_client().get("/app/health")
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
