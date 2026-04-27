import { useEffect, useRef, useState, useCallback } from 'react'
import { useToast } from './Toast'
import Markdown from './Markdown'
import { AgentAvatar } from './AgentAvatar'
import { useNotifications } from '../context/NotificationContext'
import { ApprovalCard, ToolCard, TypingIndicator } from './agent-chat/ChatBlocks'
import {
  Send, Square, Terminal as TermIcon, CheckCircle2,
  Paperclip, X, File as FileIcon, Upload,
  Ticket as TicketIcon, Plus, ShieldAlert, Check,
  Pencil, Copy,
} from 'lucide-react'

interface SkillItem {
  name: string
  description: string
  prefix: string
  has_scripts: boolean
}

interface SlashPopup {
  open: boolean
  query: string
  items: SkillItem[]
  selectedIndex: number
  anchorStart: number
}

interface PermissionRequest {
  requestId: string
  toolName: string
  input: Record<string, unknown>
  title: string | null
  description: string | null
  createdAt: number
}

interface AgentChatProps {
  agent: string
  sessionId?: string
  accentColor?: string
  externalLoading?: boolean
  externalError?: string | null
  onPendingCountChange?: (sessionId: string, count: number) => void
  onNeedsAttention?: (sessionId: string) => void
  // Thread-mode props (thread-areas feature)
  workingDir?: string
  threadTicketId?: string
  onTurnCompleted?: () => void
}

// Terminal-server URL
import { TS_HTTP, TS_WS } from '../lib/terminal-url'

interface AttachedFile {
  file: File
  previewUrl?: string
  name: string
  type: string
}

interface FileRef {
  name: string
  type: string
  previewUrl?: string
  base64?: string
}

type ChatMessage =
  | { role: 'user'; text: string; files?: FileRef[]; ts: number; uuid?: string }
  | { role: 'assistant'; blocks: AssistantBlock[]; ts: number; streaming?: boolean; uuid?: string }
  | { role: 'system'; text: string; ts: number; uuid?: string }

type AssistantBlock =
  | { type: 'text'; text: string }
  | { type: 'thinking'; text: string }
  | { type: 'tool_use'; toolName: string; toolId: string; input: string; result?: string; done?: boolean; subagentType?: string; subagentStatus?: string; subagentSummary?: string; subagentTools?: Array<{ toolName: string; input: string; toolUseId: string; ts: number }> }

type Status = 'idle' | 'connecting' | 'running' | 'error'

export default function AgentChat({ agent, sessionId, accentColor = '#00FFA7', externalLoading = false, externalError = null, onPendingCountChange, onNeedsAttention, workingDir: _workingDir, threadTicketId: _threadTicketId, onTurnCompleted }: AgentChatProps) {
  const { dismissBySession } = useNotifications()
  const toast = useToast()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const [isThinking, setIsThinking] = useState(false)
  const [ticketId, setTicketId] = useState<string | null>(null)
  const [tickets, setTickets] = useState<{ id: string; title: string; status: string }[]>([])
  const [showTicketPicker, setShowTicketPicker] = useState(false)
  const [allSkills, setAllSkills] = useState<SkillItem[]>([])
  const [slashPopup, setSlashPopup] = useState<SlashPopup>({
    open: false, query: '', items: [], selectedIndex: 0, anchorStart: -1,
  })
  const [pendingApprovals, setPendingApprovals] = useState<PermissionRequest[]>([])
  const [editingUuid, setEditingUuid] = useState<string | null>(null)
  const [editingText, setEditingText] = useState('')
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const dragCounterRef = useRef(0)
  const subagentToolRef = useRef<{ toolName: string; toolUseId: string; input: string; parentToolUseId: string } | null>(null)

  // Auto-dismiss global notifications when the user opens this session
  useEffect(() => {
    if (sessionId) {
      dismissBySession(sessionId)
    }
  }, [sessionId, dismissBySession])

  // Notify parent when pending approvals count changes
  useEffect(() => {
    if (sessionId && onPendingCountChange) {
      onPendingCountChange(sessionId, pendingApprovals.length)
    }
  }, [pendingApprovals.length, sessionId, onPendingCountChange])

  // Auto-scroll to bottom
  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      }
    })
  }, [])

  // Connect WebSocket
  useEffect(() => {
    if (!sessionId) return

    setStatus('connecting')
    setErrorMsg(null)
    let cancelled = false
    let ws: WebSocket | null = null

    ;(async () => {
      // 1) HTTP preflight â€” fails fast on ECONNREFUSED so we can show a real error
      //    instead of hanging in 'connecting' forever (same pattern as AgentTerminal).
      try {
        const res = await fetch(`${TS_HTTP}/api/health`)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
      } catch {
        if (cancelled) return
        setStatus('error')
        setErrorMsg(`Could not reach terminal-server at ${TS_HTTP}. Is it running?`)
        return
      }
      if (cancelled) return

      // 2) Open WS
      ws = new WebSocket(`${TS_WS}/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        ws!.send(JSON.stringify({ type: 'join_session', sessionId }))
        setStatus('idle')
      }

      ws.onmessage = (ev) => {
        if (cancelled) return
        let msg: any
        try { msg = JSON.parse(ev.data) } catch { return }

        switch (msg.type) {
          case 'session_joined':
            // Restore chat history from server â€” preserve uuid from each message
            if (msg.chatHistory && msg.chatHistory.length > 0) {
              setMessages(msg.chatHistory.map((m: any) => ({
                ...m,
                uuid: m.uuid,
                streaming: false,
              })))
              scrollToBottom()
            }
            // Restore ticket binding (Feature 1.3)
            setTicketId(msg.ticketId || null)
            break

          case 'chat_history':
            // Fallback history restore
            if (msg.messages?.length > 0) {
              setMessages(msg.messages.map((m: any) => ({ ...m, streaming: false })))
              scrollToBottom()
            }
            break

          case 'chat_event':
            handleChatEvent(msg.event || msg)
            break

          case 'ticket_bound':
            if (msg.ticketId) {
              setTicketId(msg.ticketId)
            }
            break

          case 'permission_request':
            if (msg.requestId) {
              setPendingApprovals(prev => [...prev, {
                requestId: msg.requestId,
                toolName: msg.toolName,
                input: msg.input || {},
                title: msg.title || null,
                description: msg.description || null,
                createdAt: Date.now(),
              }])
              // Request OS notification permission silently on first request
              if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
                Notification.requestPermission().catch(() => {})
              }
              // Fire OS notification when tab is hidden and notifications are enabled
              if (
                document.hidden &&
                typeof Notification !== 'undefined' &&
                Notification.permission === 'granted' &&
                localStorage.getItem('evonexus.notifications.enabled') !== 'false'
              ) {
                try {
                  const n = new Notification(`Agent @${agent} is waiting for your approval`, {
                    body: msg.title || msg.toolName || 'Permission request',
                    icon: '/favicon.ico',
                    tag: `approval-${msg.requestId}`,
                  })
                  n.onclick = () => { window.focus() }
                } catch {
                  // Notification API unavailable (e.g. Firefox private mode) â€” no-op
                }
              }
            }
            break

          case 'chat_error':
            setStatus('error')
            setIsThinking(false)
            setPendingApprovals([])
            setErrorMsg(msg.message || 'Unknown error')
            setMessages(prev => [...prev, { role: 'system', text: `Error: ${msg.message}`, ts: Date.now() }])
            break

          case 'chat_complete':
            setStatus('idle')
            setIsThinking(false)
            setPendingApprovals([])
            // Signal unread response when user is in another tab
            if (document.hidden && sessionId && onNeedsAttention) {
              onNeedsAttention(sessionId)
            }
            // Thread-mode: fire turn-completed (Option D summary trigger)
            if (onTurnCompleted) {
              onTurnCompleted()
            }
            setMessages(prev => {
              const copy = [...prev]
              for (let i = copy.length - 1; i >= 0; i--) {
                if (copy[i].role === 'assistant') {
                  copy[i] = { ...copy[i], streaming: false } as any
                  break
                }
              }
              return copy
            })
            break

          case 'pong':
            break
        }
      }

      ws.onerror = () => {
        if (cancelled) return
        setStatus('error')
        setErrorMsg('WebSocket error')
      }

      ws.onclose = () => {
        if (pingRef.current) { clearInterval(pingRef.current); pingRef.current = null }
      }

      pingRef.current = setInterval(() => {
        if (ws!.readyState === WebSocket.OPEN) {
          ws!.send(JSON.stringify({ type: 'ping' }))
        }
      }, 25000)
    })()

    return () => {
      cancelled = true
      if (pingRef.current) { clearInterval(pingRef.current); pingRef.current = null }
      try { ws?.close() } catch {}
      wsRef.current = null
    }
  }, [sessionId])

  // Revoke object URLs on unmount
  useEffect(() => {
    return () => {
      attachedFiles.forEach(f => {
        if (f.previewUrl) URL.revokeObjectURL(f.previewUrl)
      })
    }
  }, [attachedFiles])

  // Fetch skills once on mount for slash-command autocomplete
  useEffect(() => {
    fetch('/api/skills', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.skills) {
          setAllSkills(data.skills.sort((a: SkillItem, b: SkillItem) => a.name.localeCompare(b.name)))
        }
      })
      .catch(() => {})
  }, [])

  // Fetch open tickets for this agent when picker opens (Feature 1.3)
  useEffect(() => {
    if (!showTicketPicker) return
    fetch(`/api/tickets?assignee_agent=${encodeURIComponent(agent)}&status=open&status=in_progress`, {
      credentials: 'include',
    })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.tickets) setTickets(data.tickets)
      })
      .catch(() => {})
  }, [showTicketPicker, agent])

  const bindTicket = useCallback(async (newTicketId: string | null) => {
    if (!sessionId) return
    try {
      await fetch(`${TS_HTTP}/api/sessions/${sessionId}/ticket`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticketId: newTicketId }),
      })
      setTicketId(newTicketId)
      setShowTicketPicker(false)
    } catch (err) {
      console.error('Failed to bind ticket', err)
    }
  }, [sessionId])

  const createAndBindTicket = useCallback(async () => {
    const title = prompt('New ticket title:')
    if (!title?.trim()) return
    try {
      const res = await fetch('/api/tickets', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({
          title: title.trim(),
          assignee_agent: agent,
          priority: 'medium',
          status: 'open',
        }),
      })
      if (!res.ok) throw new Error('Failed to create')
      const ticket = await res.json()
      await bindTicket(ticket.id)
    } catch (err: any) {
      toast.error('Falha ao criar ticket', err?.message)
    }
  }, [agent, bindTicket])

  const respondToApproval = useCallback((requestId: string, approved: boolean) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ type: 'permission_response', requestId, approved }))
    setPendingApprovals(prev => prev.filter(r => r.requestId !== requestId))
  }, [])

  const handleChatEvent = useCallback((msg: any) => {
    // Track thinking state for typing indicator
    if (msg.type === 'thinking_start') {
      setIsThinking(true)
    }
    if (msg.type === 'text_start' || msg.type === 'text_delta') {
      setIsThinking(false)
    }

    setMessages(prev => {
      const copy = [...prev]

      switch (msg.type) {
        case 'text_start':
        case 'message_start': {
          const last = copy[copy.length - 1]
          if (!last || last.role !== 'assistant' || !(last as any).streaming) {
            copy.push({ role: 'assistant', blocks: [], ts: Date.now(), streaming: true })
          }
          break
        }

        case 'text_delta': {
          const last = copy[copy.length - 1]
          if (last?.role === 'assistant') {
            const blocks = [...(last as any).blocks]
            const lastBlock = blocks[blocks.length - 1]
            if (lastBlock?.type === 'text') {
              blocks[blocks.length - 1] = { ...lastBlock, text: lastBlock.text + (msg.text || '') }
            } else {
              blocks.push({ type: 'text', text: msg.text || '' })
            }
            copy[copy.length - 1] = { ...last, blocks } as any
          }
          break
        }

        case 'thinking_start': {
          const last = copy[copy.length - 1]
          if (!last || last.role !== 'assistant' || !(last as any).streaming) {
            copy.push({ role: 'assistant', blocks: [], ts: Date.now(), streaming: true })
          }
          break
        }

        case 'thinking_delta': {
          // Silently consume â€” we show typing indicator instead
          break
        }

        case 'tool_use_start': {
          setIsThinking(false)
          // Subagent tool â€” accumulate in ref, don't add a block
          if (msg.parentToolUseId) {
            subagentToolRef.current = {
              toolName: msg.toolName,
              toolUseId: msg.toolId,
              input: '',
              parentToolUseId: msg.parentToolUseId,
            }
            break
          }
          const last = copy[copy.length - 1]
          if (last?.role === 'assistant') {
            const blocks = [...(last as any).blocks]
            blocks.push({
              type: 'tool_use',
              toolName: msg.toolName,
              toolId: msg.toolId,
              input: '',
              done: false,
            })
            copy[copy.length - 1] = { ...last, blocks } as any
          }
          break
        }

        case 'tool_input_delta': {
          // Subagent tool input â€” accumulate in ref
          if (msg.parentToolUseId) {
            if (subagentToolRef.current && subagentToolRef.current.parentToolUseId === msg.parentToolUseId) {
              subagentToolRef.current = { ...subagentToolRef.current, input: subagentToolRef.current.input + (msg.json || '') }
            }
            break
          }
          const last = copy[copy.length - 1]
          if (last?.role === 'assistant') {
            const blocks = [...(last as any).blocks]
            const lastBlock = blocks[blocks.length - 1]
            if (lastBlock?.type === 'tool_use') {
              blocks[blocks.length - 1] = { ...lastBlock, input: lastBlock.input + (msg.json || '') }
              copy[copy.length - 1] = { ...last, blocks } as any
            }
          }
          break
        }

        case 'block_stop': {
          // Subagent block finished â€” flush to parent Agent block's subagentTools
          if (msg.parentToolUseId && subagentToolRef.current) {
            const entry = {
              toolName: subagentToolRef.current.toolName,
              input: subagentToolRef.current.input,
              toolUseId: subagentToolRef.current.toolUseId,
              ts: Date.now(),
            }
            const parentId = msg.parentToolUseId
            subagentToolRef.current = null
            // Find parent Agent block across all messages
            for (let mi = copy.length - 1; mi >= 0; mi--) {
              const m = copy[mi]
              if (m.role !== 'assistant') continue
              const blocks = [...(m as any).blocks]
              let found = false
              for (let bi = blocks.length - 1; bi >= 0; bi--) {
                if (blocks[bi].type === 'tool_use' && blocks[bi].toolId === parentId) {
                  const existing: typeof entry[] = blocks[bi].subagentTools || []
                  blocks[bi] = { ...blocks[bi], subagentTools: [...existing, entry] }
                  copy[mi] = { ...m, blocks } as any
                  found = true
                  break
                }
              }
              if (found) break
            }
            break
          }
          const last = copy[copy.length - 1]
          if (last?.role === 'assistant') {
            const blocks = [...(last as any).blocks]
            const lastBlock = blocks[blocks.length - 1]
            if (lastBlock?.type === 'tool_use' && !lastBlock.done) {
              blocks[blocks.length - 1] = { ...lastBlock, done: true }
              copy[copy.length - 1] = { ...last, blocks } as any
            }
          }
          break
        }

        case 'task_started': {
          // Subagent started â€” find the Agent tool_use block and enrich it
          const last2 = copy[copy.length - 1]
          if (last2?.role === 'assistant') {
            const blocks = [...(last2 as any).blocks]
            // Find the Agent tool block by toolUseId or last Agent block
            for (let k = blocks.length - 1; k >= 0; k--) {
              if (blocks[k].type === 'tool_use' && blocks[k].toolName === 'Agent') {
                blocks[k] = { ...blocks[k], subagentType: msg.description, subagentStatus: 'running' }
                break
              }
            }
            copy[copy.length - 1] = { ...last2, blocks } as any
          }
          break
        }

        case 'task_progress': {
          const last3 = copy[copy.length - 1]
          if (last3?.role === 'assistant') {
            const blocks = [...(last3 as any).blocks]
            for (let k = blocks.length - 1; k >= 0; k--) {
              if (blocks[k].type === 'tool_use' && blocks[k].toolName === 'Agent' && blocks[k].subagentStatus === 'running') {
                blocks[k] = { ...blocks[k], subagentSummary: msg.summary || msg.description }
                break
              }
            }
            copy[copy.length - 1] = { ...last3, blocks } as any
          }
          break
        }

        case 'task_complete': {
          const last4 = copy[copy.length - 1]
          if (last4?.role === 'assistant') {
            const blocks = [...(last4 as any).blocks]
            for (let k = blocks.length - 1; k >= 0; k--) {
              if (blocks[k].type === 'tool_use' && blocks[k].toolName === 'Agent') {
                blocks[k] = { ...blocks[k], subagentStatus: msg.status, done: true }
                break
              }
            }
            copy[copy.length - 1] = { ...last4, blocks } as any
          }
          break
        }

        case 'tool_use_summary': {
          // Show summary text after tool completes
          const last5 = copy[copy.length - 1]
          if (last5?.role === 'assistant' && msg.summary) {
            const blocks = [...(last5 as any).blocks]
            blocks.push({ type: 'text', text: msg.summary })
            copy[copy.length - 1] = { ...last5, blocks } as any
          }
          break
        }

        case 'result': {
          const last = copy[copy.length - 1]
          if (last?.role === 'assistant') {
            copy[copy.length - 1] = { ...last, streaming: false } as any
          }
          if (msg.isError && msg.errors?.length) {
            copy.push({ role: 'system', text: `Error: ${msg.errors.join(', ')}`, ts: Date.now() })
          }
          break
        }
      }

      return copy
    })

    scrollToBottom()
    if (msg.type === 'text_start' || msg.type === 'message_start') {
      setStatus('running')
    }
  }, [scrollToBottom])

  // File handling
  const processFiles = useCallback((files: FileList | File[]) => {
    const arr = Array.from(files)
    const newAttachments: AttachedFile[] = arr.map(file => {
      const isImage = file.type.startsWith('image/')
      return {
        file,
        name: file.name,
        type: file.type,
        previewUrl: isImage ? URL.createObjectURL(file) : undefined,
      }
    })
    setAttachedFiles(prev => [...prev, ...newAttachments])
  }, [])

  const removeFile = useCallback((index: number) => {
    setAttachedFiles(prev => {
      const next = [...prev]
      if (next[index].previewUrl) URL.revokeObjectURL(next[index].previewUrl!)
      next.splice(index, 1)
      return next
    })
  }, [])

  // Convert File to base64
  const fileToBase64 = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        const result = reader.result as string
        // Strip data URL prefix
        const base64 = result.includes(',') ? result.split(',')[1] : result
        resolve(base64)
      }
      reader.onerror = reject
      reader.readAsDataURL(file)
    })
  }

  // Drag-drop handlers
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    dragCounterRef.current++
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragging(true)
    }
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    dragCounterRef.current--
    if (dragCounterRef.current === 0) {
      setIsDragging(false)
    }
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    dragCounterRef.current = 0
    setIsDragging(false)
    if (e.dataTransfer.files.length > 0) {
      processFiles(e.dataTransfer.files)
    }
  }, [processFiles])

  // Extract plain text from a message for copying
  const getMessageText = (msg: ChatMessage): string => {
    if (msg.role === 'user' || msg.role === 'system') return msg.text
    return msg.blocks
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map(b => b.text)
      .join('\n\n')
  }

  // Copy a message to clipboard with brief visual feedback
  const copyMessage = useCallback((msg: ChatMessage, idx: number) => {
    const text = getMessageText(msg)
    if (!text) return
    navigator.clipboard.writeText(text).then(() => {
      setCopiedIndex(idx)
      setTimeout(() => setCopiedIndex(prev => prev === idx ? null : prev), 1500)
    }).catch(() => {})
  }, [])

  // Pencil button: enter inline edit mode for the given user message
  const startEdit = useCallback((msg: ChatMessage) => {
    if (msg.role !== 'user' || !msg.uuid) return
    setEditingUuid(msg.uuid)
    setEditingText(msg.text)
  }, [])

  // Cancel inline edit
  const cancelEdit = useCallback(() => {
    setEditingUuid(null)
    setEditingText('')
  }, [])

  // Commit inline edit â€” truncates messages from edit point and sends rewind
  const commitEdit = useCallback(() => {
    const text = editingText.trim()
    const uuid = editingUuid
    if (!text || !uuid || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

    setMessages(prev => {
      const cutIdx = prev.findIndex(m => m.uuid === uuid)
      const base = cutIdx !== -1 ? prev.slice(0, cutIdx) : prev
      return [...base, {
        role: 'user' as const,
        text,
        ts: Date.now(),
      }]
    })

    setEditingUuid(null)
    setEditingText('')
    setStatus('running')
    setErrorMsg(null)

    wsRef.current.send(JSON.stringify({
      type: 'chat_send',
      prompt: text,
      rewindFromUuid: uuid,
    }))

    scrollToBottom()
  }, [editingText, editingUuid, scrollToBottom])

  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles: File[] = []
    for (const item of items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) {
          const ext = file.type.split('/')[1] || 'png'
          const named = file.name && file.name !== 'image.png'
            ? file
            : new File([file], `pasted-${Date.now()}.${ext}`, { type: file.type })
          imageFiles.push(named)
        }
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault()
      processFiles(imageFiles)
    }
  }, [processFiles])

  // Send message
  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if ((!text && attachedFiles.length === 0) || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

    // Build file refs for display
    const fileMeta: FileRef[] = attachedFiles.map(f => ({
      name: f.name,
      type: f.type,
      previewUrl: f.previewUrl,
    }))

    // Build files with base64 for server
    const filesForServer: FileRef[] = []
    for (const af of attachedFiles) {
      const base64 = await fileToBase64(af.file)
      filesForServer.push({
        name: af.name,
        type: af.type,
        base64,
      })
    }

    setMessages(prev => [...prev, {
      role: 'user' as const,
      text,
      files: fileMeta.length > 0 ? fileMeta : undefined,
      ts: Date.now(),
    }])

    setInput('')
    setAttachedFiles([])
    setStatus('running')
    setErrorMsg(null)

    wsRef.current.send(JSON.stringify({
      type: 'chat_send',
      prompt: text,
      files: filesForServer.length > 0 ? filesForServer : undefined,
    }))

    scrollToBottom()
    if (inputRef.current) {
      inputRef.current.style.height = 'auto'
      inputRef.current.focus()
    }
  }, [input, attachedFiles, scrollToBottom])

  // Detect slash-command region from caret position
  const detectSlash = useCallback((text: string, caret: number) => {
    // Scan backwards from caret to find a '/' preceded by start-of-string, space, or newline
    const before = text.slice(0, caret)
    const match = before.match(/(^|[\s\n])(\/[\w-]*)$/)
    if (!match) return null
    const anchorStart = before.lastIndexOf('/')
    const query = match[2].slice(1) // text after '/'
    return { anchorStart, query }
  }, [])

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value
    const caret = e.target.selectionStart ?? val.length
    setInput(val)

    const detected = detectSlash(val, caret)
    if (detected) {
      const { anchorStart, query } = detected
      const q = query.toLowerCase()
      const filtered = q
        ? allSkills
            .map(s => {
              const nameIdx = s.name.toLowerCase().indexOf(q)
              if (nameIdx === -1) return null
              return { skill: s, nameIdx }
            })
            .filter((x): x is { skill: SkillItem; nameIdx: number } => x !== null)
            .sort((a, b) => a.nameIdx - b.nameIdx || a.skill.name.localeCompare(b.skill.name))
            .slice(0, 8)
            .map(x => x.skill)
        : allSkills.slice(0, 8)
      setSlashPopup({ open: true, query, items: filtered, selectedIndex: 0, anchorStart })
    } else {
      setSlashPopup(p => p.open ? { ...p, open: false } : p)
    }
  }, [allSkills, detectSlash])

  const insertSlash = useCallback((skill: SkillItem) => {
    const ta = inputRef.current
    if (!ta) return
    const { anchorStart } = slashPopup
    const before = input.slice(0, anchorStart)
    const after = input.slice(ta.selectionStart ?? input.length)
    // Find end of partial word after anchorStart up to current caret
    const newVal = before + '/' + skill.name + ' ' + after
    setInput(newVal)
    setSlashPopup(p => ({ ...p, open: false }))
    // Restore focus & caret position
    requestAnimationFrame(() => {
      if (ta) {
        const pos = (before + '/' + skill.name + ' ').length
        ta.focus()
        ta.setSelectionRange(pos, pos)
      }
    })
  }, [input, slashPopup])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashPopup.open) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashPopup(p => ({
          ...p,
          selectedIndex: p.items.length === 0 ? 0 : (p.selectedIndex + 1) % p.items.length,
        }))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashPopup(p => ({
          ...p,
          selectedIndex: p.items.length === 0 ? 0 : (p.selectedIndex - 1 + p.items.length) % p.items.length,
        }))
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        if (slashPopup.items.length > 0) {
          insertSlash(slashPopup.items[slashPopup.selectedIndex])
        }
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setSlashPopup(p => ({ ...p, open: false }))
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const stopChat = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'chat_stop' }))
    }
    setStatus('idle')
    setIsThinking(false)
  }, [])

  const isConnecting = externalLoading || status === 'connecting'
  const effectiveError = externalError || (status === 'error' ? errorMsg : null)
  const inputDisabled = isConnecting || !!effectiveError
  const canSend = (input.trim().length > 0 || attachedFiles.length > 0) && !inputDisabled && status !== 'running'

  return (
    <div
      className="relative flex h-full min-h-0 flex-col bg-[var(--bg-primary)]"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Corner status indicator */}
      {(isConnecting || effectiveError) && (
        <div
          className="absolute top-3 right-3 z-40 flex items-center gap-1.5 px-2 py-1 rounded-full border text-[10px] max-w-[280px]"
          style={{
            background: effectiveError ? '#ef444415' : '#F59E0B15',
            borderColor: effectiveError ? '#ef444440' : '#F59E0B40',
            color: effectiveError ? '#ef4444' : '#F59E0B',
          }}
          title={effectiveError || 'Connecting...'}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${effectiveError ? '' : 'animate-pulse'}`}
            style={{ background: effectiveError ? '#ef4444' : '#F59E0B' }}
          />
          <span className="truncate">{effectiveError || 'Connecting...'}</span>
        </div>
      )}

      {/* Ticket binding pill (Feature 1.3) */}
      {sessionId && (
        <div className="absolute top-3 left-3 z-40">
          <button
            onClick={() => setShowTicketPicker(v => !v)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[10px] transition-colors"
            style={{
              background: ticketId ? `${accentColor}10` : '#161b22',
              borderColor: ticketId ? `${accentColor}30` : '#21262d',
              color: ticketId ? accentColor : '#667085',
            }}
            title={ticketId ? `Ticket #${ticketId.slice(0, 8)} attached` : 'Attach to a ticket'}
          >
            <TicketIcon size={11} />
            <span className="font-mono">
              {ticketId ? `#${ticketId.slice(0, 8)}` : 'No ticket'}
            </span>
          </button>
          {showTicketPicker && (
            <div
              className="absolute left-0 mt-1.5 w-[min(18rem,calc(100vw-2rem))] max-h-80 overflow-y-auto rounded-lg border bg-[#161b22] shadow-xl z-50"
              style={{ borderColor: '#21262d' }}
            >
              <div className="px-3 py-2 border-b border-[#21262d] text-[10px] text-[#667085] uppercase tracking-wider">
                Attach to ticket
              </div>
              {ticketId && (
                <button
                  onClick={() => bindTicket(null)}
                  className="w-full text-left px-3 py-2 text-xs text-red-400 hover:bg-white/5 border-b border-[#21262d] flex items-center gap-2"
                >
                  <X size={12} /> Detach current ticket
                </button>
              )}
              <button
                onClick={createAndBindTicket}
                className="w-full text-left px-3 py-2 text-xs text-[#e6edf3] hover:bg-white/5 border-b border-[#21262d] flex items-center gap-2"
                style={{ color: accentColor }}
              >
                <Plus size={12} /> Create new ticket
              </button>
              {tickets.length === 0 ? (
                <div className="px-3 py-3 text-[11px] text-[#667085] italic">
                  No open tickets for @{agent}
                </div>
              ) : (
                tickets.map(t => (
                  <button
                    key={t.id}
                    onClick={() => bindTicket(t.id)}
                    className="w-full text-left px-3 py-2 text-xs hover:bg-white/5 transition-colors flex items-start gap-2"
                  >
                    <span
                      className="font-mono text-[10px] mt-0.5 shrink-0"
                      style={{ color: t.id === ticketId ? accentColor : '#667085' }}
                    >
                      #{t.id.slice(0, 6)}
                    </span>
                    <span className="text-[#e6edf3] truncate flex-1">{t.title}</span>
                    {t.id === ticketId && <CheckCircle2 size={11} style={{ color: accentColor }} />}
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      )}

      {/* Pending approval badge */}
      {pendingApprovals.length > 0 && (
        <div
          className="absolute top-3 z-40 flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[10px] animate-pulse"
          style={{
            right: sessionId ? '12px' : '12px',
            background: '#F59E0B15',
            borderColor: '#F59E0B40',
            color: '#F59E0B',
          }}
        >
          <ShieldAlert size={11} />
          <span>{pendingApprovals.length === 1 ? 'Awaiting your approval' : `${pendingApprovals.length} awaiting approval`}</span>
        </div>
      )}

      {/* Drag-drop overlay */}
      {isDragging && (
        <div
          className="absolute inset-0 z-50 flex flex-col items-center justify-center gap-3 pointer-events-none"
          style={{
            background: `${accentColor}08`,
            border: `2px dashed ${accentColor}50`,
          }}
        >
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{ background: `${accentColor}15` }}
          >
            <Upload size={24} style={{ color: accentColor }} />
          </div>
          <p className="text-sm font-medium" style={{ color: accentColor }}>
            Solte os arquivos aqui
          </p>
        </div>
      )}

      <AgentChatTimeline
        agent={agent}
        accentColor={accentColor}
        messages={messages}
        pendingApprovals={pendingApprovals}
        status={status}
        isThinking={isThinking}
        editingUuid={editingUuid}
        editingText={editingText}
        copiedIndex={copiedIndex}
        scrollRef={scrollRef}
        onCancelEdit={cancelEdit}
        onCommitEdit={commitEdit}
        onEditingTextChange={setEditingText}
        onCopyMessage={copyMessage}
        onStartEdit={startEdit}
        onRespondToApproval={respondToApproval}
        getMessageText={getMessageText}
      />

      {/* Input area */}
      <div className="sticky bottom-0 z-10 flex-shrink-0 border-t border-[color:var(--border)] bg-[var(--bg-primary)] px-3 py-3 sm:px-4">
        <div className="mx-auto max-w-3xl space-y-2">
          {/* File previews */}
          {attachedFiles.length > 0 && (
            <div className="flex flex-wrap gap-2 px-1">
              {attachedFiles.map((af, idx) => (
                <div key={idx} className="relative group">
                  {af.previewUrl ? (
                    <div className="relative">
                      <img
                        src={af.previewUrl}
                        alt={af.name}
                        className="w-16 h-16 object-cover rounded-lg border border-[#21262d]"
                      />
                      <button
                        onClick={() => removeFile(idx)}
                        className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-[#161b22] border border-[#21262d] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity text-[#667085] hover:text-[#ef4444]"
                      >
                        <X size={9} />
                      </button>
                    </div>
                  ) : (
                    <div className="relative flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[#21262d] bg-[#161b22] pr-6">
                      <FileIcon size={11} className="text-[#667085] flex-shrink-0" />
                      <span className="text-[11px] text-[#8b949e] truncate max-w-[120px]">{af.name}</span>
                      <button
                        onClick={() => removeFile(idx)}
                        className="absolute right-1.5 text-[#667085] hover:text-[#ef4444] transition-colors"
                      >
                        <X size={10} />
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Input row wrapper â€” relative so popup can anchor to bottom of it */}
          <div className="relative">
            {/* Slash-command autocomplete popup */}
            {slashPopup.open && (
              <div
                className="absolute left-0 right-0 z-50 max-h-[240px] overflow-y-auto rounded-xl border bg-[#161b22] shadow-xl sm:max-h-[280px]"
                style={{ borderColor: '#21262d', maxHeight: '280px', bottom: 'calc(100% + 6px)' }}
              >
                <div className="px-3 py-1.5 border-b border-[#21262d] text-[10px] text-[#667085] uppercase tracking-wider">
                  Skills
                </div>
                {slashPopup.items.length === 0 ? (
                  <div className="px-3 py-3 text-[11px] text-[#667085] italic">
                    No matching skills
                  </div>
                ) : (
                  slashPopup.items.map((skill, idx) => (
                    <button
                      key={skill.name}
                      onMouseDown={(e) => { e.preventDefault(); insertSlash(skill) }}
                      className="w-full text-left px-3 py-2 text-xs flex items-baseline gap-3 transition-colors"
                      style={{
                        background: idx === slashPopup.selectedIndex ? `${accentColor}15` : 'transparent',
                        borderLeft: idx === slashPopup.selectedIndex ? `2px solid ${accentColor}` : '2px solid transparent',
                      }}
                    >
                      <span
                        className="font-mono shrink-0"
                        style={{ color: accentColor }}
                      >
                        /{skill.name}
                      </span>
                      {skill.description && (
                        <span className="text-[#667085] truncate text-[11px]">
                          {skill.description.slice(0, 80)}
                        </span>
                      )}
                    </button>
                  ))
                )}
              </div>
            )}

          {/* Input row */}
          <div
            className="flex items-end gap-2 rounded-xl border bg-[#161b22] px-3 py-2"
            style={{ borderColor: '#21262d' }}
          >
            {/* Paperclip button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              className="flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors mb-0.5"
              title="Anexar arquivo"
            >
              <Paperclip size={14} />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                if (e.target.files) processFiles(e.target.files)
                e.target.value = ''
              }}
            />

            {/* Textarea */}
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder={`Message @${agent}...`}
              rows={1}
              className="flex-1 resize-none bg-transparent text-sm text-[#e6edf3] placeholder:text-[#667085] focus:outline-none max-h-32 disabled:cursor-not-allowed disabled:opacity-60"
              style={{ minHeight: '28px' }}
              onInput={(e) => {
                const el = e.currentTarget
                el.style.height = 'auto'
                el.style.height = Math.min(el.scrollHeight, 128) + 'px'
              }}
              disabled={inputDisabled}
            />

            {/* Send / Stop */}
            {status === 'running' ? (
              <button
                onClick={stopChat}
                className="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-lg border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors mb-0.5"
              >
                <Square size={14} />
              </button>
            ) : (
              <button
                onClick={sendMessage}
                disabled={!canSend}
                className="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-lg border transition-colors mb-0.5"
                style={{
                  borderColor: canSend ? `${accentColor}40` : '#21262d',
                  background: canSend ? `${accentColor}15` : 'transparent',
                  color: canSend ? accentColor : '#667085',
                }}
              >
                <Send size={14} />
              </button>
            )}
          </div>
          </div>{/* end input row wrapper (relative) */}
        </div>

      </div>

      {/* Typing indicator keyframe styles */}
      <style>{`
        @keyframes chat-bounce {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
          40% { transform: translateY(-5px); opacity: 1; }
        }
        @keyframes chat-pulse {
          0%, 100% { opacity: 0.5; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  )
}

interface AgentChatTimelineProps {
  agent: string
  accentColor: string
  messages: ChatMessage[]
  pendingApprovals: PermissionRequest[]
  status: Status
  isThinking: boolean
  editingUuid: string | null
  editingText: string
  copiedIndex: number | null
  scrollRef: React.RefObject<HTMLDivElement | null>
  onCancelEdit: () => void
  onCommitEdit: () => void
  onEditingTextChange: (value: string) => void
  onCopyMessage: (msg: ChatMessage, idx: number) => void
  onStartEdit: (msg: ChatMessage) => void
  onRespondToApproval: (requestId: string, approved: boolean) => void
  getMessageText: (msg: ChatMessage) => string
}

function AgentChatTimeline({
  agent,
  accentColor,
  messages,
  pendingApprovals,
  status,
  isThinking,
  editingUuid,
  editingText,
  copiedIndex,
  scrollRef,
  onCancelEdit,
  onCommitEdit,
  onEditingTextChange,
  onCopyMessage,
  onStartEdit,
  onRespondToApproval,
  getMessageText,
}: AgentChatTimelineProps) {
  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4 sm:px-6 sm:py-6 sm:space-y-5">
      {messages.length === 0 && (
        <div className="flex flex-col items-center justify-center h-full text-center">
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center mb-4"
            style={{ background: `${accentColor}15`, border: `1px solid ${accentColor}30` }}
          >
            <TermIcon size={24} style={{ color: accentColor }} />
          </div>
          <p className="text-[#e6edf3] font-medium text-sm mb-1">
            Chat with @{agent}
          </p>
          <p className="text-[#667085] text-xs max-w-[300px]">
            Type a message below to start a conversation. The agent has access to your workspace tools.
          </p>
        </div>
      )}

      {messages.map((msg, i) => (
        <div key={i}>
          {msg.role === 'user' && editingUuid && msg.uuid === editingUuid && (
            <div className="flex justify-end">
              <div className="w-full max-w-[88%] rounded-2xl border bg-[#1a2744] px-3 py-2 sm:max-w-[85%]" style={{ borderColor: accentColor + '60' }}>
                <textarea
                  value={editingText}
                  onChange={(e) => onEditingTextChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      e.preventDefault()
                      onCancelEdit()
                    } else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                      e.preventDefault()
                      onCommitEdit()
                    }
                  }}
                  autoFocus
                  rows={Math.min(10, Math.max(2, editingText.split('\n').length))}
                  className="w-full bg-transparent text-sm text-[#e6edf3] placeholder:text-[#667085] focus:outline-none resize-none"
                />
                <div className="flex justify-end gap-2 mt-2 pt-2 border-t border-[#21262d]">
                  <button
                    onClick={onCancelEdit}
                    className="px-3 py-1 rounded-md text-xs text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={onCommitEdit}
                    disabled={!editingText.trim()}
                    className="px-3 py-1 rounded-md text-xs border transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    style={{
                      borderColor: `${accentColor}40`,
                      background: `${accentColor}15`,
                      color: accentColor,
                    }}
                  >
                    Send
                  </button>
                </div>
              </div>
            </div>
          )}

          {msg.role === 'user' && !(editingUuid && msg.uuid === editingUuid) && (
            <div className="flex justify-end group/usermsg items-end gap-1">
              <div className="flex items-center gap-0.5 opacity-0 group-hover/usermsg:opacity-100 transition-opacity mr-1">
                <button
                  onClick={() => onCopyMessage(msg, i)}
                  className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-md text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d]"
                  title={copiedIndex === i ? 'Copied' : 'Copy message'}
                >
                  {copiedIndex === i ? <Check size={12} className="text-[#00FFA7]" /> : <Copy size={12} />}
                </button>
                {msg.uuid && status !== 'running' && !editingUuid && (
                  <button
                    onClick={() => onStartEdit(msg)}
                    className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-md text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d]"
                    title="Edit message"
                  >
                    <Pencil size={12} />
                  </button>
                )}
              </div>
              <div className="max-w-[88%] space-y-2 sm:max-w-[70%]">
                {(msg as any).files && (msg as any).files.length > 0 && (
                  <div className="flex flex-wrap gap-2 justify-end">
                    {(msg as any).files.map((f: FileRef, fi: number) => (
                      f.previewUrl ? (
                        <img
                          key={fi}
                          src={f.previewUrl}
                          alt={f.name}
                          className="w-24 h-24 object-cover rounded-xl border border-[#21262d]"
                        />
                      ) : (
                        <div
                          key={fi}
                          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[#21262d] bg-[#161b22]"
                        >
                          <FileIcon size={12} className="text-[#667085]" />
                          <span className="text-[11px] text-[#8b949e] truncate max-w-[140px]">{f.name}</span>
                        </div>
                      )
                    ))}
                  </div>
                )}
                {(msg as any).text && (
                  <div className="px-4 py-2.5 rounded-2xl rounded-br-md bg-[#1a2744] border border-[#21262d] text-[#e6edf3] text-sm leading-relaxed">
                    {(msg as any).text}
                  </div>
                )}
              </div>
            </div>
          )}

          {msg.role === 'assistant' && (
            <div className="flex gap-3 group/asstmsg">
              <div className="flex-shrink-0 mt-0.5">
                <AgentAvatar name={agent} size={28} />
              </div>
              <div className="flex-1 min-w-0 space-y-2">
                {(msg as any).blocks.map((block: AssistantBlock, j: number) => (
                  <div key={j}>
                    {block.type === 'text' && (
                      <div className="text-sm text-[#e6edf3] leading-relaxed prose-invert max-w-none">
                        <Markdown>{block.text}</Markdown>
                      </div>
                    )}
                    {block.type === 'tool_use' && (
                      <ToolCard block={block} accentColor={accentColor} />
                    )}
                  </div>
                ))}
                {(msg as any).streaming && (() => {
                  const blocks = (msg as any).blocks as AssistantBlock[]
                  const hasVisibleContent = blocks.some((b) => b.type === 'text' || b.type === 'tool_use')
                  return !hasVisibleContent
                })() && (
                  <TypingIndicator accentColor={accentColor} isThinking={isThinking} />
                )}
                {!(msg as any).streaming && getMessageText(msg) && (
                  <div className="opacity-0 group-hover/asstmsg:opacity-100 transition-opacity">
                    <button
                      onClick={() => onCopyMessage(msg, i)}
                      className="flex items-center justify-center w-6 h-6 rounded-md text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d]"
                      title={copiedIndex === i ? 'Copied' : 'Copy message'}
                    >
                      {copiedIndex === i ? <Check size={12} className="text-[#00FFA7]" /> : <Copy size={12} />}
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {msg.role === 'system' && (
            <div className="text-center">
              <span className="text-[11px] text-[#667085] bg-[#161b22] px-3 py-1 rounded-full border border-[#21262d]">
                {msg.text}
              </span>
            </div>
          )}
        </div>
      ))}

      {pendingApprovals.map((req) => (
        <ApprovalCard
          key={req.requestId}
          req={req}
          accentColor={accentColor}
          onAllow={() => onRespondToApproval(req.requestId, true)}
          onDeny={() => onRespondToApproval(req.requestId, false)}
        />
      ))}

      {status === 'running' && messages[messages.length - 1]?.role !== 'assistant' && (
        <div className="flex gap-3">
          <div className="flex-shrink-0 mt-0.5">
            <AgentAvatar name={agent} size={28} />
          </div>
          <TypingIndicator accentColor={accentColor} isThinking />
        </div>
      )}
    </div>
  )
}

// â”€â”€ Sub-components â”€â”€
// Suppress unused import warning

