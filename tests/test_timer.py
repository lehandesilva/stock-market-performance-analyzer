"""`smpa timer`: the systemd user units that keep price history growing."""

from pathlib import Path

from smpa import timer


def test_units_run_smpa_prices_with_this_config_after_the_close():
    u = timer.units("/home/me/smpa/.venv/bin/python", Path("/home/me/my smpa/config.toml"))
    service, t = u["smpa-prices.service"], u["smpa-prices.timer"]
    assert 'ExecStart=/home/me/smpa/.venv/bin/python -m smpa.cli -c "/home/me/my smpa/config.toml" prices' in service
    assert "WorkingDirectory=/home/me/my smpa\n" in service and "Type=oneshot" in service
    assert "OnCalendar=Mon..Fri 16:00 Asia/Colombo" in t and "Persistent=true" in t
    assert "%%" in timer.units("/p", Path("/data/100%/config.toml"))["smpa-prices.service"]  # not a specifier


def test_install_writes_units_and_prints_commands_without_systemctl(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(timer.shutil, "which", lambda _: None)
    config = tmp_path / "config.toml"
    config.write_text("")
    written = timer.install(config, python="/usr/bin/python3")
    assert sorted(p.name for p in written) == ["smpa-prices.service", "smpa-prices.timer"]
    assert all(p.parent == tmp_path / "systemd" / "user" for p in written)
    assert "systemctl --user enable --now smpa-prices.timer" in capsys.readouterr().out
    timer.remove()
    assert not any((tmp_path / "systemd" / "user").iterdir())
