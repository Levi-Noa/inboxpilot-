import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import ChatInterface from './components/ChatInterface'
import Sidebar from './components/Sidebar'
import type { Message } from './types'

interface AttachmentPayload {
  filename: string
  mime_type: string
  content_base64: string
}

const API_BASE = import.meta.env.VITE_API_URL || ''

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [threadId, setThreadId] = useState<string>('')
  const [sessionTopic, setSessionTopic] = useState<string>('New Session')
  const [gmailConnected, setGmailConnected] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [isDark, setIsDark] = useState(false)
  // Track the IDs of the last search results shown — avoids re-rendering the same candidate list
  const lastShownSearchKeyRef = useRef<string>('')

  // Initialize
  useEffect(() => {
    checkGmailStatus()
    generateThreadId()

    const saved = localStorage.getItem('inboxpilot-theme')
    if (saved === 'dark') {
      setIsDark(true)
      document.documentElement.classList.add('theme-dark')
    }

  }, [])

  useEffect(() => {
    document.documentElement.classList.toggle('theme-dark', isDark)
    localStorage.setItem('inboxpilot-theme', isDark ? 'dark' : 'light')
  }, [isDark])

  const generateThreadId = () => {
    const id = `thread-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
    setThreadId(id)
    setSessionTopic('New Session')
    setMessages([])
    lastShownSearchKeyRef.current = ''
  }

  const deriveTopicFromMessage = (content: string) => {
    const normalized = content.replace(/\s+/g, ' ').trim()
    if (!normalized) return 'New Session'
    return normalized.length > 48 ? `${normalized.slice(0, 48)}...` : normalized
  }

  const checkGmailStatus = async () => {
    try {
      const response = await axios.get(`${API_BASE}/api/gmail/status`)
      setGmailConnected(response.data.connected)
    } catch (err) {
      console.error('Failed to check Gmail status:', err)
    }
  }

  const connectGmail = async () => {
    setConnecting(true)
    setConnectError(null)
    try {
      // Tell the backend to start the OAuth flow (returns immediately)
      await axios.post(`${API_BASE}/api/gmail/connect`, {}, { timeout: 8000 })

      // Poll status every 2 s — backend opens a browser window on the server machine
      const poll = setInterval(async () => {
        try {
          const status = await axios.get(`${API_BASE}/api/gmail/status`)
          if (status.data.connected) {
            clearInterval(poll)
            setGmailConnected(true)
            setConnecting(false)
          } else if (status.data.oauth_error) {
            clearInterval(poll)
            setConnectError(`OAuth error: ${status.data.oauth_error}`)
            setConnecting(false)
          }
        } catch { /* keep polling */ }
      }, 2000)
      // Stop polling after 5 min
      setTimeout(() => { clearInterval(poll); setConnecting(false) }, 5 * 60 * 1000)
    } catch (err) {
      console.error('Failed to connect Gmail:', err)
      const msg = axios.isAxiosError(err)
        ? (err.code === 'ECONNABORTED'
            ? `Connection timed out — is the backend running on port ${new URL(API_BASE).port}?`
            : (err.message ?? 'Request failed'))
        : 'Could not connect to backend.'
      setConnectError(msg)
      setConnecting(false)
    }
  }

  const sendMessage = async (content: string, attachments: AttachmentPayload[] = []) => {
    if (!content.trim() || loading) return

    if (messages.length === 0) {
      setSessionTopic(deriveTopicFromMessage(content))
    }

    // Add user message to state ONLY if it's not a hidden command
    const isHiddenCommand = content.startsWith('_') && content.endsWith('_')
    if (!isHiddenCommand) {
      const userMessage: Message = {
        role: 'human',
        content,
        timestamp: new Date(),
      }
      setMessages(prev => [...prev, userMessage])
    }
    setLoading(true)

    try {
      const response = await axios.post(`${API_BASE}/api/chat`, {
        message: content,
        thread_id: threadId,
        model: 'gpt-4o',
        provider: 'openai',
        attachments,
      }, { timeout: 120000 })

      const data = response.data

      // Only display search results if they are different from the last shown ones
      const incomingResults = (data.searchResults || data.search_results) ?? []
      const incomingKey = incomingResults.map((r: { id: string }) => r.id).join(',')
      const isNewResults = incomingKey.length > 0 && incomingKey !== lastShownSearchKeyRef.current
      if (isNewResults) lastShownSearchKeyRef.current = incomingKey

      const assistantMessage: Message = {
        role: 'assistant',
        content: data.content || data.response,
        timestamp: new Date(),
        searchResults: isNewResults ? incomingResults : undefined,
        reviewData: (data.reviewData || data.review_data) ?? undefined,
        emailCard: data.emailCard ?? undefined,
      }

      setMessages(prev => [...prev, assistantMessage])

    } catch (err) {
      console.error('Failed to send message:', err)
      let errorText = 'Error communicating with server'
      if (axios.isAxiosError(err)) {
        const apiMessage = (err.response?.data as { response?: string; error?: string } | undefined)
        errorText = apiMessage?.response || apiMessage?.error || err.message || errorText
      }
      const errorMessage: Message = {
        role: 'system',
        content: errorText,
        timestamp: new Date(),
      }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setLoading(false)
    }
  }

  // Clicking an email card sends the option number as a chat message,
  // which appears as a user bubble and resumes the graph interrupt.
  const selectEmail = (optionNumber: string) => {
    void sendMessage(optionNumber)
  }

  // Called when the user clicks Modify and submits feedback text.
  const handleModifyDraft = (feedback: string) => {
    // Mark the review card as decided so buttons disappear
    setMessages(prev =>
      prev.map((msg, idx) =>
        idx === prev.length - 1 && msg.reviewData
          ? { ...msg, reviewData: { ...msg.reviewData, decided: true } }
          : msg
      )
    )
    // Send the modification feedback to the agent
    void sendMessage(feedback)
  }

  // Called when the user clicks Send, Save Draft, or Don't Send in the ReviewCard.
  // Marks the review card message as decided and sends the decision to the agent.
  const handleReviewDecision = async (decision: 'yes' | 'save' | 'no', files?: File[]) => {
    // Mark the last message that has reviewData as decided so buttons disappear
    setMessages(prev =>
      prev.map((msg, idx) =>
        idx === prev.length - 1 && msg.reviewData
          ? { ...msg, reviewData: { ...msg.reviewData, decided: true } }
          : msg
      )
    )

    if (decision === 'no') {
      void sendMessage('_reject_draft_')
      return
    }

    let attachmentPayload: AttachmentPayload[] = []
    if (files && files.length > 0) {
      const toBase64 = (file: File): Promise<string> =>
        new Promise((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => {
            const result = String(reader.result || '')
            resolve(result.includes(',') ? result.split(',')[1] : result)
          }
          reader.onerror = () => reject(new Error(`Failed reading file: ${file.name}`))
          reader.readAsDataURL(file)
        })
      attachmentPayload = await Promise.all(
        files.map(async (f) => ({
          filename: f.name,
          mime_type: f.type || 'application/octet-stream',
          content_base64: await toBase64(f),
        }))
      )
    }

    if (decision === 'save') {
      void sendMessage('_save_draft_', attachmentPayload)
      return
    }

    // 'yes' — encode any attached files and send
    void sendMessage('_send_draft_', attachmentPayload)
  }

  return (
    <div className="app-shell">
      <div className="fixed inset-0 pointer-events-none -z-10">
        <div className="absolute -top-24 -left-16 h-64 w-64 rounded-full bg-teal-300/35 blur-3xl" />
        <div className="absolute top-24 right-0 h-72 w-72 rounded-full bg-amber-200/45 blur-3xl" />
      </div>

      <div className="flex h-screen overflow-hidden w-full p-3 md:p-6 gap-3 md:gap-5">
        <button
          type="button"
          onClick={() => setMobileSidebarOpen(true)}
          className="lg:hidden fixed top-4 left-4 z-40 panel-glass rounded-xl px-3 py-2 text-slate-700 text-sm border border-slate-300"
        >
          Workspace
        </button>

        {mobileSidebarOpen && (
          <button
            type="button"
            aria-label="Close sidebar"
            onClick={() => setMobileSidebarOpen(false)}
            className="fixed inset-0 z-40 bg-slate-500/25 lg:hidden"
          />
        )}

        <div className={`fixed lg:relative z-50 lg:z-auto top-0 left-0 h-full lg:h-auto transform transition-transform duration-300 ${mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}>
          <Sidebar
            gmailConnected={gmailConnected}
            connecting={connecting}
            connectError={connectError}
            onAuthenticateGmail={connectGmail}
            onNewThread={generateThreadId}
            sessionTopic={sessionTopic}
            onCloseMobile={() => setMobileSidebarOpen(false)}
            isDark={isDark}
          />
        </div>

        <div className="flex-1 flex flex-col gap-3">
          <ChatInterface
            messages={messages}
            loading={loading}
            onSendMessage={sendMessage}
            onSelectEmail={selectEmail}
            onReviewDecision={handleReviewDecision}
            onModifyDraft={handleModifyDraft}
            isDark={isDark}
            onToggleTheme={() => setIsDark((v) => !v)}
          />
        </div>
      </div>
    </div>
  )
}