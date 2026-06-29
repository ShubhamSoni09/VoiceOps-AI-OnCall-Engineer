import { describe, expect, it } from 'vitest'
import { mergeLocalDrafts, messagesFromRoom } from './timelineMessages.js'

describe('timeline message mapping', () => {
  it('maps system speaker audit messages as audit rows', () => {
    const messages = messagesFromRoom({
      messages: [
        {
          id: 'msg-audit',
          role: 'system',
          actor_name: 'System',
          source: 'system',
          text: 'Sam Ortiz corrected SPEAKER_00 from Priya Nair to Sam Ortiz.',
          created_at: '2026-06-17T10:00:00Z',
          metadata: { source: 'speaker_mapping', event: 'speaker_mapping_corrected' },
        },
      ],
    })

    expect(messages).toHaveLength(1)
    expect(messages[0]).toMatchObject({
      id: 'msg-audit',
      role: 'system',
      role2: 'audit',
      name: 'System',
      fromVoice: false,
      metadata: { source: 'speaker_mapping', event: 'speaker_mapping_corrected' },
    })
  })

  it('preserves demo gate result metadata for right-rail audit summaries', () => {
    const messages = messagesFromRoom({
      messages: [
        {
          id: 'msg-gate',
          role: 'agent',
          actor_name: 'VoiceOps',
          source: 'agent',
          text: 'Mock closure harness finished: passed.',
          created_at: '2026-06-18T13:00:00Z',
          metadata: {
            source: 'demo_gate_result',
            gate_id: 'mock_e2e',
            gate_job_id: 'gate-demo',
            gate_status: 'succeeded',
          },
        },
      ],
    })

    expect(messages[0].metadata).toMatchObject({
      source: 'demo_gate_result',
      gate_id: 'mock_e2e',
      gate_job_id: 'gate-demo',
      gate_status: 'succeeded',
    })
  })

  it('preserves RAG citations for agent answer rendering', () => {
    const messages = messagesFromRoom({
      messages: [
        {
          id: 'msg-rag',
          role: 'agent',
          actor_name: 'VoiceOps',
          source: 'agent',
          text: 'Files mentioned: app.py.',
          created_at: '2026-06-18T13:02:00Z',
          metadata: {
            source: 'rag_query',
            citations: [
              {
                source: 'memory',
                source_id: 'mem-1',
                title: 'code reference',
                excerpt: 'app.py',
                actor_name: 'Priya Nair',
                score: 12,
              },
            ],
            retrieval: {
              provider: 'local_sparse',
              short_memory_hits: 2,
              long_memory_hits: 4,
              indexed_documents: 9,
              candidate_count: 6,
            },
          },
        },
      ],
    })

    expect(messages[0]).toMatchObject({
      role: 'agent',
      codeReferences: [],
      citations: [
        {
          source: 'memory',
          source_id: 'mem-1',
          title: 'code reference',
          excerpt: 'app.py',
          actor_name: 'Priya Nair',
          score: 12,
        },
      ],
      ragRetrieval: {
        provider: 'local_sparse',
        short_memory_hits: 2,
        long_memory_hits: 4,
        indexed_documents: 9,
        candidate_count: 6,
      },
    })
  })

  it('preserves speaker attribution metadata after mapping correction', () => {
    const messages = messagesFromRoom({
      messages: [
        {
          id: 'msg-speaker',
          role: 'user',
          actor_name: 'Sam Ortiz',
          source: 'meeting_audio',
          text: 'Need to fix app.py',
          created_at: '2026-06-17T10:01:00Z',
          speaker_label: 'SPEAKER_00',
          confidence: 0.68,
          metadata: {
            identified_user_id: 'user-admin',
            identity_confidence: 0.97,
            identity_source: 'manual',
            original_actor_name: 'Unknown speaker',
          },
        },
      ],
    })

    expect(messages[0]).toMatchObject({
      role: 'user',
      name: 'Sam Ortiz',
      role2: 'identified speaker',
      fromVoice: true,
      speakerLabel: 'SPEAKER_00',
      confidence: 0.68,
      identityConfidence: 0.97,
      identitySource: 'manual',
    })
  })

  it('keeps only live drafts that have not been finalized by room messages', () => {
    const roomMessages = [
      { id: 'msg-final', liveTempId: 'live-1', text: 'Final text' },
    ]
    const previousMessages = [
      { id: 'draft-1', tempId: 'live-1', draft: true, text: 'Draft text' },
      { id: 'draft-2', tempId: 'live-2', draft: true, text: 'Still pending' },
      { id: 'normal', text: 'Old normal row' },
    ]

    const merged = mergeLocalDrafts(roomMessages, previousMessages)

    expect(merged.map((message) => message.id)).toEqual(['msg-final', 'draft-2'])
  })
})
