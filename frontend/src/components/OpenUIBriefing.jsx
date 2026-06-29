import { Renderer } from '@openuidev/react-lang'
import { voiceopsLibrary } from '../openui/library.jsx'
import { WORKSPACE_BRIEFING } from '../openui/sample.js'

/**
 * The agent's workspace briefing, rendered by the OpenUI runtime from OpenUI Lang
 * (not hand-written JSX). Swap WORKSPACE_BRIEFING for a streamed LLM response and
 * flip isStreaming to true for the live generative path.
 */
export default function OpenUIBriefing() {
  return (
    <Renderer
      response={WORKSPACE_BRIEFING}
      library={voiceopsLibrary}
      isStreaming={false}
    />
  )
}
