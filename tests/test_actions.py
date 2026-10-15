import pathlib
import re

import pytest
import yaml

_REPOSITORY_ROOT = pathlib.Path(__file__).parent.parent
_README_PATH = _REPOSITORY_ROOT / "README.md"
_VERSION_PATH = _REPOSITORY_ROOT / "VERSION"
_ACTION_PATHS = sorted(_REPOSITORY_ROOT.glob("*/action.yml"))
_INSTALL_PACKAGES_PATH = _REPOSITORY_ROOT / "install-packages" / "action.yml"
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
def test_install_packages_installs_only_under_the_lock() -> None:
    """Swapping packages under a running pipeline breaks it midway, so every install takes the shared lock."""
    install_lines = [
        line.strip()
        for step in _load(_INSTALL_PACKAGES_PATH)["runs"]["steps"]
        for line in step.get("run", "").splitlines()
        if "pip install" in line
    ]

    assert install_lines != []
    for line in install_lines:
        assert line.startswith('flock -n "$FLOCK_FILE" '), line


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
