import { describe, expect, it } from 'vitest'
import { buildRoomStatus, resolveRoomId } from './App.jsx'

describe('buildRoomStatus', () => {
  it('keeps the room ready while the work dashboard is still syncing', () => {
    expect(buildRoomStatus({
      collab: { id: 'main' },
      workDashboard: null,
      roomSyncStatus: 'connecting',
    })).toEqual({
      state: 'ready',
      message: 'work dashboard syncing',
      dashboardSyncing: true,
    })
  })

  it('only shows room loading before the collaboration snapshot exists', () => {
    expect(buildRoomStatus({
      collab: null,
      workDashboard: null,
      roomSyncStatus: 'connecting',
    })).toEqual({
      state: 'checking',
      message: 'timeline syncing',
    })
  })

  it('preserves hard sync errors', () => {
    expect(buildRoomStatus({
      loadError: 'Join room failed',
      collab: { id: 'main' },
      workDashboard: {},
      roomSyncStatus: 'live',
    })).toEqual({
      state: 'issue',
      message: 'Join room failed',
    })
  })
})

describe('resolveRoomId', () => {
  it('uses main by default', () => {
    expect(resolveRoomId({ search: '' })).toBe('main')
  })

  it('reads a safe room id from the URL', () => {
    expect(resolveRoomId({ search: '?room_id=alpha-team_1' })).toBe('alpha-team_1')
  })

  it('keeps a short room alias for older links', () => {
    expect(resolveRoomId({ search: '?room=beta.room' })).toBe('beta.room')
  })

  it('rejects path-like or empty room ids', () => {
    expect(resolveRoomId({ search: '?room_id=../secret' })).toBe('main')
    expect(resolveRoomId({ search: '?room_id=' })).toBe('main')
  })
})
