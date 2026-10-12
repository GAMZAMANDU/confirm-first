"""Codex command hooks for confirm-first (Python standard library only)."""

import json
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path


APPROVAL = re.compile(r"^\s*(응|네|넵|예|ㅇㅇ|ㅇㅋ|오케이|좋아|좋아요|그래|그대로|진행|진행해|진행해줘|이대로 진행|승인|승인할게|승인합니다|맞아요|맞습니다|동의|동의합니다|ok|okay|yes|y|go|lgtm)[\s.!~]*$", re.I)
READ_TOOLS = {"Read", "Grep", "Glob", "LS", "List", "Search", "WebSearch", "WebFetch", "TodoRead", "PlanRead", "update_plan", "request_user_input_async"}
READ_MCP = re.compile(r"^(get|read|list|search|query|find|view|show|describe|whoami|status|snapshot|screenshot|lsp_|tabs_context|batch_get|trace_|state_(read|get|list))", re.I)
READ_COMMANDS = {"pwd", "ls", "cat", "head", "tail", "wc", "stat", "file", "rg", "grep", "git"}
GIT_READ = {"status", "diff", "log", "show", "ls-files", "rev-parse", "grep", "blame", "ls-tree", "cat-file"}
CONTEXT = ("[CONFIRM-FIRST] 변경 작업 전에 요청을 조사하고 사용자에게 확인을 받으세요. "
           "request_user_input_async 질문창을 사용하세요. 질문 제목에 '요청 해석:'과 '진행 계획:'을 각각 명시하고 "
           "선택지는 '이대로 진행'과 '해석 수정'을 제시하세요. 질문창을 띄운 뒤 사용자의 별도 답변을 기다리세요. "
           "질문 도구가 제공되지 않는 환경에서만 일반 메시지로 같은 내용을 질문하세요. "
           "질문이나 설명만 필요한 경우 바로 답하세요. 승인 전에는 읽기 작업만 허용됩니다. "
           "사용자가 계획을 수정하면 갱신된 해석과 계획을 다시 확인받으세요.")
DENY_REASON = ("[confirm-first] 사용자 승인 전 변경 작업이 차단되었습니다. "
               "읽기 작업으로 맥락을 파악한 뒤 request_user_input_async 질문창으로 해석과 계획을 확인받으세요.")


def state_dir():
    base = os.environ.get("PLUGIN_DATA") or os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "confirm-first" if not os.environ.get("PLUGIN_DATA") else Path(base)
    return Path(os.environ.get("APPDATA") or Path.home() / ".local" / "state") / "confirm-first"


def connect():
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "state.sqlite3", timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, phase TEXT NOT NULL, turn TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
    return db


def get_state(db, session):
    return db.execute("SELECT phase, turn FROM sessions WHERE id = ?", (session,)).fetchone() or ("idle", "")


def set_state(db, session, phase, turn):
    db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)", (session, phase, turn))
    db.commit()


def set_question(db, session, title):
    db.execute("INSERT OR REPLACE INTO questions VALUES (?, ?)", (session, title))
    db.commit()


def clear_question(db, session):
    db.execute("DELETE FROM questions WHERE id = ?", (session,))
    db.commit()


def get_question(db, session):
    row = db.execute("SELECT title FROM questions WHERE id = ?", (session,)).fetchone()
    return row[0] if row else None


def parse_ui_reply(prompt):
    if not isinstance(prompt, str):
        return None
    opening = "<send_user_message_question_reply>"
    closing = "</send_user_message_question_reply>"
    wrapped = prompt.strip()
    if not (wrapped.startswith(opening) and wrapped.endswith(closing)):
        return None
    try:
        answers = json.loads(wrapped[len(opening):-len(closing)].strip())
        if not isinstance(answers, list) or len(answers) != 1:
            return None
        item = answers[0]
        if not isinstance(item, dict) or item.get("answer") != "이대로 진행":
            return None
        question_id = json.loads(item.get("questionItemId", ""))
        if (isinstance(question_id, list) and len(question_id) == 3
                and question_id[0] == "request_user_input_async"
                and isinstance(question_id[1], str) and question_id[1]
                and question_id[2] == 0 and isinstance(item.get("question"), str)):
            return question_id[1], item["question"]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def approved_reply(prompt, title):
    return bool(APPROVAL.fullmatch(prompt)) or (title is not None and
            (reply := parse_ui_reply(prompt)) is not None and reply[1] == title)


def transcript_approved(path, turn):
    """Handle async question replies, which skip UserPromptSubmit in Codex Desktop.

    The rollout format is not a public API. Unknown formats fail closed.
    """
    if not isinstance(path, str) or not path or not turn:
        return False
    try:
        with open(path, "rb") as transcript:
            transcript.seek(0, os.SEEK_END)
            size = transcript.tell()
            transcript.seek(max(0, size - 2_000_000))
            if size > 2_000_000:
                transcript.readline()
            lines = transcript.readlines()
    except (OSError, ValueError):
        return False
    questions = {}
    for line in lines:
        try:
            record = json.loads(line)
            if record.get("type") != "response_item":
                continue
            item = record.get("payload", {})
            metadata = item.get("internal_chat_message_metadata_passthrough") or {}
            if metadata.get("turn_id") != turn:
                continue
            if item.get("type") == "function_call" and item.get("name") == "request_user_input_async":
                title = confirmation_question(json.loads(item.get("arguments", "")))
                call_id = item.get("call_id")
                if title and isinstance(call_id, str) and call_id:
                    questions[call_id] = title
            elif item.get("type") == "message" and item.get("role") == "user":
                for content in item.get("content", []):
                    reply = parse_ui_reply(content.get("text")) if isinstance(content, dict) else None
                    if reply and questions.get(reply[0]) == reply[1]:
                        return True
        except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
            continue
    return False


def output_context(event, message):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}, ensure_ascii=False))


def deny():
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": DENY_REASON}}, ensure_ascii=False))


def read_only_shell(command):
    # Refuse shell expansion, redirection, substitution and command chaining.
    if not isinstance(command, str) or not command.strip() or re.search(r"[$`<>;&|\n\r]", command):
        return False
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if not args:
        return False
    head = Path(args[0]).name
    if head not in READ_COMMANDS:
        return False
    if head == "git":
        return (len(args) >= 2 and args[1] in GIT_READ
                and not any(x.startswith(("--output", "-o", "--ext-diff", "--textconv", "--open-files-in-pager"))
                            for x in args[2:]))
    if head == "rg" and any(x.startswith(("--pre", "--hostname-bin")) for x in args[1:]):
        return False
    if head == "grep" and any(x.startswith("--devices=") and x != "--devices=skip" for x in args[1:]):
        return False
    return True


def read_only_tool(name, tool_input):
    if name == "Bash":
        if not isinstance(tool_input, dict):
            return False
        return read_only_shell(tool_input.get("command", tool_input.get("cmd")))
    if name in READ_TOOLS:
        return True
    if name.startswith("mcp__"):
        return bool(READ_MCP.match(name.rsplit("__", 1)[-1]))
    return False


def confirmation_question(tool_input):
    if not isinstance(tool_input, dict):
        return False
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return False
    for question in questions:
        if not isinstance(question, dict):
            continue
        title = question.get("title")
        options = question.get("options")
        if (isinstance(title, str) and "요청 해석:" in title and "진행 계획:" in title
                and isinstance(options, list) and "이대로 진행" in options and "해석 수정" in options):
            return title
    return None


def handle(data):
    event = data.get("hook_event_name")
    session = data.get("session_id")
    turn = data.get("turn_id") or ""
    if not isinstance(session, str) or not session:
        if event == "PreToolUse":
            deny()
        elif event == "Stop":
            print("{}")
        return
    with connect() as db:
        phase, saved_turn = get_state(db, session)
        if event == "SessionStart":
            output_context(event, CONTEXT)
        elif event == "UserPromptSubmit":
            prompt = data.get("prompt") or ""
            if phase == "asked" and approved_reply(prompt, get_question(db, session)):
                set_state(db, session, "approved", turn)
                clear_question(db, session)
                output_context(event, "[CONFIRM-FIRST] 사용자가 앞서 제시한 해석과 계획을 승인했습니다. 이 턴에서 작업을 진행하세요.")
            else:
                set_state(db, session, "armed", turn)
                clear_question(db, session)
                output_context(event, CONTEXT)
        elif event == "PreToolUse":
            if phase == "armed" and saved_turn == turn and transcript_approved(data.get("transcript_path"), turn):
                set_state(db, session, "approved", turn)
                clear_question(db, session)
                phase = "approved"
            if phase in {"idle", "approved"}:
                return
            name = str(data.get("tool_name") or "")
            tool_input = data.get("tool_input")
            if name == "request_user_input_async" and phase == "armed":
                title = confirmation_question(tool_input)
                if title is not None:
                    set_question(db, session, title)
                    set_state(db, session, "asked", turn)
                    return
            if not read_only_tool(name, tool_input):
                deny()
        elif event == "Stop":
            message = data.get("last_assistant_message") or ""
            if phase == "armed" and saved_turn == turn and "요청 해석:" in message and "진행 계획:" in message:
                set_state(db, session, "asked", turn)
            print("{}")


if __name__ == "__main__":
    try:
        handle(json.load(sys.stdin))
    except Exception as exc:
        print(f"confirm-first hook error: {exc}", file=sys.stderr)
        # A failed PreToolUse hook can let the tool proceed. Return a valid deny instead.
        if 'data' in locals() and data.get("hook_event_name") == "PreToolUse":
            deny()
        elif 'data' in locals() and data.get("hook_event_name") == "Stop":
            print("{}")
