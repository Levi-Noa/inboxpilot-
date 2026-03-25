import { useRef, useEffect } from 'react'

interface InputBarProps {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  loading: boolean
}

export default function InputBar({
  value,
  onChange,
  onSend,
  loading,
}: InputBarProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px'
    }
  }, [value])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      e.preventDefault()
      onSend()
    }
  }

  return (
    <div className="border-t border-slate-200 bg-white/55 p-3 md:p-4">
      <div className="panel-glass rounded-2xl p-3 md:p-4">
        <div className="flex items-end gap-2 md:gap-3">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask the agent to find, read, summarize, or draft..."
            className="flex-1 p-3.5 border border-slate-300 bg-white text-slate-700 rounded-xl focus:outline-none focus:ring-2 focus:ring-teal-400 resize-none max-h-32"
            rows={1}
          />

          <button
            onClick={onSend}
            disabled={loading || !value.trim()}
            className="min-w-[58px] px-5 py-3 rounded-xl title-font font-bold bg-gradient-to-r from-teal-300 to-cyan-300 text-slate-900 hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed transition"
          >
            {loading ? '...' : 'Send'}
          </button>
        </div>
        <div className="mt-2.5 flex items-center justify-between text-[11px] text-slate-500">
          <span>Ctrl+Enter to send</span>
        </div>
      </div>
    </div>
  )
}