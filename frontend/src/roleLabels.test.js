import { describe, expect, it } from 'vitest'
import { teammateRoleLabel } from './roleLabels.js'

describe('role labels', () => {
  it('normalizes legacy on-call role labels to teammate language', () => {
    expect(teammateRoleLabel('on-call')).toBe('teammate')
    expect(teammateRoleLabel('On-call engineer')).toBe('teammate')
    expect(teammateRoleLabel('')).toBe('teammate')
    expect(teammateRoleLabel('reviewer')).toBe('reviewer')
  })
})
