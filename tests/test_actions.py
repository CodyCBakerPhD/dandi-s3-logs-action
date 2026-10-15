import fcntl
import os
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

_REPOSITORY_ROOT = pathlib.Path(__file__).parent.parent
_README_PATH = _REPOSITORY_ROOT / "README.md"
_VERSION_PATH = _REPOSITORY_ROOT / "VERSION"
_ACTION_PATHS = sorted(_REPOSITORY_ROOT.glob("*/action.yml"))
_INSTALL_PACKAGES_PATH = _REPOSITORY_ROOT / "install-packages" / "action.yml"
_PREPARE_STEP_NAME = "Prepare the environment and install the packages"
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
    [("run-command", {"command"}), ("install-packages", set())],
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


def _prepare(tmp_path: pathlib.Path, env_path: pathlib.Path) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the step that prepares the environment with its default inputs and a fake `uv`."""
    action = _load(_INSTALL_PACKAGES_PATH)
    (step,) = (step for step in action["runs"]["steps"] if step["name"] == _PREPARE_STEP_NAME)
    uv = tmp_path / "uv"
    uv.write_text(_FAKE_UV, encoding="utf-8")
    uv.chmod(0o755)
    log = tmp_path / "uv.log"

    inputs = {name: str(specification.get("default", "")) for name, specification in action["inputs"].items()}
    inputs |= {"env-path": str(env_path), "uv-path": str(uv), "flock-file": str(tmp_path / "flocks" / "lock")}
    environment = {**os.environ, "UV_LOG": str(log)}
    for variable, expression in step["env"].items():
        environment[variable] = inputs[expression.removeprefix("${{ inputs.").removesuffix(" }}")]

    result = subprocess.run(  # noqa: S603
        [shutil.which("bash"), "-c", step["run"]], env=environment, capture_output=True, text=True, check=False,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


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
def test_install_packages_leaves_a_directory_that_is_not_an_environment_alone(tmp_path: pathlib.Path) -> None:
    """A mistyped `env-path`, such as the base directory, must never be deleted to make room."""
    env_path = tmp_path / "base"
    env_path.mkdir()
    (env_path / "extraction_cache").mkdir()

    result, calls = _prepare(tmp_path, env_path)

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
