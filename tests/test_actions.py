import fcntl
import os
import pathlib
import re
import shutil
import subprocess
import tarfile

import pytest
import yaml

_REPOSITORY_ROOT = pathlib.Path(__file__).parent.parent
_README_PATH = _REPOSITORY_ROOT / "README.md"
_VERSION_PATH = _REPOSITORY_ROOT / "VERSION"
_ACTION_PATHS = sorted(_REPOSITORY_ROOT.glob("*/action.yml"))
_INSTALL_PACKAGES_PATH = _REPOSITORY_ROOT / "install-packages" / "action.yml"
_PREPARE_STEP_NAME = "Prepare the environment and install the packages"
_UPLOAD_SUMMARIES_PATH = _REPOSITORY_ROOT / "upload-summaries" / "action.yml"
_ANY_SELF_REFERENCE_PATTERN = re.compile(r"CodyCBakerPhD/dandi-s3-logs-action(?:/[a-z-]+)?@([\w.-]+)")
_WORKFLOW_ENV_PATTERN = re.compile(r"\$\{\{\s*env\.")

# The tag this tree is meant to be published under. Every reference to this repository must name it,
# and the release workflow takes the tag from it.
_MAJOR_TAG = _VERSION_PATH.read_text(encoding="utf-8").strip()


def _load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.ai_generated
def test_actions_are_discovered() -> None:
    action_names = [path.parent.name for path in _ACTION_PATHS]

    assert action_names == ["install-packages", "run-command", "upload-summaries"]


@pytest.mark.ai_generated
@pytest.mark.parametrize("action_path", _ACTION_PATHS, ids=lambda path: path.parent.name)
def test_action_is_composite_with_a_default_for_every_optional_input(action_path: pathlib.Path) -> None:
    action = _load(action_path)

    assert action["runs"]["using"] == "composite"
    for name, specification in action["inputs"].items():
        assert specification["required"] is True or "default" in specification, name


@pytest.mark.ai_generated
@pytest.mark.parametrize("action_path", _ACTION_PATHS, ids=lambda path: path.parent.name)
def test_action_keeps_expressions_out_of_run_bodies(action_path: pathlib.Path) -> None:
    """
    Inputs reach `run:` bodies through `env:`.

    Interpolating an input into a `run:` body lets its value rewrite the script. Passed through `env:`, it stays data.
    """
    for step in _load(action_path)["runs"]["steps"]:
        assert "${{" not in step.get("run", ""), step["name"]


@pytest.mark.ai_generated
@pytest.mark.parametrize("action_path", _ACTION_PATHS, ids=lambda path: path.parent.name)
def test_action_does_not_read_the_calling_workflow_env(action_path: pathlib.Path) -> None:
    """
    Everything an action needs arrives as an input.

    An action that reads the calling workflow's `env` fails when a workflow forgets to set it, and nothing
    at the call site says the value was needed. The EMBER workflow ran for months without `ENV_PATH`.
    """
    assert _WORKFLOW_ENV_PATTERN.search(action_path.read_text(encoding="utf-8")) is None


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("action_name", "unchecked"),
    [("run-command", {"command"}), ("install-packages", set()), ("upload-summaries", set())],
)
def test_action_checks_its_required_inputs_before_anything_runs(action_name: str, unchecked: set[str]) -> None:
    """A composite action does not enforce `required`, so an empty path would only fail later and less clearly."""
    action = _load(_REPOSITORY_ROOT / action_name / "action.yml")
    first_step = action["runs"]["steps"][0]
    environment_names = {value: name for name, value in first_step["env"].items()}

    for name, specification in action["inputs"].items():
        if specification.get("required") is not True or name in unchecked:
            continue
        variable = environment_names[f"${{{{ inputs.{name} }}}}"]
        assert f'[ -n "${variable}" ]' in first_step["run"], name


@pytest.mark.ai_generated
def test_install_packages_changes_the_environment_in_one_step() -> None:
    """That step holds the lock throughout, so nothing may change the environment outside it."""
    changing = [
        step["name"]
        for step in _load(_INSTALL_PACKAGES_PATH)["runs"]["steps"]
        if re.search(r"pip install|\bvenv\b|rm -rf", step.get("run", ""))
    ]

    assert changing == [_PREPARE_STEP_NAME]


# Stands in for `uv`. It records each call, and `venv` builds just enough of an environment for the
# step to read its Python version back.
_FAKE_UV = """#!/usr/bin/env bash
echo "$*" >> "$UV_LOG"
if [ "$1" = venv ]; then
  mkdir -p "$4/bin"
  touch "$4/pyvenv.cfg"
  printf '#!/bin/sh\\necho %s\\n' "$3" > "$4/bin/python"
  chmod +x "$4/bin/python"
fi
"""

_requires_flock = pytest.mark.skipif(shutil.which("flock") is None, reason="The step takes its lock with `flock`.")


def _make_environment(path: pathlib.Path, python_version: str) -> None:
    (path / "bin").mkdir(parents=True)
    (path / "pyvenv.cfg").touch()
    python = path / "bin" / "python"
    python.write_text(f"#!/bin/sh\necho {python_version}\n", encoding="utf-8")
    python.chmod(0o755)


def _prepare(
    tmp_path: pathlib.Path, env_path: pathlib.Path, *, with_uv: bool = True, path_prefix: str = "", **overrides: str,
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the step that prepares the environment with its default inputs and a fake `uv`."""
    action = _load(_INSTALL_PACKAGES_PATH)
    (step,) = (step for step in action["runs"]["steps"] if step["name"] == _PREPARE_STEP_NAME)
    uv = tmp_path / "uv"
    if with_uv:
        uv.write_text(_FAKE_UV, encoding="utf-8")
        uv.chmod(0o755)
    log = tmp_path / "uv.log"

    inputs = {name: str(specification.get("default", "")) for name, specification in action["inputs"].items()}
    inputs |= {"env-path": str(env_path), "uv-path": str(uv), "flock-file": str(tmp_path / "flocks" / "lock")}
    inputs |= overrides
    environment = {**os.environ, "UV_LOG": str(log)}
    if path_prefix:
        environment["PATH"] = f"{path_prefix}{os.pathsep}{environment['PATH']}"
    for variable, expression in step["env"].items():
        environment[variable] = inputs[expression.removeprefix("${{ inputs.").removesuffix(" }}")]

    result = subprocess.run(  # noqa: S603
        [shutil.which("bash"), "-c", step["run"]], env=environment, capture_output=True, text=True, check=False,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


@pytest.mark.ai_generated
@_requires_flock
def test_install_packages_downloads_uv_when_it_is_missing(tmp_path: pathlib.Path) -> None:
    """A fresh runner has no `uv`, so the step fetches the release archive and installs `uv` at `uv-path`."""
    archive = tmp_path / "uv.tar.gz"
    staged = tmp_path / "staged" / "uv-some-target"
    staged.mkdir(parents=True)
    (staged / "uv").write_text(_FAKE_UV, encoding="utf-8")
    (staged / "uv").chmod(0o755)
    (staged / "uvx").write_text("#!/bin/sh\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staged, arcname=staged.name)
    # Stands in for `curl`, recording the URL asked for and returning the archive.
    stand_ins = tmp_path / "bin"
    stand_ins.mkdir()
    curl = stand_ins / "curl"
    curl.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/curl.log"\ncat "{archive}"\n', encoding="utf-8")
    curl.chmod(0o755)
    uv_path = tmp_path / "base" / "uv"

    result, calls = _prepare(
        tmp_path, tmp_path / "base" / "env", with_uv=False, path_prefix=str(stand_ins), **{"uv-path": str(uv_path)},
    )

    assert result.returncode == 0, result.stderr
    assert os.access(uv_path, os.X_OK)
    assert not (tmp_path / "base" / "uvx").exists()
    assert "https://github.com/astral-sh/uv/releases/latest/download/uv-" in (tmp_path / "curl.log").read_text()
    assert [call.split()[0] for call in calls] == ["venv", "pip"]


@pytest.mark.ai_generated
@_requires_flock
def test_install_packages_creates_a_missing_environment(tmp_path: pathlib.Path) -> None:
    env_path = tmp_path / "base" / "env"

    result, calls = _prepare(tmp_path, env_path)

    assert result.returncode == 0, result.stderr
    assert calls[0] == f"venv --python 3.14 {env_path}"
    assert calls[1].startswith(f"pip install --python {env_path}/bin/python --upgrade ")


@pytest.mark.ai_generated
@_requires_flock
def test_install_packages_reuses_an_environment_on_the_right_python(tmp_path: pathlib.Path) -> None:
    env_path = tmp_path / "env"
    _make_environment(env_path, "3.14")
    (env_path / "kept").touch()

    result, calls = _prepare(tmp_path, env_path)

    assert result.returncode == 0, result.stderr
    assert (env_path / "kept").exists()
    assert [call.split()[0] for call in calls] == ["pip"]


@pytest.mark.ai_generated
@_requires_flock
def test_install_packages_recreates_an_environment_on_another_python(tmp_path: pathlib.Path) -> None:
    env_path = tmp_path / "env"
    _make_environment(env_path, "3.13")
    (env_path / "stale").touch()

    result, calls = _prepare(tmp_path, env_path)

    assert result.returncode == 0, result.stderr
    assert not (env_path / "stale").exists()
    assert [call.split()[0] for call in calls] == ["venv", "pip"]


@pytest.mark.ai_generated
@_requires_flock
def test_install_packages_recreates_an_environment_on_request(tmp_path: pathlib.Path) -> None:
    env_path = tmp_path / "env"
    _make_environment(env_path, "3.14")
    (env_path / "drifted").touch()

    result, calls = _prepare(tmp_path, env_path, recreate="true")

    assert result.returncode == 0, result.stderr
    assert not (env_path / "drifted").exists()
    assert [call.split()[0] for call in calls] == ["venv", "pip"]


@pytest.mark.ai_generated
@_requires_flock
@pytest.mark.parametrize("recreate", ["false", "true"])
def test_install_packages_leaves_a_directory_that_is_not_an_environment_alone(
    tmp_path: pathlib.Path, recreate: str,
) -> None:
    """A mistyped `env-path`, such as the base directory, must never be deleted, even when asked to recreate."""
    env_path = tmp_path / "base"
    env_path.mkdir()
    (env_path / "extraction_cache").mkdir()

    result, calls = _prepare(tmp_path, env_path, recreate=recreate)

    assert result.returncode != 0
    assert (env_path / "extraction_cache").exists()
    assert calls == []


@pytest.mark.ai_generated
@_requires_flock
def test_install_packages_changes_nothing_while_the_lock_is_held(tmp_path: pathlib.Path) -> None:
    env_path = tmp_path / "env"
    _make_environment(env_path, "3.13")
    lock_path = tmp_path / "flocks" / "lock"
    lock_path.parent.mkdir()

    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result, calls = _prepare(tmp_path, env_path)

    assert result.returncode != 0
    assert (env_path / "pyvenv.cfg").exists()
    assert calls == []


def _run_upload(
    tmp_path: pathlib.Path, extraction_cache: pathlib.Path, repository_path: pathlib.Path, repository_url: str = "",
) -> subprocess.CompletedProcess:
    """Run upload-summaries' steps in order, as a composite action would, with no global git identity."""
    action = _load(_UPLOAD_SUMMARIES_PATH)
    inputs = {
        "extraction-cache-path": str(extraction_cache),
        "repository-path": str(repository_path),
        "repository-url": repository_url,
    }
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    environment = {**os.environ, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
    for step in action["runs"]["steps"]:
        step_environment = dict(environment)
        for variable, expression in step["env"].items():
            step_environment[variable] = inputs[expression.removeprefix("${{ inputs.").removesuffix(" }}")]
        working_directory = step.get("working-directory", str(tmp_path))
        working_directory = working_directory.replace("${{ inputs.extraction-cache-path }}", str(extraction_cache))
        result = subprocess.run(  # noqa: S603
            [shutil.which("bash"), "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            env=step_environment,
            cwd=working_directory,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            break
    return result


def _git(*arguments: str | pathlib.Path) -> str:
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(  # noqa: S603
        [shutil.which("git"), *map(str, arguments)], env=environment, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def summaries_remote(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a bare repository with one commit, standing in for the published summaries repository."""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _git("init", "--quiet", "--bare", "--initial-branch=main", remote)
    _git("init", "--quiet", "--initial-branch=main", seed)
    (seed / "README.md").write_text("summaries\n", encoding="utf-8")
    _git("-C", seed, "add", "README.md")
    _git("-C", seed, "commit", "--quiet", "-m", "start")
    _git("-C", seed, "push", "--quiet", remote, "main")
    return remote


@pytest.fixture
def extraction_cache(tmp_path: pathlib.Path) -> pathlib.Path:
    cache = tmp_path / "extraction_cache"
    (cache / "summaries" / "000001").mkdir(parents=True)
    (cache / "summaries" / "000001" / "by_day.tsv").write_text("day\tbytes\n", encoding="utf-8")
    (cache / "summaries" / "totals.json").write_text("{}\n", encoding="utf-8")
    (cache / "summaries" / "archive_totals.json").write_text("{}\n", encoding="utf-8")
    (cache / "ips").mkdir()
    (cache / "ips" / "region_codes_to_coordinates.yaml").write_text("{}\n", encoding="utf-8")
    return cache


_requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="The action runs `git`.")


@pytest.mark.ai_generated
@_requires_git
def test_upload_summaries_clones_a_missing_repository_and_pushes(
    tmp_path: pathlib.Path, summaries_remote: pathlib.Path, extraction_cache: pathlib.Path,
) -> None:
    """On a fresh runner the clone is made from `repository-url`, and the commit needs no global git identity."""
    result = _run_upload(tmp_path, extraction_cache, tmp_path / "clone", repository_url=str(summaries_remote))

    assert result.returncode == 0, result.stderr
    published = _git("--git-dir", summaries_remote, "ls-tree", "-r", "--name-only", "main").split()
    assert "content/summaries/000001/by_day.tsv" in published
    assert "content/region_codes_to_coordinates.yaml" in published
    author = _git("--git-dir", summaries_remote, "log", "-1", "--format=%ae", "main").strip()
    assert author == "41898282+github-actions[bot]@users.noreply.github.com"


@pytest.mark.ai_generated
@_requires_git
def test_upload_summaries_pushes_nothing_when_nothing_changed(
    tmp_path: pathlib.Path, summaries_remote: pathlib.Path, extraction_cache: pathlib.Path,
) -> None:
    clone = tmp_path / "clone"
    _run_upload(tmp_path, extraction_cache, clone, repository_url=str(summaries_remote))
    commits_before = _git("--git-dir", summaries_remote, "rev-list", "--count", "main")

    result = _run_upload(tmp_path, extraction_cache, clone, repository_url=str(summaries_remote))

    assert result.returncode == 0, result.stderr
    assert _git("--git-dir", summaries_remote, "rev-list", "--count", "main") == commits_before


@pytest.mark.ai_generated
@_requires_git
def test_upload_summaries_stops_when_there_is_no_clone_and_no_url(
    tmp_path: pathlib.Path, extraction_cache: pathlib.Path,
) -> None:
    result = _run_upload(tmp_path, extraction_cache, tmp_path / "clone")

    assert result.returncode != 0
    assert "set repository-url" in result.stdout
    assert not (tmp_path / "clone").exists()


@pytest.mark.ai_generated
@_requires_git
def test_upload_summaries_leaves_a_directory_that_is_not_a_clone_alone(
    tmp_path: pathlib.Path, summaries_remote: pathlib.Path, extraction_cache: pathlib.Path,
) -> None:
    """A mistyped `repository-path` must never be cloned into or committed from."""
    not_a_clone = tmp_path / "base"
    not_a_clone.mkdir()

    result = _run_upload(tmp_path, extraction_cache, not_a_clone, repository_url=str(summaries_remote))

    assert result.returncode != 0
    assert "is not the top of a git clone" in result.stdout
    assert list(not_a_clone.iterdir()) == []


@pytest.mark.ai_generated
@_requires_git
def test_upload_summaries_refuses_a_directory_inside_another_repository(
    tmp_path: pathlib.Path, summaries_remote: pathlib.Path, extraction_cache: pathlib.Path,
) -> None:
    """Being inside some repository is not enough, or the summaries would be committed to that one."""
    outer = tmp_path / "outer"
    _git("init", "--quiet", outer)
    inside = outer / "summaries"
    inside.mkdir()

    result = _run_upload(tmp_path, extraction_cache, inside, repository_url=str(summaries_remote))

    assert result.returncode != 0
    assert "is not the top of a git clone" in result.stdout
    assert _git("-C", outer, "status", "--porcelain") == ""


@pytest.mark.ai_generated
@_requires_git
def test_upload_summaries_fails_when_the_commit_fails(
    tmp_path: pathlib.Path, summaries_remote: pathlib.Path, extraction_cache: pathlib.Path,
) -> None:
    """A failed commit used to be swallowed, so the run reported success having published nothing."""
    clone = tmp_path / "clone"
    _git("clone", "--quiet", summaries_remote, clone)
    hook = clone / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    result = _run_upload(tmp_path, extraction_cache, clone)

    assert result.returncode != 0
    assert _git("--git-dir", summaries_remote, "rev-list", "--count", "main").strip() == "1"


@pytest.mark.ai_generated
def test_every_self_reference_names_the_version_being_published() -> None:
    """
    Nothing in the repository may point at a tag other than the one `VERSION` names.

    A reference left on the previous tag resolves and runs, so neither a workflow run nor a test of the
    files against each other catches it.
    """
    sources = [_README_PATH, *_ACTION_PATHS]

    references = {
        (str(path.relative_to(_REPOSITORY_ROOT)), tag)
        for path in sources
        for tag in _ANY_SELF_REFERENCE_PATTERN.findall(path.read_text(encoding="utf-8"))
    }
    stale = {reference for reference in references if reference[1] != _MAJOR_TAG}

    assert references != set()
    assert stale == set()


@pytest.mark.ai_generated
@pytest.mark.parametrize("action_path", _ACTION_PATHS, ids=lambda path: path.parent.name)
def test_readme_documents_every_input(action_path: pathlib.Path) -> None:
    readme = _README_PATH.read_text(encoding="utf-8")

    assert f"CodyCBakerPhD/dandi-s3-logs-action/{action_path.parent.name}@{_MAJOR_TAG}" in readme
    for name in _load(action_path)["inputs"]:
        assert f"| `{name}` |" in readme, name


@pytest.mark.ai_generated
def test_version_file_names_a_major_tag() -> None:
    """`VERSION` is compared against the release tag, so it has to be shaped like one."""
    assert re.fullmatch(r"v\d+", _MAJOR_TAG) is not None, _MAJOR_TAG
