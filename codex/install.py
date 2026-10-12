"""Install confirm-first as user-wide Codex hooks without a marketplace."""

import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path


EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop")
SOURCE = Path(__file__).with_name("confirm_first.py")


def install(root: Path):
    hooks_file = root / "hooks.json"
    script = root / "hooks" / "confirm-first.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    command = f"python3 {shlex.quote(str(script))}"
    windows_command = f'py -3 "{script}"'

    if hooks_file.exists():
        config = json.loads(hooks_file.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(config.get("hooks", {}), dict):
            raise ValueError(f"Invalid Codex hooks file: {hooks_file}")
        backup = hooks_file.with_name("hooks.json.before-confirm-first")
        if not backup.exists():
            shutil.copy2(hooks_file, backup)
    else:
        config = {"hooks": {}}

    hooks = config.setdefault("hooks", {})
    for event in EVENTS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError(f"Invalid {event} hooks")
        # Replace only our own entry, preserving all other hooks and their order.
        entries[:] = [entry for entry in entries if not (isinstance(entry, dict) and any(
            isinstance(h, dict) and "confirm-first.py" in str(h.get("command", ""))
            for h in entry.get("hooks", [])
        ))]
        entries.append({"hooks": [{"type": "command", "command": command,
                                    "commandWindows": windows_command, "timeout": 10}]})

    shutil.copy2(SOURCE, script)
    with tempfile.NamedTemporaryFile("w", dir=root, prefix="hooks-", suffix=".json", delete=False,
                                     encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = Path(handle.name)
    temp.replace(hooks_file)
    return hooks_file, script


if __name__ == "__main__":
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    try:
        hooks_file, script = install(home)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Installed {script}\nUpdated {hooks_file}")
