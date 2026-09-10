"""The settings screen, from the rules rather than from the incidents.

Three rules are being checked here, and each one has already cost this
repository something in a different costume.

*Every setting is reachable.* The screen renders a form it is sent, so a config
field missing from `settings.SECTIONS` is a field nobody can ever change and
nothing says so -- the `trace.query_left` bug wearing a settings hat. The test
walks `config.Config` rather than listing what was added last.

*A credential never leaves this process.* The window is a WebView; it does not
read files and it does not receive secrets. That is a property of the payload,
so the payload is what gets searched.

*What is written is what is read back.* Saving rewrites the whole config, and a
type that survives the round trip on this machine and not on the next one is a
setting that silently reverts.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib

import pytest

from sunday import config, settings
from sunday.agent import prompts


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private SUNDAY_HOME, so nothing here can touch the real one."""
    monkeypatch.setattr(config, "SUNDAY_HOME", tmp_path)
    monkeypatch.setattr(config, "CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "memory")
    for key in config.CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    config.refresh_credentials()
    yield tmp_path
    config.refresh_credentials()


# -- every setting is reachable -------------------------------------------


def config_fields() -> set[str]:
    """Every `section.field` in `config.Config`, walked rather than listed."""
    found = set()
    for spec in dataclasses.fields(config.Config()):
        section = getattr(config.Config(), spec.name)
        if not dataclasses.is_dataclass(section):
            continue
        for entry in dataclasses.fields(section):
            found.add(f"{spec.name}.{entry.name}")
    return found


def test_every_config_section_is_on_the_screen():
    listed = {name for name, _, _ in settings.SECTIONS}
    holders = {
        spec.name
        for spec in dataclasses.fields(config.Config())
        if dataclasses.is_dataclass(getattr(config.Config(), spec.name))
    }
    missing = holders - listed
    assert not missing, (
        f"config has {sorted(missing)} and the settings screen has no section for "
        "it, so every field in it is unreachable"
    )


def test_every_config_field_is_rendered():
    """A field that exists and is not on the screen cannot be changed.

    Written from the rule and not from a bug: when the rule is *every* setting,
    the test enumerates the fields rather than the ones somebody remembered.
    """
    rendered = {field["key"] for section in settings.schema() for field in section["fields"]}
    missing = config_fields() - rendered
    assert not missing, f"{sorted(missing)} exists in config and is on no screen"


def test_every_rendered_field_has_copy():
    """A label and a sentence, or the box is a mystery with a number in it."""
    bare = [
        field["key"]
        for section in settings.schema()
        for field in section["fields"]
        if field["key"] not in settings.HELP
    ]
    assert not bare, f"{sorted(bare)} is rendered with no label and no help"


def test_the_hidden_list_is_only_what_is_really_hidden():
    """`source` is which file was loaded, not a setting. Nothing else qualifies,
    and a growing exclusion list is how "every setting" quietly stops being
    true."""
    assert set(settings.HIDDEN) == {"source"}


# -- a credential never leaves this process --------------------------------


def test_the_payload_never_carries_a_credential(home, monkeypatch):
    """The window gets presence and four characters. Never a key.

    Searched over the whole serialised payload rather than the credential
    block, because the leak worth catching is the one somewhere else -- a
    debug field, a path, an error message with the value interpolated into it.
    """
    secret = "sk-livetestkey000000deadbeef9f2a"
    config.save_credentials({"DEEPSEEK_API_KEY": secret, "GOOGLE_CLIENT_SECRET": "gs-9876543210"})

    payload = json.dumps(settings.describe(config.Config()))
    assert secret not in payload
    assert "gs-9876543210" not in payload
    # And it is still useful: which key is in there is visible.
    assert "9f2a" in payload


def test_a_credential_is_reported_as_set_without_its_value(home):
    config.save_credentials({"GOOGLE_CLIENT_ID": "1234567890.apps.googleusercontent.com"})
    entries = {entry["key"]: entry for entry in settings.credentials()}
    assert entries["GOOGLE_CLIENT_ID"]["set"] is True
    assert entries["GOOGLE_CLIENT_ID"]["source"] == "settings"
    assert entries["GOOGLE_CLIENT_SECRET"]["set"] is False
    assert entries["GOOGLE_CLIENT_SECRET"]["hint"] == ""


def test_the_settings_file_wins_over_the_environment(home, monkeypatch):
    """The order that makes the screen honest.

    The other way round, a key typed into the settings screen on a machine that
    happens to have a `.env` would save cleanly and change nothing -- which is
    the class of control this codebase keeps writing notes about.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-the-environment")
    config.refresh_credentials()
    assert config.DEEPSEEK_API_KEY == "from-the-environment"
    assert config.credential_source("DEEPSEEK_API_KEY") == "environment"

    config.save_credentials({"DEEPSEEK_API_KEY": "from-the-screen"})
    assert config.DEEPSEEK_API_KEY == "from-the-screen"
    assert config.credential_source("DEEPSEEK_API_KEY") == "settings"


def test_clearing_a_credential_falls_back_rather_than_emptying(home, monkeypatch):
    """Cleared means "I am not setting this here", not "there is no key".

    If the environment still has one, that is the value in use, and the screen
    says so rather than showing an empty box over a working key.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-the-environment")
    config.save_credentials({"DEEPSEEK_API_KEY": "from-the-screen"})
    config.save_credentials({"DEEPSEEK_API_KEY": ""})
    assert config.DEEPSEEK_API_KEY == "from-the-environment"
    assert config.read_credentials().get("DEEPSEEK_API_KEY") is None


def test_an_untouched_credential_is_left_alone(home):
    """Absent and empty are different questions, which is why the window sends
    only the boxes somebody typed into."""
    config.save_credentials({"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret"})
    config.save_credentials({"GOOGLE_CLIENT_ID": "other"})
    assert config.GOOGLE_CLIENT_SECRET == "secret"


def test_a_corrupt_credential_file_is_an_ordinary_first_run(home):
    config.CREDENTIALS_PATH.write_text("{ not json", encoding="utf-8")
    config.refresh_credentials()
    assert config.read_credentials() == {}
    assert config.DEEPSEEK_API_KEY == ""


def test_the_credential_file_is_already_on_the_deny_list():
    """It needs no new rule, and this is the check that it needs no new rule.

    `credentials*` has been on `secret_paths` since the list was written, so the
    agent reading this file shuts the web door for the turn exactly as reading
    `.env` does. Move the file and this fails, which is the point.
    """
    from sunday import guardrail

    assert guardrail.is_secret_path(config.CREDENTIALS_PATH)


# -- what is written is what is read back ----------------------------------


def test_the_whole_config_survives_a_round_trip(tmp_path):
    """Every type the dataclasses hold, through the emitter and back.

    Not "the fields I changed". A config file that records only the differences
    is a file whose meaning moves when a default moves, and the defaults here
    are measured numbers that do move.
    """
    cfg = config.Config()
    cfg.assistant.name = "Friday"
    cfg.prompts.system = 'You are {name}.\nYou like "quotes" and {braces} and \\backslashes.'
    cfg.files.roots = [{"label": "work", "path": "D:/Projects"}, "C:/Users/you/Documents"]
    cfg.memory.distance_cutoff = 0.7
    cfg.limits.tool_calls = 9
    cfg.ui.trace = False

    path = tmp_path / "config.toml"
    path.write_text(settings.emit(cfg), encoding="utf-8")
    back = config.load(path)

    assert back.assistant.name == "Friday"
    assert back.prompts.system == cfg.prompts.system
    assert back.files.roots == cfg.files.roots
    assert back.memory.distance_cutoff == 0.7
    assert back.limits.tool_calls == 9
    assert back.ui.trace is False
    assert back.guardrail.secret_paths == config.Config().guardrail.secret_paths


def test_the_emitted_file_is_valid_toml(tmp_path):
    raw = tomllib.loads(settings.emit(config.Config()))
    assert raw["models"]["context_tokens"] == config.Config().models.context_tokens
    assert raw["files"]["roots"] == []


def test_saving_writes_the_file_the_loader_reads(home, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    settings.apply({"assistant.name": "Friday", "limits.tool_calls": "7"})
    assert settings.target_path() == home / "config.toml"
    assert config.get().assistant.name == "Friday"
    assert config.get().limits.tool_calls == 7


def test_a_number_that_is_not_a_number_says_which_field(home, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    with pytest.raises(settings.Invalid) as raised:
        settings.apply({"limits.tool_calls": "lots"})
    assert "Tool calls per turn" in str(raised.value)


def test_a_folder_that_is_not_there_is_saved_and_said(home, monkeypatch):
    """A warning, not a refusal. Nothing here creates folders -- but a path
    that will exist tomorrow is the user's business, and refusing to save it
    would be this program deciding what their disk looks like."""
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    outcome = settings.apply(
        {"files.roots": [{"label": "gone", "path": str(home / "not-a-folder")}]}
    )
    assert outcome.warnings and "not a folder that exists" in outcome.warnings[0]
    assert config.get().files.paths() == [str(home / "not-a-folder")]


def test_only_the_model_needs_a_restart(home, monkeypatch):
    """The honest list, checked in both directions.

    Everything else is read fresh each turn, the ear is reopened when an audio
    setting moves, and the shell re-reads `[ui]` after every save -- so a
    longer list here would be the app asking to be restarted for changes it
    has already applied, and a shorter one would be a setting that appears to
    save and does nothing.

    `[ui]` in particular must never be on it. The restart the screen offers is
    the sidecar's, and the sidecar reads none of `[ui]`'s launch switches: the
    button offered for them used to change neither.
    """
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)

    quiet = settings.apply({
        "limits.tool_calls": "9",
        "ui.trace": False,
        "ui.autostart": True,
        "ui.start_minimised": True,
        "ui.fps_focused": "30",
    })
    assert quiet.restart == []
    assert not [key for key in settings.RESTART_REQUIRED if key.startswith("ui.")]

    loud = settings.apply({"models.agent": "qwen3:8b"})
    assert loud.restart == ["Model"]


def test_a_refused_save_changes_nothing_that_is_running(home, monkeypatch):
    """Every value is checked before any of them is applied.

    The first draft set them on the live config one at a time, so a refusal
    half-way down the list left the running process answering to the new name
    while the screen, and the file, both said nothing had been saved.
    """
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    before = config.get().assistant.name
    with pytest.raises(settings.Invalid):
        settings.apply({"assistant.name": "Zed", "limits.tool_calls": "lots"})
    assert config.get().assistant.name == before
    assert not (home / "config.toml").exists()


def test_a_refused_save_writes_no_credentials(home, monkeypatch):
    """Credentials are a second file, and a refused save writes neither."""
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    with pytest.raises(settings.Invalid):
        settings.apply(
            {"limits.tool_calls": "lots"},
            credentials={"DEEPSEEK_API_KEY": "sk-" + "a" * 32},
        )
    assert not config.CREDENTIALS_PATH.exists()
    assert config.DEEPSEEK_API_KEY == ""


def test_credentials_are_saved_with_the_values(home, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    settings.apply(
        {"limits.tool_calls": "6"}, credentials={"GOOGLE_CLIENT_ID": "abc.apps"}
    )
    assert config.GOOGLE_CLIENT_ID == "abc.apps"
    assert config.get().limits.tool_calls == 6


def test_a_folder_written_as_a_bare_path_reaches_the_editor_whole(home, monkeypatch):
    """A bare string is a valid root, and the editor reads `.label` and `.path`.

    Sent as written it had neither: an empty row on screen, and the next save
    that touched the list dropped it as a row with no path -- a folder removed
    from the config by editing a different one.
    """
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    config.get().files.roots = [str(home), {"label": "work", "path": str(home / "w")}]
    field = next(
        f for s in settings.schema() for f in s["fields"] if f["key"] == "files.roots"
    )
    assert field["value"] == [
        {"label": "", "path": str(home)},
        {"label": "work", "path": str(home / "w")},
    ]
    # What the editor sends back, unchanged, keeps both.
    settings.apply({"files.roots": field["value"]})
    assert config.get().files.paths() == [str(home), str(home / "w")]


def test_every_place_a_refusal_names_is_on_the_screen():
    """If a string tells the user to go somewhere, that somewhere has to exist.

    Four Google refusals sent people to "Calendar and mail", which is not a
    page: the button is under Keys and sign-in. The model relays these word for
    word, so a wrong one is the assistant giving directions to a room that is
    not there. Walked from the source rather than listed, because a listed
    version is how every other rule in this family was missed.
    """
    import re
    from pathlib import Path

    package = Path(settings.__file__).parent
    window = (package.parents[1] / "app" / "src" / "lib" / "Settings.svelte").read_text(
        encoding="utf-8"
    )
    added_by_window = {"Keys and sign-in", "Long-term memory"}
    for page in added_by_window:
        assert f"title: '{page}'" in window, page
    pages = {title for _, title, _ in settings.SECTIONS} | added_by_window

    named = []
    for path in package.rglob("*.py"):
        # Implicit concatenation: `"...under "` then `"Folders."` is one string.
        source = re.sub(r'"\s*\n\s*f?"', "", path.read_text(encoding="utf-8"))
        for found in re.finditer(r'settings screen[^."]*?\bunder ([A-Z][^.,"]*)', source):
            named.append((path.name, found.group(1).strip()))
    assert len(named) >= 8, f"the pattern found only {named}, so it is not reading the source"
    wrong = [(where, place) for where, place in named if place not in pages]
    assert not wrong, wrong


def test_an_audio_change_is_reported_so_the_ear_can_be_reopened(home, monkeypatch):
    """The ear reads the whole config when it is built and never again."""
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    assert settings.apply({"limits.tool_calls": "8"}).audio_changed is False
    assert settings.apply({"wake.threshold": "0.4"}).audio_changed is True


# -- the prompt -------------------------------------------------------------


def test_an_empty_prompt_means_the_built_in(home, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    assert prompts.system("Alexa") == prompts.SYSTEM.replace("{name}", "Alexa")


def test_a_written_prompt_replaces_it_and_still_gets_the_name(home, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    settings.apply({"prompts.system": "You are {name}. Be brief."})
    assert prompts.system() == "You are Alexa. Be brief."


def test_a_brace_in_a_written_prompt_does_not_raise(home, monkeypatch):
    """`str.format` would treat it as a field and raise KeyError on every turn,
    from a settings box that looked like it saved cleanly."""
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    settings.apply({"prompts.system": 'You are {name}. Never emit {"json": true}.'})
    assert prompts.system() == 'You are Alexa. Never emit {"json": true}.'


def test_the_screen_can_show_what_reset_would_restore(home, monkeypatch):
    """Reset is a deletion, so the built-in has to travel for it to be shown."""
    monkeypatch.setattr(config, "REPO_ROOT", home / "nowhere")
    config.reload(None)
    described = settings.describe()
    assert described["prompt"]["builtin"] == prompts.SYSTEM
    assert described["prompt"]["builtin_tokens"] > 0


# -- optional things are absent rather than broken --------------------------


def test_calendar_and_mail_are_unbound_without_a_google_client(monkeypatch):
    """Unbinding rather than prompting.

    A tool that is bound and cannot run means the model calls it, reads a
    refusal and relays a paragraph about OAuth to somebody who never asked for
    mail. The registry answers this, so the model never gets the chance.
    """
    from sunday import tools

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    names = {tool.name for tool in tools.available()}
    assert "calendar_read" not in names
    assert "mail_search" not in names

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")
    names = {tool.name for tool in tools.available()}
    assert {"calendar_read", "mail_search"} <= names


def test_what_is_switched_off_is_said_rather_than_silently_missing(monkeypatch):
    """Unbinding a tool is invisible, and this is the other half of that rule.

    Without this the assistant simply never mentions mail, and a user who set
    up Google and mistyped a key has no way to find out.
    """
    from sunday.tools import capabilities

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    said = capabilities.list_capabilities()
    assert "calendar_read" in said and "mail_search" in said
    assert "settings screen" in said


def test_every_tool_with_a_predicate_says_why_it_is_off():
    """A rule applied surface by surface will miss one, so it is enumerated."""
    from sunday import tools

    bare = [
        tool.name
        for tool in tools.all_tools()
        if tool.requires is not None and not tool.requires_note
    ]
    assert not bare, (
        f"{sorted(bare)} can be switched off and has nothing to say about why, "
        "so it would simply vanish from what the assistant claims it can do"
    )


def test_the_deepseek_key_is_optional_and_the_web_still_works(monkeypatch):
    """The one optional thing that needs no prompting at all.

    Without a key the summariser hands back the material it was given, which is
    public text either way. Worth a test because it would be easy to "fix" this
    into a refusal and quietly take the web half away from anyone who never
    signed up for DeepSeek.
    """
    from sunday import web

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    assert web.summarise("gold price", "Gold closed at 2400.") == "Gold closed at 2400."


# -- the shipped template belongs to nobody --------------------------------


def test_a_frozen_sidecar_reads_no_repository_files(home, monkeypatch, tmp_path):
    """The installed build has no repository, and must not act as if it had.

    Frozen, `REPO_ROOT` resolves to the install folder -- so a `config.toml`
    or a `.env` somebody dropped there would become this copy's settings and
    credentials. Only `SUNDAY_HOME` and the real environment count.
    """
    repo = tmp_path / "install"
    repo.mkdir()
    (repo / "config.toml").write_text('[assistant]\nname = "Planted"\n', encoding="utf-8")
    (repo / ".env").write_text("DEEPSEEK_API_KEY=sk-planted\n", encoding="utf-8")
    monkeypatch.setattr(config, "REPO_ROOT", repo)
    monkeypatch.setattr(config, "ENV_PATH", repo / ".env")

    loaded = []
    monkeypatch.setattr(config, "load_dotenv", lambda path: loaded.append(path))

    monkeypatch.setattr(config, "FROZEN", True)
    assert config.config_path() is None
    config._load_dotenv()
    assert loaded == []

    (home / "config.toml").write_text('[assistant]\nname = "Mine"\n', encoding="utf-8")
    assert config.config_path() == home / "config.toml"

    # And in a checkout, both are still found.
    monkeypatch.setattr(config, "FROZEN", False)
    assert config.config_path() == repo / "config.toml"
    config._load_dotenv()
    assert loaded == [repo / ".env"]


def test_the_shipped_template_names_no_folders():
    """A template carrying the builder's own folders is the same mistake as a
    shipped `.env`: two paths that do not exist on the machine that copied it,
    producing "no such folder" about folders nobody asked for."""
    import pathlib

    raw = tomllib.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "config.example.toml").read_text(
            encoding="utf-8"
        )
    )
    assert raw["files"]["roots"] == []
    assert config.Config().files.roots == []
