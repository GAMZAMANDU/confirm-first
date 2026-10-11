export type Phase = 'idle' | 'armed' | 'asked'

declare module 'claude-code' {
  interface PluginState {
    'confirm-first': { isOn: boolean; phase: Phase }
  }
}
