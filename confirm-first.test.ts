import { expect, test } from 'claude-code/testing'

import { isReadOnlyBash } from './hooks/register'

const PRESENTATION = { isFullscreen: false, columns: 80 }
const COMPOSER = { kind: 'composer' } as const
const START = { cwd: '/tmp', surface: 'terminal', isInteractive: true, source: 'startup' }

test('read-only Bash is told from mutating Bash', () => {
  for (const cmd of ['ls -la', 'cat a | head', 'rg "a|b" src', 'find . -name "*.js"', 'git status', 'git diff HEAD~1 && ls', 'rtk git status', 'cd x && ls']) {
    expect(isReadOnlyBash(cmd)).toBe(true)
  }
  for (const cmd of ['ls > x', 'git status && rm x', 'find . -delete', 'echo $(rm x)', 'git commit -m a', 'npm install', 'sed -i s/a/b/ f', 'git branch -D x', 'cat a >> b', 'rm -rf x', 'find . -exec rm {} ;']) {
    expect(isReadOnlyBash(cmd)).toBe(false)
  }
})

test('armed: changes denied, reads pass, approval answer unlocks edits', async ($, on) => {
  on('tool.call', (_$, e) =>
    e.tool === 'AskUserQuestion'
      ? ({ result: { questions: [], answers: { q: '이대로 진행' } } } as never)
      : ({ result: 'ok' } as never),
  )
  on('prompt.submit', (_$, e) => ({ text: e.text, context: e.context }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: undefined }) as never)
  on('store.get', () => ({ value: undefined }) as never)
  on('store.set', () => ({ value: undefined }) as never)
  on('command.run', () => ({ text: 'engine' }))

  await $.session.start(START as never)
  const on1 = await $.command.run({ command: 'confirm-first', args: 'on', origin: COMPOSER, presentation: PRESENTATION })
  expect(on1.text).toContain('ON')

  const submitted = await $.prompt.submit({ text: 'fix the bug', origin: COMPOSER, wait: false })
  expect(String(submitted.context?.[0])).toContain('CONFIRM-FIRST')

  const edit = await $.tool.call({ tool: 'Edit', file_path: '/x', old_string: 'a', new_string: 'b' } as never)
  expect(edit.deny).toContain('confirm-first')
  const read = await $.tool.call({ tool: 'Read', file_path: '/x' } as never)
  expect(read.deny).toBeUndefined()
  const rm = await $.tool.call({ tool: 'Bash', command: 'rm -rf x' } as never)
  expect(rm.deny).toContain('confirm-first')
  const ls = await $.tool.call({ tool: 'Bash', command: 'ls -la' } as never)
  expect(ls.deny).toBeUndefined()

  await $.tool.call({ tool: 'AskUserQuestion', questions: [] } as never)
  const edit2 = await $.tool.call({ tool: 'Edit', file_path: '/x', old_string: 'a', new_string: 'b' } as never)
  expect(edit2.deny).toBeUndefined()
})

test('a bare assignment does not block a read-only command', () => {
  expect(isReadOnlyBash('F=/tmp/x; grep -n a $F')).toBe(true)
  expect(isReadOnlyBash('F=$(rm x); ls')).toBe(false)
  expect(isReadOnlyBash('F=a; rm $F')).toBe(false)
})

test('choosing a non-revise option approves; the revise option does not', async ($, on) => {
  const answer = { current: '진단만 먼저' }
  on('tool.call', (_$, e) =>
    e.tool === 'AskUserQuestion'
      ? ({ result: { questions: [], answers: { q: answer.current } } } as never)
      : ({ result: 'ok' } as never),
  )
  on('prompt.submit', (_$, e) => ({ text: e.text, context: e.context }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: undefined }) as never)
  on('store.get', () => ({ value: undefined }) as never)
  on('store.set', () => ({ value: undefined }) as never)
  on('command.run', () => ({ text: 'engine' }))

  const ask = {
    tool: 'AskUserQuestion',
    questions: [{ question: 'q', header: 'h', multiSelect: false, options: [{ label: '진단만 먼저', description: '' }, { label: '해석 수정', description: '' }] }],
  } as never
  const edit = { tool: 'Edit', file_path: '/x', old_string: 'a', new_string: 'b' } as never

  await $.session.start(START as never)
  await $.command.run({ command: 'confirm-first', args: 'on', origin: COMPOSER, presentation: PRESENTATION })

  await $.prompt.submit({ text: 'one', origin: COMPOSER, wait: false })
  answer.current = '해석 수정'
  await $.tool.call(ask)
  expect((await $.tool.call(edit)).deny).toContain('confirm-first')

  answer.current = '진단만 먼저'
  await $.tool.call(ask)
  expect((await $.tool.call(edit)).deny).toBeUndefined()
})

test('off: nothing is blocked', async ($, on) => {
  on('tool.call', () => ({ result: 'ok' }) as never)
  on('prompt.submit', (_$, e) => ({ text: e.text, context: e.context }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: undefined }) as never)
  on('store.get', () => ({ value: undefined }) as never)
  on('store.set', () => ({ value: undefined }) as never)
  on('command.run', () => ({ text: 'engine' }))

  await $.session.start(START as never)
  await $.prompt.submit({ text: 'fix the bug', origin: COMPOSER, wait: false })
  const edit = await $.tool.call({ tool: 'Edit', file_path: '/x', old_string: 'a', new_string: 'b' } as never)
  expect(edit.deny).toBeUndefined()
})

test('a free-text "ok" reply after the question approves', async ($, on) => {
  on('tool.call', (_$, e) =>
    e.tool === 'AskUserQuestion'
      ? ({ result: { questions: [], answers: { q: '해석 수정' } } } as never)
      : ({ result: 'ok' } as never),
  )
  on('prompt.submit', (_$, e) => ({ text: e.text, context: e.context }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: undefined }) as never)
  on('store.get', () => ({ value: undefined }) as never)
  on('store.set', () => ({ value: undefined }) as never)
  on('command.run', () => ({ text: 'engine' }))

  await $.session.start(START as never)
  await $.command.run({ command: 'confirm-first', args: 'on', origin: COMPOSER, presentation: PRESENTATION })
  await $.prompt.submit({ text: 'do it', origin: COMPOSER, wait: false })
  await $.tool.call({ tool: 'AskUserQuestion', questions: [] } as never)
  const still = await $.tool.call({ tool: 'Edit', file_path: '/x', old_string: 'a', new_string: 'b' } as never)
  expect(still.deny).toContain('confirm-first')

  const reply = await $.prompt.submit({ text: '응', origin: COMPOSER, wait: false })
  expect(String(reply.context?.[0])).toContain('승인')
  const after = await $.tool.call({ tool: 'Edit', file_path: '/x', old_string: 'a', new_string: 'b' } as never)
  expect(after.deny).toBeUndefined()
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`the band above the prompt toggles the mode on ${surface}`, async ($, on) => {
    on('session.start', (_$, e) => ({ cwd: e.cwd }))
    on('command.register', () => ({ value: undefined }) as never)
    on('store.get', () => ({ value: undefined }) as never)
    on('store.set', () => ({ value: undefined }) as never)

    await $.session.start(START as never)
    const ui = await $.ui.mount({
      plugin: 'confirm-first',
      surface,
      component: 'AbovePrompt',
      props: { hasSurvey: false, isWorking: false, maxRows: 5, bodyColumns: 80 } as never,
    })
    expect(await ui.find({ type: 'Button', key: 'toggle', text: '켜기' })).toBeDefined()

    await ui.press({ key: 'toggle' })
    expect(await ui.find({ type: 'Button', key: 'toggle', text: '끄기' })).toBeDefined()
  })
}
