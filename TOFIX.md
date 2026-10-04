# TOFIX

Findings from a code scan on 2026-10-04.

## High

- `src/main.py:70` - `is_admin(None)` returns `True`, and `get_signed_in_user()` (`src/main.py:73`) returns `None` whenever the `X-Goog-Authenticated-User-Email` header is absent. Any request that reaches the service without passing through IAP (IAP not yet enabled, or a caller holding `run.invoker` who calls the `run.app` URL directly with an identity token) is therefore treated as admin and can start/suspend every machine. The header itself is also trusted unsigned (`src/main.py:75`). Verify the signed `X-Goog-IAP-JWT-Assertion` header (google.auth `id_token.verify_token` against the IAP audience) to get the email, and make the "no identity means admin" fallback opt-in (e.g. only when an explicit `LOCAL_DEV=1` env var is set).

## Medium

- `src/main.py:205` - `app.run(debug=True, host="0.0.0.0", ...)` exposes the Werkzeug interactive debugger (arbitrary code execution) on every network interface when the file is run directly. Bind to `127.0.0.1`, or take `debug` from an env var defaulting to off.
- `src/main.py:89` - `hmac.compare_digest` on two `str` values raises `TypeError` when either contains non-ASCII characters, so `?token=é` produces a 500 instead of a 403. Compare bytes: `hmac.compare_digest(supplied.encode(), ACCESS_TOKEN.encode())`.
- `.gcloudignore:28` - the `!/build_info.json` exception says it "is what the app/version endpoint serves", but `src/main.py` has no version endpoint and nothing produces `build_info.json`. The ignore list also names paths that do not exist (`/Makefile`, `/package.json`, `/package-lock.json`, `/templates`, `/requirements.thawed.txt`, `/db`, `/misc`, `/gcloud`). Trim it to what the repo actually contains.

## Low

- `support/gjslint.cfg:1` and `support/jsl.conf:1` - configs for Closure Linter and JavaScript Lint, tools that nothing in the build runs (the only JS is a 9-line inline function in `src/templates/machines.html:31`). Delete the `support/` directory.
- `config/project.lua:4` - `DESCRIPTION_SHORT = "The machines project in GAE"` is stale: the app now runs on Cloud Run (`Dockerfile:1`, `.gcp.conf:12`, `doc/iap.md` notes the old App Engine resource is gone), and this text is rendered into `README.md`. Update it.
- `pyproject.toml:26` - `mypy_path = "src:python:scripts"` names a `python/` directory that does not exist; and `rsconstruct.toml:28`/`rsconstruct.toml:32` list `config` (only `.lua` files) in the ruff and mypy `src_dirs`. Drop both stale entries (fleet-wide pattern: 89 repos list a `.lua`-only `config` in ruff/mypy `src_dirs` and 82 carry this `mypy_path`, so fix it at the fleet's source too).
- `doc/TODO.txt:6` - "lint all python scripts" is already done (ruff and mypy run over `src`, `scripts` and `tests`); move it to `doc/DONE.txt`.
