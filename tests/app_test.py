"""
Tests for the machines web app.

google.auth and the compute API client are mocked out so these tests run
without any GCP credentials.
"""

import base64
import contextlib
import hashlib
import importlib
import os
import sys
import threading
import unittest.mock
import urllib.parse
from typing import override

import flask.testing
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

    def delete(self, key):
        self.entities.pop((key.kind, key.name), None)

    def put(self, entity):
        self.entities[(entity.key.kind, entity.key.name)] = entity

    def transaction(self):
        return contextlib.nullcontext()

    def query(self, kind):
        if kind == "student":
            return FakeQuery(self.students)
        return FakeQuery([e for (k, _name), e in self.entities.items() if k == kind])


class FakeQuery:
    """A Datastore query over a fixed list: fetch, narrowed by equality filters."""

    def __init__(self, entities):
        self.entities = entities

    def add_filter(self, **kwargs):  # the real client is called with the keyword filter=
        flt = kwargs["filter"]
        assert flt.operator == "="
        self.entities = [e for e in self.entities if e.get(flt.property_name) == flt.value]
        return self

    def fetch(self, limit=None):
        return list(self.entities)[:limit]


class FakeMailer:
    """
    Stands in for the verification mailer: keeps the links it was asked to
    mail, (email, link) in the order sent, and the addresses it was told to
    forget; or fails, like Identity Platform out of quota.
    """

    def __init__(self):
        self.sent = []
        self.forgotten = []
        self.fail = False

    def send(self, email, continue_url):
        if self.fail:
            # src is importable only after the sys.path line above
            verification_error = importlib.import_module("src.verification").VerificationError
            raise verification_error("Identity Platform: QUOTA_EXCEEDED")
        self.sent.append((email, continue_url))

    def forget(self, email):
        self.forgotten.append(email)


class BufferedClient(flask.testing.FlaskClient):
    """
    A test client that reads each reply whole and closes it, as a WSGI server
    does. The default leaves a streamed reply (a file from
    send_from_directory) open until garbage collection.
    """

    @override
    def open(self, *args, **kwargs):
        kwargs.setdefault("buffered", True)
        return super().open(*args, **kwargs)


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
    module.app.test_client_class = BufferedClient
    module.app.config["MAILER"] = FakeMailer()
    return module, fake_compute


class FakeStudent(dict):
    """A Datastore student entity: a dict with a key carrying the email."""

    def __init__(self, email, owner):
        super().__init__(owner=owner)
        self.key = FakeKey("student", email)


def set_mapping(module, mapping):
    """Makes the app's fake Datastore return the given email -> owner roster."""
    module.datastore_client.students = [FakeStudent(email, owner) for email, owner in mapping.items()]


def add_user(module, subject, email, owner=None):
    """
    Creates a login in the fake Datastore, joined to the user of its email
    (which holds the assignment), and returns the login.
    """
    login = datastore.Entity(key=FakeKey("login", subject))
    login.update({"email": email, "name": email.split("@")[0], "picture": "", "user": email})
    user = module.datastore_client.get(FakeKey("user", email))
    if user is None:
        user = datastore.Entity(key=FakeKey("user", email))
        user.update({"email": email, "name": email.split("@")[0], "picture": ""})
    if owner is not None:
        user["owner"] = owner
    module.datastore_client.put(user)
    module.datastore_client.put(login)
    return login


def signed_in_client(module, email="admin@gmail.com", subject=None, owner=None):
    """A test client whose session belongs to a (new) account."""
    subject = subject or f"google:{email}"
    add_user(module, subject, email, owner=owner)
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


def test_privacy_page_is_public():
    module, _fake_compute = load_app()
    response = module.app.test_client().get("/privacy")
    assert response.status_code == 200
    assert "<h1>Privacy</h1>" in response.get_data(as_text=True)


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


def test_actions_reject_get():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module)
    for path in ("/start", "/stop", "/delete", "/create"):
        assert client.get(f"{path}?name=machine-keren&zone=us-central1-a").status_code == 405


def test_actions_require_sign_in():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "RUNNING", owner="keren")
    client = module.app.test_client()
    for path in ("/start", "/stop", "/delete", "/create"):
        assert client.post(path, data={"name": "machine-keren", "zone": "us-central1-a"}).status_code == 302
    instances.stop.assert_not_called()
    instances.delete.assert_not_called()
    instances.insert.assert_not_called()


def test_start_resumes_suspended_machine():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "SUSPENDED")
    client = signed_in_client(module)
    response = client.post("/start", data={"name": "machine-bare", "zone": "us-central1-a"})
    assert response.status_code == 302
    instances.resume.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-bare")
    instances.start.assert_not_called()


def test_stop_stops_running_machine():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "RUNNING")
    client = signed_in_client(module)
    response = client.post("/stop", data={"name": "machine-keren", "zone": "us-central1-a"}, follow_redirects=True)
    assert "Stopping machine machine-keren" in response.get_data(as_text=True)
    instances.stop.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-keren")
    instances.suspend.assert_not_called()


def test_start_starts_terminated_machine():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "TERMINATED")
    client = signed_in_client(module)
    response = client.post("/start", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 302
    instances.start.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-keren")


def test_actions_refuse_machine_in_transition():
    module, fake_compute = load_app(ADMIN)
    instances = set_instance_status(fake_compute, "STOPPING")
    client = signed_in_client(module)
    for path in ("/start", "/stop"):
        response = client.post(path, data={"name": "machine-keren", "zone": "us-central1-a"}, follow_redirects=True)
        assert "is STOPPING right now" in response.get_data(as_text=True)
    instances.resume.assert_not_called()
    instances.stop.assert_not_called()
    instances.start.assert_not_called()


def test_actions_require_name_and_zone():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module)
    assert client.post("/stop", data={"name": "machine-keren"}).status_code == 400


def test_cards_offer_stop_or_start_and_delete():
    module, _fake_compute = load_app(ADMIN)
    body = signed_in_client(module).get("/").get_data(as_text=True)
    # one running machine (Stop), two not running (Start), every one can be deleted
    assert body.count('action="/stop"') == 1
    assert body.count('action="/start"') == 2
    assert body.count('action="/delete"') == 3
    assert "Suspend" not in body


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


def test_an_address_typed_in_reaches_no_machine():
    """Anyone can type a student's address into the form: that must not reach their machine."""
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = module.app.test_client()
    assert request_link(client, "keren@gmail.com").status_code == 200
    assert sign_in(client, "keren@gmail.com").status_code == 401
    assert 'id="email-form"' in client.get("/").get_data(as_text=True)
    assert not [key for key in module.datastore_client.entities if key[0] in ("login", "user")]
    instances = set_instance_status(fake_compute, "RUNNING", owner="keren")
    assert client.post("/stop", data={"name": "machine-keren", "zone": "us-central1-a"}).status_code == 302
    instances.stop.assert_not_called()


def test_an_admin_address_typed_in_is_not_admin():
    module, _fake_compute = load_app(ADMIN)
    client = module.app.test_client()
    request_link(client, "admin@gmail.com")
    assert "All machines" not in client.get("/").get_data(as_text=True)


def test_assigned_owner_wins_for_any_account():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module, "raz@example.com", subject="email:raz@example.com", owner="raz")
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
    response = client.post("/stop", data={"name": "machine-keren", "zone": "us-central1-a"})
    assert response.status_code == 302
    instances.stop.assert_called_once()


def test_student_cannot_toggle_others_machine():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "RUNNING", owner="raz")
    client = signed_in_client(module, "keren@gmail.com")
    for path in ("/stop", "/delete"):
        response = client.post(path, data={"name": "machine-raz", "zone": "us-central1-a"})
        assert response.status_code == 403
    instances.stop.assert_not_called()
    instances.delete.assert_not_called()
    instances.resume.assert_not_called()
    instances.start.assert_not_called()


def test_student_cannot_toggle_unowned_machine():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "SUSPENDED")
    client = signed_in_client(module, "keren@gmail.com")
    response = client.post("/start", data={"name": "machine-bare", "zone": "us-central1-a"})
    assert response.status_code == 403
    instances.resume.assert_not_called()


# ─── sign-in ─────────────────────────────────────────────────────────────────

def test_email_sign_up_needs_the_mailed_link():
    """Asking for an address gives no account and no way in: only the link's reader chooses the password."""
    module, _fake_compute = load_app(ADMIN)
    client = module.app.test_client()
    assert client.get("/api/config").get_json()["password_min_length"] == module.auth.PASSWORD_MIN_LENGTH
    response = request_link(client, "  Carol@Example.com ")
    assert "mailed a link to carol@example.com" in response.get_json()["message"]
    assert [to for to, _ in module.app.config["MAILER"].sent] == ["carol@example.com"]
    # nothing to sign in to yet, nor a user, and the form is no signed-in page
    assert not [key for key in module.datastore_client.entities if key[0] in ("login", "user")]
    assert client.get("/api/config").get_json()["signed_in"] is False
    assert sign_in(client, "carol@example.com", "correct horse").status_code == 401
    # the link's page says for which address, then the password is chosen
    token = mailed_token(module, "carol@example.com")
    assert client.get("/verify").status_code == 200
    assert client.post("/auth/email/verify", json={"token": token}).get_json() == {"email": "carol@example.com"}
    response = client.post("/auth/email/password", json={"token": token, "password": "correct horse"})
    assert response.get_json() == {"next": "/"}
    entities = module.datastore_client.entities
    login = entities[("login", "email:carol@example.com")]
    assert login["user"] == "carol@example.com"
    assert login["password_hash"] != "correct horse"
    assert "Signed in as carol@example.com" in client.get("/").get_data(as_text=True)
    assert module.app.config["MAILER"].forgotten == ["carol@example.com"]
    # the link worked once, and nothing is left of it
    assert client.post("/auth/email/password", json={"token": token, "password": "another one"}).status_code == 400
    assert ("email_verification", "carol@example.com") not in entities
    client.post("/auth/logout")
    assert 'id="email-form"' in client.get("/").get_data(as_text=True)
    assert sign_in(client, "carol@example.com", "correct horse").status_code == 200
    assert "Signed in as carol@example.com" in client.get("/").get_data(as_text=True)


def test_only_the_hash_of_the_token_is_stored():
    module, _fake_compute = load_app()
    request_link(module.app.test_client(), "carol@example.com")
    token = mailed_token(module, "carol@example.com")
    pending = module.datastore_client.entities[("email_verification", "carol@example.com")]
    assert pending["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in pending.values()


def test_email_bad_input():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    for body in ({"email": "not an email"}, {}, {"email": ["carol@example.com"]}):
        response = client.post("/auth/email/register", json=body)
        assert response.status_code == 400
        assert "error" in response.get_json()
    assert client.post("/auth/email/register", data="not json").status_code == 400
    assert not module.app.config["MAILER"].sent
    for token in ("", "forged", None):
        response = client.post("/auth/email/verify", json={"token": token})
        assert response.status_code == 400
        assert "link" in response.get_json()["error"]
    request_link(client, "carol@example.com")
    token = mailed_token(module, "carol@example.com")
    response = client.post("/auth/email/password", json={"token": token, "password": "short"})
    assert response.status_code == 400
    assert "8 characters" in response.get_json()["error"]
    # a refused password does not spend the link
    assert client.post("/auth/email/verify", json={"token": token}).status_code == 200
    assert client.get("/api/config").get_json()["signed_in"] is False


def test_a_new_link_voids_the_old_and_waits_a_minute():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    request_link(client, "carol@example.com")
    first = mailed_token(module, "carol@example.com")
    # asking again at once mails nothing more, and answers the same
    assert request_link(client, "carol@example.com").status_code == 200
    assert len(module.app.config["MAILER"].sent) == 1
    pending = module.datastore_client.entities[("email_verification", "carol@example.com")]
    pending["sent_at"] -= module.auth.RESEND_INTERVAL
    request_link(client, "carol@example.com")
    second = mailed_token(module, "carol@example.com")
    assert second != first
    assert client.post("/auth/email/verify", json={"token": first}).status_code == 400
    assert client.post("/auth/email/verify", json={"token": second}).status_code == 200
    # and a link works for a day
    pending = module.datastore_client.entities[("email_verification", "carol@example.com")]
    pending["sent_at"] -= module.auth.LINK_LIFETIME
    response = client.post("/auth/email/verify", json={"token": second})
    assert response.status_code == 400
    assert "expired" in response.get_json()["error"]


def test_the_answer_is_the_same_whether_or_not_the_address_has_an_account():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "email:keren@gmail.com", "keren@gmail.com")
    known = request_link(module.app.test_client(), "keren@gmail.com")
    unknown = request_link(module.app.test_client(), "nobody@example.com")
    assert known.status_code == unknown.status_code == 200
    assert known.get_json()["message"].replace("keren@gmail.com", "X") == unknown.get_json()["message"].replace("nobody@example.com", "X")


def test_a_mail_that_cannot_be_sent_is_said_and_leaves_nothing():
    module, _fake_compute = load_app()
    module.app.config["MAILER"].fail = True
    response = request_link(module.app.test_client(), "carol@example.com")
    assert response.status_code == 503
    assert "could not be sent" in response.get_json()["error"]
    assert ("email_verification", "carol@example.com") not in module.datastore_client.entities


def test_password_login_failures_look_the_same():
    module, _fake_compute = load_app()
    client = module.app.test_client()
    register(module, client, "a@example.com")
    client.post("/auth/logout")
    wrong_password = sign_in(client, "a@example.com", "wrongwrong")
    no_account = sign_in(client, "b@example.com", "wrongwrong")
    assert wrong_password.status_code == no_account.status_code == 401
    assert wrong_password.get_json() == no_account.get_json()
    assert wrong_password.get_json()["error"] == "wrong email or password (a new address signs in only after you open the link we mailed it)"
    # a login of another way, with no password, answers the same
    add_user(module, "google:1", "c@example.com")
    assert sign_in(client, "c@example.com", "wrongwrong").get_json() == no_account.get_json()


def test_a_forgotten_password_is_replaced_by_mail():
    """Asking for a link for an address with a login answers the same, and the link sets a new password."""
    module, _fake_compute = load_app()
    client = module.app.test_client()
    register(module, client, password="old password")
    client.post("/auth/logout")
    # the first link is spent, so nothing paces the next one
    register(module, client, password="new password")
    client.post("/auth/logout")
    assert sign_in(client, password="old password").status_code == 401
    assert sign_in(client, password="new password").status_code == 200


def test_the_link_page_is_served_and_keeps_its_token_to_itself():
    module, _fake_compute = load_app()
    response = module.app.test_client().get("/verify?token=abc")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<meta name="referrer" content="no-referrer">' in body
    assert 'data-page="verify"' in body
    assert 'id="password-form"' in body
    assert "Content-Security-Policy" in response.headers


GOOGLE = {"GOOGLE_CLIENT_ID": "client-id", "GOOGLE_CLIENT_SECRET": "client-secret"}


def test_google_sign_in_is_one_redirect_and_checks_the_state():
    module, _fake_compute = load_app({**ADMIN, **GOOGLE})
    client = module.app.test_client()
    response = client.get("/auth/oauth/google")
    assert response.status_code == 302
    location = urllib.parse.urlsplit(response.headers["Location"])
    params = urllib.parse.parse_qs(location.query)
    assert location.netloc == "accounts.google.com"
    # back to /auth/login, the redirect URI registered with the OAuth client
    assert params["redirect_uri"] == ["http://localhost/auth/login"]
    assert params["scope"] == ["openid email profile"]
    assert params["prompt"] == ["select_account"]
    assert client.get("/auth/login?state=wrong&code=c").status_code == 400
    # Google comes back to /auth/login, never to the generic callback
    assert client.get("/auth/oauth/google/callback?state=x&code=c").status_code == 404


def test_google_sign_in_creates_an_account():
    module, _fake_compute = load_app({**ADMIN, **GOOGLE})
    client = module.app.test_client()
    google_sign_in(module, client, "123", "Admin@gmail.com")
    stored = module.datastore_client.entities[("login", "google:123")]
    assert stored["email"] == "admin@gmail.com"
    assert "badge" in client.get("/").get_data(as_text=True)


def test_google_email_google_did_not_verify_is_refused():
    module, _fake_compute = load_app({**ADMIN, **GOOGLE})
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = module.app.test_client()
    response = google_sign_in(module, client, "9", "keren@gmail.com", verified=False)
    assert urllib.parse.unquote_plus(response.location) == (
        "/?error=Google did not confirm an email address for you: verify one with Google and sign in again")
    # nothing is stored, and nobody is signed in
    assert not [key for key in module.datastore_client.entities if key[0] in ("login", "user")]
    assert 'id="email-form"' in client.get("/").get_data(as_text=True)


def test_sign_in_page_offers_three_icons():
    module, _fake_compute = load_app()
    body = module.app.test_client().get("/").get_data(as_text=True)
    assert 'href="/auth/oauth/google"' in body
    assert 'href="/auth/oauth/github"' in body
    assert 'id="email-method"' in body
    # the email form waits behind its icon
    assert '<form id="email-form" class="email-form" novalidate hidden>' in body


# ─── one user, several logins ────────────────────────────────────────────────

GITHUB = {"GITHUB_CLIENT_ID": "gh-id", "GITHUB_CLIENT_SECRET": "gh-secret"}


def google_sign_in(_module, client, sub, email, *, verified=True):
    """Signs the client in with Google as the given identity, through the code flow."""
    client.get("/auth/oauth/google")
    with client.session_transaction() as session:
        state = session["oauth_state"]
    claims = {"sub": sub, "email": email, "email_verified": verified, "name": email.split("@")[0], "picture": ""}
    with unittest.mock.patch("src.auth.exchange_code", return_value={"id_token": "signed"}), \
            unittest.mock.patch("src.auth.verify_google_token", return_value=claims) as verify:
        response = client.get(f"/auth/login?state={state}&code=c")
        assert response.status_code == 302
    verify.assert_called_once_with("signed")
    return response


def github_sign_in(client, github_id, email):
    """Signs the client in with GitHub as the given identity; email "" means no verified primary address."""
    client.get("/auth/oauth/github")
    with client.session_transaction() as session:
        state = session["oauth_state"]
    identity = (f"github:{github_id}", email, "Name", "", bool(email))
    with unittest.mock.patch("src.auth.exchange_code", return_value={"access_token": "token"}), \
            unittest.mock.patch("src.auth.fetch_identity", return_value=identity):
        response = client.get(f"/auth/oauth/github/callback?state={state}&code=c")
        assert response.status_code == 302
    return response


def test_google_and_github_with_the_same_email_are_one_user():
    module, _fake_compute = load_app({**ADMIN, **GITHUB, **GOOGLE})
    google_sign_in(module, module.app.test_client(), "g1", "Keren@gmail.com")
    github_sign_in(module.app.test_client(), 77, "keren@gmail.com")
    entities = module.datastore_client.entities
    assert entities[("login", "google:g1")]["user"] == "keren@gmail.com"
    assert entities[("login", "github:77")]["user"] == "keren@gmail.com"
    assert [name for kind, name in entities if kind == "user"] == ["keren@gmail.com"]
    body = signed_in_client(module).get("/").get_data(as_text=True)
    assert body.count('<br><span class="source">keren@gmail.com</span>') == 1  # one row on the Students table
    assert "GitHub, Google" in body


def test_an_assignment_holds_for_every_way_the_user_signs_in():
    module, _fake_compute = load_app({**ADMIN, **GITHUB, **GOOGLE})
    google = module.app.test_client()
    google_sign_in(module, google, "g1", "keren@gmail.com")
    admin = signed_in_client(module)
    assert admin.post("/admin/assign", data={"email": "keren@gmail.com", "owner": "raz"}).status_code == 302
    github = module.app.test_client()
    github_sign_in(github, 77, "keren@gmail.com")
    for client in (google, github):
        body = client.get("/").get_data(as_text=True)
        assert "machine-raz" in body
        assert "machine-keren" not in body


def test_asking_for_a_link_does_not_touch_the_user_of_the_address():
    module, fake_compute = load_app(ADMIN)
    add_user(module, "google:g1", "dana@proton.me", owner="raz")
    squatter = module.app.test_client()
    request_link(squatter, "dana@proton.me")
    assert ("login", "email:dana@proton.me") not in module.datastore_client.entities
    assert 'id="email-form"' in squatter.get("/").get_data(as_text=True)
    instances = set_instance_status(fake_compute, "TERMINATED", owner="raz")
    assert squatter.post("/start", data={"name": "machine-raz", "zone": "us-central1-a"}).status_code == 302
    instances.start.assert_not_called()


def test_a_mailed_address_joins_the_user_of_its_email():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "google:g1", "dana@proton.me", owner="raz")
    client = module.app.test_client()
    register(module, client)
    assert "machine-raz" in client.get("/").get_data(as_text=True)
    assert module.datastore_client.entities[("login", "email:dana@proton.me")]["user"] == "dana@proton.me"
    assert [name for kind, name in module.datastore_client.entities if kind == "user"] == ["dana@proton.me"]


def test_a_login_stays_with_its_user_when_the_provider_email_changes():
    module, _fake_compute = load_app({**ADMIN, **GITHUB})
    github_sign_in(module.app.test_client(), 77, "keren@gmail.com")
    github_sign_in(module.app.test_client(), 77, "keren@work.example")
    login = module.datastore_client.entities[("login", "github:77")]
    assert login["user"] == "keren@gmail.com"
    assert login["email"] == "keren@work.example"
    assert ("user", "keren@work.example") not in module.datastore_client.entities


def test_github_without_a_verified_email_is_refused():
    module, _fake_compute = load_app({**ADMIN, **GITHUB})
    set_mapping(module, {"keren@gmail.com": "keren"})
    client = module.app.test_client()
    response = github_sign_in(client, 77, "")
    assert urllib.parse.unquote_plus(response.location) == (
        "/?error=GitHub did not confirm an email address for you: verify one with GitHub and sign in again")
    assert not [key for key in module.datastore_client.entities if key[0] in ("login", "user")]
    assert 'id="email-form"' in client.get("/").get_data(as_text=True)


def test_account_panel_lists_every_way_the_user_signs_in():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    add_user(module, "github:77", "keren@gmail.com")
    add_user(module, "email:keren@gmail.com", "keren@gmail.com")
    add_user(module, "google:other", "someone@gmail.com")
    body = signed_in_client(module, "keren@gmail.com", subject="google:g1").get("/").get_data(as_text=True)
    assert 'id="account-menu"' in body
    assert "Your 3 ways to sign in" in body
    assert "Google ID g1" in body
    assert "GitHub user ID 77" in body
    # an email login's id is its email; the account's own address is not repeated per login
    assert "Email keren@gmail.com" in body
    assert "reports keren@gmail.com" not in body
    assert "Google ID other" not in body
    # the one in use comes first and says so
    assert body.index("Google ID g1") < body.index("GitHub user ID 77")
    assert body.count("this session") == 1
    assert "keren (from the roster)" in body
    assert "<dd>Student</dd>" in body


def test_the_top_bar_shows_the_users_name():
    module, _fake_compute = load_app({**ADMIN, **GOOGLE})
    client = module.app.test_client()
    google_sign_in(module, client, "g1", "keren@gmail.com")
    module.datastore_client.entities[("user", "keren@gmail.com")]["name"] = "Keren Cohen"
    body = client.get("/").get_data(as_text=True)
    assert '<span class="email">Keren Cohen</span>' in body
    assert 'title="Signed in as keren@gmail.com"' in body


def test_a_user_without_a_name_is_called_by_their_email():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "github:7", "dana@proton.me")
    module.datastore_client.entities[("user", "dana@proton.me")]["name"] = ""
    body = signed_in_client(module, "dana@proton.me", subject="github:7").get("/").get_data(as_text=True)
    assert '<span class="email">dana</span>' in body


def test_a_user_renames_themself_for_every_way_they_sign_in():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "github:7", "keren@gmail.com")
    client = signed_in_client(module, "keren@gmail.com")
    response = client.post("/account/name", data={"name": "  Keren   Cohen "}, follow_redirects=True)
    assert "You are now called Keren Cohen." in response.get_data(as_text=True)
    assert module.datastore_client.entities[("user", "keren@gmail.com")]["name"] == "Keren Cohen"
    github = module.app.test_client()
    with github.session_transaction() as session:
        session["subject"] = "github:7"
    assert '<span class="email">Keren Cohen</span>' in github.get("/").get_data(as_text=True)
    # and the admin sees it on the Students table
    assert '<span class="student-name">Keren Cohen</span>' in signed_in_client(module).get("/").get_data(as_text=True)


def test_a_name_must_be_sensible():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module, "keren@gmail.com")
    before = module.datastore_client.entities[("user", "keren@gmail.com")]["name"]
    for bad in ("", "   ", "x" * 61, "bad\u0000name"):
        response = client.post("/account/name", data={"name": bad}, follow_redirects=True)
        assert "A name is 1 to 60 printable characters." in response.get_data(as_text=True)
    assert module.datastore_client.entities[("user", "keren@gmail.com")]["name"] == before
    assert module.app.test_client().post("/account/name", data={"name": "x"}).status_code == 302
    # the form's own limit is the server's
    assert f'maxlength="{module.users.NAME_MAX_LENGTH}"' in client.get("/").get_data(as_text=True)


def test_a_login_without_a_user_is_nobody():
    """An unproven login (the old kind) signs in to nothing, whatever its session says."""
    module, _fake_compute = load_app(ADMIN)
    login = datastore.Entity(key=FakeKey("login", "email:dana@proton.me"))
    login.update({"email": "dana@proton.me", "name": "dana", "picture": ""})
    module.datastore_client.put(login)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session["subject"] = "email:dana@proton.me"
    assert 'id="email-form"' in client.get("/").get_data(as_text=True)


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
    assert module.app.test_client().get("/api/config").get_json()["providers"] == {"google": False, "github": False}
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
    add_user(module, "email:dana@example.com", "dana@example.com")
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


def test_admin_assigns_a_machine_to_a_user():
    module, fake_compute = load_app(ADMIN)
    add_user(module, "email:dana@example.com", "dana@example.com")
    admin = signed_in_client(module)
    response = admin.post("/admin/assign", data={"email": "dana@example.com", "owner": "raz"})
    assert response.status_code == 302
    assert module.datastore_client.entities[("user", "dana@example.com")]["owner"] == "raz"
    dana = module.app.test_client()
    with dana.session_transaction() as session:
        session["subject"] = "email:dana@example.com"
    body = dana.get("/").get_data(as_text=True)
    assert "machine-raz" in body
    assert "machine-keren" not in body
    instances = set_instance_status(fake_compute, "TERMINATED", owner="raz")
    response = dana.post("/start", data={"name": "machine-raz", "zone": "us-central1-a"}, follow_redirects=True)
    assert "Starting machine machine-raz" in response.get_data(as_text=True)
    instances.start.assert_called_once()
    # clearing it takes the machine away again
    admin.post("/admin/assign", data={"email": "dana@example.com", "owner": ""})
    assert "owner" not in module.datastore_client.entities[("user", "dana@example.com")]
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
    student = signed_in_client(module, "keren@gmail.com")
    response = student.post("/admin/assign", data={"email": "keren@gmail.com", "owner": "raz"})
    assert response.status_code == 403
    assert "owner" not in module.datastore_client.entities[("user", "keren@gmail.com")]
    anonymous = module.app.test_client().post("/admin/assign", data={"email": "keren@gmail.com", "owner": "raz"})
    assert anonymous.status_code == 302
    assert "owner" not in module.datastore_client.entities[("user", "keren@gmail.com")]


def test_assign_refuses_unknown_machines_and_users():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "email:dana@example.com", "dana@example.com")
    admin = signed_in_client(module)
    assert admin.post("/admin/assign", data={"email": "dana@example.com", "owner": "nobody"}).status_code == 400
    assert admin.post("/admin/assign", data={"email": "ghost@example.com", "owner": "raz"}).status_code == 404
    # a login is not a user to assign to
    assert admin.post("/admin/assign", data={"email": "email:dana@example.com", "owner": "raz"}).status_code == 404


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


# ─── creating and deleting machines ──────────────────────────────────────────

def set_quota(fake_compute, free_cpus):
    """Makes every region report free_cpus CPUs of quota left."""
    fake_compute.regions.return_value.get.return_value.execute.return_value = {
        "quotas": [
            {"metric": "CPUS", "limit": 24.0, "usage": 24.0 - free_cpus},
            {"metric": "N2_CPUS", "limit": 100.0, "usage": 0.0},
            {"metric": "DISKS_TOTAL_GB", "limit": 4096.0, "usage": 0.0},
        ],
    }


def secrets_of(fake_compute):
    return fake_compute.projects.return_value.secrets.return_value


def test_student_without_a_machine_is_offered_one():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@gmail.com": "dana"})
    body = signed_in_client(module, "dana@gmail.com").get("/").get_data(as_text=True)
    assert 'action="/create"' in body
    assert "No machine is assigned to you" not in body


def test_admin_sees_how_many_machines_are_up():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren", "raz@gmail.com": "raz", "dana@gmail.com": "dana"})
    body = signed_in_client(module).get("/").get_data(as_text=True)
    assert "<strong>1</strong> up" in body
    assert "<strong>2</strong> stopped" in body
    assert "<strong>3</strong> machines in all" in body
    assert "<strong>2</strong> of 3 roster students have one" in body


def test_admin_without_machines_is_not_told_to_contact_the_instructor():
    module, fake_compute = load_app(ADMIN)
    fake_compute.instances.return_value.aggregatedList.return_value.execute.return_value = {"items": {}}
    body = signed_in_client(module).get("/").get_data(as_text=True)
    assert "Contact your instructor" not in body
    assert "No machines right now" in body
    assert "<strong>0</strong> up" in body


def test_student_with_a_machine_or_without_assignment_is_not_offered_one():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    assert 'action="/create"' not in signed_in_client(module, "keren@gmail.com").get("/").get_data(as_text=True)
    assert 'action="/create"' not in signed_in_client(module, "nobody@gmail.com").get("/").get_data(as_text=True)


def test_student_creates_their_machine_with_its_own_key():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@gmail.com": "dana"})
    set_quota(fake_compute, free_cpus=8)
    instances = fake_compute.instances.return_value
    instances.insert.return_value.execute.return_value = {"name": "operation-1"}
    response = signed_in_client(module, "dana@gmail.com").post("/create", follow_redirects=True)
    assert "Creating machine machine-dana-1" in response.get_data(as_text=True)
    body = instances.insert.call_args.kwargs["body"]
    assert instances.insert.call_args.kwargs["zone"] == "us-central1-a"
    assert body["name"] == "machine-dana-1"
    assert body["labels"] == {"owner": "dana"}
    assert body["scheduling"]["maxRunDuration"] == {"seconds": "14400"}
    public_key = body["metadata"]["items"][0]["value"]
    assert public_key.startswith("ubuntu:ssh-ed25519 ")
    assert public_key.endswith(" dana@machine-dana-1")
    secrets = secrets_of(fake_compute)
    assert secrets.create.call_args.kwargs["secretId"] == "machine-key-machine-dana-1"
    stored = secrets.addVersion.call_args.kwargs["body"]["payload"]["data"]
    assert base64.b64decode(stored).startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")


def test_create_goes_to_the_next_zone_when_the_first_is_full():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@gmail.com": "dana"})
    full = {"quotas": [{"metric": "CPUS", "limit": 8.0, "usage": 8.0}]}
    room = {"quotas": [{"metric": "CPUS", "limit": 8.0, "usage": 2.0}]}
    fake_compute.regions.return_value.get.return_value.execute.side_effect = [full, room]
    instances = fake_compute.instances.return_value
    instances.insert.return_value.execute.return_value = {"name": "operation-1"}
    signed_in_client(module, "dana@gmail.com").post("/create")
    assert instances.insert.call_args.kwargs["zone"] == "us-east1-c"


def test_create_says_so_when_there_is_no_room():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@gmail.com": "dana"})
    set_quota(fake_compute, free_cpus=1)
    response = signed_in_client(module, "dana@gmail.com").post("/create", follow_redirects=True)
    assert "no room for another running machine" in response.get_data(as_text=True)
    fake_compute.instances.return_value.insert.assert_not_called()


def test_one_machine_per_student_by_default():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    set_quota(fake_compute, free_cpus=8)
    response = signed_in_client(module, "keren@gmail.com").post("/create", follow_redirects=True)
    assert "as many as you may have (1)" in response.get_data(as_text=True)
    fake_compute.instances.return_value.insert.assert_not_called()


def test_a_student_with_a_higher_limit_creates_another_machine():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    set_quota(fake_compute, free_cpus=8)
    student = signed_in_client(module, "keren@gmail.com")
    admin = signed_in_client(module)
    assert admin.post("/admin/assign", data={"email": "keren@gmail.com", "owner": "", "limit": "3"}).status_code == 302
    body = student.get("/").get_data(as_text=True)
    assert "Your machines" in body
    assert "You have 1 of the 3 machines you may have." in body
    assert "Create another machine" in body
    instances = fake_compute.instances.return_value
    instances.insert.return_value.execute.return_value = {"name": "operation-1"}
    student.post("/create")
    # machine-keren (an older name) is not one of the numbered ones, so number 1 is free
    assert instances.insert.call_args.kwargs["body"]["name"] == "machine-keren-1"
    assert instances.insert.call_args.kwargs["body"]["labels"] == {"owner": "keren"}


def test_machine_numbers_fill_the_lowest_gap():
    module, _fake_compute = load_app()
    names = {"machine-keren-1", "machine-keren-3", "machine-raz-2"}
    assert module.provision.next_number("keren", names) == 2
    assert module.provision.next_number("raz", names) == 1


def test_a_student_at_the_limit_is_not_offered_another():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    add_user(module, "google:keren@gmail.com", "keren@gmail.com")
    module.datastore_client.get(FakeKey("user", "keren@gmail.com"))["machine_limit"] = 0
    body = signed_in_client(module, "keren@gmail.com", subject="google:keren@gmail.com").get("/").get_data(as_text=True)
    assert 'action="/create"' not in body
    assert "<dd>up to 0 at once</dd>" in body
    assert signed_in_client(module, "keren@gmail.com", subject="google:keren@gmail.com").post("/create").status_code == 302
    fake_compute.instances.return_value.insert.assert_not_called()


def test_admin_sets_and_clears_the_machine_limit():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "google:keren", "keren@gmail.com")
    admin = signed_in_client(module)
    user = module.datastore_client.entities[("user", "keren@gmail.com")]
    admin.post("/admin/assign", data={"email": "keren@gmail.com", "owner": "", "limit": "4"})
    assert user["machine_limit"] == 4
    assert 'value="4" aria-label="Machine limit for keren@gmail.com"' in admin.get("/").get_data(as_text=True)
    # back to the default: stored as no limit at all
    admin.post("/admin/assign", data={"email": "keren@gmail.com", "owner": "", "limit": "1"})
    assert "machine_limit" not in user
    for bad in ("-1", "11", "two"):
        response = admin.post("/admin/assign", data={"email": "keren@gmail.com", "owner": "", "limit": bad})
        assert response.status_code == 400


def test_admin_may_assign_a_roster_owner_that_has_no_machine_yet():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@gmail.com": "dana"})
    add_user(module, "google:keren", "keren@gmail.com")
    admin = signed_in_client(module)
    assert admin.post("/admin/assign", data={"email": "keren@gmail.com", "owner": "dana"}).status_code == 302
    assert module.datastore_client.entities[("user", "keren@gmail.com")]["owner"] == "dana"


def test_a_racing_second_create_does_not_replace_the_key():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@gmail.com": "dana"})
    set_quota(fake_compute, free_cpus=8)
    insert = fake_compute.instances.return_value.insert.return_value.execute
    insert.side_effect = HttpError(unittest.mock.Mock(status=409, reason="Conflict"), b"")
    response = signed_in_client(module, "dana@gmail.com").post("/create", follow_redirects=True)
    assert "already being created" in response.get_data(as_text=True)
    secrets_of(fake_compute).addVersion.assert_not_called()


def test_a_machine_whose_key_cannot_be_stored_is_removed():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"dana@gmail.com": "dana"})
    set_quota(fake_compute, free_cpus=8)
    instances = fake_compute.instances.return_value
    instances.insert.return_value.execute.return_value = {"name": "operation-1"}
    secrets_of(fake_compute).addVersion.return_value.execute.side_effect = HttpError(
        unittest.mock.Mock(status=403, reason="Forbidden"), b"")
    response = signed_in_client(module, "dana@gmail.com").post("/create")
    assert response.status_code == 502
    instances.delete.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-dana-1")


def test_unassigned_account_cannot_create():
    module, fake_compute = load_app(ADMIN)
    response = signed_in_client(module, "nobody@gmail.com").post("/create")
    assert response.status_code == 403
    fake_compute.instances.return_value.insert.assert_not_called()


def test_student_deletes_their_machine_and_its_key():
    module, fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    instances = set_instance_status(fake_compute, "RUNNING", owner="keren")
    client = signed_in_client(module, "keren@gmail.com")
    response = client.post("/delete", data={"name": "machine-keren", "zone": "us-central1-a"}, follow_redirects=True)
    assert "Deleting machine machine-keren" in response.get_data(as_text=True)
    instances.delete.assert_called_once_with(project="test-project", zone="us-central1-a", instance="machine-keren")
    secrets_of(fake_compute).delete.assert_called_once_with(
        name="projects/test-project/secrets/machine-key-machine-keren")


def test_delete_asks_first():
    module, _fake_compute = load_app(ADMIN)
    set_mapping(module, {"keren@gmail.com": "keren"})
    body = signed_in_client(module, "keren@gmail.com").get("/").get_data(as_text=True)
    assert 'data-delete="machine-keren"' in body
    assert 'id="delete-dialog"' in body


def test_owner_labels_become_valid_machine_names():
    module, _fake_compute = load_app()
    assert module.provision.instance_name("dana_k", 2) == "machine-dana-k-2"
    assert module.provision.instance_name("x" * 60, 1) is None


# ─── email sign-in helpers ───────────────────────────────────────────────────

def request_link(client, email="dana@proton.me"):
    """ asks for a link by mail, giving the address only """
    return client.post("/auth/email/register", json={"email": email})


def mailed_token(module, email):
    """ the token of the last link mailed to email """
    link = [link for to, link in module.app.config["MAILER"].sent if to == email][-1]
    assert link.startswith("http://localhost/verify?token=")
    return link.split("token=")[1]


def register(module, client, email="dana@proton.me", password="longenough"):
    """ asks for a link, follows it, chooses the password: signed in """
    request_link(client, email)
    token = mailed_token(module, email.strip().lower())
    response = client.post("/auth/email/password", json={"token": token, "password": password})
    assert response.status_code == 200
    return response


def sign_in(client, email="dana@proton.me", password="longenough"):
    return client.post("/auth/email/login", json={"email": email, "password": password})


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
    assert f'src="/static/{icon}.svg" alt="{name}"' in body
    assert f"<dt>Signed in with</dt>\n\t\t\t\t\t\t\t<dd>{name}</dd>" in body
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
        # sign-in is a plain redirect: nothing from another origin runs in our pages
        assert csp.startswith("default-src 'self'; ")
        assert "accounts.google.com" not in csp
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


def test_the_settings_button_opens_a_dialog_to_set_the_name():
    module, _fake_compute = load_app(ADMIN)
    add_user(module, "github:7", "keren@gmail.com")
    module.datastore_client.entities[("user", "keren@gmail.com")]["name"] = "Keren"
    body = signed_in_client(module, "keren@gmail.com").get("/").get_data(as_text=True)
    assert 'id="settings-button"' in body
    dialog = body[body.index('<dialog id="settings-dialog"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    assert 'action="/account/name"' in dialog
    assert 'id="settings-name" name="name" type="text" value="Keren"' in dialog
    # the name is set there only, not in the account panel
    assert body.count('action="/account/name"') == 1


def test_the_top_bar_shows_the_providers_photo():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module, "keren@gmail.com", subject="github:7")
    photo = "https://avatars.githubusercontent.com/u/7?v=4"
    module.datastore_client.entities[("login", "github:7")]["picture"] = photo
    assert '<img class="avatar" src="/account/picture" alt="">' in client.get("/").get_data(as_text=True)
    response = client.get("/account/picture")
    assert response.status_code == 302
    assert response.headers["Location"] == photo
    assert "avatars.githubusercontent.com" in client.get("/").headers["Content-Security-Policy"]
    # a Google photo works too
    module.datastore_client.entities[("login", "github:7")]["picture"] = "https://lh3.googleusercontent.com/a/x=s96-c"
    assert client.get("/account/picture").status_code == 302


def test_no_photo_no_image():
    module, _fake_compute = load_app(ADMIN)
    client = signed_in_client(module, "keren@gmail.com", subject="email:keren@gmail.com")
    assert 'class="avatar"' not in client.get("/").get_data(as_text=True)
    assert client.get("/account/picture").status_code == 404
    # nor a photo from anywhere but the providers' photo hosts
    for url in ("http://avatars.githubusercontent.com/u/7", "https://evil.example/x.png", "https://googleusercontent.com.evil.example/x"):
        module.datastore_client.entities[("login", "email:keren@gmail.com")]["picture"] = url
        assert client.get("/account/picture").status_code == 404
    assert module.app.test_client().get("/account/picture").status_code == 302  # signed out: to sign-in
