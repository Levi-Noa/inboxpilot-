import type { EmailCardData } from '../types'

interface EmailCardProps {
  data: EmailCardData
}

export default function EmailCard({ data }: EmailCardProps) {
  return (
    <section className="my-4 panel-glass rounded-2xl p-5 md:p-6 fade-up border border-indigo-300/40">
      <p className="text-xs uppercase tracking-[0.18em] text-indigo-500 mb-3 font-semibold">
        Email Context
      </p>

      {/* Metadata */}
      <div className="space-y-1.5 mb-4 text-sm">
        <div className="flex gap-2">
          <span className="text-slate-400 w-16 flex-shrink-0">From:</span>
          <span className="text-slate-700 font-medium break-all">{data.from}</span>
        </div>
        <div className="flex gap-2">
          <span className="text-slate-400 w-16 flex-shrink-0">Subject:</span>
          <span className="text-slate-700 font-medium">{data.subject}</span>
        </div>
        <div className="flex gap-2">
          <span className="text-slate-400 w-16 flex-shrink-0">Date:</span>
          <span className="text-slate-600">{data.date}</span>
        </div>
      </div>

      {/* Email body */}
      <div className="bg-white/70 border border-slate-200 rounded-xl px-4 py-3 text-sm text-slate-700 whitespace-pre-wrap leading-relaxed max-h-52 overflow-y-auto">
        {data.body || '(empty email body)'}
      </div>
    </section>
  )
}
