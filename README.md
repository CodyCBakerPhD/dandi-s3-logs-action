# dandi-s3-logs-action

GitHub action for running [`s3_log_extraction`](https://github.com/dandi/s3-log-extraction) and its [DANDI plugin](https://github.com/dandi/dandi-s3-log-extraction) on a self-hosted runner.

| Action | What it does |
| --- | --- |
| `run-command` | Runs an `s3logextraction` or `dandis3logextraction` command under `duct`, holding a machine-wide lock. |
| `upload-summaries` | Copies the access summaries out of an extraction cache into a cloned repository, then commits and pushes them. |

## Requirements

The actions run on a self-hosted runner and install nothing. The runner needs:

- `flock`.
- A Python environment with `s3_log_extraction`, `dandi_s3_log_extraction` and `duct` installed.
- A `uv` executable.

The commands read their secrets from the job's environment rather than from inputs. Set them in the workflow's `env`:

| Variable | Used by |
| --- | --- |
| `S3_LOG_EXTRACTION_PASSWORD` | Commands that read or write the encrypted cache. |
| `MAXMIND_ACCOUNT_ID` / `MAXMIND_LICENSE_KEY` | `update summaries`, which geolocates requesters against the GeoLite2 database. |

## Running a command

```yaml
- uses: CodyCBakerPhD/dandi-s3-logs-action/run-command@v1
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
- uses: CodyCBakerPhD/dandi-s3-logs-action/upload-summaries@v1
  with:
    extraction-cache-path: /data/s3-logs/extraction_cache
    repository-path: /data/s3-logs/access-summaries
```

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `extraction-cache-path` | yes | | Path to the extraction cache that holds the `summaries` directory and the `ips` region coordinates. |
| `repository-path` | yes | | Path to a clone of the repository the summaries are published to. |

It copies every `.tsv` under the cache's `summaries` into `content/summaries`. It also copies `totals.json`, `archive_totals.json` and `region_codes_to_coordinates.yaml` into `content`. Generate the totals and the region coordinates first.

The clone is used as found. Its git configuration must already be able to push.
