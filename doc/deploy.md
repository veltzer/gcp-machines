# Deploying the app to Cloud Run

The app runs as a Cloud Run service called `machines` in `us-central1`
(it used to run on App Engine; see the migration notes below).

## Regular deploy

```bash
gcloud_deploy.sh
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
1. Deploy: `gcloud_deploy.sh`.

## Sign-in

Anyone can create an account, but only a proven email gets one. The
sign-in page (`src/html/index.html`,
`src/static/app.js`, the scene in `src/static/scene.svg`) follows
gcp-myworld's and offers three ways in, one icon each; `src/auth.py` does
the work and users and their logins live in Datastore (see
`src/users.py`):

- Google: the OAuth 2.0 authorization code flow through
  `/auth/oauth/google`, coming back to `/auth/login` (the redirect URI
  registered with the client). The server trades the code for Google's
  signed ID token and verifies it against `GOOGLE_CLIENT_ID`. Offered
  only when `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set.
- GitHub: the same flow through `/auth/oauth/github` and its
  `/callback`. Offered only when `GITHUB_CLIENT_ID` and
  `GITHUB_CLIENT_SECRET` are set.
- Email and password: the Email icon opens the form, which talks to
  `POST /auth/email/login` (email and password) and, to get a link by mail,
  `POST /auth/email/register` (the address only), all JSON. Always
  available. The password is chosen on the page the mailed link leads to
  (next section).

Both providers are plain redirects, so nothing of theirs runs in our
pages and the Content-Security-Policy allows only this origin.

Each way of signing in is a login (kind `login`, keyed `google:<sub>`,
`github:<id>`, `email:<address>`), and each person is a user (kind
`user`, keyed by the lowercased email). A login joins the user of its
email when it is made, and a login exists only for a proven email:
Google's `email_verified`, GitHub's verified primary address, or for an
email login the mailed link followed. A provider that does not vouch for
the email sends the person back to the sign-in page with "<Provider> did
not confirm an email address for you: verify one with <Provider> and sign
in again", and nothing is stored. So Google, GitHub and a password under
one proven address are one user,
with one row on the Students table. A login joins once and stays, even
if the provider's address changes later. The session cookie holds only
the login's key.

Which machines a user sees:

- An admin (a user whose email is listed in `gcp_admin_emails`) sees and
  controls every machine.
- A user an admin assigned an owner to (the `owner` field of the `user`
  entity) controls the machine with that `owner` label, whichever way
  they sign in.
- Otherwise the user's email is looked up in the course roster (kind
  `student`, pushed by `python scripts/iap.py sync` from
  `data.gi/students.txt`).
- Every login has a user, so there is no half-signed-in login that an
  admin could assign a machine to: an assignment is always to a user (the
  Students table's Save posts the user's email), and an address that has
  not proven itself is not on the table at all.

`scripts/migrate_logins.py` moved the accounts of before (one `user`
entity per way of signing in) to this layout on 2026-11-15, and
`scripts/migrate_verified_only.py` removes the logins that were never
proven ("Existing accounts" below).

### Email verification

Google Cloud has no general mail service; the verification mail is sent by
Identity Platform, used for nothing else (`src/verification.py`). The
password is never taken at sign-up, so only whoever reads the mailbox ever
sets one for an address:

1. The form's "Get a link by mail" posts the address alone to
   `POST /auth/email/register`. The answer is the same whether or not the
   address has an account. The app keeps a pending verification (kind
   `email_verification`, keyed by the address) with the SHA-256 of a random
   token and when it was sent, creates a throwaway Identity Platform user
   for the address and has Google mail it a `VERIFY_EMAIL` link. A new link
   voids the old one, and at most one is mailed per address per minute.
1. The link opens Google's page on `veltzer-machines-id.firebaseapp.com`,
   whose Continue button leads to `/verify?token=...` on
   `machines.veltzer.org`. That page (`src/html/verify.html`) tells the
   browser to send no referrer, removes the token from the address bar,
   asks `POST /auth/email/verify` which address the token is for, and lets
   its reader choose the password (at least 8 characters).
1. `POST /auth/email/password` with the token and the password spends the
   link (it works once, for a day), creates or replaces the
   `email:<address>` login with the password's hash, joins it to the user
   of that address, signs in and deletes the throwaway Identity Platform
   user. The same flow replaces a forgotten password.

Identity Platform is never asked whether the address is verified: what
proves it is our own token coming back, which travelled only inside the
mail. Until then the address signs in to nothing:
`POST /auth/email/login` answers every failure with "wrong email or
password (a new address signs in only after you open the link we mailed
it)".

All of it runs on the app's own credentials (the service account has
`roles/identitytoolkit.admin`), no API key. Google does not let this
project change the mail's text, sender or the page its link opens (the
config API answers `EMAIL_TEMPLATE_UPDATE_NOT_ALLOWED`), so those are
Google's defaults: it comes from `noreply@veltzer-machines-id.firebaseapp.com`.

Setup, done once with the owner's credentials (`T=$(gcloud auth
print-access-token)`, header `x-goog-user-project: veltzer-machines-id`):

1. `POST https://identitytoolkit.googleapis.com/v2/projects/veltzer-machines-id/identityPlatform:initializeAuth`
1. `PATCH .../v2/projects/veltzer-machines-id/config?updateMask=signIn.email,authorizedDomains`
   with `signIn.email.enabled: true` and `machines.veltzer.org` added to
   `authorizedDomains` (the Continue link must point at an authorized
   domain).

Creating the clients, which cannot be scripted for a project outside an
organization:

1. Google: in the console, `APIs & Services -> Credentials -> Create
   credentials -> OAuth client ID`, type `Web application`. Authorized
   JavaScript origins: `https://machines.veltzer.org` and
   `http://localhost:8080`. Authorized redirect URIs:
   `https://machines.veltzer.org/auth/login` and
   `http://localhost:8080/auth/login`. Paste the client ID into
   `gcp_oauth_client_id`, store the client secret with `pass insert
   keys/google.com.machines-oauth` (first line; notes may follow), and run
   `gcloud_project_setup.sh`, which copies it to the Secret Manager secret
   `machines-google-client-secret`. The consent screen is the External one
   already configured for IAP. (The JavaScript origins date from Google's
   sign-in button, which the app no longer uses.)
1. GitHub: `Settings -> Developer settings -> OAuth Apps -> New OAuth
   App`, homepage `https://machines.veltzer.org`, callback
   `https://machines.veltzer.org/auth/oauth/github/callback`. Paste the
   client ID into `gcp_github_client_id`, store the client secret with
   `pass insert keys/github.com.machines-oauth`, and run
   `gcloud_project_setup.sh`, which copies it to the Secret Manager secret
   `machines-github-client-secret` (`gcp_secrets` in `.gcp.conf`; the
   service gets it as `GITHUB_CLIENT_SECRET`). Done on 2026-11-15: the app
   is "Machines" (`github.com/settings/applications/4022558`).

### Existing accounts

Before this flow, an email and password login was made at sign-up and
signed in before its address was proven, with no user. `python
scripts/migrate_verified_only.py` (as the project owner, never a service
account; dry run unless `--apply`; safe to run again) finds the logins that
are not proven, that is an email login with `verified` false or any login
with no user, and deletes them. It also strips the fields the old flow
kept (`account_id`, `verification_sent`, `verification_token`,
`verified`) from the logins that stay. An unproven login that an admin
assigned a machine or a machine limit is never deleted: the script lists
it (`KEEP`), changes nothing at all and exits with status 1, so an admin
can decide (a student may have machines under it) and run it again. Run it
after deploying: until then the old code still reads those fields.

## Machines and their keys

Machines are made on demand: a student whose account maps to an owner (by
the roster or an admin's assignment) gets a Create button while they have
fewer machines than their limit: 1 unless an admin set another (0 to 10,
the number next to the assignment on the Students table, stored as
`machine_limit` on the user). It makes `machine-<owner>-<n>`, with the
lowest number the owner does not use yet, in the first of
`provision.ALLOWED_ZONES` whose region has CPU quota left, labelled with
the owner. The fixed name per number is what keeps two racing clicks from
making two machines where one was allowed. From its card the student can
Start, Stop and Delete each machine (Delete removes the disk and the key
for good; a new machine comes with a new key). Admins can do the same to
every machine. An admin may assign any owner label of the roster or of an
existing machine. Whatever is running turns itself off 4 hours
after it starts (`provision.MAX_RUN_SECONDS`); the disk is kept.

Each machine gets its own ed25519 key pair: the private key goes straight
into Secret Manager as `machine-key-<instance>` (never to disk), the public
key onto the machine for the `ubuntu` user. A student downloads their
machine's key from its card (`GET /key`, allowed only to the machine's
student and admins) and connects with the `ssh` command shown there. The
app's service account needs `roles/secretmanager.admin` for this
(`python scripts/service_account.py grant`).

So that `connect` and `tunnel` reach machines the students make, put the
admin key `~/.ssh/id_machines.pub` into the project-wide ssh keys once,
which every machine accepts on top of its own:

```bash
python scripts/machines.py admin-key
```

To get a course ready:

```bash
python scripts/iap.py push-roster   # email -> owner roster to Datastore
```

`python scripts/machines.py create` still makes, in one go, a machine for
every roster owner who has none (re-runnable); `delete` removes all
machines and their key secrets.

A user with a roster email sees its machine at once, whichever way they
sign in, as every account has a proven email.

## Local development

```bash
MACHINES_DEV_LOGIN=1 python -m src.main
```

Run it from the repository root. It serves on `http://localhost:8080` with
your local credentials (so it shows the real machines and uses the real
Datastore), and the sign-in page also offers a development login that
signs in as any email, treated as proven. The development login is never
available on Cloud Run. There is no mail locally either: the link of "Get a
link by mail" is written to the server log (`verification link for ...`),
and opening it, choosing the password and signing in works as in
production.

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
