import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Database, Globe, Loader2, Plug, Plus, Settings, X, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import IntegrationDrawer from '../components/IntegrationDrawer'
import { PageSkeleton } from '../components/PageStates'
import { CustomIntegrationModal } from './integrations/CustomIntegrationModal'
import { DatabasesTab } from './integrations/DatabasesTab'
import { IntegrationCard } from './integrations/IntegrationCard'
import { SocialAccountsTab } from './integrations/SocialAccountsTab'
import type {
  CustomIntegrationForm,
  DatabaseFlavor,
  Integration,
  SocialPlatform,
  TabKey,
} from './integrations/types'

interface StatCardProps {
  label: string
  value: string | number
  icon: LucideIcon
}

function StatCard({ label, value, icon: Icon }: StatCardProps) {
  return (
    <div className="group relative bg-[#161b22] border border-[#21262d] rounded-2xl p-5 transition-all duration-300 hover:border-[#00FFA7]/40 hover:shadow-[0_0_24px_rgba(0,255,167,0.06)]">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#00FFA7]/20 to-transparent rounded-t-2xl" />
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15">
          <Icon size={18} className="text-[#00FFA7]" />
        </div>
      </div>
      <p className="text-3xl font-bold text-[#e6edf3] tracking-tight">{value}</p>
      <p className="text-sm text-[#667085] mt-1">{label}</p>
    </div>
  )
}

const TAB_ITEMS: Array<{ key: TabKey; label: string; icon: LucideIcon }> = [
  { key: 'integrations', label: 'Integrations', icon: Plug },
  { key: 'social', label: 'Social', icon: Globe },
  { key: 'databases', label: 'Databases', icon: Database },
]

export default function Integrations() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabKey>('integrations')
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [platforms, setPlatforms] = useState<SocialPlatform[]>([])
  const [dbFlavors, setDbFlavors] = useState<DatabaseFlavor[]>([])
  const [loading, setLoading] = useState(true)
  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const [selectedIntegration, setSelectedIntegration] = useState<Integration | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [modalIsEdit, setModalIsEdit] = useState(false)
  const [modalInitial, setModalInitial] = useState<(CustomIntegrationForm & { slug: string }) | undefined>(undefined)
  const [deleteTarget, setDeleteTarget] = useState<Integration | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [envToast, setEnvToast] = useState(false)

  const loadData = useCallback(() => {
    Promise.all([
      api.get('/integrations').catch(() => ({ integrations: [] })),
      api.get('/social-accounts').catch(() => ({ platforms: [] })),
      api.get('/config/env').catch(() => ({ entries: [] })),
      api.get('/integrations/databases').catch(() => ({ flavors: [], total: 0 })),
    ]).then(([intData, socialData, envData, dbData]) => {
      const ints = (intData?.integrations || []).map((i: any) => ({
        name: i.name || '',
        type: i.type || i.category || '',
        status: (i.status === 'ok' || i.configured) ? 'ok' as const : 'pending' as const,
        kind: i.kind || 'core',
        slug: i.slug,
        description: i.description,
        envKeys: i.envKeys,
        category: i.category,
      }))
      setIntegrations(ints)
      setPlatforms(socialData?.platforms || [])
      setDbFlavors((dbData?.flavors as DatabaseFlavor[]) || [])

      const envMap: Record<string, string> = {}
      for (const entry of (envData?.entries ?? [])) {
        if (entry.type === 'var' && entry.key) {
          envMap[entry.key] = entry.value ?? ''
        }
      }
      setEnvValues(envMap)
    }).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleDisconnect = async (platformId: string, index: number) => {
    try {
      const data = await api.delete(`/social-accounts/${platformId}/${index}`)
      setPlatforms(data?.platforms || [])
    } catch (e) {
      console.error(e)
    }
  }

  const openCreateModal = () => {
    setModalInitial(undefined)
    setModalIsEdit(false)
    setModalOpen(true)
  }

  const openEditModal = (int: Integration) => {
    const rawKeys: string[] = int.envKeys || []
    setModalInitial({
      slug: int.slug || '',
      displayName: int.name,
      description: int.description || '',
      category: int.category || 'other',
      envKeys: rawKeys.map((k) => ({ name: k, value: '' })),
    })
    setModalIsEdit(true)
    setModalOpen(true)
  }

  const handleDeleteConfirm = async () => {
    if (!deleteTarget?.slug) return
    setDeleting(true)
    try {
      await api.delete(`/integrations/custom/${deleteTarget.slug}`)
      setDeleteTarget(null)
      loadData()
    } catch (e) {
      console.error(e)
    } finally {
      setDeleting(false)
    }
  }

  const coreIntegrations = integrations.filter((i) => i.kind === 'core')
  const customIntegrations = integrations.filter((i) => i.kind === 'custom')
  const connectedCount = integrations.filter((i) => i.status === 'ok').length
  const totalSocialAccounts = platforms.reduce((sum, p) => sum + p.accounts.length, 0)
  const connectedPlatformsCount = platforms.filter((p) => p.has_connected).length
  const totalDbConnections = dbFlavors.reduce((sum, f) => sum + f.count, 0)
  const sqlDbCount = dbFlavors.filter((f) => f.slug === 'postgres' || f.slug === 'mysql').reduce((sum, f) => sum + f.count, 0)
  const nosqlDbCount = dbFlavors.filter((f) => f.slug === 'mongo' || f.slug === 'redis').reduce((sum, f) => sum + f.count, 0)

  if (loading) {
    return (
      <div className="max-w-[1400px] mx-auto">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('integrations.title')}</h1>
          <p className="text-[#667085] text-sm mt-1">Connected services, APIs, social accounts & databases</p>
        </div>
        <PageSkeleton rows={4} cards={3} />
      </div>
    )
  }

  return (
    <div className="max-w-[1400px] mx-auto">
      <IntegrationDrawer
        integration={selectedIntegration}
        envValues={envValues}
        onClose={() => setSelectedIntegration(null)}
        onSaved={() => {
          setSelectedIntegration(null)
          loadData()
        }}
      />

      <CustomIntegrationModal
        open={modalOpen}
        initial={modalInitial}
        isEdit={modalIsEdit}
        onClose={() => setModalOpen(false)}
        onSaved={(envWritten) => {
          loadData()
          if (envWritten) {
            setEnvToast(true)
            setTimeout(() => setEnvToast(false), 6000)
          }
        }}
      />

      {envToast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-5 py-3 rounded-xl bg-[#161b22] border border-[#00FFA7]/30 shadow-2xl text-sm text-[#e6edf3]">
          <CheckCircle2 size={16} className="text-[#00FFA7] shrink-0" />
          <span>Saved - env values written to <code className="text-[#00FFA7] font-mono text-xs">.env</code>. Restart services to pick up the new values.</span>
          <button type="button" onClick={() => setEnvToast(false)} className="ml-2 text-[#667085] hover:text-[#e6edf3]">
            <X size={14} />
          </button>
        </div>
      )}

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={() => setDeleteTarget(null)} />
          <div className="relative w-full max-w-sm bg-[#0C111D] border border-[#21262d] rounded-2xl shadow-2xl p-6">
            <h3 className="text-base font-semibold text-[#e6edf3] mb-2">Delete Custom Integration</h3>
            <p className="text-sm text-[#667085] mb-5">
              Delete <span className="text-[#e6edf3] font-medium">{deleteTarget.name}</span>? This removes the SKILL.md file permanently.
            </p>
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="px-4 py-2 rounded-lg text-sm text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteConfirm}
                disabled={deleting}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-red-500/80 text-white text-sm font-semibold hover:bg-red-500 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {deleting && <Loader2 size={14} className="animate-spin" />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="mb-8">
        <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('integrations.title')}</h1>
        <p className="text-[#667085] text-sm mt-1">Connected services, APIs, social accounts & databases</p>
      </div>

      <div className="mb-6 flex items-center gap-1 p-1 rounded-xl bg-[#0C111D] border border-[#21262d] w-fit">
        {TAB_ITEMS.map(({ key, label, icon: TabIcon }) => {
          const active = activeTab === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                active
                  ? 'bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/25 shadow-[0_0_12px_rgba(0,255,167,0.08)]'
                  : 'text-[#667085] hover:text-[#e6edf3] border border-transparent'
              }`}
            >
              <TabIcon size={14} />
              {label}
            </button>
          )
        })}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        {activeTab === 'integrations' ? (
          <>
            <StatCard label="Connected" value={connectedCount} icon={CheckCircle2} />
            <StatCard label="Core Integrations" value={coreIntegrations.length} icon={Plug} />
            <StatCard label="Custom Integrations" value={customIntegrations.length} icon={Settings} />
          </>
        ) : activeTab === 'social' ? (
          <>
            <StatCard label="Connected Platforms" value={connectedPlatformsCount} icon={CheckCircle2} />
            <StatCard label="Social Accounts" value={totalSocialAccounts} icon={Globe} />
            <StatCard label="Platforms Available" value={platforms.length} icon={Plug} />
          </>
        ) : (
          <>
            <StatCard label="Total Databases" value={totalDbConnections} icon={Database} />
            <StatCard label="SQL (Postgres, MySQL)" value={sqlDbCount} icon={Plug} />
            <StatCard label="NoSQL (Mongo, Redis)" value={nosqlDbCount} icon={Plug} />
          </>
        )}
      </div>

      {activeTab === 'integrations' && (
        <>
          <div className="mb-10">
            <div className="flex items-center gap-2.5 mb-4">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
                <Plug size={14} className="text-[#00FFA7]" />
              </div>
              <h2 className="text-base font-semibold text-[#e6edf3]">Core Integrations</h2>
              <span className="text-xs px-2 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20">
                {coreIntegrations.length}
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {coreIntegrations.map((int) => (
                <IntegrationCard
                  key={int.slug || int.name}
                  int={int}
                  onSelect={setSelectedIntegration}
                />
              ))}
            </div>
          </div>

          <div className="mb-10">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2.5">
                <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
                  <Settings size={14} className="text-[#00FFA7]" />
                </div>
                <h2 className="text-base font-semibold text-[#e6edf3]">Custom Integrations</h2>
                {customIntegrations.length > 0 && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20">
                    {customIntegrations.length}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={openCreateModal}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 transition-all"
              >
                <Plus size={13} /> Add Custom
              </button>
            </div>

            {customIntegrations.length === 0 ? (
              <div
                onClick={openCreateModal}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    openCreateModal()
                  }
                }}
                className="cursor-pointer rounded-xl border border-dashed border-[#21262d] hover:border-[#00FFA7]/30 bg-[#161b22]/50 p-8 flex flex-col items-center justify-center gap-2 transition-colors group"
              >
                <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15 group-hover:bg-[#00FFA7]/15 transition-colors">
                  <Plus size={20} className="text-[#00FFA7]" />
                </div>
                <p className="text-sm font-medium text-[#667085] group-hover:text-[#e6edf3] transition-colors">Add custom integration</p>
                <p className="text-xs text-[#3F3F46]">Creates a SKILL.md template in .claude/skills/</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {customIntegrations.map((int) => (
                  <IntegrationCard
                    key={int.slug || int.name}
                    int={int}
                    onSelect={() => {}}
                    onEdit={openEditModal}
                    onDelete={setDeleteTarget}
                  />
                ))}
                <div
                  onClick={openCreateModal}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      openCreateModal()
                    }
                  }}
                  className="cursor-pointer rounded-xl border border-dashed border-[#21262d] hover:border-[#00FFA7]/30 bg-[#161b22]/50 p-5 flex flex-col items-center justify-center gap-2 transition-colors group min-h-[120px]"
                >
                  <Plus size={18} className="text-[#3F3F46] group-hover:text-[#00FFA7] transition-colors" />
                  <p className="text-xs text-[#3F3F46] group-hover:text-[#667085] transition-colors">Add custom integration</p>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {activeTab === 'social' && (
        <SocialAccountsTab platforms={platforms} onDisconnect={handleDisconnect} />
      )}

      {activeTab === 'databases' && (
        <DatabasesTab flavors={dbFlavors} onReload={loadData} />
      )}
    </div>
  )
}
