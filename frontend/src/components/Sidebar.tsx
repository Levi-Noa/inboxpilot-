interface SidebarProps {
  gmailConnected: boolean
  connecting?: boolean
  connectError?: string | null
  onAuthenticateGmail: () => void
  onNewThread: () => void
  sessionTopic: string
  onCloseMobile: () => void
  isDark: boolean
}

export default function Sidebar({
  gmailConnected,
  connecting = false,
  connectError = null,
  onAuthenticateGmail,
  onNewThread,
  sessionTopic,
  onCloseMobile,
  isDark,
}: SidebarProps) {
  return (
    <aside className="w-[290px] md:w-[320px] panel-glass text-slate-700 p-5 md:p-6 flex flex-col rounded-2xl h-full lg:h-[calc(100vh-3rem)] float-in">
      <div className="flex items-start justify-between gap-3 mb-6">
        <div>
          <p className="text-[11px] uppercase tracking-[0.2em] text-slate-400">Agent Console</p>
          <h1 className="title-font text-2xl md:text-3xl font-bold text-slate-800">InboxPilot</h1>
          <p className="text-xs text-slate-400 mt-1"></p>
        </div>
        <button
          type="button"
          onClick={onCloseMobile}
          className="lg:hidden px-2 py-1 text-xs rounded-md border border-slate-300 text-slate-600"
        >
          Close
        </button>
      </div>

      <div className="panel-glass rounded-2xl p-4 mb-4">
        <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400 mb-2">Connection</p>
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-700">Gmail Access</span>
          <span
            className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs font-semibold border ${
              gmailConnected
                ? isDark
                  ? 'bg-emerald-500/20 text-emerald-200 border-emerald-400/30'
                  : 'bg-emerald-200 text-emerald-900 border-emerald-400'
                : isDark
                  ? 'bg-rose-500/20 text-rose-200 border-rose-400/30'
                  : 'bg-rose-200 text-rose-900 border-rose-400'
            }`}
          >
            <span className={`h-2 w-2 rounded-full ${gmailConnected ? 'bg-emerald-500' : 'bg-rose-500'}`} />
            {gmailConnected ? 'Connected' : 'Offline'}
          </span>
        </div>
        {!gmailConnected && (
          <>
            <button
              type="button"
              onClick={onAuthenticateGmail}
              disabled={connecting}
              className="mt-3 w-full rounded-xl bg-gradient-to-r from-teal-500 to-cyan-400 text-slate-900 font-semibold py-2.5 hover:brightness-110 transition disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {connecting ? 'Connecting…' : 'Connect Gmail'}
            </button>
            {connectError && (
              <p className="mt-2 text-xs text-rose-600 break-words">{connectError}</p>
            )}
          </>
        )}
      </div>



      <div className="panel-glass rounded-2xl p-4 mb-4">
        <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400 mb-2">Session</p>
        <p className="text-xs text-slate-600 leading-relaxed break-words">{sessionTopic || 'New Session'}</p>
      </div>

      <button
        onClick={onNewThread}
        className="w-full rounded-xl py-3 title-font font-bold tracking-wide bg-gradient-to-r from-amber-300 to-orange-300 text-slate-900 hover:scale-[1.01] transition-transform"
      >
        Start New Chat
      </button>

      <div className="mt-auto pt-5 text-xs text-slate-400 flex items-center justify-between">
      </div>
    </aside>
  )
}