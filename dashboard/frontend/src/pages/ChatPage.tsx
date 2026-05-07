import { useEffect, useState, useCallback, useRef } from 'react'
import { Bot, MessageSquare, ChevronDown, Search, X, Sparkles } from 'lucide-react'
import AgentChat from '../components/AgentChat'
import ChatSessionList, { type ChatSession } from '../components/ChatSessionList'
import { AgentAvatar } from '../components/AgentAvatar'
import { getAgentMeta } from '../lib/agent-meta'
import { useAuth } from '../context/AuthContext'
import { useNotificationBadge } from '../hooks/useNotificationBadge'

const isLocal = import.meta.env.DEV || /^(localhost|127\.0\.0\.1)$/i.test(window.location.hostname)
const TS_HTTP = isLocal
  ? `http://${window.location.hostname}:32352`
  : `${window.location.origin}/terminal`

interface AgentInfo { name: string; label: string; color: string; avatar?: string }

const STORAGE_AGENT = 'evo:chat-page-agent'
const STORAGE_SESSION = 'evo:chat-page-session'

function fmtName(slug: string) {
  return slug.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
}

export default function ChatPage() {
  const { hasAgentAccess } = useAuth()
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [agentsLoading, setAgentsLoading] = useState(true)
  const [selectedAgent, setSelectedAgent] = useState<string | null>(() => {
    try { return localStorage.getItem(STORAGE_AGENT) } catch { return null }
  })
  const [ddOpen, setDdOpen] = useState(false)
  const [ddSearch, setDdSearch] = useState('')
  const ddRef = useRef<HTMLDivElement>(null)
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeSession, setActiveSession] = useState<string | null>(() => {
    try { return localStorage.getItem(STORAGE_SESSION) } catch { return null }
  })
  const [connecting, setConnecting] = useState(false)
  const [connectErr, setConnectErr] = useState<string | null>(null)
  const [sideOpen, setSideOpen] = useState(false)
  const approvalsRef = useRef<Map<string, number>>(new Map())
  const [totalPending, setTotalPending] = useState(0)
  const [attention, setAttention] = useState(false)

  useEffect(() => {
    const h = () => { if (!document.hidden) setAttention(false) }
    document.addEventListener('visibilitychange', h)
    return () => document.removeEventListener('visibilitychange', h)
  }, [])

  useNotificationBadge(totalPending, attention)

  const onPending = useCallback((sid: string, c: number) => {
    approvalsRef.current.set(sid, c)
    setTotalPending(Array.from(approvalsRef.current.values()).reduce((a, b) => a + b, 0))
  }, [])

  const onAttention = useCallback(() => setAttention(true), [])

  // Load agents
  useEffect(() => {
    setAgentsLoading(true)
    fetch('/api/agents', { credentials: 'include' })
      .then(r => r.ok ? r.json() : { agents: [] })
      .then(data => {
        const names: string[] = Array.isArray(data) ? data : (data.agents || [])
        const list = names
          .filter(n => hasAgentAccess(n))
          .map(n => { const m = getAgentMeta(n); return { name: n, label: m.label || fmtName(n), color: m.color, avatar: m.avatar } })
          .sort((a, b) => a.label.localeCompare(b.label))
        setAgents(list)
        if (!selectedAgent && list.length > 0) setSelectedAgent(list[0].name)
      })
      .catch(() => setAgents([]))
      .finally(() => setAgentsLoading(false))
  }, [hasAgentAccess])

  // Close dropdown on outside click
  useEffect(() => {
    if (!ddOpen) return
    const down = (e: MouseEvent) => { if (ddRef.current && !ddRef.current.contains(e.target as Node)) { setDdOpen(false); setDdSearch('') } }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { setDdOpen(false); setDdSearch('') } }
    document.addEventListener('mousedown', down)
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('mousedown', down); document.removeEventListener('keydown', key) }
  }, [ddOpen])

  // Persist
  useEffect(() => { if (selectedAgent) try { localStorage.setItem(STORAGE_AGENT, selectedAgent) } catch {} }, [selectedAgent])
  useEffect(() => { if (activeSession) try { localStorage.setItem(STORAGE_SESSION, activeSession) } catch {} }, [activeSession])

  // Load sessions
  const loadSessions = useCallback(async () => {
    if (!selectedAgent) return
    try {
      const r = await fetch(`${TS_HTTP}/api/sessions/by-agent/${selectedAgent}`)
      if (!r.ok) return
      const d = await r.json()
      const list: ChatSession[] = (d.sessions || []).map((s: any) => ({
        id: s.id, name: s.name || selectedAgent, active: s.active,
        preview: s.preview || undefined,
        ts: typeof s.lastActivity === 'number' ? s.lastActivity : (s.lastActivity ? new Date(s.lastActivity).getTime() : undefined),
        ticketId: s.ticketId || null, archived: s.archived || false,
      }))
      setSessions(list)
      setActiveSession(prev => (prev && list.some(s => s.id === prev)) ? prev : (list.length > 0 ? list[0].id : null))
    } catch {}
  }, [selectedAgent])

  useEffect(() => { if (selectedAgent) loadSessions() }, [selectedAgent, loadSessions])

  // Auto-create session
  useEffect(() => {
    if (!selectedAgent || sessions.length > 0) return
    setConnectErr(null); setConnecting(true)
    fetch(`${TS_HTTP}/api/sessions/for-agent`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agentName: selectedAgent }),
    })
      .then(r => { if (!r.ok) throw new Error(); return r.json() })
      .then(d => {
        if (!d) return
        const s: ChatSession = { id: d.sessionId, name: d.session?.name || selectedAgent, active: d.session?.active ?? false, ts: Date.now() }
        setSessions([s]); setActiveSession(d.sessionId)
      })
      .catch(() => setConnectErr(`Não foi possível conectar ao terminal-server em ${TS_HTTP}.`))
      .finally(() => setConnecting(false))
  }, [selectedAgent, sessions.length])

  const createSession = useCallback(async () => {
    if (!selectedAgent) return
    try {
      const r = await fetch(`${TS_HTTP}/api/sessions/create`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agentName: selectedAgent }),
      })
      if (!r.ok) return
      const d = await r.json()
      const ns: ChatSession = { id: d.sessionId, name: d.session?.name || `${selectedAgent} #${sessions.length + 1}`, active: false, ts: Date.now() }
      setSessions(p => [ns, ...p]); setActiveSession(d.sessionId)
    } catch {}
  }, [selectedAgent, sessions])

  const selectSession = useCallback((id: string) => { setActiveSession(id); setSideOpen(false); loadSessions() }, [loadSessions])

  const renameSession = useCallback(async (id: string, name: string) => {
    try { await fetch(`${TS_HTTP}/api/sessions/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); loadSessions() } catch {}
  }, [loadSessions])

  const archiveSession = useCallback(async (id: string, archived: boolean) => {
    try { await fetch(`${TS_HTTP}/api/sessions/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ archived }) }); loadSessions() } catch {}
  }, [loadSessions])

  const deleteSession = useCallback(async (id: string) => {
    try { await fetch(`${TS_HTTP}/api/sessions/${id}`, { method: 'DELETE' }) } catch {}
    setSessions(p => p.filter(s => s.id !== id))
    setActiveSession(prev => prev !== id ? prev : (sessions.filter(s => s.id !== id && !s.archived)[0]?.id || null))
  }, [sessions])

  const pickAgent = useCallback((n: string) => {
    setSelectedAgent(n); setActiveSession(null); setSessions([]); setConnectErr(null); setDdOpen(false); setDdSearch('')
  }, [])

  const meta = selectedAgent ? getAgentMeta(selectedAgent) : null
  const accent = meta?.color || '#00FFA7'
  const filtered = ddSearch ? agents.filter(a => a.name.toLowerCase().includes(ddSearch.toLowerCase()) || a.label.toLowerCase().includes(ddSearch.toLowerCase())) : agents

  return (
    <div className="flex h-screen bg-[#0C111D] overflow-hidden">
      {/* Mobile toggle */}
      <button onClick={() => setSideOpen(true)} className="lg:hidden fixed top-20 left-4 z-50 p-2 rounded-lg bg-[#182230] border border-[#344054] text-[#D0D5DD] hover:text-[#00FFA7] transition-colors">
        <MessageSquare size={18} />
      </button>
      {sideOpen && <div className="fixed inset-0 bg-black/60 z-40 lg:hidden" onClick={() => setSideOpen(false)} />}

      {/* Sidebar */}
      <aside className={`fixed lg:relative z-50 lg:z-auto top-0 bottom-0 left-0 w-[320px] min-w-[320px] bg-[#0a0f1a] border-r border-[#21262d] flex flex-col transition-transform duration-200 lg:translate-x-0 ${sideOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}>
        {/* Header */}
        <div className="flex-shrink-0 px-4 pt-5 pb-3 border-b border-[#21262d]">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: `${accent}15`, border: `1px solid ${accent}25` }}>
                <Sparkles size={14} style={{ color: accent }} />
              </div>
              <div>
                <h2 className="text-[13px] font-semibold text-[#e6edf3] tracking-tight">Chat</h2>
                <p className="text-[10px] text-[#667085]">Multi-Agente</p>
              </div>
            </div>
            <button onClick={() => setSideOpen(false)} className="lg:hidden p-1 rounded hover:bg-white/10 text-[#667085]"><X size={16} /></button>
          </div>

          {/* Agent selector */}
          <div className="relative" ref={ddRef}>
            <button onClick={() => setDdOpen(v => !v)} className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border transition-all" style={{ borderColor: ddOpen ? `${accent}50` : '#21262d', background: ddOpen ? `${accent}08` : '#0d1117' }}>
              {selectedAgent ? (
                <>
                  <AgentAvatar name={selectedAgent} size={28} />
                  <div className="flex-1 min-w-0 text-left">
                    <p className="text-[12px] font-medium text-[#e6edf3] truncate">{fmtName(selectedAgent)}</p>
                    <p className="text-[10px] truncate" style={{ color: accent }}>{meta?.label || 'Agent'}</p>
                  </div>
                </>
              ) : (
                <>
                  <div className="w-7 h-7 rounded-full bg-[#161b22] border border-[#21262d] flex items-center justify-center"><Bot size={14} className="text-[#667085]" /></div>
                  <span className="text-[12px] text-[#667085] flex-1 text-left">Selecione um agente...</span>
                </>
              )}
              <ChevronDown size={14} className={`text-[#667085] transition-transform duration-200 ${ddOpen ? 'rotate-180' : ''}`} />
            </button>

            {ddOpen && (
              <div className="absolute mt-1.5 left-0 right-0 z-[100] rounded-xl border border-[#21262d] bg-[#0d1117] shadow-2xl max-h-80 flex flex-col overflow-hidden">
                <div className="flex items-center gap-2 px-3 py-2 border-b border-[#21262d]">
                  <Search size={13} className="text-[#667085] flex-shrink-0" />
                  <input type="text" value={ddSearch} onChange={e => setDdSearch(e.target.value)} placeholder="Buscar agente..." className="flex-1 bg-transparent text-[12px] text-[#e6edf3] placeholder-[#3F3F46] outline-none" autoFocus />
                  {ddSearch && <button onClick={() => setDdSearch('')} className="text-[#667085] hover:text-[#e6edf3]"><X size={12} /></button>}
                </div>
                <div className="overflow-y-auto max-h-64">
                  {agentsLoading ? (
                    <div className="px-3 py-4 text-center text-[11px] text-[#667085]">Carregando agentes...</div>
                  ) : filtered.length === 0 ? (
                    <div className="px-3 py-4 text-center text-[11px] text-[#667085]">Nenhum agente encontrado</div>
                  ) : filtered.map(a => (
                    <button key={a.name} onClick={() => pickAgent(a.name)} className="w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-white/5" style={{ background: a.name === selectedAgent ? `${a.color}10` : undefined }}>
                      <AgentAvatar name={a.name} size={26} />
                      <div className="flex-1 min-w-0">
                        <p className={`text-[12px] font-medium truncate ${a.name === selectedAgent ? 'text-[#e6edf3]' : 'text-[#c9d1d9]'}`}>{fmtName(a.name)}</p>
                        <p className="text-[10px] truncate" style={{ color: a.color }}>{a.label}</p>
                      </div>
                      {a.name === selectedAgent && <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: a.color, boxShadow: `0 0 4px ${a.color}88` }} />}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Session list */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <ChatSessionList sessions={sessions} activeSessionId={activeSession} onSelectSession={selectSession} onNewSession={createSession} accentColor={accent} approvalCounts={approvalsRef.current} onRename={renameSession} onArchive={archiveSession} onDelete={deleteSession} />
        </div>

        <div className="flex-shrink-0 px-4 py-3 border-t border-[#21262d]">
          <div className="flex items-center gap-2 text-[10px] text-[#3F3F46]">
            <Sparkles size={10} />
            <span>Memória global ativa — aprendizados persistem entre sessões</span>
          </div>
        </div>
      </aside>

      {/* Main chat area */}
      <main className="flex-1 flex flex-col min-w-0 bg-[#0C111D] relative">
        {selectedAgent && <div className="pointer-events-none absolute top-0 right-0 h-[400px] w-[400px] blur-3xl" style={{ background: `radial-gradient(circle, ${accent} 0%, transparent 60%)`, opacity: 0.04 }} />}
        {!selectedAgent ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center space-y-4 max-w-sm">
              <div className="w-16 h-16 mx-auto rounded-2xl bg-[#161b22] border border-[#21262d] flex items-center justify-center"><MessageSquare size={28} className="text-[#667085]" /></div>
              <div>
                <h3 className="text-[15px] font-semibold text-[#e6edf3] mb-1">Chat Multi-Agente</h3>
                <p className="text-[12px] text-[#667085] leading-relaxed">Selecione um agente na barra lateral para iniciar uma conversa. Os agentes possuem permissão total para executar qualquer ação necessária.</p>
              </div>
              <div className="flex flex-wrap justify-center gap-2 pt-2">
                {agents.slice(0, 6).map(a => (
                  <button key={a.name} onClick={() => pickAgent(a.name)} className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#21262d] bg-[#0d1117] hover:bg-[#161b22] transition-colors group">
                    <AgentAvatar name={a.name} size={20} />
                    <span className="text-[11px] text-[#8b949e] group-hover:text-[#e6edf3] transition-colors">{fmtName(a.name)}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <>
            <header className="flex-shrink-0 h-14 flex items-center px-4 lg:px-6 gap-3 border-b border-[#21262d] bg-[#0d1117]/80 backdrop-blur-sm relative z-10">
              <div className="rounded-full flex-shrink-0" style={{ padding: 2, background: `${accent}30` }}><AgentAvatar name={selectedAgent} size={32} /></div>
              <div className="flex flex-col min-w-0">
                <h2 className="text-[14px] font-semibold text-[#e6edf3] truncate">{fmtName(selectedAgent)}</h2>
                <p className="text-[10px] truncate" style={{ color: accent }}>{meta?.label || 'Agent'} · Permissão total</p>
              </div>
              <div className="ml-auto flex items-center gap-2">
                <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-medium" style={{ background: `${accent}10`, border: `1px solid ${accent}25`, color: accent }}>
                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: accent, boxShadow: `0 0 4px ${accent}88` }} />
                  Auto-approve ativo
                </span>
              </div>
            </header>
            <div className="flex-1 min-h-0 relative z-10">
              <AgentChat key={`chat-${selectedAgent}-${activeSession || 'default'}`} agent={selectedAgent} sessionId={activeSession || undefined} accentColor={accent} externalLoading={connecting} externalError={connectErr} autoApprove={true} onPendingCountChange={onPending} onNeedsAttention={onAttention} />
            </div>
          </>
        )}
      </main>
    </div>
  )
}
