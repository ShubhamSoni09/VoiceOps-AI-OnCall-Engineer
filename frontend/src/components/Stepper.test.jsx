import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { IDLE_STEPS } from '../data.js'
import Stepper from './Stepper.jsx'

describe('Stepper', () => {
  it('renders the meeting closure flow instead of a deployment pipeline', () => {
    const html = renderToStaticMarkup(<Stepper steps={IDLE_STEPS} />)

    for (const label of ['Capture', 'Memory', 'Proposal', 'Approval', 'Tests', 'Branch', 'Handoff']) {
      expect(html).toContain(label)
    }
    expect(html).not.toContain('Deploy')
    expect(html).not.toContain('PR')
  })
})
