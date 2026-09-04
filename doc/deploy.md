# Deploying the app to Cloud Run

The app runs as a Cloud Run service called `machines` in `us-central1`
(it used to run on App Engine; see the migration notes below).

## Regular deploy

```bash
gcloud_run_deploy.sh
```

The script (from the utils-bash repo) wraps `gcloud run deploy --source .`:
Cloud Build builds the `Dockerfile` and the new revision replaces the old
one with no downtime. Everything the old `app.yaml` used to declare
(environment variables, scaling, the service account) lives in `.gcp.conf`
as `gcp_run_args`, so a redeploy always converges the service to what that
file says. IAP enablement is a service property and survives redeploys
untouched.

## One-time project setup

1. Enable the needed APIs: they are listed in `.services`, and
   `gcloud_service_set.sh` (utils-bash) enables what is missing;
   `gcloud_service_set.sh --check` only reports the drift.
1. Create the app's service account if it does not exist yet:
   `python scripts/service_account.py create`.
1. Deploy: `gcloud_run_deploy.sh`.
1. Put IAP in front of the service — see `doc/iap.md`; the service is
   deployed `--no-allow-unauthenticated`, so until IAP is on (and its
   service agent holds `roles/run.invoker`) nobody but the project owners
   can reach it.
1. Grant access: `python scripts/iap.py sync`.

## Local development

```bash
python src/main.py
```

Runs the Flask dev server on port 8080 with your local credentials; without
IAP headers the app behaves as admin.

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
