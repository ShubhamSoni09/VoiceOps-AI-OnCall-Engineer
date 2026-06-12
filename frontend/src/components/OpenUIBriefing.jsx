import { Renderer } from '@openuidev/react-lang'
import { voiceopsLibrary } from '../openui/library.jsx'
import { INCIDENT_BRIEFING } from '../openui/sample.js'

/**
 * The agent's incident briefing, rendered by the OpenUI runtime from OpenUI Lang
 * (not hand-written JSX). Swap INCIDENT_BRIEFING for a streamed LLM response and
 * flip isStreaming to true for the live generative path.
 */
export default function OpenUIBriefing() {
  return (
    <Renderer
      response={INCIDENT_BRIEFING}
      library={voiceopsLibrary}
      isStreaming={false}
    />
  )
}
