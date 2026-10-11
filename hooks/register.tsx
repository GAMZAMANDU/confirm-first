import type { Register } from 'claude-code'

import type { Phase } from '../types'

// Intent-confirmation mode.
//   idle   : nothing pending, every tool runs as usual
//   armed  : a prompt arrived; mutating tools are denied until the user approves
//   asked  : the model has put its reading + plan to the user via AskUserQuestion
// Approval is read from the AskUserQuestion answer (or a plain "ok" reply), never
// from anything the model runs, so the model cannot approve for itself.

const isOn = { plugin: 'confirm-first', key: 'isOn' } as const
const phase = { plugin: 'confirm-first', key: 'phase' } as const

const APPROVE_LABEL = '이대로 진행'
const APPROVE_REPLY = /^\s*(응|네|넵|예|ㅇㅇ|ㅇㅋ|오케이|좋아|좋아요|그래|그대로|진행|진행해|진행해줘|ok|okay|yes|y|go|lgtm)[\s.!~]*$/i

const ARM_TEXT = `[CONFIRM-FIRST MODE — 의도 확인 모드 ON]
이 요청에 대해 아직 사용자 승인이 없다. 승인 전에는 파일 수정/쓰기/변경성 명령이 차단된다 (읽기·검색은 자유).
순서:
1. Read/Grep/Glob/읽기전용 Bash/WebSearch 등으로 필요한 맥락을 먼저 파악한다.
2. AskUserQuestion 으로 확인받는다. question 본문에 반드시 포함:
   - 요청 해석: 내가 이해한 목표·범위 (1~2문장)
   - 진행 계획: 단계, 건드릴 파일/영역
   - 가정·불확실한 점 (있을 때만)
   첫 번째 옵션 라벨은 정확히 "${APPROVE_LABEL}" 로 한다. 그 옵션을 고르면 차단이 자동으로 풀린다 (별도 명령 없음).
   나머지는 1~2개, 마지막은 "해석 수정". "해석 수정"(또는 수정/아니/취소로 시작하는 옵션) 외의 옵션을 고르면 승인으로 처리된다. 사용자는 Other 로 자유 입력 가능.
3. "${APPROVE_LABEL}" 이 선택되면 바로 작업을 시작한다.
4. 수정 요청이면 해석·계획을 갱신해 다시 AskUserQuestion.
5. 코드/파일 변경이 필요 없는 순수 질문·설명이면 확인 없이 답한다.`

const REPLY_TEXT = `[CONFIRM-FIRST MODE] 직전 요청의 확인이 아직 끝나지 않았다. 이 메시지가 승인이 아니라면:
- 해석/계획 수정 → 반영해서 AskUserQuestion 으로 다시 확인
- 완전히 새로운 요청 → 새 요청으로 보고 해석·계획을 확인받는다
변경 작업은 승인 전까지 차단된다.`

const APPROVED_TEXT = `[CONFIRM-FIRST MODE] 사용자가 직전 해석·계획을 승인했다. 차단 해제됨. 합의한 계획대로 작업을 시작한다.`

// Built-in tools that change things or reach outside the session.
const DENY_TOOLS = new Set([
  'Edit', 'Write', 'NotebookEdit', 'MultiEdit',
  'CronCreate', 'CronDelete', 'RemoteTrigger', 'PushNotification', 'SendMessage',
  'TaskStop', 'EnterWorktree', 'ExitWorktree', 'Artifact', 'ArtifactData',
  'ArtifactComments', 'SendUserFile', 'DesignSync',
])

// MCP tools are denied unless the last name segment reads as a lookup.
const MCP_READ =
  /^(get|read|list|search|query|find|view|show|describe|whoami|status|snapshot|screenshot|take_|lsp_|tabs_context|batch_get|trace_|notepad_read|notepad_stats|project_memory_read|state_read|state_get|state_list|wiki_(query|read|list)|shared_memory_(read|list)|session_search|app_screenshot|app_list)/i

const RO_CMDS = new Set([
  'ls', 'cat', 'head', 'tail', 'grep', 'egrep', 'fgrep', 'rg', 'find', 'pwd', 'which', 'wc', 'stat',
  'file', 'tree', 'du', 'df', 'jq', 'echo', 'printf', 'date', 'whoami', 'sort', 'uniq', 'cut', 'tr',
  'diff', 'basename', 'dirname', 'realpath', 'readlink', 'cd', 'true', 'test', '[', 'uname', 'ps',
  'type', 'command', 'nl', 'column', 'md5', 'shasum',
])
const GIT_RO = new Set([
  'status', 'log', 'diff', 'show', 'rev-parse', 'ls-files', 'blame', 'grep', 'describe', 'shortlog',
  'ls-remote', 'rev-list', 'cat-file', 'ls-tree', 'remote', 'config',
])
const GIT_BRANCH_OK = new Set(['-a', '-r', '-v', '-vv', '--list', '--show-current', '--all', '--remotes', '--verbose'])

function stripQuotes(s: string): string {
  return s.replace(/'[^']*'/g, 'Q').replace(/"(?:[^"\\]|\\.)*"/g, 'Q')
}

// Deny-by-default: true only when every segment is a known read-only command.
export function isReadOnlyBash(cmd: string): boolean {
  if (/\$\(|`|<\(|>\(/.test(cmd)) return false
  const s = stripQuotes(cmd)
  if (/>/.test(s)) return false
  const segs = s.split(/&&|\|\||[;|\n&]/).map(x => x.trim()).filter(Boolean)
  if (segs.length === 0) return false
  for (const seg of segs) {
    const toks = seg.split(/\s+/)
    let hadAssign = false
    while (toks.length > 0 && /^\w+=/.test(toks[0])) {
      toks.shift()
      hadAssign = true
    }
    if (toks.length === 0 && hadAssign) continue // `F=path` alone changes nothing; `$(...)` was refused above
    if (toks[0] === 'rtk') toks.shift()
    if (toks.length === 0) return false
    const head = toks[0].replace(/^.*\//, '')
    if (head === 'git') {
      const rest = toks.slice(1)
      let i = 0
      while (i < rest.length && rest[i].startsWith('-')) i++
      const sub = rest[i]
      const args = rest.slice(i + 1)
      if (sub === 'branch') {
        if (!args.every(a => GIT_BRANCH_OK.has(a))) return false
      } else if (sub === 'config') {
        if (!args.some(a => a === '--get' || a === '--list' || a === '-l' || a === '--get-all')) return false
      } else if (sub === 'remote') {
        if (args.some(a => !a.startsWith('-') && a !== 'show')) return false
      } else if (sub === undefined || !GIT_RO.has(sub)) {
        return false
      }
      continue
    }
    if (!RO_CMDS.has(head)) return false
    if (head === 'find' && toks.some(t => /^-(delete|exec|execdir|ok|okdir|fprint|fprintf|fls)$/.test(t))) return false
    if (head === 'sort' && toks.some(t => /^-o/.test(t))) return false
    if (head === 'command' && toks[1] !== undefined && !/^-?v$/.test(toks[1])) return false
  }
  return true
}

function denyText(what: string): string {
  return `[confirm-first] 사용자 승인 전이라 ${what} 은(는) 차단됨. 재시도 말 것. 맥락은 Read/Grep/Glob/읽기전용 Bash 로 파악하고, 요청 해석+진행 계획을 AskUserQuestion 으로 확인받을 것 (첫 옵션 "${APPROVE_LABEL}").`
}

const PHASE_TEXT: Record<Phase, string> = {
  idle: 'confirm-first ON',
  armed: 'confirm-first: 확인 중',
  asked: 'confirm-first: 승인 대기',
}

// Status line text for a phase; undefined clears it while the mode is off.
function label(active: boolean, current: Phase): string | undefined {
  return active ? PHASE_TEXT[current] : undefined
}

// Picking any offered option except a "revise" one approves; free text approves
// only when it reads as a yes.
const REVISE = /^(해석 수정|수정|아니|취소|중단)/

function isApproving(answer: string, labels: readonly string[]): boolean {
  const a = answer.trim()
  if (a.startsWith(APPROVE_LABEL) || APPROVE_REPLY.test(a)) return true

  return labels.includes(a) && !REVISE.test(a)
}

function wasApproved(ran: { result?: unknown; text?: string }, labels: readonly string[]): boolean {
  const result = ran.result as { answers?: Record<string, unknown> } | undefined
  if (result && typeof result.answers === 'object' && result.answers !== null) {
    return Object.values(result.answers).some(v => isApproving(String(v), labels))
  }
  return typeof ran.text === 'string' && ran.text.includes(`="${APPROVE_LABEL}`)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'confirm-first',
      description: '의도 확인 모드: 요청 해석·계획을 확인받은 뒤에만 변경 작업 진행',
      argumentHint: 'on | off | status',
    })
    const saved = await $.store.get('isOn')
    await $.state.set(isOn, saved === true)
    await $.state.set(phase, 'idle')
    $.ui.status(label(saved === true, 'idle'))
    // diagnostic: which surface does this host report?
    $.ui.toast(`confirm-first mod loaded (surface: ${e.surface ?? 'none'})`)

    return next(e)
  })

  on('command.run', { command: 'confirm-first' }, async ($, e) => {
    const { value: wasOn = false } = await $.state.get(isOn)
    const arg = e.args.trim().toLowerCase()
    const want = arg === 'on' ? true : arg === 'off' ? false : arg === '' ? !wasOn : null

    if (want === null && arg !== 'status') {
      return { text: '사용법: /confirm-first on | off | status' }
    }
    if (want !== null) {
      await $.store.set('isOn', want)
      await $.state.set(isOn, want)
      await $.state.set(phase, 'idle')
      $.ui.toast(`confirm-first ${want ? 'ON' : 'OFF'}`)
    }
    const now = want ?? wasOn
    $.ui.status(label(now, 'idle'))

    return {
      text: `confirm-first: ${now ? 'ON (새 요청부터 해석·계획 확인 후 진행)' : 'OFF'}`,
    }
  })

  on('prompt.submit', async ($, e, next) => {
    const { value: active = false } = await $.state.get(isOn)
    const isPerson = e.origin.kind === 'composer' || e.origin.kind === 'bridge'
    if (!active || !isPerson) return next(e)

    const { value: current = 'idle' } = await $.state.get(phase)
    let note: string
    if (current === 'asked' && APPROVE_REPLY.test(e.text)) {
      await $.state.set(phase, 'idle')
      $.ui.status(label(true, 'idle'))
      $.ui.toast(PHASE_TEXT['idle'])
      note = APPROVED_TEXT
    } else if (current === 'asked') {
      note = REPLY_TEXT
    } else {
      await $.state.set(phase, 'armed')
      $.ui.status(label(true, 'armed'))
      $.ui.toast(PHASE_TEXT['armed'])
      note = ARM_TEXT
    }

    return next({ ...e, context: [...(e.context ?? []), note] })
  })

  on('tool.call', async ($, e, next) => {
    const { value: active = false } = await $.state.get(isOn)
    if (!active) return next(e)
    const { value: current = 'idle' } = await $.state.get(phase)
    if (current === 'idle') return next(e)

    const tool = String(e.tool)

    if (e.tool === 'AskUserQuestion') {
      const labels = e.questions.flatMap(q => q.options.map(o => o.label))
      await $.state.set(phase, 'asked')
      $.ui.status(label(true, 'asked'))
      if (current !== 'asked') $.ui.toast(PHASE_TEXT['asked'])
      const ran = await next(e)
      if (ran.deny === undefined && ran.isError !== true && wasApproved(ran, labels)) {
        await $.state.set(phase, 'idle')
        $.ui.status(label(true, 'idle'))
        $.ui.toast(PHASE_TEXT['idle'])
      }

      return ran
    }
    if (e.tool === 'Bash') {
      return isReadOnlyBash(e.command) ? next(e) : { deny: denyText('변경 가능성이 있는 Bash 명령') }
    }
    if (DENY_TOOLS.has(tool)) return { deny: denyText(tool) }
    if (tool.startsWith('mcp__') && !MCP_READ.test(tool.split('__').pop() ?? '')) {
      return { deny: denyText(`MCP 도구 ${tool}`) }
    }

    return next(e)
  }).catch(($, e, next) => next(e))

  // A one-row band above the prompt: status dot, name, hint, and a click-to-toggle button.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const { value: active = false } = await $.state.get(isOn)
    const { value: current = 'idle' } = await $.state.get(phase)
    const { Box, Text, Button } = $.ui.resolve(e)
    const color = !active ? 'inactive' : current === 'armed' ? 'warning' : current === 'asked' ? 'suggestion' : 'success'
    const hint = !active ? '꺼짐' : current === 'armed' ? '요청 해석을 확인하는 중' : current === 'asked' ? '답변을 기다리는 중' : '변경 전에 해석·계획을 확인합니다'

    return (
      <Box gap={1}>
        <Text color={color}>●</Text>
        <Text bold={active} dimColor={!active}>
          confirm-first
        </Text>
        <Text dimColor>{hint}</Text>
        <Button
          key="toggle"
          label={active ? '끄기' : '켜기'}
          onPress={async () => {
            await $.store.set('isOn', !active)
            await $.state.set(isOn, !active)
            await $.state.set(phase, 'idle')
            $.ui.status(label(!active, 'idle'))
          }}
        />
      </Box>
    )
  })

  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    const { value: active = false } = await $.state.get(isOn)
    if (!active) return next(e)
    const { value: current = 'idle' } = await $.state.get(phase)
    const label = current === 'idle' ? 'confirm-first' : current === 'asked' ? 'confirm-first: 승인 대기' : 'confirm-first: 확인 중'

    return next({ ...e, props: { ...e.props, modes: [...e.props.modes, label] } })
  })
}
