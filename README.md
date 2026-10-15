# dandi-s3-logs-action

GitHub action for running [`s3_log_extraction`](https://github.com/dandi/s3-log-extraction) and its [DANDI plugin](https://github.com/dandi/dandi-s3-log-extraction) on a self-hosted runner.

| Action | What it does |
| --- | --- |
| `install-packages` | Creates the runner's Python environment if needed, then installs or upgrades `s3_log_extraction`, the DANDI plugin and `duct` in it, holding the same lock. |
| `run-command` | Runs an `s3logextraction` or `dandis3logextraction` command under `duct`, holding a machine-wide lock. |
| `upload-summaries` | Copies the access summaries out of an extraction cache into a cloned repository, then commits and pushes them. |

## Requirements

The actions run on a self-hosted runner. Only `install-packages` installs anything. The runner needs:

- `flock`.
- `curl` and `tar`, if `install-packages` has to download `uv`.
- A Python environment with `s3_log_extraction`, `dandi_s3_log_extraction` and `duct` installed. `install-packages` creates it and installs them.

The commands read their secrets from the job's environment rather than from inputs. Set them in the workflow's `env`:

| Variable | Used by |
| --- | --- |
| `S3_LOG_EXTRACTION_PASSWORD` | Commands that read or write the encrypted cache. |
| `MAXMIND_ACCOUNT_ID` / `MAXMIND_LICENSE_KEY` | `update summaries`, which geolocates requesters against the GeoLite2 database. |

## Installing the packages

```yaml
- uses: CodyCBakerPhD/dandi-s3-logs-action/install-packages@v3
  with:
    env-path: /data/s3-logs/env
    uv-path: /data/s3-logs/uv
    flock-file: /data/s3-logs/flocks/s3logextraction.lock
```

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `env-path` | yes | | Path to the Python environment. It is created if missing. |
| `uv-path` | yes | | Path to the `uv` executable that creates the environment and installs the packages. Downloaded there if missing. |
| `flock-file` | yes | | Lock file shared by every run on the machine. |
| `python-version` | no | `3.14` | Python the environment runs, as `major.minor`. |
| `recreate` | no | `false` | Set to `true` to delete the environment and create it again even when it already runs `python-version`. |
| `s3-log-extraction` | no | `s3_log_extraction[geolocation,remote]>=1.11.10` | Requirement to install `s3_log_extraction` from. |
| `dandi-s3-log-extraction` | no | `dandi_s3_log_extraction[geolocation,remote] @ git+https://github.com/dandi/dandi-s3-log-extraction.git@main` | Requirement to install the DANDI plugin from. The plugin has no PyPI release yet. |
| `con-duct` | no | `con-duct` | Requirement to install `duct` from. |

If nothing is at `uv-path`, the latest `uv` release for the runner's platform (Linux x86_64 or aarch64) is downloaded from GitHub and placed there. Only `uv` itself is installed, and no install script is run.

The environment at `env-path` is reused from one install to the next:

- If nothing is there, it is created with `uv venv` on `python-version`. `uv` downloads that Python if the runner does not have it.
- If an environment is there on `python-version`, the packages are upgraded in place.
- If an environment is there on any other Python, or `recreate` is `true`, it is deleted and created again. Only the packages above are installed in the new one, so anything else installed by hand has to be installed again.
- If something other than a virtual environment is there, the install stops and leaves it alone, whatever `recreate` says. A mistyped path is never deleted to make room.

The packages are installed together with `uv pip install --upgrade`, and the versions they end up at are reported. The defaults bring everything the other actions and the extraction need:

- The `geolocation` extras are needed by `update summaries`.
- The `remote` extras bring `s5cmd` for remote extraction.
- `duct` is what `run-command` runs every command under.

To install other versions without waiting on a release of this action, set the requirement inputs.

The whole install holds the same lock as `run-command`, taken without waiting. While a pipeline holds it, the install fails at once rather than changing the environment a command is using. Run it from a manually dispatched workflow rather than inside the scheduled ones, so that a scheduled run never changes what it runs.

## Running a command

```yaml
- uses: CodyCBakerPhD/dandi-s3-logs-action/run-command@v3
  with:
    command: update summaries
    flags: --mode archive --cache /data/s3-logs/extraction_cache
    entrypoint: dandis3logextraction
    env-path: /data/s3-logs/env
    uv-path: /data/s3-logs/uv
    flock-file: /data/s3-logs/flocks/s3logextraction.lock
```

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `command` | yes | | Command to run, with spaces between the command groups. For example, `update summaries`. |
| `flags` | no | `""` | Space-separated, double-dash flags to pass to the command. |
| `entrypoint` | no | `s3logextraction` | CLI entrypoint. Use `dandis3logextraction` for the DANDI plugin. |
| `env-path` | yes | | Path to the Python environment that holds the extraction packages and `duct`. |
| `uv-path` | yes | | Path to the `uv` executable that runs the command. |
| `flock-file` | yes | | Lock file shared by every run on the machine. |

The `dandis3logextraction` entrypoint does not re-expose the `update ip` group. Run those commands through the default `s3logextraction` entrypoint.

The lock is taken without waiting. A run that starts while another holds the lock fails at once, so schedules sharing a lock file should not overlap.

## Uploading summaries

```yaml
- uses: CodyCBakerPhD/dandi-s3-logs-action/upload-summaries@v3
  with:
    extraction-cache-path: /data/s3-logs/extraction_cache
    repository-path: /data/s3-logs/access-summaries
```

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `extraction-cache-path` | yes | | Path to the extraction cache that holds the `summaries` directory and the `ips` region coordinates. |
| `repository-path` | yes | | Path to a clone of the repository the summaries are published to. Cloned from `repository-url` if nothing is there. |
| `repository-url` | no | `""` | URL to clone the summaries repository from when `repository-path` does not exist, for example `git@github.com:dandi/access-summaries.git`. |

It copies every `.tsv` under the cache's `summaries` into `content/summaries`. It also copies `totals.json`, `archive_totals.json` and `region_codes_to_coordinates.yaml` into `content`. Generate the totals and the region coordinates first.

If nothing is at `repository-path`, it is cloned from `repository-url` first. A path that exists has to be the top of a git clone, or the action stops and leaves it alone. The clone and every push use the runner user's own git credentials, such as an SSH key, so `repository-url` should be one that user can push to.

The clone is pulled before the summaries are copied in, so the pull never merges over uncommitted files. If the runner's user has no git identity, the clone gets a local one as `github-actions[bot]`. The commit is skipped only when nothing changed, so any other commit failure stops the step instead of pushing nothing.
