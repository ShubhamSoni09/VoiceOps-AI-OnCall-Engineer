import { describe, expect, it } from 'vitest'
import {
  applySpeakerAttribution,
  buildLiveAudioPayload,
  chooseSupportedAudioMimeType,
  nextCaptionHint,
  removeInstantCaption,
  upsertInstantCaption,
  upsertLivePartial,
} from './liveTranscript.js'

const NOW = '10:24 AM'

describe('live transcript reducer', () => {
  it('updates one provisional row per temp id', () => {
    const first = upsertLivePartial([], { temp_id: 's1-1', text: 'hello' }, NOW)
    const second = upsertLivePartial(first, { temp_id: 's1-1', text: 'hello team' }, NOW)

    expect(second).toHaveLength(1)
    expect(second[0]).toMatchObject({
      id: 'live-s1-1',
      text: 'hello team',
      draft: true,
      speakerLabel: 'calibrating',
    })
  })

  it('does not let stale partials overwrite final attribution', () => {
    const partial = upsertLivePartial([], { temp_id: 's1-1', text: 'draft text' }, NOW)
    const final = applySpeakerAttribution(
      partial,
      {
        temp_id: 's1-1',
        segment: {
          speaker_label: 'SPEAKER_00',
          identified_user_name: 'Alice',
          text: 'final text',
          confidence: 0.82,
          identity_source: 'manual',
          identity_confidence: 0.95,
        },
      },
      NOW,
    )
    const stale = upsertLivePartial(final, { temp_id: 's1-1', text: 'older draft' }, NOW)

    expect(stale).toHaveLength(1)
    expect(stale[0]).toMatchObject({
      name: 'Alice',
      text: 'final text',
      draft: false,
      speakerLabel: 'SPEAKER_00',
      identitySource: 'manual',
      identityConfidence: 0.95,
    })
  })

  it('creates a final row when attribution arrives before a partial', () => {
    const messages = applySpeakerAttribution(
      [],
      {
        temp_id: 's1-2',
        segment: {
          speaker_label: 'SPEAKER_01',
          text: 'Bob joined late',
          confidence: 0.74,
        },
      },
      NOW,
    )

    expect(messages).toHaveLength(1)
    expect(messages[0]).toMatchObject({
      id: 'live-s1-2',
      name: 'Unknown speaker',
      role2: 'speaker attribution',
      text: 'Bob joined late',
      draft: false,
    })
  })

  it('removes browser captions when backend attribution arrives', () => {
    const captions = upsertInstantCaption([], 'local browser caption', NOW)
    const attributed = applySpeakerAttribution(
      captions,
      {
        temp_id: 's1-3',
        segment: {
          speaker_label: 'SPEAKER_00',
          identified_user_name: 'Priya',
          text: 'backend transcript',
        },
      },
      NOW,
    )

    expect(attributed).toHaveLength(1)
    expect(attributed[0].name).toBe('Priya')
    expect(removeInstantCaption(captions)).toEqual([])
  })

  it('creates caption hints as transcript deltas for local mock live mode', () => {
    const first = nextCaptionHint('Alice opened app.py', '')
    const second = nextCaptionHint('Alice opened app.py and found the health route', first.nextSent)
    const duplicate = nextCaptionHint('Alice opened app.py and found the health route', second.nextSent)
    const restarted = nextCaptionHint('Bob says VoiceOps what did we decide', second.nextSent)

    expect(first).toEqual({
      debugText: 'Alice opened app.py',
      nextSent: 'Alice opened app.py',
    })
    expect(second).toEqual({
      debugText: 'and found the health route',
      nextSent: 'Alice opened app.py and found the health route',
    })
    expect(duplicate).toEqual({
      debugText: null,
      nextSent: 'Alice opened app.py and found the health route',
    })
    expect(restarted).toEqual({
      debugText: 'Bob says VoiceOps what did we decide',
      nextSent: 'Bob says VoiceOps what did we decide',
    })
  })

  it('adds browser caption hints only for the mock live provider', () => {
    const mock = buildLiveAudioPayload({
      sequence: 3,
      mimeType: 'audio/webm',
      audioBase64: 'AAAA',
      provider: 'mock',
      captionText: 'Alice said fix app.py',
      lastCaptionSent: '',
    })
    const whisperx = buildLiveAudioPayload({
      sequence: 4,
      mimeType: 'audio/webm',
      audioBase64: 'BBBB',
      provider: 'whisperx',
      captionText: 'Alice said fix app.py',
      lastCaptionSent: '',
    })

    expect(mock.payload).toMatchObject({
      type: 'audio_chunk',
      sequence: 3,
      mime_type: 'audio/webm',
      audio_base64: 'AAAA',
      debug_text: 'Alice said fix app.py',
    })
    expect(mock.nextCaptionSent).toBe('Alice said fix app.py')
    expect(whisperx.payload).toEqual({
      type: 'audio_chunk',
      sequence: 4,
      mime_type: 'audio/webm',
      audio_base64: 'BBBB',
    })
    expect(whisperx.nextCaptionSent).toBe('')
  })

  it('selects the first supported recording MIME type', () => {
    const mediaRecorder = {
      isTypeSupported: (candidate) => candidate === 'audio/mp4',
    }

    expect(chooseSupportedAudioMimeType(mediaRecorder)).toBe('audio/mp4')
  })

  it('falls back to browser default recorder MIME when no candidate is supported', () => {
    const mediaRecorder = {
      isTypeSupported: () => false,
    }

    expect(chooseSupportedAudioMimeType(mediaRecorder)).toBe('')
    expect(chooseSupportedAudioMimeType(null)).toBe('')
  })
})
