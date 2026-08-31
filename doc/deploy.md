# Deploying the app to Cloud Run

The app runs as a Cloud Run service called `machines` in `us-central1`
(it used to run on App Engine; see the migration notes below).

## Regular deploy

```bash
./scripts/deploy.sh
```

The script wraps `gcloud run deploy --source .`: Cloud Build builds the
`Dockerfile` and the new revision replaces the old one with no downtime.
Everything the old `app.yaml` used to declare (environment variables,
scaling, the service account) lives in the script as flags, so a redeploy
always converges the service to what the script says. IAP enablement is a
service property and survives redeploys untouched.

## One-time project setup

1. Enable the needed APIs (`python scripts/apis_check.py` shows what is
   missing): `run.googleapis.com`, `cloudbuild.googleapis.com`,
   `artifactregistry.googleapis.com`, plus the pre-existing
   `compute.googleapis.com`, `datastore.googleapis.com` and
   `iap.googleapis.com`.
1. Create the app's service account if it does not exist yet:
   `python scripts/service_account.py create` (the account keeps its
   historical `gae-machines-engine-sa` name).
1. Deploy: `./scripts/deploy.sh`.
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

- The URL changed from `https://<project>.appspot.com` to the service's
  `https://machines-<project-number>.us-central1.run.app` address
  (`gcp_run_browse.py` from the utils-python repo prints and opens it) —
  hand students the new link.
- IAP grants do not carry over between the App Engine and Cloud Run IAP
  resources; after enabling IAP on the service, re-run
  `python scripts/iap.py sync`. The Datastore student mapping is untouched
  by the migration.
- Once the Cloud Run service is verified working, disable the old App
  Engine app (console: `App Engine -> Settings -> Disable application`) so
  the stale copy stops serving; the `appengine.googleapis.com` API can then
  be disabled as well.
