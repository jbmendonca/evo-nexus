import { useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart3, Crown, Package, RefreshCw, ShoppingCart, UserCheck, UserX, Users } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../lib/api'

type ClasseAbc = '' | 'A' | 'B' | 'C'

interface ClienteRankingRow {
  id_cliente: string
  nome_cliente: string
  cidade: string | null
  estado: string | null
  segmento_rfm: string | null
  classificacao_abc: string | null
  num_compras: number
  itens_comprados: number
  valor_total: number
  ticket_medio: number
  skus_distintos: number
  primeira_compra: string | null
  ultima_compra: string | null
  dias_desde_ultima: number | null
}

interface AbcDistributionRow {
  classificacao_abc: string | null
  total_clientes: number
  total_valor: number
}

interface ClientesRankingResponse {
  clientes: ClienteRankingRow[]
  distribuicao_abc: AbcDistributionRow[]
}

interface TopClienteRow {
  id_cliente: string
  nome: string | null
  cidade: string | null
  estado: string | null
  rfm_segmento: string | null
  total_compras: number
  num_compras: number
  ticket_medio: number
  ultima_compra: string | null
}

interface TopClientesResponse {
  clientes: TopClienteRow[]
}

interface ClienteProdutoRow {
  id_produto: string
  nome_produto: string
  categoria: string | null
  quantidade_total: number
  valor_total: number
  num_compras: number
  preco_medio: number
  participacao_valor_cliente?: number
  participacao_qtd_cliente?: number
}

interface ClienteProdutosResponse {
  cliente: ClienteRankingRow
  top_produtos_por_valor: ClienteProdutoRow[]
  top_produtos_por_quantidade: ClienteProdutoRow[]
}

const PERIODOS = [
  { label: '30d', value: 30 },
  { label: '90d', value: 90 },
  { label: 'Ano', value: 365 },
]

const ABC_CORES: Record<string, string> = {
  A: '#00FFA7',
  B: '#22d3ee',
  C: '#a78bfa',
}

function currency(value: number | undefined | null) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value ?? 0)
}

function compactCurrency(value: number | undefined | null) {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value ?? 0)
}

function number(value: number | undefined | null) {
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(value ?? 0)
}

function percent(value: number | undefined | null) {
  return `${number(value)}%`
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
  icon: typeof Users
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

export default function PainelClientes() {
  const [periodo, setPeriodo] = useState(90)
  const [classeAbc, setClasseAbc] = useState<ClasseAbc>('')
  const [segmentoRfm, setSegmentoRfm] = useState('')
  const [ranking, setRanking] = useState<ClientesRankingResponse | null>(null)
  const [topClientes, setTopClientes] = useState<TopClientesResponse | null>(null)
  const [selectedClienteId, setSelectedClienteId] = useState<string | null>(null)
  const [clienteProdutos, setClienteProdutos] = useState<ClienteProdutosResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingCliente, setLoadingCliente] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const carregarDados = useCallback(() => {
    setLoading(true)
    setError(null)
    const abcParam = classeAbc ? `&classe_abc=${classeAbc}` : ''
    const segmentoParam = segmentoRfm ? `&segmento_rfm=${encodeURIComponent(segmentoRfm)}` : ''
    const rankingUrl = `/ciss/dashboard/clientes/ranking?limit=150${abcParam}${segmentoParam}`

    const topFallbackFromRanking = (clientes: ClienteRankingRow[]): TopClientesResponse => ({
      clientes: clientes.slice(0, 10).map((item) => ({
        id_cliente: item.id_cliente,
        nome: item.nome_cliente,
        cidade: item.cidade,
        estado: item.estado,
        rfm_segmento: item.segmento_rfm,
        total_compras: item.valor_total,
        num_compras: item.num_compras,
        ticket_medio: item.ticket_medio,
        ultima_compra: item.ultima_compra,
      })),
    });

    (api.get(rankingUrl) as Promise<ClientesRankingResponse>)
      .then((rankingResp) => {
        setRanking(rankingResp)
        setTopClientes(topFallbackFromRanking(rankingResp.clientes))
        setSelectedClienteId((current) => {
          if (current && rankingResp.clientes.some((item) => item.id_cliente === current)) return current
          return rankingResp.clientes[0]?.id_cliente ?? null
        })
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [classeAbc, periodo, segmentoRfm])

  useEffect(() => {
    carregarDados()
  }, [carregarDados])

  useEffect(() => {
    if (!selectedClienteId) {
      setClienteProdutos(null)
      return
    }
    setLoadingCliente(true)
    api.get(`/ciss/dashboard/clientes/${encodeURIComponent(selectedClienteId)}/top-produtos?limit=8`)
      .then((resp) => setClienteProdutos(resp as ClienteProdutosResponse))
      .catch(() => setClienteProdutos(null))
      .finally(() => setLoadingCliente(false))
  }, [selectedClienteId])

  const segmentosDisponiveis = useMemo(() => {
    if (!ranking) return []
    return [...new Set(ranking.clientes.map((item) => item.segmento_rfm).filter(Boolean) as string[])].sort()
  }, [ranking])

  const resumo = useMemo(() => {
    const clientes = ranking?.clientes ?? []
    const totalClientes = clientes.length
    const totalReceita = clientes.reduce((sum, item) => sum + (item.valor_total ?? 0), 0)
    const totalCompras = clientes.reduce((sum, item) => sum + (item.num_compras ?? 0), 0)
    const ativos30d = clientes.filter((item) => (item.dias_desde_ultima ?? 9999) <= 30).length
    const inativos90d = clientes.filter((item) => (item.dias_desde_ultima ?? 0) > 90).length
    const ticketMedio = totalCompras > 0 ? totalReceita / totalCompras : 0
    return { totalClientes, totalReceita, totalCompras, ativos30d, inativos90d, ticketMedio }
  }, [ranking])

  const dadosAbc = useMemo(() => {
    const rows = ranking?.distribuicao_abc ?? []
    return rows.map((item) => ({
      ...item,
      classificacao_abc: (item.classificacao_abc || 'N/D').toUpperCase(),
      total_valor: item.total_valor ?? 0,
      total_clientes: item.total_clientes ?? 0,
    }))
  }, [ranking])

  if (loading && !ranking) {
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

  if (error && !ranking) {
    return (
      <div className="max-w-[1100px] mx-auto">
        <div className="bg-red-500/10 border border-red-500/25 rounded-xl p-6 text-red-200">
          <p className="font-semibold">Falha ao carregar Painel Clientes</p>
          <p className="text-sm mt-1 text-red-200/80">{error}</p>
        </div>
      </div>
    )
  }

  if (!ranking) return null

  return (
    <div className="max-w-[1500px] mx-auto space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[#f2f4f7] tracking-tight">Painel Clientes</h1>
          <p className="text-sm text-[#98a2b3] mt-1">
            Analitico de clientes com ranking, segmentacao ABC e cesta de compra.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center rounded-lg border border-[#21262d] bg-[#161b22] p-1">
            {PERIODOS.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => setPeriodo(item.value)}
                className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                  periodo === item.value ? 'bg-[#00FFA7] text-[#07130f]' : 'text-[#98a2b3] hover:text-white'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={carregarDados}
            className="inline-flex items-center gap-2 rounded-lg border border-[#21262d] bg-[#161b22] px-3 py-2 text-sm text-[#d0d5dd] hover:border-[#00FFA7]/40"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            Atualizar
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <StatTile label="Clientes no ranking" value={number(resumo.totalClientes)} detail="Clientes com compras registradas" icon={Users} />
        <StatTile label="Receita consolidada" value={currency(resumo.totalReceita)} detail={`${number(resumo.totalCompras)} compras no total`} icon={ShoppingCart} />
        <StatTile label="Ticket medio" value={currency(resumo.ticketMedio)} detail="Media ponderada por compra" icon={BarChart3} />
        <StatTile label="Inativos > 90 dias" value={number(resumo.inativos90d)} detail={`${number(resumo.ativos30d)} ativos nos ultimos 30 dias`} icon={UserX} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1.7fr_1fr] gap-6">
        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between mb-5">
            <div>
              <h2 className="text-base font-semibold text-[#f2f4f7]">Ranking 360 de clientes</h2>
              <p className="text-xs text-[#667085] mt-1">Classificacao ABC, recorrencia e valor acumulado.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center rounded-lg border border-[#21262d] bg-[#0c111d] p-1">
                {(['', 'A', 'B', 'C'] as ClasseAbc[]).map((classe) => (
                  <button
                    key={classe || 'all'}
                    type="button"
                    onClick={() => setClasseAbc(classe)}
                    className={`px-3 py-1.5 rounded-md text-xs transition-colors ${
                      classeAbc === classe ? 'bg-[#1f2937] text-white' : 'text-[#98a2b3] hover:text-white'
                    }`}
                  >
                    {classe || 'Todos'}
                  </button>
                ))}
              </div>
              <select
                value={segmentoRfm}
                onChange={(event) => setSegmentoRfm(event.target.value)}
                className="rounded-lg border border-[#344054] bg-[#0c111d] px-3 py-2 text-sm text-[#d0d5dd] focus:border-[#00FFA7]/40 focus:outline-none"
              >
                <option value="">Todos os segmentos</option>
                {segmentosDisponiveis.map((segmento) => (
                  <option key={segmento} value={segmento}>{segmento}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-[#667085]">
                <tr>
                  <th className="text-left py-2 pr-4">Cliente</th>
                  <th className="text-left py-2 pr-4">Segmento</th>
                  <th className="text-right py-2 pr-4">Compras</th>
                  <th className="text-right py-2 pr-4">Ticket</th>
                  <th className="text-right py-2">Total</th>
                </tr>
              </thead>
              <tbody>
                {ranking.clientes.slice(0, 30).map((cliente) => {
                  const ativo = cliente.id_cliente === selectedClienteId
                  const badgeClass = cliente.classificacao_abc === 'A'
                    ? 'text-[#00FFA7] border-[#00FFA7]/30 bg-[#00FFA7]/10'
                    : cliente.classificacao_abc === 'B'
                      ? 'text-cyan-200 border-cyan-500/30 bg-cyan-500/10'
                      : 'text-violet-200 border-violet-500/30 bg-violet-500/10'

                  return (
                    <tr
                      key={cliente.id_cliente}
                      className={`border-t border-[#21262d] cursor-pointer ${ativo ? 'bg-[#00FFA7]/5' : 'hover:bg-white/5'}`}
                      onClick={() => setSelectedClienteId(cliente.id_cliente)}
                    >
                      <td className="py-3 pr-4">
                        <div className="flex items-center gap-2">
                          <p className="font-medium text-[#f2f4f7]">{cliente.nome_cliente || cliente.id_cliente}</p>
                          <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] ${badgeClass}`}>
                            {cliente.classificacao_abc || 'C'}
                          </span>
                        </div>
                        <p className="text-xs text-[#667085]">{cliente.cidade || '-'} {cliente.estado ? `- ${cliente.estado}` : ''}</p>
                      </td>
                      <td className="py-3 pr-4 text-[#d0d5dd]">{cliente.segmento_rfm || '-'}</td>
                      <td className="py-3 pr-4 text-right text-[#d0d5dd]">{number(cliente.num_compras)}</td>
                      <td className="py-3 pr-4 text-right text-[#d0d5dd]">{currency(cliente.ticket_medio)}</td>
                      <td className="py-3 text-right text-[#f2f4f7]">{currency(cliente.valor_total)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Crown size={18} className="text-[#00FFA7]" />
            <h2 className="text-base font-semibold text-[#f2f4f7]">Top compradores</h2>
          </div>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={topClientes?.clientes ?? []}>
                <CartesianGrid stroke="#21262d" strokeDasharray="3 3" />
                <XAxis dataKey="nome" stroke="#667085" tick={{ fontSize: 10 }} interval={0} angle={-30} height={70} textAnchor="end" />
                <YAxis stroke="#667085" tick={{ fontSize: 11 }} width={74} tickFormatter={compactCurrency} />
                <Tooltip
                  contentStyle={{ background: '#0c111d', border: '1px solid #344054', borderRadius: 8, color: '#f2f4f7' }}
                  formatter={(value) => currency(Number(value))}
                />
                <Bar dataKey="total_compras" fill="#00FFA7" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_1.5fr] gap-6">
        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Users size={18} className="text-[#00FFA7]" />
            <h2 className="text-base font-semibold text-[#f2f4f7]">Distribuicao ABC</h2>
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={dadosAbc} dataKey="total_clientes" nameKey="classificacao_abc" innerRadius={58} outerRadius={90} paddingAngle={3}>
                  {dadosAbc.map((entry) => (
                    <Cell
                      key={entry.classificacao_abc}
                      fill={ABC_CORES[entry.classificacao_abc] || '#6b7280'}
                    />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#0c111d', border: '1px solid #344054', borderRadius: 8, color: '#f2f4f7' }}
                  formatter={(value, _name, item) => [`${number(Number(value))} clientes`, item?.payload?.classificacao_abc || '-']}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="space-y-2 mt-3">
            {dadosAbc.map((item) => (
              <div key={item.classificacao_abc} className="flex items-center justify-between rounded-lg border border-[#21262d] bg-[#0c111d] px-3 py-2">
                <div className="flex items-center gap-2">
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-full"
                    style={{ background: ABC_CORES[item.classificacao_abc] || '#6b7280' }}
                  />
                  <span className="text-sm text-[#d0d5dd]">Classe {item.classificacao_abc}</span>
                </div>
                <div className="text-right">
                  <p className="text-sm text-[#f2f4f7]">{number(item.total_clientes)} clientes</p>
                  <p className="text-xs text-[#667085]">{currency(item.total_valor)}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <div className="flex items-center justify-between gap-3 mb-4">
            <div className="flex items-center gap-2">
              <Package size={18} className="text-[#00FFA7]" />
              <h2 className="text-base font-semibold text-[#f2f4f7]">Cesta do cliente selecionado</h2>
            </div>
            {loadingCliente && <span className="text-xs text-[#98a2b3]">Carregando...</span>}
          </div>

          {clienteProdutos ? (
            <div className="space-y-5">
              <div className="rounded-lg border border-[#21262d] bg-[#0c111d] p-3">
                <p className="text-sm font-semibold text-[#f2f4f7]">{clienteProdutos.cliente.nome_cliente}</p>
                <p className="text-xs text-[#667085]">
                  Ultima compra: {clienteProdutos.cliente.ultima_compra || '-'} | Dias sem comprar: {number(clienteProdutos.cliente.dias_desde_ultima)}
                </p>
                <p className="text-xs text-[#667085] mt-1">
                  Segmento: {clienteProdutos.cliente.segmento_rfm || '-'} | Ticket medio: {currency(clienteProdutos.cliente.ticket_medio)}
                </p>
              </div>

              <div>
                <p className="text-xs uppercase tracking-wider text-[#667085] mb-2">Top produtos por valor</p>
                <div className="space-y-2">
                  {clienteProdutos.top_produtos_por_valor.slice(0, 6).map((produto) => (
                    <div key={`valor-${produto.id_produto}`} className="flex items-center justify-between rounded-lg border border-[#21262d] bg-[#0c111d] px-3 py-2">
                      <div className="min-w-0 pr-3">
                        <p className="truncate text-sm text-[#d0d5dd]">{produto.nome_produto}</p>
                        <p className="text-xs text-[#667085]">{number(produto.quantidade_total)} un. | {number(produto.num_compras)} compras</p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-semibold text-[#f2f4f7]">{currency(produto.valor_total)}</p>
                        <p className="text-xs text-[#667085]">{percent(produto.participacao_valor_cliente)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <p className="text-xs uppercase tracking-wider text-[#667085] mb-2">Top produtos por quantidade</p>
                <div className="space-y-2">
                  {clienteProdutos.top_produtos_por_quantidade.slice(0, 6).map((produto) => (
                    <div key={`qtd-${produto.id_produto}`} className="flex items-center justify-between rounded-lg border border-[#21262d] bg-[#0c111d] px-3 py-2">
                      <div className="min-w-0 pr-3">
                        <p className="truncate text-sm text-[#d0d5dd]">{produto.nome_produto}</p>
                        <p className="text-xs text-[#667085]">Preco medio {currency(produto.preco_medio)}</p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-semibold text-[#f2f4f7]">{number(produto.quantidade_total)} un.</p>
                        <p className="text-xs text-[#667085]">{percent(produto.participacao_qtd_cliente)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-[#21262d] bg-[#0c111d] p-5 text-sm text-[#98a2b3]">
              Selecione um cliente no ranking para ver detalhes de compra.
            </div>
          )}
        </section>
      </div>

      <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
        <div className="flex items-center gap-2 mb-4">
          <UserCheck size={18} className="text-[#00FFA7]" />
          <h2 className="text-base font-semibold text-[#f2f4f7]">Resumo de engajamento</h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="rounded-lg border border-[#21262d] bg-[#0c111d] p-3">
            <p className="text-xs text-[#667085]">Clientes ativos (30d)</p>
            <p className="mt-1 text-lg font-semibold text-[#f2f4f7]">{number(resumo.ativos30d)}</p>
          </div>
          <div className="rounded-lg border border-[#21262d] bg-[#0c111d] p-3">
            <p className="text-xs text-[#667085]">Clientes inativos (90d+)</p>
            <p className="mt-1 text-lg font-semibold text-[#f2f4f7]">{number(resumo.inativos90d)}</p>
          </div>
          <div className="rounded-lg border border-[#21262d] bg-[#0c111d] p-3">
            <p className="text-xs text-[#667085]">Receita por cliente (media)</p>
            <p className="mt-1 text-lg font-semibold text-[#f2f4f7]">
              {currency(resumo.totalClientes ? resumo.totalReceita / resumo.totalClientes : 0)}
            </p>
          </div>
        </div>
      </section>
    </div>
  )
}
