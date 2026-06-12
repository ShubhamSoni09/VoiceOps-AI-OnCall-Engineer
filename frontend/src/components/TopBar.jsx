import { MoonStars, Sun } from '@phosphor-icons/react'

export default function TopBar({ theme, onToggleTheme }) {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="logo">V</div>
        <b>VoiceOps</b>
        <span className="tag">on-call console</span>
      </div>

      <div className="statusbar">
        <span className="pill crit"><span className="dot" />1 critical</span>
        <span className="pill act"><span className="dot" />2 active</span>
        <span className="pill ok"><span className="dot" />error budget 87.3%</span>
      </div>

      <div className="topright">
        <button
          className="icon-btn"
          onClick={onToggleTheme}
          title="Toggle light / dark"
          aria-label="Toggle theme"
        >
          {theme === 'dark' ? <MoonStars size={18} /> : <Sun size={18} />}
        </button>
        <div className="oncall">
          <span className="who">
            <small>on-call</small>
            <span>Priya Nair</span>
          </span>
          <span className="avatar">PN</span>
        </div>
      </div>
    </header>
  )
}
