import { useState, useEffect, useCallback } from 'react'
import { NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'
import { useCommandPalette } from './CommandPalette'
import NotificationBell from './NotificationBell'
import { DOCS_NAV_ITEM, getVisibleNavGroups, type NavGroup, type NavItem } from '../lib/navigation'
import {
  ArrowUpCircle,
  ChevronDown,
  LogOut,
  Menu,
  Moon,
  Search,
  Sun,
  X,
} from 'lucide-react'

interface VersionInfo {
  current: string
  latest: string | null
  update_available: boolean
  release_url: string | null
  release_notes: string | null
}

const STORAGE_KEY = 'sidebar-collapsed-groups'

function loadCollapsedState(): Record<string, boolean> {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) return JSON.parse(stored)
  } catch {}
  return {}
}

function saveCollapsedState(state: Record<string, boolean>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {}
}

const roleBadgeClass: Record<string, string> = {
  admin: 'bg-purple-500/20 text-purple-400',
  operator: 'bg-blue-500/20 text-blue-400',
  viewer: 'bg-gray-500/20 text-gray-400',
}

export default function Sidebar() {
  const { user, logout, hasPermission } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const { openCommandPalette } = useCommandPalette()
  const { t } = useTranslation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadCollapsedState)
  const visibleGroups = getVisibleNavGroups(hasPermission)

  useEffect(() => {
    fetch('/api/version/check')
      .then((r) => r.json())
      .then((data) => setVersionInfo(data))
      .catch(() => {})
  }, [])

  const toggleGroup = useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = { ...prev, [key]: !prev[key] }
      saveCollapsedState(next)
      return next
    })
  }, [])

  const renderLink = (item: NavItem) => (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.to === '/'}
      onClick={() => setMobileOpen(false)}
      className={({ isActive }) =>
        `items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
          isActive
            ? 'text-[#00FFA7] bg-[#00FFA7]/10 border-l-2 border-[#00FFA7]'
            : 'text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 border-l-2 border-transparent'
        }`
      }
    >
      <item.icon size={16} />
      {t(`nav.${item.labelKey}`)}
    </NavLink>
  )

  const renderGroup = (group: NavGroup) => {
    const isCollapsed = collapsed[group.key] ?? false

    return (
      <div key={group.key} className="mb-1">
        {group.collapsible ? (
          <button
            onClick={() => toggleGroup(group.key)}
            className="group mt-2 flex w-full cursor-pointer items-center justify-between px-3 py-1.5"
          >
            <span className="select-none text-[10px] font-semibold uppercase tracking-wider text-[#667085]">
              {t(`nav.groups.${group.key}`)}
            </span>
            <ChevronDown
              size={12}
              className={`text-[#667085] transition-transform duration-200 group-hover:text-[#D0D5DD] ${
                isCollapsed ? '-rotate-90' : ''
              }`}
            />
          </button>
        ) : (
          <div className="px-3 py-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-[#667085]">
              {t(`nav.groups.${group.key}`)}
            </span>
          </div>
        )}

        <div
          className={`overflow-hidden transition-all duration-200 ease-in-out ${
            group.collapsible && isCollapsed ? 'max-h-0 opacity-0' : 'max-h-96 opacity-100'
          }`}
        >
          <div className="flex flex-col gap-0.5">
            {group.items.map(renderLink)}
          </div>
        </div>
      </div>
    )
  }

  const sidebarContent = (
    <>
      <div className="flex items-center justify-between px-5 py-6">
        <img src="/EVO_NEXUS.webp" alt="EvoNexus" className="h-8 w-auto" />
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => openCommandPalette()}
            className="hidden rounded p-1.5 text-[#667085] transition-colors hover:bg-white/10 hover:text-[#D0D5DD] lg:inline-flex"
            title="Search (Ctrl+K)"
          >
            <Search size={16} />
          </button>
          <button
            type="button"
            onClick={toggleTheme}
            className="rounded p-1.5 text-[#667085] transition-colors hover:bg-white/10 hover:text-[#D0D5DD]"
            title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <NotificationBell />
          <button
            onClick={() => setMobileOpen(false)}
            className="rounded p-1 text-[#667085] hover:bg-white/10 lg:hidden"
          >
            <X size={20} />
          </button>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        {visibleGroups.map(renderGroup)}

        <div className="mt-2">
          <NavLink
            to={DOCS_NAV_ITEM.to}
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? 'text-[#00FFA7] bg-[#00FFA7]/10 border-l-2 border-[#00FFA7]'
                  : 'text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 border-l-2 border-transparent'
              }`
            }
          >
            <DOCS_NAV_ITEM.icon size={16} />
            {t('nav.docs')}
          </NavLink>
        </div>
      </nav>

      {user && (
        <div className="border-t border-[#344054] px-4 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#00FFA7]/20 text-sm font-bold text-[#00FFA7]">
              {(user.display_name || user.username).charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-white">{user.display_name || user.username}</p>
              <span
                className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  roleBadgeClass[user.role] || roleBadgeClass.viewer
                }`}
              >
                {user.role}
              </span>
            </div>
            <button
              onClick={logout}
              className="shrink-0 rounded-lg p-1.5 text-[#667085] transition-colors hover:bg-red-500/10 hover:text-red-400"
              title={t('nav.logout')}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      )}

      {versionInfo && (
        <div className="border-t border-[#344054]/50 px-4 py-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-[#667085]">v{versionInfo.current}</span>
            {versionInfo.update_available && versionInfo.release_url && (
              <a
                href={versionInfo.release_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-[#00FFA7] transition-colors hover:text-[#00FFA7]/80"
                title={t('nav.updateAvailable', { version: versionInfo.latest })}
              >
                <ArrowUpCircle size={12} />
                <span>v{versionInfo.latest}</span>
              </a>
            )}
          </div>
        </div>
      )}

      <div className="border-t border-[#344054]/50 px-4 py-3">
        <a
          href="https://evolutionfoundation.com.br"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1.5 text-[10px] text-[#667085] transition-colors hover:text-[#00FFA7]"
        >
          by <span className="font-semibold text-[#00FFA7]/60">Evolution Foundation</span>
        </a>
      </div>
    </>
  )

  return (
    <>
      <button
        onClick={() => setMobileOpen(true)}
        className="fixed left-4 top-4 z-50 rounded-lg border border-[color:var(--border)] bg-[color:var(--bg-card)] p-2 text-[color:var(--text-secondary)] transition-colors hover:text-[#00FFA7] lg:hidden"
      >
        <Menu size={20} />
      </button>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 lg:hidden" onClick={() => setMobileOpen(false)} />
      )}

      <aside
        className={`
          fixed bottom-0 left-0 top-0 z-50 flex w-60 flex-col border-r border-[#344054]
          bg-[color:var(--bg-sidebar)] transition-transform duration-200 ease-in-out
          lg:translate-x-0
          ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
        `}
      >
        {sidebarContent}
      </aside>
    </>
  )
}

