"""
Tests for scripts/migrate_verified_only.py: what plan() finds in a set of
logins, and what main() does with it, against a fake Datastore (nothing
here touches Google Cloud).
"""

import importlib.util
import os
import sys
import unittest.mock

import pytest
from google.cloud import datastore

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "migrate_verified_only.py")


def load_script():
    spec = importlib.util.spec_from_file_location("migrate_verified_only", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeKey:
    def __init__(self, kind, name):
        self.kind = kind
        self.name = name


class FakeClient:
    """ the corner of datastore.Client the script uses, over a dict of logins """

    def __init__(self, logins):
        self.logins = {login.key.name: login for login in logins}

    def query(self, kind):
        assert kind == "login"
        return unittest.mock.Mock(fetch=lambda: list(self.logins.values()))

    def put_multi(self, entities):
        for entity in entities:
            self.logins[entity.key.name] = entity

    def delete_multi(self, keys):
        for key in keys:
            del self.logins[key.name]


def login(subject, **fields):
    entity = datastore.Entity(key=FakeKey("login", subject))
    entity.update({"email": subject.split(":", 1)[1], "name": "n", "picture": ""} | fields)
    return entity


OLD_FIELDS = {"account_id": "a1", "verification_sent": "then", "verification_token": "t", "verified": True}


def run(logins, *argv):
    """ runs main() against the logins with the given arguments: (client, exit status, printed output) """
    module = load_script()
    client = FakeClient(logins)
    out: list[str] = []

    def printed(*values: object) -> None:
        out.append(" ".join(map(str, values)))

    with unittest.mock.patch.object(sys, "argv", ["migrate_verified_only.py", *argv]), \
            unittest.mock.patch("google.auth.default", return_value=(unittest.mock.Mock(spec=[]), "test-project")), \
            unittest.mock.patch("google.cloud.datastore.Client", return_value=client), \
            unittest.mock.patch("builtins.print", side_effect=printed):
        try:
            module.main()
            status = 0
        except SystemExit as error:
            status = error.code
            if isinstance(status, str):
                out.append(status)
                status = 1
    return client, status, "\n".join(out)


def mixed():
    return [
        login("google:1", user="a@x.com", verified=True),
        login("google:2", verified=False),
        login("email:b@x.com", verified=False, account_id="a1", password_hash="h"),
        login("email:c@x.com", user="c@x.com", **OLD_FIELDS),
        login("github:3", user="d@x.com"),
        login("email:e@x.com", user="e@x.com", verified=False),
    ]


def test_plan_finds_the_unproven_logins():
    module = load_script()
    delete, strip, assigned = module.plan(mixed())
    assert sorted(entity.key.name for entity in delete) == ["email:b@x.com", "email:e@x.com", "google:2"]
    assert sorted(entity.key.name for entity in strip) == ["email:c@x.com", "google:1"]
    assert assigned == []


def test_a_dry_run_changes_nothing():
    client, status, out = run(mixed())
    assert status == 0
    assert "delete login google:2 (no user)" in out
    assert "delete login email:e@x.com (email not verified)" in out
    assert "strip login email:c@x.com: account_id, verification_sent, verification_token, verified" in out
    assert "Dry run" in out
    assert len(client.logins) == 6
    assert "verified" in client.logins["email:c@x.com"]


def test_apply_deletes_the_unproven_and_strips_the_rest():
    client, status, _out = run(mixed(), "--apply")
    assert status == 0
    assert sorted(client.logins) == ["email:c@x.com", "github:3", "google:1"]
    assert dict(client.logins["email:c@x.com"]) == {"email": "c@x.com", "name": "n", "picture": "", "user": "c@x.com"}
    assert "verified" not in client.logins["google:1"]


def test_it_is_safe_to_run_again():
    client, _status, _out = run(mixed(), "--apply")
    again, status, out = run(list(client.logins.values()), "--apply")
    assert status == 0
    assert "logins to delete: 0, to strip: 0" in out
    assert sorted(again.logins) == ["email:c@x.com", "github:3", "google:1"]


@pytest.mark.parametrize("assignment", [{"owner": "raz"}, {"machine_limit": 3}, {"owner": "raz", "machine_limit": 2}])
def test_an_unproven_login_with_an_assignment_is_kept_and_nothing_is_changed(assignment):
    logins = [*mixed(), login("email:dana@x.com", verified=False, **assignment)]
    client, status, out = run(logins, "--apply")
    assert status == 1
    assert "KEEP login email:dana@x.com: unproven, but assigned" in out
    # nothing at all was changed, not even what could have been
    assert len(client.logins) == 7
    assert "verified" in client.logins["email:c@x.com"]


def test_it_refuses_a_service_account():
    module = load_script()
    credentials = unittest.mock.Mock(service_account_email="app@test-project.iam.gserviceaccount.com")
    with pytest.raises(SystemExit, match="Refusing to run as service account"):
        module.require_default_account(credentials)
    module.require_default_account(unittest.mock.Mock(spec=[]))
