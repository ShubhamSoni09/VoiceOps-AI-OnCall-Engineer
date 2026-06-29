export const APP_NAME = (import.meta.env.VITE_APP_NAME || 'VoiceOps').trim() || 'VoiceOps'
export const APP_INITIAL = APP_NAME.trim().charAt(0).toUpperCase() || 'V'
export const APP_CONSOLE_TITLE = `${APP_NAME} · Team Console`
