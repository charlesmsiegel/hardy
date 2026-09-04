from __future__ import annotations

from pathlib import Path

import pytest

from hardy import config, layout


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch):
    for variable in config.SETTINGS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("HARDY_CONFIG", raising=False)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_apply_when_no_config_file_exists(tmp_path: Path, monkeypatch):
    """Without `chdir`, `load` reads the real `Path.cwd()` -- the developer's

    own checkout -- for both the project config and its project-directory
    scan. This passes today only because this repo happens to hold no
    `session.json` anywhere under it; it would break the moment someone ran
    `hardy chat` here.
    """
    monkeypatch.chdir(tmp_path)
    settings = config.load(tmp_path / "missing.toml")
    assert settings.model == config.DEFAULT_MODEL
    assert settings.lean_command == ("lake", "env", "lean")
    assert settings.latex_command[0] == "pdflatex"
    assert settings.lean_project is None
    assert settings.project == "main"
    assert settings.root == Path.cwd()
    assert settings.lean_timeout == config.DEFAULT_LEAN_TIMEOUT
    assert settings.path is None


def test_config_file_supplies_model_paths_and_commands(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "provider/model-1"\nlean_project = "~/lean"\nlean_command = "lake env lean --quiet"\n')
    settings = config.load(path)
    assert settings.model == "provider/model-1"
    assert settings.lean_command == ("lake", "env", "lean", "--quiet")
    assert settings.lean_project == Path.home() / "lean"
    assert settings.path == path


def test_environment_beats_the_file_and_flags_beat_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write(tmp_path / "config.toml", 'model = "from-file"\n')
    monkeypatch.setenv("HARDY_MODEL", "from-environment")
    assert config.load(path).model == "from-environment"
    assert config.load(path, model="from-flag").model == "from-flag"


def test_unknown_settings_are_rejected_rather_than_ignored(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'modle = "typo"\n')
    with pytest.raises(ValueError, match="unknown settings"):
        config.load(path)


def test_a_byte_order_mark_does_not_hide_the_first_setting(tmp_path: Path):
    """Windows editors and PowerShell write UTF-8 with a leading BOM.

    Read as plain utf-8 the mark becomes part of the first key, so tomllib
    rejects the file and every Hardy command fails on a config that looks
    perfectly ordinary in an editor.
    """
    path = tmp_path / "config.toml"
    path.write_bytes(b'\xef\xbb\xbfmodel = "provider/model-1"\n')
    assert config.load(path).model == "provider/model-1"


def test_editing_a_config_with_a_byte_order_mark_keeps_it_readable(tmp_path: Path):
    """The line-based editors read the file too, so a BOM must not survive into
    a key they then write back."""
    path = tmp_path / "config.toml"
    path.write_bytes(b'\xef\xbb\xbfmodel = "old"\n')
    config.write_setting(path, "model", "new")
    assert config.load(path).model == "new"
    config.remove_setting(path, "model")
    assert config.load(path).model == config.DEFAULT_MODEL






def test_default_config_path_follows_hardy_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HARDY_CONFIG", str(tmp_path / "elsewhere.toml"))
    assert config.default_config_path() == tmp_path / "elsewhere.toml"


def test_lean_timeout_is_a_number_of_seconds(tmp_path: Path):
    assert config.load(write(tmp_path / "a.toml", "lean_timeout = 45\n")).lean_timeout == 45.0
    with pytest.raises(ValueError, match="number of seconds"):
        config.load(write(tmp_path / "b.toml", 'lean_timeout = "soon"\n'))












def test_writing_a_setting_upserts_one_line(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('# comment\nmodel = "old"\nlean_timeout = 90\n', encoding="utf-8")
    config.write_setting(path, "model", "new")
    config.write_setting(path, "runs_root", "here")
    assert path.read_text(encoding="utf-8") == '# comment\nmodel = "new"\nlean_timeout = 90\nruns_root = "here"\n'
    assert config.load(path).model == "new"


def test_writing_an_unknown_setting_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown setting"):
        config.write_setting(tmp_path / "config.toml", "nonsense", "x")










def test_a_requested_config_path_survives_the_file_not_existing_yet(tmp_path: Path):
    """`/model` saves here, and falling back to the platform default would write
    someone's choice into an unrelated file."""
    requested = tmp_path / "new" / "config.toml"
    settings = config.load(requested)
    assert settings.path is None
    assert settings.config_path == requested


def test_an_existing_config_path_is_still_reported(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "x"\n')
    assert config.load(path).config_path == path


def test_removing_a_setting_leaves_every_other_line_alone(tmp_path: Path):
    path = write(tmp_path / "config.toml", '# comment\nmodel = "x"\nruns_root = "gone"\nlean_timeout = 90\n')
    config.remove_setting(path, "runs_root")
    assert path.read_text(encoding="utf-8") == '# comment\nmodel = "x"\nlean_timeout = 90\n'


def test_removing_an_absent_setting_or_file_is_a_no_op(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "x"\n')
    config.remove_setting(path, "runs_root")
    assert path.read_text(encoding="utf-8") == 'model = "x"\n'
    config.remove_setting(tmp_path / "missing.toml", "runs_root")


def test_removing_an_unknown_setting_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown setting"):
        config.remove_setting(tmp_path / "config.toml", "nonsense")


def test_the_default_config_lives_in_the_global_hardy_directory(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert config.default_config_path() == tmp_path / ".hardy" / "config.toml"


def test_the_environment_override_still_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HARDY_CONFIG", str(tmp_path / "elsewhere.toml"))
    assert config.default_config_path() == tmp_path / "elsewhere.toml"


def test_a_legacy_config_moves_into_the_global_directory(tmp_path: Path):
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = "claude-opus-5"\nlean_timeout = 90\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"

    assert config.migrate_global(legacy, destination) is True

    assert not legacy.exists()
    moved = destination.read_text(encoding="utf-8")
    assert 'model = "claude-opus-5"' in moved
    assert "lean_timeout = 90" in moved


def test_the_move_drops_the_setting_that_no_longer_exists(tmp_path: Path):
    """`read_file` raises on an unknown key, so a verbatim copy would not start.

    Every installer-written config carries `workspace`, and that setting is
    being removed. Copying the file unchanged would leave Hardy refusing to
    load its own migrated configuration.
    """
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = "x"\nworkspace = ".hardy"\nlean_timeout = 90\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"

    config.migrate_global(legacy, destination)

    moved = destination.read_text(encoding="utf-8")
    assert "workspace" not in moved
    assert 'model = "x"' in moved
    assert "lean_timeout = 90" in moved
    # The proof that matters: the migrated file loads.
    assert config.read_file(destination)["model"] == "x"


def test_a_quoted_retired_key_is_dropped_too(tmp_path: Path):
    """TOML permits a quoted key, which the unquoted regex would not match.

    `"workspace" = ...` decodes to the same `workspace` setting `tomllib`
    gives an unquoted key. Missing it here would delete the source and
    install a destination still carrying the retired key -- and every later
    load rejects it as unknown, so Hardy would not start at all.
    """
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = "x"\n"workspace" = ".hardy"\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"

    config.migrate_global(legacy, destination)

    assert config.read_file(destination)["model"] == "x"


def test_a_multiline_retired_value_is_migrated_cleanly(tmp_path: Path):
    """Line-editing cannot handle TOML's grammar.

    Reproduced: a legacy config spelling `workspace` as a multiline value --
    `workspace = \"\"\"` with the string and the closing delimiter on their
    own following lines -- left those continuation lines behind when only the
    assignment line was dropped. Since the source is then deleted, the
    destination was a file `tomllib` could not parse, and Hardy would not
    start.
    """
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        'model = "x"\nworkspace = """\n.hardy\n"""\nlean_timeout = 90\n',
        encoding="utf-8",
    )
    destination = tmp_path / ".hardy" / "config.toml"

    assert config.migrate_global(legacy, destination) is True

    # The proof that matters: the migrated file parses at all.
    loaded = config.read_file(destination)
    assert loaded["model"] == "x"
    assert loaded["lean_timeout"] == 90
    assert "workspace" not in loaded


def test_the_move_keeps_runs_root_which_is_still_a_live_setting(tmp_path: Path):
    """`runs_root` is out of scope for this change and stays readable by

    `workflow.py`, `acceptance.py`, and `cli.py`. The move keeps what
    `SETTINGS` names, so a live setting must survive it; this pins `runs_root`
    coming through right alongside `workspace` being dropped, which is the
    pair a narrowed or widened allowlist would get wrong.
    """
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        'model = "x"\nworkspace = ".hardy"\nruns_root = "custom-runs"\nlean_timeout = 90\n',
        encoding="utf-8",
    )
    destination = tmp_path / ".hardy" / "config.toml"

    config.migrate_global(legacy, destination)

    moved = destination.read_text(encoding="utf-8")
    assert "workspace" not in moved
    assert 'runs_root = "custom-runs"' in moved
    # The proof that matters: the migrated file loads, and keeps the setting.
    assert config.read_file(destination)["runs_root"] == "custom-runs"


def test_the_move_does_not_clobber_a_config_that_already_exists(tmp_path: Path):
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = "old"\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"
    destination.parent.mkdir(parents=True)
    destination.write_text('model = "current"\n', encoding="utf-8")

    assert config.migrate_global(legacy, destination) is False
    assert 'model = "current"' in destination.read_text(encoding="utf-8")


def test_nothing_to_move_is_not_an_error(tmp_path: Path):
    assert config.migrate_global(tmp_path / "absent.toml", tmp_path / "new.toml") is False


def test_the_workspace_setting_is_gone(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('workspace = ".hardy"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown settings"):
        config.read_file(path)


def test_the_project_config_names_the_active_problem(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text('project = "sylow"\n', encoding="utf-8")
    settings = config.load(tmp_path / "absent-global.toml", root=tmp_path)
    assert settings.project == "sylow"
    assert settings.layout.problem == tmp_path / "sylow"


def test_a_custom_global_config_does_not_suppress_the_project_config(tmp_path: Path):
    """`HARDY_CONFIG` selects the global layer only.

    Letting it win over everything would mean a wrapper pointing it at its own
    settings file silently opened -- and wrote -- the wrong problem's record.
    """
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text('project = "sylow"\n', encoding="utf-8")
    custom = tmp_path / "custom-global.toml"
    custom.write_text('model = "claude-opus-5"\n', encoding="utf-8")

    settings = config.load(custom, root=tmp_path)

    assert settings.model == "claude-opus-5"
    assert settings.project == "sylow"


def test_the_flag_beats_the_project_config(tmp_path: Path):
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text('project = "sylow"\n', encoding="utf-8")
    settings = config.load(tmp_path / "absent.toml", root=tmp_path, project="galois")
    assert settings.project == "galois"


def test_the_only_project_present_is_the_default(tmp_path: Path):
    (tmp_path / ".hardy").mkdir()
    (tmp_path / "galois").mkdir()
    (tmp_path / "galois" / "session.json").write_text("{}", encoding="utf-8")
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.project == "galois"


def test_an_empty_root_falls_back_to_main_without_reading_stdin(tmp_path: Path):
    """Non-interactive selection must be deterministic.

    Prompting here would hang `hardy batch` and CI, fail at EOF, or consume the
    first piped message as a slug.
    """
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.project == "main"


def test_two_projects_and_no_active_setting_falls_back_to_main(tmp_path: Path):
    (tmp_path / ".hardy").mkdir()
    for slug in ("galois", "sylow"):
        (tmp_path / slug).mkdir()
        (tmp_path / slug / "session.json").write_text("{}", encoding="utf-8")
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.project == "main"


def test_a_project_config_cannot_name_an_executable(tmp_path: Path):
    """The project layer selects a problem; it does not choose programs.

    `.hardy/config.toml` is committed and arrives with any clone, and `_chat`
    builds the CAS runtime -- which runs the configured executable to probe its
    version -- before the prompt appears. An unrestricted merge would let a
    repository run an arbitrary program the moment someone starts Hardy in it.
    """
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text(
        'project = "sylow"\ncas_command = "/tmp/evil"\nlean_command = "/tmp/evil"\n',
        encoding="utf-8",
    )
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.project == "sylow"
    assert settings.cas_command is None
    assert "/tmp/evil" not in " ".join(settings.lean_command)


def test_the_environment_names_the_root_before_the_project_config_is_found(tmp_path: Path, monkeypatch):
    """Otherwise HARDY_ROOT is advertised and inert.

    The project layer lives inside the root, so a root resolved after the
    environment is read would send Hardy to the current directory for its
    project config and open the wrong problem there.
    """
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".hardy").mkdir(parents=True)
    (elsewhere / ".hardy" / "config.toml").write_text('project = "sylow"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HARDY_ROOT", str(elsewhere))

    settings = config.load(tmp_path / "absent.toml")

    assert settings.root == elsewhere
    assert settings.project == "sylow"


def test_runs_root_survives(tmp_path: Path):
    """Staged runs are out of scope; `prove` and `accept` still read this."""
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.runs_root == Path("runs")


def test_a_slug_that_escapes_the_root_is_refused(tmp_path: Path):
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text('project = "../other"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="one directory name"):
        config.load(tmp_path / "absent.toml", root=tmp_path)


def test_the_move_keeps_only_settings_this_hardy_understands(tmp_path: Path):
    """Reproduced: the migration deleted the config and bricked the install.

    Dropping a fixed list of retired keys copied every OTHER unrecognised key
    through verbatim, and `read_file` refuses those exactly as flatly -- so a
    legacy file carrying anything Hardy no longer knows produced a destination
    that will not load, and then unlinked the source. There was no config left
    to repair from and no command left to repair with: `doctor` reads the same
    file and failed the same way.
    """
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        'model = "x"\nworkspace = ".hardy"\nlegacy_thing = "y"\n', encoding="utf-8"
    )
    destination = tmp_path / ".hardy" / "config.toml"

    assert config.migrate_global(legacy, destination) is True

    assert "legacy_thing" not in destination.read_text(encoding="utf-8")
    # The proof that matters: what the migration produced is a file Hardy loads.
    assert config.read_file(destination) == {"model": "x"}


def test_a_project_config_says_how_many_settings_it_dropped(tmp_path: Path, capsys):
    """Silence here is a trap for the user who writes `model = ...` in it.

    The key is known to `read_file`, so nothing refuses the file; it is simply
    not one this layer may set, and a user watching Hardy go on with the old
    model has nothing anywhere to tell them why.
    """
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text(
        'project = "sylow"\nmodel = "some-other-model"\n', encoding="utf-8"
    )
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.model == config.DEFAULT_MODEL
    said = capsys.readouterr().out
    assert "ignoring 1 settings" in said
    assert "may only set: project" in said


def test_a_directory_named_something_no_slug_may_be_is_not_offered(tmp_path: Path):
    """`existing_projects` answers `active_project`, so its list becomes a slug.

    A directory carrying a record can be named anything an unpacking tool or a
    checkout put there, including a name `validate_slug` refuses -- and the
    single-project case returns that name as the slug the session opens,
    getting past the check every other route into a slug goes through.
    """
    (tmp_path / ".hardy").mkdir()
    for name in ("com1", "galois"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "session.json").write_text("{}", encoding="utf-8")

    assert config.existing_projects(tmp_path) == ["galois"]


def test_a_multiline_value_in_a_legacy_config_survives_the_move(tmp_path: Path):
    """The same brick as the retired-key one, reached through a live key.

    A legacy `model` written as a triple-quoted string spanning two lines
    parses to a value with a newline in it. The serialiser escaped only `\\`
    and `"`, so that newline went out raw inside a single-line quoted value --
    which TOML's grammar forbids -- and the source was then DELETED. The
    destination was a file `tomllib` refuses and the settings were gone, so
    Hardy could not start and had nothing left to start from.
    """
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = """foo\nbar"""\nlean_timeout = 90\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"

    assert config.migrate_global(legacy, destination) is True

    # The proof that matters: the migrated file parses, and says what it said.
    assert config.read_file(destination)["model"] == "foo\nbar"
    assert not legacy.exists()


@pytest.mark.parametrize(
    "value",
    ["tab\there", "carriage\rreturn", "nul\x00byte", "delete\x7fchar", 'quote"and\\slash'],
)
def test_every_control_character_round_trips_through_the_move(tmp_path: Path, value: str):
    """One escaping rule for the whole class, not a patch for the newline.

    A migration that can brick on one control character can brick on any of
    them, and the source is deleted either way.
    """
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    # Every character as `\uXXXX`, which TOML accepts anywhere a basic string
    # does. Written here rather than through the code under test, so the
    # fixture cannot agree with a serialiser that is wrong.
    literal = "".join(f"\\u{ord(character):04X}" for character in value)
    legacy.write_text(f'model = "{literal}"\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"

    assert config.migrate_global(legacy, destination) is True
    assert config.read_file(destination)["model"] == value


def _recorded(root: Path, *slugs: str) -> None:
    (root / ".hardy").mkdir(exist_ok=True)
    for slug in slugs:
        (root / slug).mkdir()
        (root / slug / "session.json").write_text("{}", encoding="utf-8")


def test_several_projects_and_a_terminal_ask_rather_than_opening_main(tmp_path: Path):
    """Reproduced: an interactive launch silently opening a third, empty problem.

    A root holding `galois/` and `sylow/`, no `project` in either config layer
    and no `--project` is a real ambiguity, and it was resolved in silence by
    opening -- or creating -- `main`. The user was never told the other two
    were there. `choose` is the answer a caller with a terminal supplies.
    """
    _recorded(tmp_path, "galois", "sylow")
    asked: list[list[str]] = []

    def choose(present: list[str]) -> str:
        asked.append(present)
        return "sylow"

    settings = config.load(tmp_path / "absent.toml", root=tmp_path, choose=choose)

    assert asked == [["galois", "sylow"]]
    assert settings.project == "sylow"


def test_declining_the_prompt_keeps_the_old_default(tmp_path: Path):
    """An empty line is an answer, and it is the answer this always gave."""
    _recorded(tmp_path, "galois", "sylow")
    settings = config.load(tmp_path / "absent.toml", root=tmp_path, choose=lambda present: None)
    assert settings.project == "main"


def test_nothing_ambiguous_is_never_asked_about(tmp_path: Path):
    """A prompt about a question nobody is missing is noise.

    A stated slug, a configured one, and a single recorded problem are each an
    answer already -- so none of them reaches the chooser.
    """
    _recorded(tmp_path, "galois")
    asked: list[list[str]] = []

    def choose(present: list[str]) -> str:
        asked.append(present)
        return "sylow"

    assert config.load(tmp_path / "absent.toml", root=tmp_path, choose=choose).project == "galois"
    _recorded(tmp_path, "sylow")
    assert (
        config.load(tmp_path / "absent.toml", root=tmp_path, project="galois", choose=choose).project
        == "galois"
    )
    assert asked == []


def test_a_chosen_slug_is_still_validated(tmp_path: Path):
    """The chooser's answer is a slug like any other.

    It arrives from a human at a terminal rather than from a file, which makes
    it no less a directory name Hardy is about to write a record through.
    """
    _recorded(tmp_path, "galois", "sylow")
    with pytest.raises(layout.LayoutError):
        config.load(tmp_path / "absent.toml", root=tmp_path, choose=lambda present: "../elsewhere")


def test_project_context_is_on_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert config.load(tmp_path / "missing.toml").project_context is True


def test_project_context_can_be_switched_off_in_every_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Running without the project's instructions has to be available without
    editing the project's own `AGENTS.md` out of the way."""
    path = write(tmp_path / "config.toml", "project_context = false\n")
    assert config.load(path).project_context is False

    on = write(tmp_path / "on.toml", "project_context = true\n")
    monkeypatch.setenv("HARDY_PROJECT_CONTEXT", "0")
    assert config.load(on).project_context is False
    monkeypatch.delenv("HARDY_PROJECT_CONTEXT")
    assert config.load(on, project_context=False).project_context is False


def test_a_project_context_value_that_is_neither_true_nor_false_is_refused(tmp_path: Path):
    """Read as true, a misspelled `off` would do nothing and say nothing, and
    the user would find out from a transcript."""
    path = write(tmp_path / "config.toml", 'project_context = "sometimes"\n')
    with pytest.raises(ValueError, match="project_context must be true or false"):
        config.load(path)


def test_a_committed_project_config_may_not_switch_the_context_off(tmp_path: Path, monkeypatch):
    """The project layer travels with a clone and is deliberately tiny. A
    repository gets to say which problem is active, not what the model is
    told about it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / layout.HARDY_DIR).mkdir()
    write(tmp_path / layout.HARDY_DIR / "config.toml", "project_context = false\n")
    assert config.load(tmp_path / "missing.toml").project_context is True


def test_the_context_window_defaults_to_the_planners_own_figure(tmp_path: Path, monkeypatch):
    """One number, in one place. A default that drifted from the constant the
    compactor plans against would cut sessions to a window nothing used."""
    from hardy import compaction

    monkeypatch.chdir(tmp_path)
    assert config.load(tmp_path / "missing.toml").context_window == compaction.CONTEXT_WINDOW


def test_the_context_window_can_be_stated_for_the_endpoint_in_use(tmp_path: Path, monkeypatch):
    """The window is a property of the endpoint, not of Hardy: a gateway
    answering `claude-opus-5` may offer a smaller one than Anthropic does, and
    a user who knows that needs somewhere to say so."""
    path = write(tmp_path / "config.toml", "context_window = 32000\n")
    assert config.load(path).context_window == 32000

    monkeypatch.setenv("HARDY_CONTEXT_WINDOW", "16000")
    assert config.load(path).context_window == 16000


def test_a_context_window_that_could_hold_nothing_is_refused(tmp_path: Path):
    """A compactor told to plan against it would cut every conversation to
    nothing and still overflow, so it is a typo rather than a preference."""
    path = write(tmp_path / "config.toml", "context_window = 0\n")
    with pytest.raises(ValueError, match="context_window must be a positive"):
        config.load(path)

    text = write(tmp_path / "text.toml", 'context_window = "large"\n')
    with pytest.raises(ValueError, match="context_window must be a number"):
        config.load(text)
