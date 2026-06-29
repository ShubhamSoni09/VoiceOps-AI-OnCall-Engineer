export function citationSourceLabel(source) {
  const labels = {
    memory: 'Memory',
    message: 'Timeline',
    timeline: 'Timeline',
    action: 'Action',
    code: 'Code',
    ontology: 'Ontology',
  }
  return labels[source] || String(source || 'Source')
}

export function citationScoreLabel(score) {
  if (typeof score !== 'number' || Number.isNaN(score)) return ''
  return `${Math.round(score)} match`
}

export function citationTierLabel(citation) {
  const tier = citation?.metadata?.memory_tier
  if (tier === 'short') return 'Short memory'
  if (tier === 'long') return 'Long memory'
  if (tier === 'ontology') return 'Ontology'
  return citationSourceLabel(citation?.source)
}

export function citationTitle(citation) {
  return (citation?.title || citationSourceLabel(citation?.source)).replace(/_/g, ' ')
}

export function citationMeta(citation) {
  return [
    citationTierLabel(citation),
    citation?.actor_name,
    citationScoreLabel(citation?.score),
  ].filter(Boolean).join(' / ')
}

export function citationKey(citation, index = 0) {
  return [
    citation?.source || 'source',
    citation?.source_id || citation?.id || citation?.title || index,
  ].join(':')
}

function citationIdentity(citation, index = 0) {
  if (!citation || typeof citation !== 'object') return `empty:${index}`
  const excerpt = String(citation.excerpt || '').replace(/\s+/g, ' ').trim().toLowerCase()
  if (excerpt) return `evidence:${citation.actor_name || ''}:${excerpt}`
  if (citation.source_id || citation.id) return `${citation.source || 'source'}:${citation.source_id || citation.id}`
  return [
    citation.source || 'source',
    citation.title || '',
    citation.actor_name || '',
  ].join(':')
}

export function uniqueCitations(citations = []) {
  const seen = new Set()
  return (Array.isArray(citations) ? citations : []).filter((citation, index) => {
    const key = citationIdentity(citation, index)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function ragTraceItems(retrieval) {
  if (!retrieval || typeof retrieval !== 'object') return []
  const tiers = retrieval.memory_tiers || {}
  const sources = retrieval.sources || {}
  return [
    retrieval.provider && `provider ${retrieval.provider}`,
    retrieval.rag_provider && retrieval.rag_provider !== retrieval.provider && `rag ${retrieval.rag_provider}`,
    typeof retrieval.short_memory_hits === 'number' && `short ${retrieval.short_memory_hits}`,
    typeof retrieval.long_memory_hits === 'number' && `long ${retrieval.long_memory_hits}`,
    typeof retrieval.ontology_hits === 'number' && `ontology ${retrieval.ontology_hits}`,
    typeof retrieval.indexed_documents === 'number' && `docs ${retrieval.indexed_documents}`,
    typeof retrieval.candidate_count === 'number' && `candidates ${retrieval.candidate_count}`,
    (typeof tiers.short === 'number' || typeof tiers.long === 'number' || typeof tiers.ontology === 'number')
      ? `shown ${tiers.short || 0}/${tiers.long || 0}/${tiers.ontology || 0}`
      : null,
    typeof sources.ontology === 'number'
      ? `sources ontology ${sources.ontology}`
      : null,
  ].filter(Boolean)
}
