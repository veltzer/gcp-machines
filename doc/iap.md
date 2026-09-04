# Protecting the app with Identity-Aware Proxy (IAP)

IAP sits in front of the Cloud Run service and forces a Google sign-in before
any request reaches the app. Access is granted per email address. Enabling it
is a one-time manual step; day-to-day student management is done with
`scripts/iap.py`.

## One-time enablement

By default IAP uses a Google-managed OAuth client which only admits
identities from the project's organization. This project belongs to a
personal account (no organization) and students sign in with plain Gmail
addresses, so a custom OAuth client with an External consent screen is
required, and that part cannot be scripted ("Project must belong to an
organization"). The `consent-create` command of `scripts/iap.py` exists for
the day the project moves under an organization.

1. Open the GCP console for the project.
1. Go to `APIs & Services -> OAuth consent screen` and configure it:
   set the app name and support email, choose `External`, and publish the
   app (students only sign in, no scopes are needed).
1. Let IAP invoke the service — the service is deployed with
   `--no-allow-unauthenticated`, so the IAP service agent needs the invoker
   role (find the project number with `gcloud projects describe`):

   ```bash
   gcloud run services add-iam-policy-binding machines \
       --region=us-central1 \
       --member="serviceAccount:service-<project-number>@gcp-sa-iap.iam.gserviceaccount.com" \
       --role=roles/run.invoker
   ```

1. Go to `Security -> Identity-Aware Proxy`
   (enable the `Cloud Identity-Aware Proxy API` if prompted).
1. Toggle IAP **on** for the `machines` Cloud Run service and, when asked
   about the OAuth client, pick the custom-client option (auto-generating
   the credentials is fine) so out-of-organization Gmail users can sign in.
1. Grant yourself access so you are not locked out:

   ```bash
   python scripts/iap.py grant your.email@gmail.com
   ```

1. Verify with an incognito window: the app must ask for a Google login,
   and a non-granted account must be rejected.

## Managing student access

Add each student's email in `data.gi/students.txt` — one student per line in
the form `<owner-name> [email]` (`python scripts/iap.py show-input-sample`
shows the format). The same file drives machine creation
(`scripts/machines.py`), which uses only the name column. The file is
git-ignored on purpose: this repository is public and student emails are
private data.

```bash
# show whether IAP is on and who has access
python scripts/iap.py status

# grant every student email in data.gi/students.txt
python scripts/iap.py sync

# same, and also revoke people that are no longer in the file
python scripts/iap.py sync --prune

# one-off grant / revoke
python scripts/iap.py grant someone@gmail.com
python scripts/iap.py revoke someone@gmail.com
```

Students need a Google account (any Gmail address works) and must use the
same address you granted.

## Per-student filtering

The app shows each signed-in student only the machine whose `owner` label
matches their email; a student with no mapping sees an empty list. Emails
listed in the `ADMIN_EMAILS` environment variable (set in
`.gcp.conf`) see and control everything, as do requests that carry
no IAP identity (local development).

The email-to-owner mapping lives in Datastore (kind `student`, key = email).
`python scripts/iap.py sync` pushes it there from `data.gi/students.txt`
while updating the IAP grants, and the app picks up changes within about a
minute — after a roster change, `sync` is the only command needed, no
redeploy.

## Notes

- The app shows "Signed in as ..." by reading the
  `X-Goog-Authenticated-User-Email` header that IAP adds. IAP strips this
  header from incoming traffic, so it cannot be spoofed from outside.
- The `ACCESS_TOKEN` shared-secret mechanism in `.gcp.conf` is a
  weaker stopgap for the period before IAP is enabled; once IAP is on, leave
  it unset.
- IAP itself is free of charge, and enabling it directly on the Cloud Run
  service needs no load balancer.
- IAP grants live on the IAP resource of this specific service and region
  (`iap_web/cloud_run-us-central1/services/machines`); grants made on the
  old App Engine resource do not carry over.
