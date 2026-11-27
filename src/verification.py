"""
The verification mail of email sign-up, with Google Identity Platform as
the mail sender.

Identity Platform is used for nothing but its verification mail: the
accounts themselves stay in Datastore (users.py). To have Google send the
mail, an Identity Platform user must exist for the address and the request
must carry that user's ID token, so sending

  1. deletes any earlier Identity Platform user for the address (which also
     voids the link in any earlier mail),
  2. creates one with a random password nobody keeps,
  3. signs in as it to get an ID token, and
  4. asks for the VERIFY_EMAIL mail, whose link opens Google's page on
     <project>.firebaseapp.com; that page offers a Continue button back to
     the continue URL, which carries our own secret token.

What proves the address is the token: it travels only inside the mail, so
whoever brings it back reads that mailbox. Identity Platform is never asked
whether the address is verified. Once the token is back, the Identity
Platform user has done its job and is deleted (forget).

Every call uses the app's own Google credentials (the service account needs
roles/identitytoolkit.admin), so no API key is involved. Google does not let
this project change the mail's text or the page the link opens (the API
answers EMAIL_TEMPLATE_UPDATE_NOT_ALLOWED), so both are Google's defaults.

Off Cloud Run there is no mail: LogMailer writes the link to the server log.
Whatever sends is a mailer: send(email, continue_url) and forget(email).
"""

import logging
import secrets
from typing import Any

import google.auth.credentials
from google.auth.transport.requests import AuthorizedSession

API = "https://identitytoolkit.googleapis.com/v1"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
HTTP_TIMEOUT = 10

logger = logging.getLogger(__name__)


class VerificationError(Exception):
    pass


class IdentityPlatform:
    """ the verification mail, sent through Identity Platform """

    def __init__(self, credentials: Any, project_id: str | None) -> None:
        credentials = google.auth.credentials.with_scopes_if_required(credentials, SCOPES)
        self.session = AuthorizedSession(credentials)
        self.admin = f"{API}/projects/{project_id}"

    def _post(self, url: str, body: dict[str, Any]):
        response = self.session.post(url, json=body, timeout=HTTP_TIMEOUT)
        data = response.json()
        if not response.ok:
            message = data.get("error", {}).get("message", response.reason)
            raise VerificationError(f"Identity Platform: {message}")
        return data

    def _lookup(self, email: str):
        """ the Identity Platform user for an address, or None """
        users = self._post(f"{self.admin}/accounts:lookup", {"email": [email]}).get("users", [])
        return users[0] if users else None

    def forget(self, email: str) -> None:
        """ the address is proven (or its link replaced): drop its Identity Platform user, if any """
        user = self._lookup(email)
        if user is not None:
            self._post(f"{self.admin}/accounts:delete", {"localId": user["localId"]})

    def send(self, email: str, continue_url: str) -> None:
        """ mail a verification link to the address, voiding any earlier one """
        self.forget(email)
        password = secrets.token_urlsafe(32)
        self._post(f"{self.admin}/accounts", {"email": email, "password": password})
        signed_in = self._post(
            f"{API}/accounts:signInWithPassword",
            {"email": email, "password": password, "returnSecureToken": True},
        )
        self._post(
            f"{API}/accounts:sendOobCode",
            {"requestType": "VERIFY_EMAIL", "idToken": signed_in["idToken"], "continueUrl": continue_url},
        )


class LogMailer:
    """ local development: the link goes to the server log instead of a mailbox """

    def send(self, email: str, continue_url: str) -> None:
        logger.warning("verification link for %s: %s", email, continue_url)

    def forget(self, email: str) -> None:
        pass
