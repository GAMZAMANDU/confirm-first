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

    def blocked(self, turn, tool, tool_input, **kwargs):
        result = self.hook("PreToolUse", turn_id=turn, tool_name=tool, tool_input=tool_input, **kwargs)
        return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def ui_reply(self, title, answer, tool="request_user_input_async"):
        item = {"questionItemId": json.dumps([tool, "call_test", 0]),
                "question": title, "answer": answer}
        return "<send_user_message_question_reply>\n" + json.dumps([item], ensure_ascii=False) + "\n</send_user_message_question_reply>"

    def test_confirmation_flow_and_next_request(self):
        self.hook("UserPromptSubmit", turn_id="one", prompt="Fix the bug")
        self.assertTrue(self.blocked("one", "apply_patch", {"command": "*** Begin Patch"}))
        self.assertFalse(self.blocked("one", "Bash", {"command": "git status"}))
        self.assertTrue(self.blocked("one", "Bash", {"command": "git diff --output=x"}))
        self.assertTrue(self.blocked("one", "mcp__fs__write_file", {"path": "x"}))
        title = "요청 해석: 버그 수정\n진행 계획: 파일 수정"
        question = {"questions": [{"title": title,
                                   "options": ["이대로 진행", "해석 수정"]}]}
        self.assertFalse(self.blocked("one", "request_user_input_async", question))
        self.hook("UserPromptSubmit", turn_id="two", prompt=self.ui_reply(title, "이대로 진행"))
        self.assertFalse(self.blocked("two", "apply_patch", {"command": "*** Begin Patch"}))
        self.hook("UserPromptSubmit", turn_id="three", prompt="다른 파일도 수정해")
        self.assertTrue(self.blocked("three", "apply_patch", {"command": "*** Begin Patch"}))

    def test_async_question_reply_in_transcript_unlocks_same_turn(self):
        turn = "one"
        title = "요청 해석: 버그 수정\n진행 계획: 파일 수정"
        call_id = "call_test"
        self.hook("UserPromptSubmit", turn_id=turn, prompt="Fix the bug")
        transcript = Path(self.temp.name) / "rollout.jsonl"
        records = [
            {"type": "response_item", "payload": {"type": "function_call", "name": "request_user_input_async",
                "call_id": call_id, "arguments": json.dumps({"questions": [{"title": title,
                    "options": ["이대로 진행", "해석 수정"]}]}),
                "internal_chat_message_metadata_passthrough": {"turn_id": turn}}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": self.ui_reply(title, "이대로 진행")}],
                "internal_chat_message_metadata_passthrough": {"turn_id": turn}}},
        ]
        transcript.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n")
        self.assertFalse(self.blocked(turn, "apply_patch", {}, transcript_path=str(transcript)))
        self.hook("UserPromptSubmit", turn_id="two", prompt="Another change")
        self.assertTrue(self.blocked("two", "apply_patch", {}, transcript_path=str(transcript)))

    def test_async_reply_requires_matching_question_call(self):
        turn = "one"
        title = "요청 해석: 버그 수정\n진행 계획: 파일 수정"
        self.hook("UserPromptSubmit", turn_id=turn, prompt="Fix")
        transcript = Path(self.temp.name) / "rollout.jsonl"
        record = {"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": self.ui_reply(title, "이대로 진행")}],
            "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}
        transcript.write_text(json.dumps(record, ensure_ascii=False) + "\n")
        self.assertTrue(self.blocked(turn, "apply_patch", {}, transcript_path=str(transcript)))

    def test_revision_and_unasked_yes_do_not_approve(self):
        self.hook("UserPromptSubmit", turn_id="one", prompt="Fix the bug")
        self.hook("UserPromptSubmit", turn_id="two", prompt="yes")
        self.assertTrue(self.blocked("two", "apply_patch", {}))
        self.hook("Stop", turn_id="two", last_assistant_message="요청 해석: 수정\n진행 계획: 편집")
        self.hook("UserPromptSubmit", turn_id="three", prompt="범위를 바꿔줘")
        self.assertTrue(self.blocked("three", "apply_patch", {}))

    def test_unrelated_question_does_not_unlock(self):
        self.hook("UserPromptSubmit", turn_id="one", prompt="Fix")
        self.assertFalse(self.blocked("one", "request_user_input_async",
                                      {"questions": [{"title": "선호하는 색상은?", "options": ["빨강", "파랑"]}]}))
        self.hook("UserPromptSubmit", turn_id="two", prompt="이대로 진행")
        self.assertTrue(self.blocked("two", "apply_patch", {}))

    def test_ui_reply_must_match_issued_question_and_approval_choice(self):
        title = "요청 해석: A\n진행 계획: B"
        question = {"questions": [{"title": title, "options": ["이대로 진행", "해석 수정"]}]}
        for reply in (self.ui_reply("요청 해석: 다른 요청\n진행 계획: B", "이대로 진행"),
                      self.ui_reply(title, "해석 수정"),
                      self.ui_reply(title, "이대로 진행", tool="other_tool")):
            self.hook("UserPromptSubmit", turn_id="one", prompt="Fix")
            self.assertFalse(self.blocked("one", "request_user_input_async", question))
            self.hook("UserPromptSubmit", turn_id="two", prompt=reply)
            self.assertTrue(self.blocked("two", "apply_patch", {}))

    def test_plain_reply_still_works_without_question_tool(self):
        self.hook("UserPromptSubmit", turn_id="one", prompt="Fix")
        self.hook("Stop", turn_id="one", last_assistant_message="요청 해석: A\n진행 계획: B")
        self.hook("UserPromptSubmit", turn_id="two", prompt="승인할게")
        self.assertFalse(self.blocked("two", "apply_patch", {}))

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
        self.assertNotIn("PostToolUse", hooks)
        self.assertTrue((root / "hooks" / "confirm-first.py").exists())
        self.assertEqual(json.loads((root / "hooks.json.before-confirm-first").read_text()), original)


if __name__ == "__main__":
    unittest.main()
