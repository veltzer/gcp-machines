# Protecting the app with Identity-Aware Proxy (IAP)

IAP sits in front of App Engine and forces a Google sign-in before any
request reaches the app. Access is granted per email address. Enabling it is
a one-time manual step; day-to-day student management is done with
`scripts/iap.py`.

## One-time enablement (console)

These steps cannot be scripted for this project: the brand (consent screen)
API only works for projects that belong to an organization, and this project
is owned by a personal account ("Project must belong to an organization").
The `consent-create` command of `scripts/iap.py` exists for the day the
project moves under an organization.

1. Open the GCP console for the project.
1. Go to `APIs & Services -> OAuth consent screen` and configure it:
   set the app name and support email, choose `External`, and publish the
   app (students only sign in, no scopes are needed).
1. Go to `Security -> Identity-Aware Proxy`
   (enable the `Cloud Identity-Aware Proxy API` if prompted).
1. Toggle IAP **on** for the `App Engine app` resource.
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

## Notes

- The app shows "Signed in as ..." by reading the
  `X-Goog-Authenticated-User-Email` header that IAP adds. IAP strips this
  header from incoming traffic, so it cannot be spoofed from outside.
- The `ACCESS_TOKEN` shared-secret mechanism in `app.yaml` is a weaker
  stopgap for the period before IAP is enabled; once IAP is on, leave it
  unset.
- IAP for App Engine is free of charge.
