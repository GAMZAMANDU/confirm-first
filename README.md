# confirm-first

변경 작업 전에 Codex가 요청 해석과 진행 계획을 제시하고, 사용자의 별도 승인을 기다리게 하는 전역 훅입니다. Claude Code용 구현도 그대로 포함되어 있습니다.

## Codex 전역 설치

Python 3가 필요합니다. 저장소를 받은 뒤 다음 명령을 실행합니다.

```sh
git clone https://github.com/GAMZAMANDU/confirm-first.git
python3 confirm-first/codex/install.py
```

설치 스크립트는 `~/.codex/hooks/confirm-first.py`를 복사하고 기존 `~/.codex/hooks.json`의 다른 훅은 보존합니다. 원본 설정은 `~/.codex/hooks.json.before-confirm-first`에 백업합니다. Codex를 다시 열고 `/hooks`에서 새 훅을 검토·신뢰한 뒤 새 채팅을 시작합니다.

## 사용

1. 변경 요청을 입력합니다. Codex는 읽기와 검색으로 맥락을 파악한 뒤 질문창에 `요청 해석:`과 `진행 계획:`을 제시합니다.
2. 동의하면 질문창에서 `이대로 진행`을 선택합니다. 해당 요청의 변경 작업이 열립니다.
3. 해석이나 계획이 다르면 수정 내용을 답합니다. Codex가 새 계획을 제시할 때까지 변경 작업은 계속 차단됩니다.

새 변경 요청은 다시 확인 절차를 거칩니다. 읽기 작업과 순수 질문의 답변은 승인을 요구하지 않습니다.

Codex에서는 Claude 플러그인의 `/confirm-first` 명령과 입력창 토글 UI를 제공하지 않습니다. 전역 훅을 끄려면 `/hooks`에서 해당 훅을 비활성화합니다.

## 구현 범위

Codex 훅은 `UserPromptSubmit`에서 확인 지침과 요청 상태를 만들고, `PreToolUse`에서 승인 전 변경 가능 도구를 차단하며 승인 질문창 호출을 기록합니다. 질문 도구가 없는 환경에서는 일반 메시지로 확인하고 `Stop`이 이를 기록합니다. 상태는 사용자 상태 디렉터리에 세션별로 저장됩니다.

Codex 문서에 따르면 일부 특수 도구는 로컬 도구 훅 경로를 사용하지 않으며, 호스팅 도구도 `PreToolUse` 대상이 아닙니다. 따라서 이 플러그인은 Codex의 모든 변경 경로를 완전히 강제하는 보안 경계가 아닙니다. [훅 동작 및 제한](https://learn.chatgpt.com/docs/hooks)

## 테스트

```sh
python3 -m unittest discover -s codex -p 'test_*.py'
```
