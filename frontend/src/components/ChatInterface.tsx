import { useRef, useEffect, useState } from 'react'
import type { Message } from '../types'
import MessageBubble from './MessageBubble'
import SearchResults from './SearchResults'
import ReviewCard from './ReviewCard'
import EmailCard from './EmailCard'
import InputBar from './InputBar'

interface ChatInterfaceProps {
  messages: Message[]
  loading: boolean
  onSendMessage: (message: string, attachments?: AttachmentPayload[]) => Promise<void>
  onSelectEmail: (emailId: string) => void
  onReviewDecision: (decision: 'yes' | 'save' | 'no', files?: File[]) => void
  onModifyDraft: (feedback: string) => void
  isDark: boolean
  onToggleTheme: () => void
}

interface AttachmentPayload {
  filename: string
  mime_type: string
  content_base64: string
}

export default function ChatInterface({
  messages,
  loading,
  onSendMessage,
  onSelectEmail,
  onReviewDecision,
  onModifyDraft,
  isDark,
  onToggleTheme,
}: ChatInterfaceProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const [inputValue, setInputValue] = useState('')
  const [attachments, setAttachments] = useState<File[]>([])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const toBase64 = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        const result = String(reader.result || '')
        const base64 = result.includes(',') ? result.split(',')[1] : result
        resolve(base64)
      }
      reader.onerror = () => reject(new Error(`Failed reading file: ${file.name}`))
      reader.readAsDataURL(file)
    })
  }

  const buildAttachmentPayload = async (): Promise<AttachmentPayload[]> => {
    if (!attachments.length) return []
    const encoded = await Promise.all(
      attachments.map(async (file) => ({
        filename: file.name,
        mime_type: file.type || 'application/octet-stream',
        content_base64: await toBase64(file),
      }))
    )
    return encoded
  }

  const handleSend = async () => {
    if (!inputValue.trim()) return
    const payload = await buildAttachmentPayload()
    const messageToSend = inputValue
    setInputValue('')
    setAttachments([])
    await onSendMessage(messageToSend, payload)
  }

  const quickPrompts = [
    'Find and summarize recent updates from my inbox',
    'Help me draft a professional response to a recent message',
    'Organize and find information about an upcoming event or trip',
  ]

  return (
    <main className="flex-1 flex flex-col overflow-hidden panel-glass rounded-2xl float-in">
      <div className="border-b border-slate-200 px-5 md:px-8 py-4 md:py-5 bg-white/55">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
          <div>
            <p className="text-[11px] tracking-[0.17em] uppercase text-slate-400"></p>
            <h2 className="title-font text-2xl md:text-3xl text-slate-800">Workspace</h2>
          </div>
          <label className="self-start md:self-auto flex items-center gap-3 cursor-pointer select-none panel-glass rounded-xl px-3 py-2 border border-slate-300">
            <span className="text-xs font-semibold text-slate-700">{isDark ? 'Dark' : 'Light'}</span>
            <button
              type="button"
              role="switch"
              aria-checked={isDark}
              aria-label="Toggle theme"
              onClick={onToggleTheme}
              className={`relative inline-flex h-7 w-14 items-center rounded-full border transition-colors ${
                isDark
                  ? 'bg-slate-800 border-slate-600'
                  : 'bg-slate-200 border-slate-300'
              }`}
            >
              <span
                className={`inline-block h-5 w-5 transform rounded-full transition-transform ${
                  isDark
                    ? 'translate-x-8 bg-teal-300'
                    : 'translate-x-1 bg-white'
                }`}
              />
            </button>
          </label>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 md:px-7 py-5 md:py-6 space-y-4">
        {messages.length === 0 ? (
          <div className="h-full min-h-[420px] flex items-center justify-center fade-up">
            <div className="max-w-xl text-center">
              <div className="mx-auto h-16 w-16 rounded-2xl bg-gradient-to-br from-teal-300 to-cyan-400 text-slate-900 grid place-items-center text-2xl font-bold title-font mb-5 soft-ring">AI</div>
              <h3 className="title-font text-3xl md:text-4xl text-slate-800">Email operations copilot</h3>
              <p className="text-slate-600 mt-3">Ask naturally, inspect results, and draft safely with an agent workflow that stays auditable.</p>

              <div className="mt-6 grid gap-2">
                {quickPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => { void onSendMessage(prompt) }}
                    className="text-left panel-glass rounded-xl px-4 py-3 text-sm text-slate-700 hover:border-teal-400/45 border border-slate-300 transition"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg, idx) => (
              <div key={idx}>
                <MessageBubble message={msg} />
                {msg.searchResults && msg.searchResults.length > 0 && (
                  <SearchResults
                    results={msg.searchResults}
                    onSelect={onSelectEmail}
                  />
                )}
                {msg.emailCard && (
                  <EmailCard data={msg.emailCard} />
                )}
                {msg.reviewData && (
                  <ReviewCard
                    data={msg.reviewData}
                    onDecision={onReviewDecision}
                    onModify={onModifyDraft}
                    decided={msg.reviewData.decided ?? false}
                  />
                )}
              </div>
            ))}

            {loading && (
              <div className="panel-glass rounded-xl px-4 py-3 inline-flex items-center gap-3 text-slate-700">
                <div className="flex gap-1.5">
                  <span className="w-2.5 h-2.5 bg-teal-300 rounded-full animate-bounce" />
                  <span className="w-2.5 h-2.5 bg-cyan-300 rounded-full animate-bounce" style={{ animationDelay: '0.12s' }} />
                  <span className="w-2.5 h-2.5 bg-amber-300 rounded-full animate-bounce" style={{ animationDelay: '0.24s' }} />
                </div>
                Agent is thinking...
              </div>
            )}

            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      <InputBar
        value={inputValue}
        onChange={setInputValue}
        onSend={() => { void handleSend() }}
        loading={loading}
        attachments={attachments}
        onAttachmentsChange={setAttachments}
      />
    </main>
  )
}