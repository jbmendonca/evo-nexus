import { useState, useCallback } from 'react';
import {
  Upload, FileDown, AlertCircle, Loader2, FileText,
  BarChart3, TrendingUp, DollarSign, FileSpreadsheet,
  CheckCircle2, XCircle, AlertTriangle, ChevronDown, ChevronUp,
  Building2, Calendar, Receipt,
} from 'lucide-react';

// ─── Tipos ────────────────────────────────────────────────────────────────────

interface Nota {
  nome_arquivo: string;
  data_nota: string | null;
  valor_bruto: number;
  valor_liquido: number;
  total_impostos: number;
  razao_social_fornecedor: string | null;
  status: 'ok' | 'parcial' | 'erro';
  erro?: string | null;
}

interface Stats {
  total: number;
  ok: number;
  errors: number;
  notas: Nota[];
  error_list: Array<{ file: string; error: string }>;
  extract_errors: string[];
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const fmt = (v: number | null | undefined) =>
  (v ?? 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

const COLORS = ['#00FFA7', '#00C9FF', '#F7B731', '#FC5C65', '#4834D4', '#26de81', '#fd9644', '#45aaf2'];

// ─── Componentes Internos ─────────────────────────────────────────────────────

function KpiCard({
  label, value, icon: Icon, color = '#00FFA7', sub,
}: {
  label: string; value: string; icon: React.ComponentType<{ size?: number; className?: string }>; color?: string; sub?: string;
}) {
  return (
    <div
      className="bg-[#101828] border border-[#344054] rounded-xl p-5 flex flex-col gap-2 hover:border-[#475467] transition-colors"
      style={{ borderLeft: `3px solid ${color}` }}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs text-[#667085] uppercase tracking-wider font-semibold">{label}</span>
        <Icon size={18} className="text-[#667085]" />
      </div>
      <span className="text-2xl font-bold text-white">{value}</span>
      {sub && <span className="text-xs text-[#667085]">{sub}</span>}
    </div>
  );
}

function StatusBadge({ status }: { status: Nota['status'] }) {
  if (status === 'ok') return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-green-400 bg-green-400/10 px-2 py-0.5 rounded-full">
      <CheckCircle2 size={11} /> OK
    </span>
  );
  if (status === 'parcial') return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-yellow-400 bg-yellow-400/10 px-2 py-0.5 rounded-full">
      <AlertTriangle size={11} /> Parcial
    </span>
  );
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-red-400 bg-red-400/10 px-2 py-0.5 rounded-full">
      <XCircle size={11} /> Erro
    </span>
  );
}

function NotaRow({ nota, idx }: { nota: Nota; idx: number }) {
  const [expanded, setExpanded] = useState(false);
  const isEven = idx % 2 === 0;

  return (
    <>
      <tr
        className={`border-b border-[#344054]/40 hover:bg-white/5 transition-colors cursor-pointer ${isEven ? 'bg-[#101828]' : 'bg-[#0d1525]'}`}
        onClick={() => setExpanded(e => !e)}
      >
        <td className="px-4 py-3 text-xs text-[#667085] font-mono">{nota.data_nota || '—'}</td>
        <td className="px-4 py-3 max-w-[280px]">
          <div className="text-sm text-white font-medium truncate">
            {nota.razao_social_fornecedor || <span className="text-[#667085] italic">Não identificado</span>}
          </div>
          <div className="text-[10px] text-[#475467] truncate mt-0.5" title={nota.nome_arquivo}>
            📄 {nota.nome_arquivo}
          </div>
        </td>
        <td className="px-4 py-3 text-sm text-right tabular-nums text-[#D0D5DD]">{fmt(nota.valor_bruto)}</td>
        <td className="px-4 py-3 text-sm text-right tabular-nums text-[#D0D5DD]">{fmt(nota.valor_liquido)}</td>
        <td className="px-4 py-3 text-sm text-right tabular-nums text-yellow-400">{fmt(nota.total_impostos)}</td>
        <td className="px-4 py-3">
          <StatusBadge status={nota.status} />
        </td>
        <td className="px-4 py-3 text-right text-[#667085]">
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </td>
      </tr>
      {expanded && (
        <tr className={isEven ? 'bg-[#101828]' : 'bg-[#0d1525]'}>
          <td colSpan={7} className="px-6 pb-4 pt-1">
            <div className="bg-[#0C111D] rounded-lg border border-[#344054]/50 p-4 text-xs text-[#667085] space-y-1">
              <p><span className="text-[#D0D5DD] font-medium">Arquivo:</span> {nota.nome_arquivo}</p>
              {nota.erro && (
                <p className="text-red-400"><span className="font-medium">Erro:</span> {nota.erro}</p>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ─── Componente Principal ─────────────────────────────────────────────────────

export default function PdfNfExtractor() {
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportingExcel, setExportingExcel] = useState(false);

  // ── Upload / Drag ──────────────────────────────────────────────────────────
  const onDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); setIsDragging(true); }, []);
  const onDragLeave = useCallback(() => setIsDragging(false), []);
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setIsDragging(false);
    const dropped = Array.from(e.dataTransfer.files).find(f => f.name.toLowerCase().endsWith('.zip'));
    if (dropped) setFile(dropped);
  }, []);

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.[0]) setFile(e.target.files[0]);
  };

  // ── Processar ──────────────────────────────────────────────────────────────
  const processFile = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setStats(null);
    setSessionId(null);

    const formData = new FormData();
    formData.append('zipfile', file);

    try {
      const res = await fetch('/api/pdf-nf/upload', { method: 'POST', body: formData });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || 'Erro ao processar arquivo.');
      }
      const data = await res.json();
      setSessionId(data.session_id);
      setStats(data.stats);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Erro desconhecido.');
    } finally {
      setLoading(false);
    }
  };

  // ── Exportar Excel ─────────────────────────────────────────────────────────
  const exportExcel = async () => {
    if (!sessionId) return;
    setExportingExcel(true);
    try {
      const res = await fetch(`/api/pdf-nf/export/${sessionId}`);
      if (!res.ok) throw new Error('Erro ao gerar Excel');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'notas_fiscais.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Falha ao exportar Excel.');
    } finally {
      setExportingExcel(false);
    }
  };

  // ── KPIs calculados ────────────────────────────────────────────────────────
  const totalBruto = stats?.notas.reduce((s, n) => s + (n.valor_bruto ?? 0), 0) ?? 0;
  const totalLiquido = stats?.notas.reduce((s, n) => s + (n.valor_liquido ?? 0), 0) ?? 0;
  const totalImpostos = stats?.notas.reduce((s, n) => s + (n.total_impostos ?? 0), 0) ?? 0;

  // ── Dados para gráfico por fornecedor ──────────────────────────────────────
  const chartData = (() => {
    if (!stats) return [];
    const map: Record<string, number> = {};
    for (const n of stats.notas) {
      const key = n.razao_social_fornecedor || 'Não identificado';
      map[key] = (map[key] ?? 0) + (n.valor_bruto ?? 0);
    }
    return Object.entries(map)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 8)
      .map(([name, value]) => ({
        name: name.length > 22 ? name.slice(0, 22) + '…' : name,
        fullName: name,
        value,
      }));
  })();

  return (
    <div className="max-w-7xl mx-auto space-y-6">

      {/* ── Cabeçalho ── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <Receipt className="text-[#00FFA7]" size={28} />
            Extrator de NF PDF
          </h1>
          <p className="text-sm text-[#667085] mt-1">
            Faça upload de um ZIP com notas fiscais em PDF. A IA irá extrair os dados automaticamente.
          </p>
        </div>
        {stats && sessionId && (
          <button
            onClick={exportExcel}
            disabled={exportingExcel}
            className="flex items-center gap-2 bg-[#00FFA7] hover:bg-[#00e696] text-black font-semibold px-4 py-2.5 rounded-lg transition-colors disabled:opacity-60 shadow-md shadow-[#00FFA7]/20"
          >
            {exportingExcel
              ? <Loader2 size={18} className="animate-spin" />
              : <FileSpreadsheet size={18} />}
            Exportar Excel
          </button>
        )}
      </div>

      {/* ── Upload Zone ── */}
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => document.getElementById('pdf-nf-upload')?.click()}
        className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all duration-200 ${
          isDragging
            ? 'border-[#00FFA7] bg-[#00FFA7]/5 scale-[1.01]'
            : file
              ? 'border-[#00FFA7]/40 bg-[#00FFA7]/5'
              : 'border-[#344054] bg-[#101828] hover:border-[#475467] hover:bg-[#101828]/80'
        }`}
      >
        <input
          id="pdf-nf-upload"
          type="file"
          className="hidden"
          accept=".zip"
          onChange={onFileChange}
        />

        {file ? (
          <div className="space-y-3">
            <div className="w-14 h-14 mx-auto rounded-full bg-[#00FFA7]/10 flex items-center justify-center">
              <FileText className="text-[#00FFA7]" size={28} />
            </div>
            <div>
              <p className="text-white font-semibold">{file.name}</p>
              <p className="text-sm text-[#667085]">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); processFile(); }}
              disabled={loading}
              className="mx-auto flex items-center gap-2 bg-[#00FFA7] hover:bg-[#00e696] text-black font-bold px-6 py-2.5 rounded-lg transition-colors disabled:opacity-50 shadow-lg shadow-[#00FFA7]/20"
            >
              {loading
                ? <><Loader2 size={18} className="animate-spin" /> Processando com IA…</>
                : <><BarChart3 size={18} /> Extrair Dados</>}
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="w-16 h-16 mx-auto rounded-full bg-[#1D2939] flex items-center justify-center">
              <Upload className="text-[#667085]" size={30} />
            </div>
            <div>
              <p className="text-white font-semibold text-lg">Arraste o arquivo ZIP aqui</p>
              <p className="text-sm text-[#667085] mt-1">ou clique para selecionar</p>
              <p className="text-xs text-[#475467] mt-2">Arquivo .zip contendo PDFs de notas fiscais</p>
            </div>
          </div>
        )}
      </div>

      {/* ── Loading state ── */}
      {loading && (
        <div className="bg-[#101828] border border-[#00FFA7]/20 rounded-xl p-6 flex items-center gap-4">
          <div className="relative">
            <div className="w-10 h-10 rounded-full border-2 border-[#00FFA7]/20 border-t-[#00FFA7] animate-spin" />
          </div>
          <div>
            <p className="text-white font-semibold">Processando com Google Gemini Vision…</p>
            <p className="text-sm text-[#667085] mt-0.5">Isso pode levar alguns minutos dependendo da quantidade de PDFs.</p>
          </div>
        </div>
      )}

      {/* ── Erro ── */}
      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl flex items-start gap-3">
          <AlertCircle className="text-red-400 shrink-0 mt-0.5" size={18} />
          <div>
            <p className="text-red-400 font-semibold text-sm">Erro ao processar</p>
            <p className="text-sm text-red-400/80 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* ── Alertas de extração ── */}
      {stats?.extract_errors && stats.extract_errors.length > 0 && (
        <div className="p-4 bg-yellow-500/10 border border-yellow-500/20 rounded-xl">
          <h4 className="text-sm font-semibold text-yellow-400 mb-2 flex items-center gap-2">
            <AlertTriangle size={16} /> Alertas de Extração
          </h4>
          <ul className="text-xs text-yellow-400/80 space-y-1 list-disc pl-4">
            {stats.extract_errors.map((err, i) => <li key={i}>{err}</li>)}
          </ul>
        </div>
      )}

      {/* ── Dashboard ── */}
      {stats && (
        <div className="space-y-6 animate-fadeIn">

          {/* KPIs */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiCard
              label="Total de Notas"
              value={String(stats.total)}
              icon={FileText}
              color="#00FFA7"
              sub={`${stats.ok} processadas · ${stats.errors} com erro`}
            />
            <KpiCard
              label="Valor Bruto Total"
              value={fmt(totalBruto)}
              icon={DollarSign}
              color="#00C9FF"
            />
            <KpiCard
              label="Valor Líquido Total"
              value={fmt(totalLiquido)}
              icon={TrendingUp}
              color="#26de81"
            />
            <KpiCard
              label="Total de Impostos"
              value={fmt(totalImpostos)}
              icon={Building2}
              color="#F7B731"
              sub={totalBruto > 0 ? `${((totalImpostos / totalBruto) * 100).toFixed(1)}% do bruto` : undefined}
            />
          </div>

          {/* Gráfico de Barras CSS nativo */}
          {chartData.length > 0 && (
            <div className="bg-[#101828] border border-[#344054] rounded-xl p-6">
              <h3 className="text-white font-semibold mb-5 flex items-center gap-2">
                <BarChart3 className="text-[#00FFA7]" size={18} />
                Valor Bruto por Fornecedor
              </h3>
              <div className="space-y-3">
                {chartData.map((item, idx) => {
                  const maxVal = Math.max(...chartData.map(d => d.value));
                  const pct = maxVal > 0 ? (item.value / maxVal) * 100 : 0;
                  const barColor = COLORS[idx % COLORS.length];
                  return (
                    <div key={idx} className="group">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs text-[#D0D5DD] truncate max-w-[60%]" title={item.fullName}>
                          {item.name}
                        </span>
                        <span className="text-xs font-semibold tabular-nums" style={{ color: barColor }}>
                          {fmt(item.value)}
                        </span>
                      </div>
                      <div className="h-2 bg-[#1D2939] rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-700 ease-out"
                          style={{ width: `${pct}%`, backgroundColor: barColor }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Tabela de Notas */}
          <div className="bg-[#101828] border border-[#344054] rounded-xl overflow-hidden">
            <div className="p-4 border-b border-[#344054] flex items-center justify-between flex-wrap gap-3">
              <h3 className="text-white font-semibold flex items-center gap-2">
                <Calendar className="text-[#00FFA7]" size={18} />
                Notas Extraídas ({stats.notas.length})
              </h3>
              {sessionId && (
                <button
                  onClick={exportExcel}
                  disabled={exportingExcel}
                  className="flex items-center gap-2 text-sm bg-[#344054] hover:bg-[#475467] text-white px-3 py-1.5 rounded-lg transition-colors disabled:opacity-60"
                >
                  {exportingExcel ? <Loader2 size={14} className="animate-spin" /> : <FileDown size={14} />}
                  Exportar Excel
                </button>
              )}
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-[#0C111D] border-b border-[#344054]">
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#667085] uppercase tracking-wider">Data</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#667085] uppercase tracking-wider">Fornecedor</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#667085] uppercase tracking-wider">Valor Bruto</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#667085] uppercase tracking-wider">Valor Líquido</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#667085] uppercase tracking-wider">Impostos</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#667085] uppercase tracking-wider">Status</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {stats.notas.map((nota, idx) => (
                    <NotaRow key={idx} nota={nota} idx={idx} />
                  ))}
                  {stats.notas.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-4 py-12 text-center text-[#667085] text-sm">
                        Nenhuma nota processada.
                      </td>
                    </tr>
                  )}
                </tbody>
                {stats.notas.length > 0 && (
                  <tfoot className="bg-[#0C111D] border-t-2 border-[#00FFA7]/30">
                    <tr>
                      <td colSpan={2} className="px-4 py-3 text-sm font-bold text-[#00FFA7]">TOTAL</td>
                      <td className="px-4 py-3 text-right font-bold text-white tabular-nums">{fmt(totalBruto)}</td>
                      <td className="px-4 py-3 text-right font-bold text-white tabular-nums">{fmt(totalLiquido)}</td>
                      <td className="px-4 py-3 text-right font-bold text-yellow-400 tabular-nums">{fmt(totalImpostos)}</td>
                      <td colSpan={2} />
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </div>

          {/* Erros detalhados */}
          {stats.error_list && stats.error_list.length > 0 && (
            <div className="bg-red-500/5 border border-red-500/20 rounded-xl p-4">
              <h4 className="text-red-400 font-semibold text-sm mb-3 flex items-center gap-2">
                <XCircle size={16} /> Arquivos com Erro ({stats.error_list.length})
              </h4>
              <div className="space-y-2">
                {stats.error_list.map((e, i) => (
                  <div key={i} className="bg-red-500/5 rounded-lg p-3 flex flex-col gap-1">
                    <span className="text-sm font-medium text-red-300">{e.file}</span>
                    <span className="text-xs text-red-400/70">{e.error}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
