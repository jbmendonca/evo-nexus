import { useEffect, useState } from 'react'
import { CheckCircle2, RefreshCw, Puzzle, Trash2, Download } from 'lucide-react'
import { api } from '../lib/api'
import { PageSkeleton } from '../components/PageStates'

type Plugin = {
  id: string
  name: string
  version: string
  category: string
  description: string
  installed: boolean
  installed_at?: string
  installed_files?: string[]
}

export default function Plugins() {
  const [plugins, setPlugins] = useState<Plugin[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const load = async () => {
    setLoading(true)
    setMessage(null)
    try {
      const data = await api.get('/plugins')
      setPlugins(data.plugins || [])
    } catch {
      setPlugins([])
      setMessage({ type: 'error', text: 'Failed to load plugins' })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const toggle = async (pluginId: string, installed: boolean) => {
    setBusy(pluginId)
    setMessage(null)
    try {
      if (installed) {
        await api.post(`/plugins/${pluginId}/uninstall`)
        setMessage({ type: 'success', text: `${pluginId} uninstalled` })
      } else {
        await api.post('/plugins/install', { plugin_id: pluginId })
        setMessage({ type: 'success', text: `${pluginId} installed` })
      }
      await load()
    } catch {
      setMessage({ type: 'error', text: `Failed to update ${pluginId}` })
    } finally {
      setBusy(null)
    }
  }

  if (loading && plugins.length === 0) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-bold text-white">Plugins</h1>
          <p className="text-sm text-[#5a6b7f]">Install and manage agent packs from the local registry.</p>
        </div>
        <PageSkeleton rows={4} cards={2} />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Plugins</h1>
          <p className="mt-1 text-sm text-[#5a6b7f]">Install and manage agent packs from the local registry.</p>
        </div>
        <button
          type="button"
          onClick={load}
          className="inline-flex items-center gap-2 rounded-xl border border-[#1e2a3a] bg-[#0b1018] px-4 py-2 text-sm font-medium text-[#e2e8f0] transition-colors hover:border-[#00FFA7]/40 hover:text-[#00FFA7]"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {message && (
        <div className={`rounded-2xl border px-4 py-3 text-sm ${message.type === 'success' ? 'border-[#00FFA7]/20 bg-[#00FFA7]/5 text-[#00FFA7]' : 'border-red-500/20 bg-red-500/5 text-red-200'}`}>
          {message.text}
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        {plugins.map((plugin) => (
          <div key={plugin.id} className="rounded-2xl border border-[color:var(--border)] bg-[var(--bg-card)] p-5 shadow-[0_18px_40px_rgba(0,0,0,0.15)]">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <Puzzle size={16} className="text-[#00FFA7]" />
                  <h2 className="truncate text-base font-semibold text-[color:var(--text-primary)]">{plugin.name}</h2>
                  <span className="rounded-full border border-[#1e2a3a] bg-[#0b1018] px-2 py-0.5 text-[10px] uppercase tracking-wide text-[#5a6b7f]">
                    {plugin.category}
                  </span>
                </div>
                <p className="mt-2 text-sm text-[color:var(--text-secondary)]">{plugin.description}</p>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/5 p-2 text-[#00FFA7]">
                {plugin.installed ? <CheckCircle2 size={18} /> : <Download size={18} />}
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-[#94a3b8]">
              <span className="rounded-full border border-[#1e2a3a] bg-[#0b1018] px-2.5 py-1">v{plugin.version}</span>
              <span className={`rounded-full border px-2.5 py-1 ${plugin.installed ? 'border-[#00FFA7]/20 bg-[#00FFA7]/5 text-[#00FFA7]' : 'border-[#1e2a3a] bg-[#0b1018] text-[#5a6b7f]'}`}>
                {plugin.installed ? 'Installed' : 'Available'}
              </span>
              {plugin.installed_at && (
                <span className="rounded-full border border-[#1e2a3a] bg-[#0b1018] px-2.5 py-1">
                  {new Date(plugin.installed_at).toLocaleString()}
                </span>
              )}
            </div>

            {plugin.installed_files && plugin.installed_files.length > 0 && (
              <div className="mt-4 rounded-xl border border-white/5 bg-[#0b1018] p-3">
                <p className="text-[11px] uppercase tracking-[0.16em] text-[#667085]">Installed files</p>
                <div className="mt-2 space-y-1 text-xs text-[#cbd5e1]">
                  {plugin.installed_files.map((file) => (
                    <div key={file} className="truncate font-mono">{file}</div>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-4 flex items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => toggle(plugin.id, plugin.installed)}
                disabled={busy === plugin.id}
                className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
                  plugin.installed
                    ? 'border border-red-500/20 bg-red-500/5 text-red-200 hover:bg-red-500/10'
                    : 'border border-[#00FFA7]/20 bg-[#00FFA7]/5 text-[#00FFA7] hover:bg-[#00FFA7]/10'
                }`}
              >
                {busy === plugin.id ? <RefreshCw size={14} className="animate-spin" /> : plugin.installed ? <Trash2 size={14} /> : <Download size={14} />}
                {plugin.installed ? 'Uninstall' : 'Install'}
              </button>
              {plugin.installed && (
                <span className="text-xs text-[#00FFA7]">Ready for use</span>
              )}
            </div>
          </div>
        ))}
      </div>

      {plugins.length === 0 && !loading && (
        <div className="rounded-2xl border border-dashed border-[#1e2a3a] px-4 py-10 text-center text-sm text-[#5a6b7f]">
          No plugins available.
        </div>
      )}
    </div>
  )
}
