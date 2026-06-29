export const LIVE_RECONNECT_DELAYS = [500, 1200, 2500]

export function reconnectDelay(attempt, delays = LIVE_RECONNECT_DELAYS) {
  if (attempt < 1 || attempt > delays.length) return null
  return delays[attempt - 1]
}

export function canReconnect(attempt, delays = LIVE_RECONNECT_DELAYS) {
  return reconnectDelay(attempt, delays) !== null
}

export function reconnectMessage(attempt, delays = LIVE_RECONNECT_DELAYS) {
  const delay = reconnectDelay(attempt, delays)
  if (delay == null) return 'Live meeting disconnected. Type a command below.'
  return `Live meeting reconnecting (${attempt}/${delays.length})`
}
