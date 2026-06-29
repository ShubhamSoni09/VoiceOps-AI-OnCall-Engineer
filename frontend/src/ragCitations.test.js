import { describe, expect, it } from 'vitest'
import { citationMeta, citationSourceLabel, ragTraceItems, uniqueCitations } from './ragCitations.js'

describe('RAG citation helpers', () => {
  it('labels short and long memory tiers in citation metadata', () => {
    expect(citationMeta({
      source: 'memory',
      actor_name: 'Priya Nair',
      score: 1013,
      metadata: { memory_tier: 'short' },
    })).toBe('Short memory / Priya Nair / 1013 match')

    expect(citationMeta({
      source: 'timeline',
      actor_name: 'Sam Ortiz',
      score: 214,
      metadata: { memory_tier: 'long' },
    })).toBe('Long memory / Sam Ortiz / 214 match')

    expect(citationMeta({
      source: 'ontology',
      score: 88,
      metadata: { memory_tier: 'ontology' },
    })).toBe('Ontology / 88 match')
    expect(citationSourceLabel('ontology')).toBe('Ontology')
  })

  it('builds compact retrieval trace chips', () => {
    const trace = ragTraceItems({
      provider: 'local_sparse',
      short_memory_hits: 3,
      long_memory_hits: 9,
      ontology_hits: 2,
      indexed_documents: 14,
      candidate_count: 6,
      memory_tiers: { short: 2, long: 1, ontology: 1 },
      sources: { ontology: 1 },
    })

    expect(trace).toEqual([
      'provider local_sparse',
      'short 3',
      'long 9',
      'ontology 2',
      'docs 14',
      'candidates 6',
      'shown 2/1/1',
      'sources ontology 1',
    ])
  })

  it('deduplicates citations by visible evidence before source ids', () => {
    const citations = [
      { source: 'memory', source_id: 'm-1', title: 'Decision', actor_name: 'Priya Nair', excerpt: 'Use app.py.' },
      { source: 'timeline', source_id: 't-9', title: 'Timeline', actor_name: 'Priya Nair', excerpt: 'Use app.py.' },
      { source: 'memory', source_id: 'm-2', title: 'Task', actor_name: 'Sam Ortiz', excerpt: 'Fix app.py.' },
      { source: 'memory', source_id: 'm-2', title: 'Task duplicate', actor_name: 'Sam Ortiz', excerpt: 'Different title is still same id.' },
    ]

    expect(uniqueCitations(citations)).toEqual([
      citations[0],
      citations[2],
      citations[3],
    ])
  })
})
