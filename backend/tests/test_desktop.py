from pathlib import Path
import shlex

from lichtwerk.desktop import seed_config
from lichtwerk.ola_runtime import find_olad, seed_ola_config


def test_desktop_build_includes_dynamically_loaded_midi_backend():
    script = Path(__file__).resolve().parents[2] / "packaging" / "build_app.sh"
    arguments = shlex.split(script.read_text().replace("\\\n", ""))
    hidden_imports = [
        arguments[index + 1]
        for index, argument in enumerate(arguments[:-1])
        if argument == "--hidden-import"
    ]
    assert "mido.backends.rtmidi" in hidden_imports


def test_seed_config_copies_missing_files_but_keeps_user_edits(tmp_path):
    template, target = tmp_path / "template", tmp_path / "target"
    (template / "fixtures").mkdir(parents=True)
    (template / "scenes.json").write_text("default")
    (template / "scenes.json.bak").write_text("backup")
    (template / "fixtures" / "a.json").write_text("fixture")
    target.mkdir()
    (target / "scenes.json").write_text("mine")
    seed_config(template, target)
    assert (target / "scenes.json").read_text() == "mine"
    assert (target / "fixtures" / "a.json").read_text() == "fixture"
    assert not (target / "scenes.json.bak").exists()


def test_seed_ola_config_does_not_overwrite(tmp_path):
    template, target = tmp_path / "t", tmp_path / "ola"
    template.mkdir()
    (template / "ola-artnet.conf").write_text("ip = 2.0.0.1\n")
    target.mkdir()
    (target / "ola-artnet.conf").write_text("ip = 10.0.0.5\n")
    seed_ola_config(template, target)
    assert (target / "ola-artnet.conf").read_text() == "ip = 10.0.0.5\n"


def test_find_olad_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert find_olad((str(tmp_path / "nope"),)) is None
