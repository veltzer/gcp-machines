# Deploying the app to Cloud Run

The app runs as a Cloud Run service called `machines` in `us-central1`
(it used to run on App Engine; see the migration notes below).

## Regular deploy

```bash
gcloud_run_deploy.sh
```

The script (from the utils-bash repo) wraps `gcloud run deploy --source .`:
Cloud Build builds the `Dockerfile` and the new revision replaces the old
one with no downtime. Everything the service needs (environment variables,
secrets, scaling, the service account) lives in `.gcp.conf` as
`gcp_run_args`, so a redeploy always converges the service to what that
file says.

## One-time project setup

1. Enable the needed APIs: they are listed in `.services`, and
   `gcloud_service_set.sh` (utils-bash) enables what is missing;
   `gcloud_service_set.sh --check` only reports the drift.
1. Create the app's service account if it does not exist yet:
   `python scripts/service_account.py create` (or `grant` to add a role
   to an existing one).
1. Create the secrets: `gcloud_project_setup.sh` (utils-bash) reads
   `gcp_secrets` in `.gcp.conf` and creates the ones that are missing,
   among them the session cookie key. Safe to re-run.
1. Create the sign-in clients (next section) and paste their IDs into
   `.gcp.conf`. Without them the app still works with email and password.
1. Deploy: `gcloud_run_deploy.sh`.

## Sign-in

Anyone can create an account. The sign-in page (`src/html/index.html`,
`src/static/app.js`, the scene in `src/static/scene.svg`) follows
gcp-myworld's and offers three ways in; `src/auth.py` does the work and
the accounts live in Datastore (kind `user`, see `src/users.py`):

- Google: the Google Identity Services button in redirect mode. Google
  POSTs a signed ID token to `/auth/login`; the server checks the CSRF
  cookie and verifies the token against `GOOGLE_CLIENT_ID`.
- GitHub: the OAuth 2.0 authorization code flow through
  `/auth/oauth/github` and its `/callback`. Offered only when
  `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` are set.
- Email and password: `POST /auth/email/register` and
  `POST /auth/email/login` (JSON). Always available.

Accounts are keyed by how they signed in (`google:<sub>`,
`github:<id>`, `email:<address>`), never by email, so one address used
two ways is two accounts. The session cookie holds only that key.

Which machines an account sees:

- An admin (a Google or GitHub account whose email is in
  `gcp_admin_emails`) sees and controls every machine.
- An account an admin assigned an owner to (the `owner` field of its
  `user` entity) controls the machines with that `owner` label.
- Otherwise a Google or GitHub account is looked up by its email in the
  course roster (kind `student`, pushed by `python scripts/iap.py sync`
  from `data.gi/students.txt`).
- A password account is never matched by email: the password form does
  not verify the address, so matching it would let anyone register a
  student's address and take over that student's machine. It gets a
  machine only by an admin's assignment.

Creating the clients, which cannot be scripted for a project outside an
organization:

1. Google: in the console, `APIs & Services -> Credentials -> Create
   credentials -> OAuth client ID`, type `Web application`. Authorized
   JavaScript origins: `https://machines.veltzer.org` and
   `http://localhost:8080`. Authorized redirect URIs:
   `https://machines.veltzer.org/auth/login` and
   `http://localhost:8080/auth/login`. Paste the client ID into
   `gcp_oauth_client_id`. The consent screen is the External one already
   configured for IAP.
1. GitHub: `Settings -> Developer settings -> OAuth Apps -> New OAuth
   App`, homepage `https://machines.veltzer.org`, callback
   `https://machines.veltzer.org/auth/oauth/github/callback`. Paste the
   client ID into `gcp_github_client_id`, store the client secret with
   `pass insert keys/github.com.machines-oauth`, add that entry to
   `gcp_secrets` and `GITHUB_CLIENT_SECRET` to `--set-secrets` in
   `.gcp.conf`, and re-run `gcloud_project_setup.sh`.

## Local development

```bash
MACHINES_DEV_LOGIN=1 python -m src.main
```

Run it from the repository root. It serves on `http://localhost:8080` with
your local credentials (so it shows the real machines and uses the real
Datastore), and the sign-in page also offers a development login that
signs in as any email, treated as verified. The development login is never
available on Cloud Run.

## Custom domain

Students use `https://machines.veltzer.org`. It is a Cloud Run domain
mapping (preview feature) on the `machines` service:

```bash
gcloud beta run domain-mappings create --service machines \
    --domain machines.veltzer.org --region us-central1
```

backed by a Cloudflare DNS record `machines CNAME ghs.googlehosted.com`
that must stay **DNS only** (not proxied), or Google cannot renew the
managed certificate. The app redirects the `run.app` URLs to this domain
(`CANONICAL_HOST`), so it is the only origin the sign-in providers need
to know.

## Migration notes (App Engine -> Cloud Run)

The app used to run on App Engine and moved to Cloud Run in 2026. For the
record, since students and the IAP setup were affected:

- The URL changed from `https://veltzer-machines-id.uc.r.appspot.com` to
  the service's `https://machines-<project-number>.us-central1.run.app`
  address (`gcloud_browse.sh` from the utils-bash repo prints and opens
  it); students were handed the new link.
- IAP grants did not carry over between the App Engine and Cloud Run IAP
  resources, so `python scripts/iap.py sync` was re-run after enabling IAP
  on the service. The Datastore student mapping was untouched.
- The App Engine application is disabled (serving status `USER_DISABLED`)
  and the `appengine.googleapis.com` API is turned off, so the old copy
  cannot serve. Nothing App Engine specific is left in the repo.
