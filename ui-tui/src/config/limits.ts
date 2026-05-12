export const LARGE_PASTE = { chars: 8000, lines: 80 }
export const LONG_MSG = 300
// Keep the live transcript bounded more aggressively. The TUI already has
// persistent session storage, so retaining hundreds of rendered messages in
// memory only increases renderer pressure during long runs.
export const MAX_HISTORY = 300
export const MAX_HISTORY_CHARS = 1_200_000
export const MAX_MESSAGE_CHARS = 80_000
export const MAX_PANEL_SECTION_CHARS = 40_000
export const MAX_REASONING_CHARS = 24_000
export const MAX_STREAM_CHARS = 120_000
export const MAX_STREAM_SEGMENTS = 24
export const MAX_TOOL_CONTEXT_CHARS = 12_000
export const MAX_TRANSCRIPT_LOAD_CHARS = 200_000
export const MAX_TRANSCRIPT_MESSAGE_CHARS = 12_000
export const THINKING_COT_MAX = 160
export const WHEEL_SCROLL_STEP = 3
