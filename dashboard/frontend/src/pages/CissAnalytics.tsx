import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  BarChart3,
  Bot,
  Package,
  RefreshCw,
  Send,
  ShoppingCart,
  TrendingUp,
  Users,
  Warehouse,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../lib/api'

type SeriesKey = 'diario' | 'semanal' | 'mensal' | 'anual'

interface Summary {
  periodo_dias: number
  data_inicio: string
  data_fim: string
  total_vendas: number
  crescimento_pct: number
  transacoes: number
  ticket_medio: number
  ticket_mediano: number
  itens_vendidos: number
  skus_vendidos: number
  clientes_ativos: number
  vendedores_ativos: number
  margem_media_pct: number
  lucro_bruto_estimado: number
}

interface SeriesPoint {
  data?: string
  periodo?: string
  total_vendas: number
  transacoes: number
  ticket_medio: number
  itens_vendidos?: number
}

interface ProductRow {
  id_produto: string
  codigo_produto: string
  nome_produto: string
  categoria: string
  quantidade: number
  total_vendas: number
  participacao_pct: number
  transacoes: number
  preco_medio: number
  margem_media_pct: number
  lucro_estimado: number
}

interface SellerRow {
  id_vendedor: string
  nome_vendedor: string
  total_vendas: number
  transacoes: number
  ticket_medio: number
  itens_vendidos: number
}

interface ReplenishmentRow {
  id_produto: string
  codigo_produto: string
  nome_produto: string
  categoria: string
  quantidade_atual: number
  estoque_minimo: number
  consumo_medio_dia: number
  dias_cobertura: number | null
  nivel_alerta: string
  quantidade_sugerida_compra: number
  valor_estimado_compra: number
}

interface Inventory {
  total_posicoes: number
  resumo_alertas: Record<string, number>
  valor_estoque_estimado: number
  cobertura_media_dias: number | null
  reposicao_sugerida: ReplenishmentRow[]
  sem_estoque_configurado: boolean
}

interface Stats {
  media_vendas_dia: number
  mediana_vendas_dia: number
  desvio_padrao_vendas_dia: number
  media_transacoes_dia: number
  padrao_dia_semana: { dia_semana: string; media_vendas: number; amostras: number }[]
  clusters_produtos: (ProductRow & { cluster: string })[]
}

interface Predictive {
  modelo: string
  status: string
  horizonte_dias?: number
  tendencia?: string
  crescimento_pct?: number
  media_recente_dia?: number
  total_previsto_7_dias?: number
  total_previsto_30_dias?: number
  previsao: { data: string; vendas_previstas: number; confianca_min: number; confianca_max: number }[]
  historico: { data: string; vendas: number }[]
}

interface Snapshot {
  ok: boolean
  gerado_em: string
  resumo: Summary
  series: Record<SeriesKey, SeriesPoint[]>
  produtos: { top_vendas: ProductRow[]; categorias: { categoria: string; total_vendas: number; quantidade: number; skus: number }[] }
  vendedores: { ranking: SellerRow[] }
  unidades: {
    selected: string[]
    available: { key: string; label: string }[]
    ranking: { key: string; label: string; total_vendas: number; participacao_pct: number; transacoes: number }[]
  }
  estoque: Inventory
  estatistica: Stats
  preditivo: Predictive
  cobertura_dados: {
    datas_vendas: { inicio?: string; fim?: string }
    tabelas: Record<string, number>
    ultimo_etl?: { tipo: string; status: string; concluido_em?: string; registros_ins?: number } | null
  }
}

interface AgentAnswer {
  ok: boolean
  agent_name?: string
  agent_provider?: string
  topic: string
  confidence?: number
  answer: string
  suggested_questions?: string[]
}

const PERIODS = [
  { label: '30d', value: 30 },
  { label: '90d', value: 90 },
  { label: 'Ano', value: 365 },
]

const DEFAULT_UNITS = [
  { key: 'matriz', label: 'Matriz' },
  { key: 'buritis', label: 'Buritis' },
  { key: 'trinta_um_de_marco', label: '31 de Março' },
  { key: 'pb_tintas', label: 'P.B Tintas' },
  { key: 'callcenter', label: 'Callcenter' },
]

const SERIES_TABS: { label: string; value: SeriesKey }[] = [
  { label: 'Diario', value: 'diario' },
  { label: 'Semanal', value: 'semanal' },
  { label: 'Mensal', value: 'mensal' },
  { label: 'Anual', value: 'anual' },
]

function currency(value: number | undefined | null) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value ?? 0)
}

function compactCurrency(value: number | undefined | null) {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL',
    maximumFractionDigits: 0,
    notation: 'compact',
  }).format(value ?? 0)
}

function number(value: number | undefined | null) {
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(value ?? 0)
}

function percent(value: number | undefined | null) {
  return `${number(value)}%`
}

function pointLabel(point: SeriesPoint) {
  return point.periodo || point.data || ''
}

function severityClass(level: string) {
  if (level === 'critico') return 'text-red-300 bg-red-500/10 border-red-500/25'
  if (level === 'alerta') return 'text-amber-300 bg-amber-500/10 border-amber-500/25'
  if (level === 'atencao') return 'text-cyan-200 bg-cyan-500/10 border-cyan-500/25'
  return 'text-[#00FFA7] bg-[#00FFA7]/10 border-[#00FFA7]/20'
}

function StatTile({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string
  value: string
  detail?: string
  icon: typeof ShoppingCart
}) {
  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs uppercase tracking-wider text-[#667085]">{label}</p>
        <Icon size={17} className="text-[#00FFA7]" />
      </div>
      <p className="mt-3 text-2xl font-semibold text-[#f2f4f7]">{value}</p>
      {detail && <p className="mt-1 text-xs text-[#98a2b3]">{detail}</p>}
    </div>
  )
}

export default function CissAnalytics() {
  const [period, setPeriod] = useState(90)
  const [seriesTab, setSeriesTab] = useState<SeriesKey>('diario')
  const [selectedUnits, setSelectedUnits] = useState<string[]>([])
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [question, setQuestion] = useState('Quais produtos mais vendem?')
  const [agentAnswer, setAgentAnswer] = useState<AgentAnswer | null>(null)
  const [asking, setAsking] = useState(false)

  const loadSnapshot = useCallback(() => {
    setLoading(true)
    setError(null)
    const unitsParam = selectedUnits.join(',')
    const suffix = unitsParam ? `&unidades=${encodeURIComponent(unitsParam)}` : ''
    api.get(`/ciss/decision/overview?periodo=${period}&limit=20${suffix}`)
      .then(setSnapshot)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [period, selectedUnits])

  useEffect(() => {
    loadSnapshot()
  }, [loadSnapshot])

  const chartData = useMemo(() => {
    const rows = snapshot?.series?.[seriesTab] ?? []
    return rows.map((row) => ({ ...row, label: pointLabel(row) }))
  }, [snapshot, seriesTab])

  const askAgent = () => {
    const trimmed = question.trim()
    if (!trimmed) return
    setAsking(true)
    api.post('/ciss/decision/ask', { question: trimmed, periodo_dias: period })
      .then(setAgentAnswer)
      .catch((e) => setAgentAnswer({ ok: false, topic: 'erro', answer: e.message }))
      .finally(() => setAsking(false))
  }

  if (loading && !snapshot) {
    return (
      <div className="max-w-[1500px] mx-auto space-y-4">
        <div className="skeleton h-20 rounded-xl" />
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="skeleton h-32 rounded-xl" />
          <div className="skeleton h-32 rounded-xl" />
          <div className="skeleton h-32 rounded-xl" />
          <div className="skeleton h-32 rounded-xl" />
        </div>
        <div className="skeleton h-96 rounded-xl" />
      </div>
    )
  }

  if (error && !snapshot) {
    return (
      <div className="max-w-[1100px] mx-auto">
        <div className="bg-red-500/10 border border-red-500/25 rounded-xl p-6 text-red-200">
          <p className="font-semibold">Falha ao carregar CISS Analytics</p>
          <p className="text-sm mt-1 text-red-200/80">{error}</p>
        </div>
      </div>
    )
  }

  const data = snapshot
  if (!data) return null

  const summary = data.resumo
  const inventory = data.estoque
  const criticalStock = inventory.resumo_alertas.critico ?? 0
  const warningStock = inventory.resumo_alertas.alerta ?? 0
  const predictive = data.preditivo
  const unitOptions = data.unidades?.available?.length ? data.unidades.available : DEFAULT_UNITS
  const unitRanking = data.unidades?.ranking ?? []

  const toggleUnit = (key: string) => {
    setSelectedUnits((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    )
  }

  return (
    <div className="max-w-[1500px] mx-auto space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[#f2f4f7] tracking-tight">CISS Analytics Pau Brasil</h1>
          <p className="text-sm text-[#98a2b3] mt-1">
            Vendas, produtos, vendedores, estoque, estatistica e agente de decisao.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap items-center gap-1 rounded-lg border border-[#21262d] bg-[#161b22] p-1">
            {unitOptions.map((unit) => {
              const active = selectedUnits.includes(unit.key)
              return (
                <button
                  key={unit.key}
                  type="button"
                  onClick={() => toggleUnit(unit.key)}
                  className={`px-3 py-1.5 rounded-md text-xs transition-colors ${
                    active ? 'bg-[#00FFA7] text-[#07130f]' : 'text-[#98a2b3] hover:text-white'
                  }`}
                  title={`Filtrar por ${unit.label}`}
                >
                  {unit.label}
                </button>
              )
            })}
          </div>
          <div className="flex items-center rounded-lg border border-[#21262d] bg-[#161b22] p-1">
            {PERIODS.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => setPeriod(item.value)}
                className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                  period === item.value ? 'bg-[#00FFA7] text-[#07130f]' : 'text-[#98a2b3] hover:text-white'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={loadSnapshot}
            className="inline-flex items-center gap-2 rounded-lg border border-[#21262d] bg-[#161b22] px-3 py-2 text-sm text-[#d0d5dd] hover:border-[#00FFA7]/40"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            Atualizar
          </button>
        </div>
      </div>

      <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
        <div className="flex items-center justify-between gap-3 mb-3">
          <h2 className="text-sm font-semibold text-[#f2f4f7]">Faturamento por unidade</h2>
          <p className="text-xs text-[#667085]">
            {selectedUnits.length === 0 ? 'Todas as lojas e callcenter' : `${selectedUnits.length} filtro(s) ativo(s)`}
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3">
          {unitRanking.map((unit) => (
            <div key={unit.key} className="rounded-lg border border-[#21262d] bg-[#0c111d] p-3">
              <p className="text-xs uppercase tracking-wider text-[#98a2b3]">{unit.label}</p>
              <p className="mt-1 text-base font-semibold text-[#f2f4f7]">{currency(unit.total_vendas)}</p>
              <p className="text-xs text-[#667085]">{unit.transacoes} vendas | {percent(unit.participacao_pct)}</p>
            </div>
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <StatTile label="Receita" value={currency(summary.total_vendas)} detail={`${percent(summary.crescimento_pct)} vs periodo anterior`} icon={ShoppingCart} />
        <StatTile label="Vendas" value={number(summary.transacoes)} detail={`Ticket medio ${currency(summary.ticket_medio)}`} icon={BarChart3} />
        <StatTile label="Produtos" value={number(summary.skus_vendidos)} detail={`${number(summary.itens_vendidos)} itens vendidos`} icon={Package} />
        <StatTile label="Estoque critico" value={number(criticalStock)} detail={`${number(warningStock)} itens em alerta`} icon={Warehouse} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1.6fr_1fr] gap-6">
        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-5">
            <div>
              <h2 className="text-base font-semibold text-[#f2f4f7]">Evolucao de vendas</h2>
              <p className="text-xs text-[#667085] mt-1">
                {summary.data_inicio} a {summary.data_fim}
              </p>
            </div>
            <div className="flex items-center rounded-lg border border-[#21262d] bg-[#0c111d] p-1">
              {SERIES_TABS.map((tab) => (
                <button
                  key={tab.value}
                  type="button"
                  onClick={() => setSeriesTab(tab.value)}
                  className={`px-3 py-1.5 rounded-md text-xs transition-colors ${
                    seriesTab === tab.value ? 'bg-[#1f2937] text-white' : 'text-[#98a2b3] hover:text-white'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid stroke="#21262d" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="#667085" tick={{ fontSize: 11 }} minTickGap={24} />
                <YAxis stroke="#667085" tick={{ fontSize: 11 }} tickFormatter={compactCurrency} width={72} />
                <Tooltip
                  contentStyle={{ background: '#0c111d', border: '1px solid #344054', borderRadius: 8, color: '#f2f4f7' }}
                  formatter={(value) => currency(Number(value))}
                />
                <Line type="monotone" dataKey="total_vendas" stroke="#00FFA7" strokeWidth={2.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Bot size={18} className="text-[#00FFA7]" />
            <h2 className="text-base font-semibold text-[#f2f4f7]">{agentAnswer?.agent_name || 'Cientista-de-Dados'}</h2>
          </div>
          <p className="text-xs text-[#667085] mb-3">
            Especialista CISS para insights, conselhos de vendas e explicacao executiva dos dados.
          </p>
          <div className="flex gap-2">
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') askAgent()
              }}
              className="min-w-0 flex-1 rounded-lg border border-[#344054] bg-[#0c111d] px-3 py-2 text-sm text-[#f2f4f7] outline-none focus:border-[#00FFA7]/60"
              placeholder="Pergunte sobre produtos, estoque, vendedores..."
            />
            <button
              type="button"
              onClick={askAgent}
              disabled={asking}
              className="inline-flex items-center justify-center rounded-lg bg-[#00FFA7] px-3 py-2 text-[#07130f] disabled:opacity-60"
              title="Perguntar"
            >
              <Send size={16} />
            </button>
          </div>
          <div className="mt-4 min-h-40 rounded-lg border border-[#21262d] bg-[#0c111d] p-4">
            {agentAnswer ? (
              <>
                <div className="flex items-center justify-between gap-3 mb-3">
                  <span className="text-xs uppercase tracking-wider text-[#667085]">{agentAnswer.topic}</span>
                  <div className="text-right">
                    {agentAnswer.confidence !== undefined && (
                      <span className="block text-xs text-[#98a2b3]">{percent(agentAnswer.confidence * 100)} confianca</span>
                    )}
                    {agentAnswer.agent_provider && (
                      <span className="block text-[11px] text-[#667085]">{agentAnswer.agent_provider}</span>
                    )}
                  </div>
                </div>
                <p className="whitespace-pre-line text-sm leading-6 text-[#d0d5dd]">{agentAnswer.answer}</p>
              </>
            ) : (
              <p className="text-sm text-[#667085]">Use perguntas diretas para consultar o DW CISS.</p>
            )}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {(agentAnswer?.suggested_questions ?? [
              'Quais produtos mais vendem?',
              'Qual vendedor vendeu mais?',
              'O que precisa repor no estoque?',
            ]).map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => setQuestion(suggestion)}
                className="rounded-full border border-[#344054] px-3 py-1 text-xs text-[#98a2b3] hover:border-[#00FFA7]/40 hover:text-white"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-5">
            <Package size={18} className="text-[#00FFA7]" />
            <h2 className="text-base font-semibold text-[#f2f4f7]">Produtos que mais vendem</h2>
          </div>
          <div className="h-72 mb-5">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.produtos.top_vendas.slice(0, 8)}>
                <CartesianGrid stroke="#21262d" strokeDasharray="3 3" />
                <XAxis dataKey="nome_produto" stroke="#667085" tick={{ fontSize: 10 }} interval={0} height={72} angle={-30} textAnchor="end" />
                <YAxis stroke="#667085" tick={{ fontSize: 11 }} tickFormatter={compactCurrency} width={72} />
                <Tooltip
                  contentStyle={{ background: '#0c111d', border: '1px solid #344054', borderRadius: 8, color: '#f2f4f7' }}
                  formatter={(value) => currency(Number(value))}
                />
                <Bar dataKey="total_vendas" fill="#00FFA7" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-[#667085]">
                <tr>
                  <th className="text-left py-2 pr-4">Produto</th>
                  <th className="text-right py-2 pr-4">Qtd.</th>
                  <th className="text-right py-2">Venda</th>
                </tr>
              </thead>
              <tbody>
                {data.produtos.top_vendas.slice(0, 10).map((product) => (
                  <tr key={product.id_produto} className="border-t border-[#21262d]">
                    <td className="py-3 pr-4">
                      <p className="font-medium text-[#f2f4f7]">{product.nome_produto}</p>
                      <p className="text-xs text-[#667085]">{product.categoria}</p>
                    </td>
                    <td className="py-3 pr-4 text-right text-[#d0d5dd]">{number(product.quantidade)}</td>
                    <td className="py-3 text-right text-[#f2f4f7]">{currency(product.total_vendas)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-5">
            <Users size={18} className="text-[#00FFA7]" />
            <h2 className="text-base font-semibold text-[#f2f4f7]">Vendedores com maior venda</h2>
          </div>
          <div className="space-y-3">
            {data.vendedores.ranking.slice(0, 10).map((seller, index) => (
              <div key={seller.id_vendedor} className="grid grid-cols-[32px_1fr_auto] items-center gap-3 border-b border-[#21262d] pb-3 last:border-b-0">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#0c111d] text-sm text-[#98a2b3]">{index + 1}</span>
                <div className="min-w-0">
                  <p className="truncate font-medium text-[#f2f4f7]">{seller.nome_vendedor}</p>
                  <p className="text-xs text-[#667085]">{seller.transacoes} vendas | ticket {currency(seller.ticket_medio)}</p>
                </div>
                <span className="text-sm font-semibold text-[#00FFA7]">{currency(seller.total_vendas)}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1.2fr_1fr] gap-6">
        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-5">
            <AlertTriangle size={18} className="text-amber-300" />
            <h2 className="text-base font-semibold text-[#f2f4f7]">Reposicao de estoque</h2>
          </div>
          {inventory.sem_estoque_configurado ? (
            <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 p-4 text-sm text-amber-100">
              O DW ainda nao tem posicao de estoque carregada. Habilite o servico de estoque no CISS/Integrim para calcular ruptura e compra sugerida.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase tracking-wider text-[#667085]">
                  <tr>
                    <th className="text-left py-2 pr-4">Produto</th>
                    <th className="text-right py-2 pr-4">Atual</th>
                    <th className="text-right py-2 pr-4">Cobertura</th>
                    <th className="text-right py-2 pr-4">Comprar</th>
                    <th className="text-left py-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {inventory.reposicao_sugerida.slice(0, 12).map((item) => (
                    <tr key={`${item.id_produto}-${item.nivel_alerta}`} className="border-t border-[#21262d]">
                      <td className="py-3 pr-4">
                        <p className="font-medium text-[#f2f4f7]">{item.nome_produto}</p>
                        <p className="text-xs text-[#667085]">{item.categoria || item.codigo_produto}</p>
                      </td>
                      <td className="py-3 pr-4 text-right text-[#d0d5dd]">{number(item.quantidade_atual)}</td>
                      <td className="py-3 pr-4 text-right text-[#d0d5dd]">{item.dias_cobertura === null ? '-' : `${number(item.dias_cobertura)}d`}</td>
                      <td className="py-3 pr-4 text-right text-[#f2f4f7]">{number(item.quantidade_sugerida_compra)}</td>
                      <td className="py-3">
                        <span className={`inline-flex rounded-full border px-2 py-1 text-xs ${severityClass(item.nivel_alerta)}`}>{item.nivel_alerta}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-5">
            <TrendingUp size={18} className="text-[#00FFA7]" />
            <h2 className="text-base font-semibold text-[#f2f4f7]">Estatistica e clusters</h2>
          </div>
          {predictive?.status === 'ok' && (
            <div className="mb-5 rounded-xl border border-[#00FFA7]/20 bg-[#00FFA7]/5 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs uppercase tracking-wider text-[#667085]">Preditivo</p>
                  <p className="mt-1 text-sm text-[#d0d5dd]">
                    Tendencia {predictive.tendencia} com {percent(predictive.crescimento_pct)} de variacao recente.
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-[#667085]">Previsao 30 dias</p>
                  <p className="text-lg font-semibold text-[#00FFA7]">{currency(predictive.total_previsto_30_dias)}</p>
                </div>
              </div>
              <p className="mt-3 text-xs text-[#98a2b3]">
                Modelo {predictive.modelo}; proximos 7 dias: {currency(predictive.total_previsto_7_dias)}.
              </p>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3 mb-5">
            <div className="rounded-lg bg-[#0c111d] border border-[#21262d] p-3">
              <p className="text-xs text-[#667085]">Media diaria</p>
              <p className="mt-1 text-lg font-semibold text-[#f2f4f7]">{currency(data.estatistica.media_vendas_dia)}</p>
            </div>
            <div className="rounded-lg bg-[#0c111d] border border-[#21262d] p-3">
              <p className="text-xs text-[#667085]">Mediana diaria</p>
              <p className="mt-1 text-lg font-semibold text-[#f2f4f7]">{currency(data.estatistica.mediana_vendas_dia)}</p>
            </div>
            <div className="rounded-lg bg-[#0c111d] border border-[#21262d] p-3">
              <p className="text-xs text-[#667085]">Desvio padrao</p>
              <p className="mt-1 text-lg font-semibold text-[#f2f4f7]">{currency(data.estatistica.desvio_padrao_vendas_dia)}</p>
            </div>
            <div className="rounded-lg bg-[#0c111d] border border-[#21262d] p-3">
              <p className="text-xs text-[#667085]">Transacoes/dia</p>
              <p className="mt-1 text-lg font-semibold text-[#f2f4f7]">{number(data.estatistica.media_transacoes_dia)}</p>
            </div>
          </div>
          <div className="space-y-2">
            {data.estatistica.clusters_produtos.slice(0, 8).map((product) => (
              <div key={`${product.id_produto}-${product.cluster}`} className="flex items-center justify-between gap-3 rounded-lg border border-[#21262d] bg-[#0c111d] px-3 py-2">
                <span className="min-w-0 truncate text-sm text-[#d0d5dd]">{product.nome_produto}</span>
                <span className="shrink-0 rounded-full border border-[#344054] px-2 py-1 text-xs text-[#98a2b3]">{product.cluster}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
