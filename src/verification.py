"""
Email verification for password accounts, with Google Identity Platform as
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
     <project>.firebaseapp.com; that page marks the address verified and
     offers a Continue button back to the app.

The app then asks whether the address is verified, and once it is, deletes
the Identity Platform user, which has done its job.

Every call uses the app's own Google credentials (the service account needs
roles/identitytoolkit.admin), so no API key is involved. Google does not let
this project change the mail's text or the page the link opens (the API
answers EMAIL_TEMPLATE_UPDATE_NOT_ALLOWED), so both are Google's defaults.
"""

import secrets

import google.auth.credentials
from google.auth.transport.requests import AuthorizedSession

API = "https://identitytoolkit.googleapis.com/v1"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
HTTP_TIMEOUT = 10


class VerificationError(Exception):
    pass


class IdentityPlatform:
    """ the verification mail, sent and checked through Identity Platform """

    def __init__(self, credentials, project_id):
        credentials = google.auth.credentials.with_scopes_if_required(credentials, SCOPES)
        self.session = AuthorizedSession(credentials)
        self.admin = f"{API}/projects/{project_id}"

    def _post(self, url, body):
        response = self.session.post(url, json=body, timeout=HTTP_TIMEOUT)
        data = response.json()
        if not response.ok:
            message = data.get("error", {}).get("message", response.reason)
            raise VerificationError(f"Identity Platform: {message}")
        return data

    def _lookup(self, email):
        """ the Identity Platform user for an address, or None """
        users = self._post(f"{self.admin}/accounts:lookup", {"email": [email]}).get("users", [])
        return users[0] if users else None

    def _delete(self, user):
        self._post(f"{self.admin}/accounts:delete", {"localId": user["localId"]})

    def send(self, email, continue_url):
        """ mail a verification link to the address, voiding any earlier one """
        earlier = self._lookup(email)
        if earlier is not None:
            self._delete(earlier)
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

    def verified(self, email):
        """ whether the link mailed to the address was followed """
        user = self._lookup(email)
        if user is None or not user.get("emailVerified"):
            return False
        self._delete(user)
        return True
