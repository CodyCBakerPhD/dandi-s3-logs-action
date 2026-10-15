# Agent instructions

## What this repository is

GitHub Actions that run `s3_log_extraction` and its DANDI plugin on a self-hosted runner. The plugin
lives in [`dandi/dandi-s3-log-extraction`](https://github.com/dandi/dandi-s3-log-extraction).

## Versioning

- These actions are versioned by their own interface, never by the package release they run. They run
  whatever the runner's environment has installed, so a package release changes nothing here.
- `install-packages` is the one exception. Its defaults name package and Python versions, so changing one
  changes what every caller that leaves that input unset installs. A caller that needs another version before
  a release sets the input instead.
- `VERSION` holds the tag this tree is meant to be published under, and is the only place that tag is
  decided. The tests read it rather than a literal, so a reference left on the previous tag fails before
  the commit lands.
- Every reference to this repository, in the README and in the actions, names that tag. Bump `VERSION`
  in the same commit that rewrites them.
- Every release gets a new tag, even one that only adds an action. A published tag is never moved, so a
  caller pinned to it keeps running what it reviewed.
- Release by publishing the draft that `Prepare release draft` keeps on every merge to `main`. Its tag
  name comes from `VERSION` and its target from that commit, so the tag is never typed. It prepares
  nothing when the tag already exists, since moving a published tag is a deliberate act rather than a
  release.
- The `action-versions-agree` pre-commit hook runs the tests that catch the references and `VERSION`
  drifting apart, so it fails at commit time rather than leaving it to CI.

## Interface

- Every value an action uses arrives as an input. No action reads `${{ env.* }}` from the calling
  workflow. That dependency is invisible at the call site, and a workflow that forgets it fails with
  an error that names neither the action nor the missing value.
- Secrets the commands read from their own environment, such as `S3_LOG_EXTRACTION_PASSWORD`, stay in
  the job's `env`. They reach the command without passing through the action.
- Inputs reach `run:` bodies through `env:`, never interpolated into the script.
- A composite action does not enforce `required`. Check a required input before using it.

## Code style

- Avoid excessive em-dashes, colons, and semicolons in written text such as documentation. Prefer
  breaking into separate, shorter sentences instead.
- Keep inline comments sparse. Only explain non-obvious "why", not "what" the code does.

## Tests

- Run `pytest` before pushing, and `pre-commit run --all-files`.
- Follow assertion style: actual on left, expected on right.
- Always mark AI-generated tests with the `ai_generated` pytest marker.
