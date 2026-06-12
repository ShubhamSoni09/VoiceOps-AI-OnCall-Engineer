import { MoonStars, Sun, SignOut } from '@phosphor-icons/react'
import { clearToken } from '../api.js'

export default function TopBar({ theme, onToggleTheme, user, statusCounts, workspace }) {
  const crit = statusCounts?.critical || 0
  const act = statusCounts?.active || 0

  let pills
  if (!workspace?.connected) {
    pills = <span className="pill">No repo connected</span>
  } else if (crit === 0 && act === 0) {
    pills = <span className="pill ok"><span className="dot" />All clear</span>
  } else {
    pills = (
      <>
        {crit > 0 && <span className="pill crit"><span className="dot" />{crit} critical</span>}
        {act > 0 && <span className="pill act"><span className="dot" />{act} active</span>}
      </>
    )
  }

  return (
    <header className="topbar">
      <div className="brand">
        <div className="logo">V</div>
        <b>VoiceOps</b>
        <span className="tag">on-call console</span>
      </div>

      <div className="statusbar">{pills}</div>

      <div className="topright">
        <button className="icon-btn" onClick={onToggleTheme} title="Toggle light / dark" aria-label="Toggle theme">
          {theme === 'dark' ? <MoonStars size={18} /> : <Sun size={18} />}
        </button>
        {user && (
          <div className="oncall">
            <span className="who">
              <small>{user.role_label}</small>
              <span>{user.name}</span>
            </span>
            <span className="avatar">{user.initials}</span>
          </div>
        )}
        <button className="icon-btn" onClick={() => { clearToken(); window.location.reload() }} title="Sign out">
          <SignOut size={18} />
        </button>
      </div>
    </header>
  )
}
