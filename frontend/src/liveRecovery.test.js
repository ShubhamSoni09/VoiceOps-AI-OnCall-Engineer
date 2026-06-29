import { describe, expect, it } from 'vitest'
import { canReconnect, reconnectDelay, reconnectMessage } from './liveRecovery.js'

describe('live recovery helpers', () => {
  it('returns bounded reconnect delays', () => {
    expect(reconnectDelay(1)).toBe(500)
    expect(reconnectDelay(2)).toBe(1200)
    expect(reconnectDelay(3)).toBe(2500)
    expect(reconnectDelay(4)).toBeNull()
  })

  it('reports reconnect availability', () => {
    expect(canReconnect(1)).toBe(true)
    expect(canReconnect(3)).toBe(true)
    expect(canReconnect(4)).toBe(false)
  })

  it('builds honest reconnect status messages', () => {
    expect(reconnectMessage(2)).toBe('Live meeting reconnecting (2/3)')
    expect(reconnectMessage(4)).toBe('Live meeting disconnected. Type a command below.')
  })
})
