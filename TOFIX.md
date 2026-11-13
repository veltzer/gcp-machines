# TOFIX

Findings from a code scan on 2026-10-04.

## Medium

- `.gcloudignore:28` - the `!/build_info.json` exception says it "is what the app/version endpoint serves", but `src/main.py` has no version endpoint and nothing produces `build_info.json`. The ignore list also names paths that do not exist (`/Makefile`, `/package.json`, `/package-lock.json`, `/templates`, `/requirements.thawed.txt`, `/db`, `/misc`, `/gcloud`). Trim it to what the repo actually contains.

## Low

- `support/gjslint.cfg:1` and `support/jsl.conf:1` - configs for Closure Linter and JavaScript Lint, tools that nothing in the build runs (the JavaScript in `src/static/app.js` is linted by oxlint). Delete the `support/` directory.
- `config/project.lua:4` - `DESCRIPTION_SHORT = "The machines project in GAE"` is stale: the app now runs on Cloud Run (`Dockerfile:1`, `.gcp.conf:12`, `doc/iap.md` notes the old App Engine resource is gone), and this text is rendered into `README.md`. Update it.
- `pyproject.toml:26` - `mypy_path = "src:python:scripts"` names a `python/` directory that does not exist; and `rsconstruct.toml:28`/`rsconstruct.toml:32` list `config` (only `.lua` files) in the ruff and mypy `src_dirs`. Drop both stale entries (fleet-wide pattern: 89 repos list a `.lua`-only `config` in ruff/mypy `src_dirs` and 82 carry this `mypy_path`, so fix it at the fleet's source too).
- `doc/TODO.txt:6` - "lint all python scripts" is already done (ruff and mypy run over `src`, `scripts` and `tests`); move it to `doc/DONE.txt`.
