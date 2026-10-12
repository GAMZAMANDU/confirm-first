import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from install import install


SCRIPT = Path(__file__).with_name("confirm_first.py")


class ConfirmFirstTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = {**os.environ, "PLUGIN_DATA": self.temp.name}
        self.session = "session-1"

    def hook(self, event, **kwargs):
        payload = {"hook_event_name": event, "session_id": self.session, **kwargs}
        result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload), text=True,
                                capture_output=True, env=self.env, check=True)
        return json.loads(result.stdout) if result.stdout else {}

    def blocked(self, turn, tool, tool_input):
        result = self.hook("PreToolUse", turn_id=turn, tool_name=tool, tool_input=tool_input)
        return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_confirmation_flow_and_next_request(self):
        self.hook("UserPromptSubmit", turn_id="one", prompt="Fix the bug")
        self.assertTrue(self.blocked("one", "apply_patch", {"command": "*** Begin Patch"}))
        self.assertFalse(self.blocked("one", "Bash", {"command": "git status"}))
        self.assertTrue(self.blocked("one", "Bash", {"command": "git diff --output=x"}))
        self.assertTrue(self.blocked("one", "mcp__fs__write_file", {"path": "x"}))
        self.hook("Stop", turn_id="one", last_assistant_message="요청 해석: 버그 수정\n진행 계획: 파일 수정\n진행할까요?")
        self.hook("UserPromptSubmit", turn_id="two", prompt="진행")
        self.assertFalse(self.blocked("two", "apply_patch", {"command": "*** Begin Patch"}))
        self.hook("UserPromptSubmit", turn_id="three", prompt="다른 파일도 수정해")
        self.assertTrue(self.blocked("three", "apply_patch", {"command": "*** Begin Patch"}))

    def test_revision_and_unasked_yes_do_not_approve(self):
        self.hook("UserPromptSubmit", turn_id="one", prompt="Fix the bug")
        self.hook("UserPromptSubmit", turn_id="two", prompt="yes")
        self.assertTrue(self.blocked("two", "apply_patch", {}))
        self.hook("Stop", turn_id="two", last_assistant_message="요청 해석: 수정\n진행 계획: 편집")
        self.hook("UserPromptSubmit", turn_id="three", prompt="범위를 바꿔줘")
        self.assertTrue(self.blocked("three", "apply_patch", {}))

    def test_shell_expansion_is_denied(self):
        self.hook("UserPromptSubmit", turn_id="one", prompt="Fix")
        for command in ("cat x; rm x", "rg --pre python foo", "echo $(touch x)", "git status > x",
                        "find . -fprint0 /tmp/x", "git diff --ext-diff"):
            self.assertTrue(self.blocked("one", "Bash", {"command": command}), command)

    def test_global_install_preserves_hooks_and_is_repeatable(self):
        root = Path(self.temp.name) / "codex"
        root.mkdir()
        original = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "existing-hook"}]}]}}
        (root / "hooks.json").write_text(json.dumps(original))
        install(root)
        install(root)
        hooks = json.loads((root / "hooks.json").read_text())["hooks"]
        self.assertEqual(len(hooks["PreToolUse"]), 2)
        self.assertEqual(hooks["PreToolUse"][0], original["hooks"]["PreToolUse"][0])
        self.assertTrue((root / "hooks" / "confirm-first.py").exists())
        self.assertEqual(json.loads((root / "hooks.json.before-confirm-first").read_text()), original)


if __name__ == "__main__":
    unittest.main()
