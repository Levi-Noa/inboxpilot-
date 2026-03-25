export interface ReviewData {
  draft: string
  to: string
  subject: string
  threadId: string
  decided?: boolean
  originalBody?: string
  originalFrom?: string
  originalDate?: string
}

export interface EmailCardData {
  from: string
  subject: string
  date: string
  body: string
}

export interface Message {
  role: 'human' | 'assistant' | 'system'
  content: string
  timestamp: Date
  searchResults?: SearchResult[]
  reviewData?: ReviewData
  emailCard?: EmailCardData
}

export interface SearchResult {
  id: string
  threadId: string
  from_: string
  subject: string
  date: string
  snippet: string
}

export interface DraftReply {
  draft: string
  subject: string
}

export interface ChatResponse {
  response: string
  content?: string
  search_results?: SearchResult[]
  searchResults?: SearchResult[]
  draft_reply?: DraftReply
  action?: 'search' | 'draft' | 'send'
  emailCard?: EmailCardData
}