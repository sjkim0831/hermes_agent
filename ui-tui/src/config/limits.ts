export const LARGE_PASTE = { chars: 8000, lines: 80 }
export const LONG_MSG = 300
// Keep the live transcript bounded more aggressively. The TUI already has
// persistent session storage, so retaining hundreds of rendered messages in
// memory only increases renderer pressure during long runs.
export const MAX_HISTORY = 300
export const MAX_REASONING_CHARS = 24000
export const MAX_STREAM_SEGMENTS = 24
export const THINKING_COT_MAX = 160
export const WHEEL_SCROLL_STEP = 3
