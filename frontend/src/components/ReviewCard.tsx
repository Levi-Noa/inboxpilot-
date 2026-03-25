import { useRef, useState } from 'react'
import type { ReviewData } from '../types'

interface ReviewCardProps {
  data: ReviewData
  onDecision: (decision: 'yes' | 'save' | 'no', files?: File[]) => void
  onModify: (feedback: string) => void
  decided: boolean
}

export default function ReviewCard({ data, onDecision, onModify, decided }: ReviewCardProps) {
  const [attachments, setAttachments] = useState<File[]>([])
  const [modifyMode, setModifyMode] = useState(false)
  const [modifyText, setModifyText] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const incoming = Array.from(e.target.files || [])
    if (!incoming.length) return
    setAttachments(prev => {
      const existingKeys = new Set(prev.map(f => `${f.name}-${f.size}`))
      const added = incoming.filter(f => !existingKeys.has(`${f.name}-${f.size}`))
      return [...prev, ...added]
    })
    e.target.value = ''
  }

  const removeFile = (idx: number) => setAttachments(prev => prev.filter((_, i) => i !== idx))

  return (
    <section className="my-4 panel-glass rounded-2xl p-5 md:p-6 fade-up border border-teal-300/40">
      <p className="text-xs uppercase tracking-[0.18em] text-teal-600 mb-3 font-semibold">
        Draft Ready to Send
      </p>

      {/* Metadata */}
      <div className="space-y-1.5 mb-4 text-sm">
        <div className="flex gap-2">
          <span className="text-slate-400 w-16 flex-shrink-0">To:</span>
          <span className="text-slate-700 font-medium break-all">{data.to}</span>
        </div>
        <div className="flex gap-2">
          <span className="text-slate-400 w-16 flex-shrink-0">Subject:</span>
          <span className="text-slate-700 font-medium">{data.subject}</span>
        </div>
      </div>

      {/* Draft body */}
      <div className="bg-white/70 border border-slate-200 rounded-xl px-4 py-3 mb-4 text-sm text-slate-700 whitespace-pre-wrap leading-relaxed max-h-52 overflow-y-auto">
        {data.draft || '(empty draft)'}
      </div>

      {/* Attachments */}
      <div className="mb-4">
        {!decided && (
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="text-xs text-teal-600 border border-teal-300/60 rounded-lg px-3 py-1.5 hover:bg-teal-50 transition"
          >
            📎 Attach files
          </button>
        )}
        <input ref={fileInputRef} type="file" multiple onChange={handleFiles} className="hidden" />
        
        {attachments.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {attachments.map((f, i) => (
              <span key={i} className="inline-flex items-center gap-1.5 text-xs bg-slate-100 border border-slate-200 rounded-full px-3 py-1 text-slate-600">
                {f.name}
                {!decided && (
                  <button type="button" onClick={() => removeFile(i)} className="text-slate-400 hover:text-rose-500">×</button>
                )}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Action buttons */}
      {modifyMode && !decided ? (
        <div className="space-y-3">
          <textarea
            value={modifyText}
            onChange={(e) => setModifyText(e.target.value)}
            placeholder="Describe how you'd like to modify the draft..."
            className="w-full border border-amber-300 rounded-xl px-4 py-3 text-sm text-slate-700 bg-white/80 focus:outline-none focus:ring-2 focus:ring-amber-300 resize-none"
            rows={3}
            autoFocus
            disabled={decided}
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (modifyText.trim()) {
                  onModify(modifyText.trim())
                }
              }}
              disabled={!modifyText.trim() || decided}
              className="flex-1 py-2.5 rounded-xl font-bold text-sm bg-gradient-to-r from-amber-400 to-orange-400 text-white hover:brightness-110 transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Submit Changes
            </button>
            <button
              type="button"
              onClick={() => { setModifyMode(false); setModifyText('') }}
              className="py-2.5 px-4 rounded-xl font-bold text-sm border border-slate-300 bg-white text-slate-600 hover:bg-slate-50 transition"
              disabled={decided}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => onDecision('yes', attachments)}
            disabled={decided}
            className="flex-1 min-w-[100px] py-2.5 rounded-xl font-bold text-sm bg-gradient-to-r from-teal-400 to-cyan-400 text-white hover:brightness-110 transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            ✉️ Send (Yes)
          </button>
          <button
            type="button"
            onClick={() => onDecision('save', attachments)}
            disabled={decided}
            className="flex-1 min-w-[100px] py-2.5 rounded-xl font-bold text-sm border border-teal-300 bg-white text-teal-700 hover:bg-teal-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            💾 Save Draft
          </button>
          <button
            type="button"
            onClick={() => setModifyMode(true)}
            disabled={decided}
            className="flex-1 min-w-[100px] py-2.5 rounded-xl font-bold text-sm border border-amber-300 bg-white text-amber-700 hover:bg-amber-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            ✏️ Modify
          </button>
          <button
            type="button"
            onClick={() => onDecision('no')}
            disabled={decided}
            className="flex-1 min-w-[100px] py-2.5 rounded-xl font-bold text-sm border border-slate-300 bg-white text-slate-600 hover:bg-rose-50 hover:border-rose-300 hover:text-rose-600 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            ✕ Reject (No)
          </button>
        </div>
      )}
      {decided && <p className="text-xs text-slate-400 italic mt-3">Final Decision Recorded</p>}
    </section>
  )
}
