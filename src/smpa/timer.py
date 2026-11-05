"""A systemd user timer that runs `smpa prices` after every trading day, so price history keeps growing.

The CSE API gives at most a year of daily closes, so a day that isn't stored is lost once it is more than a
year old. `smpa prices` (not `smpa run`) is used because the Gmail token of an app in Testing mode expires
every 7 days and needs a browser.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "smpa-prices"
# The regular session closes at 2:30 pm Colombo time. Persistent=true catches up after the machine was off.
SCHEDULE = "Mon..Fri 16:00 Asia/Colombo"


def _arg(s: str) -> str:
    s = s.replace("%", "%%")  # systemd expands %-specifiers
    return f'"{s}"' if any(c.isspace() for c in s) else s


def units(python: str, config: Path) -> dict[str, str]:
    """The .service and .timer files, running this Python with this config file."""
    service = (
        "[Unit]\nDescription=smpa: store today's CSE prices\n\n"
        "[Service]\nType=oneshot\n"
        f"WorkingDirectory={str(config.parent).replace('%', '%%')}\n"
        f"ExecStart={_arg(python)} -m smpa.cli -c {_arg(str(config))} prices\n"
    )
    timer = (
        "[Unit]\nDescription=Store CSE prices after the market closes\n\n"
        f"[Timer]\nOnCalendar={SCHEDULE}\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    return {f"{NAME}.service": service, f"{NAME}.timer": timer}


def unit_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def _systemctl(*commands: list[str]) -> bool:
    """Run each `systemctl --user ...` command; without systemctl, print them and return False."""
    cmds = [["systemctl", "--user", *c] for c in commands]
    if not shutil.which("systemctl"):
        print("timer:   systemctl not found; run:\n" + "".join(f"           {' '.join(c)}\n" for c in cmds), end="")
        return False
    for c in cmds:
        subprocess.run(c, check=True)
    return True


def install(config: Path, python: str = sys.executable) -> list[Path]:
    """Write the units and start the timer. Returns the files written."""
    d = unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in units(python, config.resolve()).items():
        (d / name).write_text(text)
        written.append(d / name)
    print(f"timer:   wrote {', '.join(str(p) for p in written)}")
    if _systemctl(["daemon-reload"], ["enable", "--now", f"{NAME}.timer"]):
        print(f"timer:   on; next run: systemctl --user list-timers {NAME}.timer")
    print('         to keep it running while you are logged out: sudo loginctl enable-linger "$USER"')
    print(f"         its output: journalctl --user -u {NAME}")
    return written


def remove() -> None:
    d = unit_dir()
    if (d / f"{NAME}.timer").exists():
        _systemctl(["disable", "--now", f"{NAME}.timer"])  # while its unit file still exists
    for name in units("python", Path("config.toml")):
        (d / name).unlink(missing_ok=True)
    _systemctl(["daemon-reload"])
    print("timer:   removed")
