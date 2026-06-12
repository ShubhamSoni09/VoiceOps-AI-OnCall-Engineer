import { useEffect, useState } from 'react'
import { fetchGithubRepos, selectGithubRepo } from '../api.js'

export default function RepoSelector({ open, onClose, onSelected, currentRepo }) {
  const [repos, setRepos] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [selecting, setSelecting] = useState(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError('')
    fetchGithubRepos()
      .then((data) => setRepos(data.repos || []))
      .catch((err) => setError(err.message || 'Could not load repositories'))
      .finally(() => setLoading(false))
  }, [open])

  async function choose(fullName) {
    setSelecting(fullName)
    setError('')
    try {
      await selectGithubRepo(fullName)
      onSelected?.(fullName)
      onClose?.()
    } catch (err) {
      setError(err.message || 'Could not select repository')
    } finally {
      setSelecting(null)
    }
  }

  if (!open) return null

  const filtered = repos.filter((r) => {
    const q = query.trim().toLowerCase()
    if (!q) return true
    return (
      r.full_name.toLowerCase().includes(q)
      || (r.description || '').toLowerCase().includes(q)
    )
  })

  return (
    <div className="repo-overlay" role="dialog" aria-modal="true">
      <div className="repo-modal">
        <div className="repo-modal-head">
          <h2>Select a repository</h2>
          <button type="button" className="repo-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <p className="repo-sub">
          Choose which GitHub repo VoiceOps should investigate, patch, and open PRs against.
        </p>
        <input
          className="repo-search"
          type="search"
          placeholder="Filter repositories…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {currentRepo && (
          <p className="repo-current">Current: <code>{currentRepo}</code></p>
        )}
        {error && <p className="repo-error">{error}</p>}
        {loading ? (
          <p className="repo-loading">Loading your repositories…</p>
        ) : (
          <ul className="repo-list">
            {filtered.map((repo) => (
              <li key={repo.full_name}>
                <button
                  type="button"
                  className="repo-item"
                  disabled={selecting === repo.full_name}
                  onClick={() => choose(repo.full_name)}
                >
                  <span className="repo-name">{repo.full_name}</span>
                  <span className="repo-meta">
                    {repo.private ? 'private' : 'public'}
                    {repo.description ? ` · ${repo.description}` : ''}
                  </span>
                </button>
              </li>
            ))}
            {!filtered.length && <li className="repo-empty">No repositories match your search.</li>}
          </ul>
        )}
      </div>
    </div>
  )
}
