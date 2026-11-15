# TOFIX

Findings from a code review on 2026-11-15 (replaces the 2026-10-04 scan; its
still-open items are carried over under "Repo hygiene").

## Medium

### Security

- **No brute-force or abuse limits** on `/auth/email/login` and
  `/auth/email/register` (`src/auth.py:162-188`). Passwords can be guessed
  without limit, and register can be used to make Google mail
  verification links to arbitrary addresses (one per address per minute,
  but unlimited addresses), which risks the project's Identity Platform
  quota and reputation. Add per-IP and per-address rate limiting (a
  Datastore counter or Cloud Armor in front of the service).
- **Account existence leaks.** Login's "same message for both failures"
  (`src/auth.py:180`) is undone by timing (no `check_password_hash` runs
  when the account does not exist, so that answer comes back much faster)
  and by register's 409 "there is already an account". Run a dummy
  `check_password_hash` on the missing-account path; accept that register
  reveals verified accounts or make it answer the same way and mail
  instead.
- **No password reset.** A verified password account that forgets its
  password is stuck; the only way out is an admin deleting the entity by
  hand. Add a reset flow (Identity Platform's `PASSWORD_RESET` OOB code can
  send the mail, the same trick as verification).
- **Sessions cannot be revoked and never expire.** The cookie holds only the
  subject, signed with `SECRET_KEY`; it is valid until the key rotates.
  Set `PERMANENT_SESSION_LIFETIME` and store a per-account session
  generation (bumped on password change/reset or by an admin) that
  `current_user` checks.
- **`scripts/service_account.py:152` writes a service-account key to disk**
  (`~/.credentials/<project>.json`, with the default umask so possibly
  world-readable). This contradicts `.gcp.conf:6-9` ("we never use an SA key
  locally") and the pass(1)-only rule for secrets. Remove the key download
  from `create`, or at least write the file `0600` and print a warning.
- **`scripts/firewall.py --all` opens every TCP/UDP port on every machine to
  `0.0.0.0/0`.** Consider restricting `sourceRanges` (a `--source` option),
  and listing/removing rules (there is no way to undo what it creates).
- **Machine ssh keys cannot be rotated.** A leaked key (`/key` is
  downloadable any number of times) can only be invalidated by recreating
  the machine. Add `machines.py rotate-key <owner>`: new key pair, new
  secret version, rewrite the instance's `ssh-keys` metadata.

### Correctness

- **`scripts/machines.py` `stop`, `continue`, `delete` and
  `firewall.py add_tag_to_all_instances` do not paginate**
  `aggregatedList` (`machines.py:381,407,438`, `firewall.py:63`); only
  `get_all_instances` does. With enough instances some are silently
  skipped (worst for `delete`, which also skips their key secrets). Use
  `get_all_instances` everywhere. `list_regions` (`machines.py:509`) has the
  same issue.
- **`machines.py continue` ignores suspended machines** (only `TERMINATED`
  is started, `machines.py:412`) and `stop` skips them too, so after a class
  where students suspended, neither command does what its name says. Resume
  `SUSPENDED` in `continue`; decide whether `stop` should stop suspended
  machines (it frees the memory snapshot storage).
- **`machines.py list` crashes on a machine without an access config**
  (`machines.py:136`, `instance["networkInterfaces"][0]["accessConfigs"][0]`).
  Use the existing `instance_ip()` helper.
- **`email_to_owner` swallows Datastore errors silently**
  (`src/main.py:106`). On a cold start where the first fetch fails, every
  student sees "No machine is assigned to you" for 60 seconds with nothing
  in the logs. Log the exception, and retry sooner than the full TTL when
  the cache was never filled.
- **`scripts/iap.py sync` pushes the roster only after the IAP grant step
  succeeds** (`iap.py:245`). IAP is off (`doc/iap.md`), so the roster push,
  which the app depends on, lives in a script about a retired feature. Move
  `push_mapping` (and `read_students`) to `machines.py roster-push` or a
  `scripts/roster.py`, and retire or archive `iap.py`.

### Operability

- **No audit log of who did what.** `/process`, `/key` and `/admin/assign`
  do not log the acting account. Add one structured log line per action
  (subject, email, machine, action) so Cloud Logging can answer "who left
  instance-7 running" or "who downloaded this key".
- **Every page load lists every instance in every zone**
  (`get_machines`, `src/main.py:226`; `/admin/assign` does it again just to
  validate the owner). With many students hitting refresh at class start
  this is slow and burns Compute API quota. Restrict the listing to
  `ALLOWED_ZONES` (`instances().list` per zone), filter by
  `labels.owner=<owner>` for students, and/or cache for a few seconds.
- **The Docker image runs as root and buffers stdout.** Add a non-root
  `USER` and `ENV PYTHONUNBUFFERED=1` so log lines reach Cloud Logging
  immediately.

## Low

### Code quality

- **The CLI scripts do not list their subcommands when invoked bare**
  (`machines.py:770`, `iap.py:351`, `service_account.py:237`: argparse
  `required=True` prints only "the following arguments are required:
  command"). Per the global rule, a bare `python scripts/machines.py` should
  print the commands with a one-line description each and nothing else.
- **Duplicated helpers across scripts:** `read_students` (machines.py,
  iap.py), `require_default_account` (machines.py, iap.py, firewall.py,
  service_account.py) and `get_compute_client`. Move them to one shared
  module (the machine spec already lives in `src/provision.py`).
- **`wait_for_operation` and `wait_for_operation_quiet` are the same
  loop** (`machines.py:180,208`); make the message optional and drop one.
  `list_machines_full` is a pure alias of `get_all_instances`.
- **`get_compute_client` calls `google.auth.default()` a second time**
  (`machines.py:111`) right after `main()` already did; pass the
  credentials in.
- **`get_machines` emits an unused `number` field** (`src/main.py:250`);
  nothing in the template reads it.
- **The admin Assign dropdown is filled by JavaScript**
  (`app.js fillAssignments`, from a comma-joined `data-owners`). Jinja
  already has `owners`; render the `<option>`s server-side, drop the JS and
  the "labels cannot hold commas" assumption.
- **`/process` and the error handlers answer with a bare HTML fragment**
  (`src/main.py:386-422`) and "Go back, refresh". Flash the message and
  redirect to `/` instead, like `/admin/assign` does.
- **`METHODS` lookup is done twice in `students_table` and once in
  `sign_in_method`** (`src/main.py:158,177`); one `method_key(user)` helper
  would do.
- **Email validation is minimal** (`src/auth.py:157`): `a@b` passes. Fine
  given verification, but `email-validator` or at least a dot in the domain
  would catch typos before a mail goes out.
- **`pyclassifiers` is a runtime dependency** (`pyproject.toml:12`) but
  nothing in `src/` imports it, so it ships in the container image. Move it
  to the dev group (or wherever the fleet tooling actually needs it).

### Machines

- **Boot image is Ubuntu 22.04** (`src/provision.py` `BOOT_IMAGE`); its standard support
  ends April 2027, mid-way through a course. Move to `ubuntu-2404-lts`
  (check the nested-virtualization setup still works).
- **10 GB boot disk** (`src/provision.py` `DISK_GB`) is tight for machines meant to run
  KVM guests (one cloud image plus a qcow2 overlay already fills much of
  it). Consider 20-30 GB.
### Docs and config

- **`doc/TODO.txt`**: "move to the non discovery api" (google-cloud-compute
  and friends) would also drop the per-thread `api_http()` workaround in
  `src/main.py`; "lint all python scripts" is done (ruff and
  pyrefly run over `src`, `scripts`, `tests`), move it to `doc/DONE.txt`.

### Repo hygiene (carried over from 2026-10-04)

- `.gcloudignore` names paths that do not exist (`/Makefile`,
  `/package.json`, `/package-lock.json`, `/templates`,
  `/requirements.thawed.txt`, `/db`, `/misc`, `/gcloud`). Trim it to what
  the repo contains.
- `support/gjslint.cfg` and `support/jsl.conf` configure Closure Linter and
  JavaScript Lint, which nothing runs (oxlint lints `src/static/app.js`).
  Delete `support/` (and its `.gcloudignore` line).
- `config/project.lua:4` `DESCRIPTION_SHORT = "The machines project in GAE"`
  is stale (the app runs on Cloud Run) and is rendered into `README.md`.
- `rsconstruct.toml:20,24` list `config` (only `.lua` files) in the ruff and
  pyrefly `src_dirs`. Drop it (fleet-wide pattern; fix at the fleet's source
  too, keeping `rsmultigit check same` clean).
