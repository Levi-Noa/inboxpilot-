import type { SearchResult } from '../types'

interface SearchResultsProps {
  results: SearchResult[]
  onSelect: (emailId: string) => void
}

export default function SearchResults({ results, onSelect }: SearchResultsProps) {
  return (
    <section className="my-4 panel-glass rounded-2xl p-4 md:p-5 fade-up">
      <p className="text-xs uppercase tracking-[0.18em] text-slate-400 mb-2">Candidate Emails</p>
      <p className="text-sm md:text-base font-semibold text-slate-700 mb-4">
        Found {results.length} relevant option{results.length !== 1 ? 's' : ''}
      </p>

      {results.map((result, idx) => (
        <button
          key={idx}
          onClick={() => onSelect(String(idx + 1))}
          className="w-full text-left p-4 rounded-xl border border-slate-300 bg-white/80 hover:bg-white hover:border-teal-400/60 transition-all duration-200 group mb-2 last:mb-0"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-teal-500 text-[11px] font-bold text-white">{idx + 1}</span>
                <h3 className="font-semibold text-slate-700 group-hover:text-teal-700 truncate">
                  {result.subject}
                </h3>
              </div>
              <p className="text-sm text-slate-600 mt-1">{result.from_}</p>
              <p className="text-xs text-slate-500 mt-2 leading-relaxed">
                {result.snippet}
              </p>
            </div>
            <div className="text-xs text-slate-500 ml-2 flex-shrink-0 text-right">
              <p>{new Date(result.date).toLocaleDateString()}</p>
              <span className="inline-block mt-2 px-2 py-1 rounded-full border border-teal-400/30 text-teal-700">Open</span>
            </div>
          </div>
        </button>
      ))}
    </section>
  )
}