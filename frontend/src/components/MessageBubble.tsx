import type { Message } from '../types'

interface MessageBubbleProps {
  message: Message
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'human'
  const isSystem = message.role === 'system'
  const hasStructuredCard = Boolean(
    message.searchResults?.length || message.emailCard || message.reviewData
  )

  // Card-first UX: when structured content exists, suppress duplicate assistant narration.
  if (!isUser && !isSystem && hasStructuredCard) return null

  if (!message.content.trim()) return null

  if (isSystem) {
    return (
      <div className="flex justify-center my-4 fade-up">
        <div className="px-4 py-2 rounded-lg text-sm bg-rose-100 border border-rose-300 text-rose-700">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} fade-up`}>
      <div className={`max-w-[88%] md:max-w-[72%] flex gap-2.5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
        <div className={`h-8 w-8 md:h-9 md:w-9 rounded-xl grid place-items-center text-xs font-bold title-font ${isUser ? 'bg-gradient-to-br from-amber-300 to-orange-300 text-slate-900' : 'bg-gradient-to-br from-teal-300 to-cyan-300 text-slate-900'}`}>
          {isUser ? 'You' : 'AI'}
        </div>
        <div
          className={`px-4 py-3 rounded-2xl border ${
            isUser
              ? 'user-msg-bubble bg-gradient-to-br from-amber-200 to-orange-200 border-amber-300 text-slate-800 rounded-tr-md'
              : 'panel-glass border-slate-300 text-slate-700 rounded-tl-md'
          }`}
        >
          <p className={`text-sm md:text-[15px] leading-relaxed whitespace-pre-wrap break-words ${isUser ? 'user-msg-text' : ''}`}>
            {message.content}
          </p>
          <p className={`text-[11px] mt-2 ${isUser ? 'text-amber-700/80' : 'text-slate-500'}`}>
            {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </p>
        </div>
      </div>
    </div>
  )
}