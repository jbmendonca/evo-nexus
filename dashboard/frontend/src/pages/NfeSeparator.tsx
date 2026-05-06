import { useState, useCallback } from 'react';
import { Upload, FileDown, AlertCircle, CheckCircle2, FileText, Loader2, Archive, Folder } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface NfeStats {
  total: number;
  ok: number;
  errors: number;
  skipped: number;
  groups: Record<string, {
    cnpj: string;
    ie: string;
    count: number;
    files: string[];
  }>;
  error_list: Array<{file: string; error: string}>;
  extract_errors: string[];
}

export default function NfeSeparator() {
  const { t } = useTranslation();
  const [files, setFiles] = useState<File[]>([]);
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [stats, setStats] = useState<NfeStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const droppedFiles = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.zip'));
    if (droppedFiles.length > 0) {
      setFiles(prev => [...prev, ...droppedFiles]);
    }
  };

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const selectedFiles = Array.from(e.target.files).filter(f => f.name.toLowerCase().endsWith('.zip'));
      setFiles(prev => [...prev, ...selectedFiles]);
    }
  };

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
  };

  const processFiles = async () => {
    if (files.length === 0) return;
    setLoading(true);
    setError(null);
    setStats(null);
    setSessionId(null);

    const formData = new FormData();
    files.forEach(f => formData.append('zipfile', f));

    try {
      const res = await fetch('/api/nfe/upload', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || 'Erro ao processar arquivos.');
      }

      const data = await res.json();
      setSessionId(data.session_id);
      setStats(data.stats);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white flex items-center gap-3">
          <Archive className="text-[#00FFA7]" size={28} />
          {t('nav.nfeSeparator')}
        </h1>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-4">
          <div 
            className="border-2 border-dashed border-[#344054] hover:border-[#00FFA7] bg-[#101828] rounded-xl p-8 text-center transition-colors cursor-pointer"
            onDragOver={onDragOver}
            onDrop={onDrop}
            onClick={() => document.getElementById('zip-upload')?.click()}
          >
            <input 
              type="file" 
              id="zip-upload" 
              className="hidden" 
              accept=".zip" 
              multiple 
              onChange={onFileChange}
            />
            <Upload className="mx-auto text-[#667085] mb-4" size={48} />
            <p className="text-sm text-white font-medium mb-1">Arraste e solte arquivos ZIP</p>
            <p className="text-xs text-[#667085]">ou clique para selecionar</p>
          </div>

          {files.length > 0 && (
            <div className="bg-[#101828] border border-[#344054] rounded-xl p-4">
              <h3 className="text-sm font-medium text-white mb-3 flex items-center justify-between">
                <span>Arquivos Selecionados</span>
                <span className="bg-[#00FFA7]/10 text-[#00FFA7] px-2 py-0.5 rounded text-xs">
                  {files.length}
                </span>
              </h3>
              <div className="space-y-2 max-h-48 overflow-y-auto pr-2">
                {files.map((file, idx) => (
                  <div key={idx} className="flex items-center justify-between text-sm bg-[#182230] p-2 rounded">
                    <span className="text-[#D0D5DD] truncate">{file.name}</span>
                    <button 
                      onClick={() => removeFile(idx)}
                      className="text-red-400 hover:text-red-300 ml-2"
                    >
                      Remover
                    </button>
                  </div>
                ))}
              </div>
              <button
                onClick={processFiles}
                disabled={loading}
                className="mt-4 w-full bg-[#00FFA7] hover:bg-[#00e696] text-black font-semibold py-2 rounded-lg flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
              >
                {loading ? <Loader2 className="animate-spin" size={18} /> : <FileText size={18} />}
                Processar XMLs
              </button>
            </div>
          )}

          {error && (
            <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl flex items-start gap-3">
              <AlertCircle className="text-red-400 shrink-0 mt-0.5" size={18} />
              <p className="text-sm text-red-400">{error}</p>
            </div>
          )}
        </div>

        <div className="lg:col-span-2 space-y-6">
          {stats && (
            <>
              <div className="grid grid-cols-3 gap-4">
                <div className="bg-[#101828] border border-[#344054] rounded-xl p-4 flex flex-col items-center justify-center">
                  <span className="text-3xl font-bold text-white">{stats.total}</span>
                  <span className="text-xs text-[#667085] uppercase tracking-wider mt-1">Total XMLs</span>
                </div>
                <div className="bg-[#101828] border border-green-500/30 rounded-xl p-4 flex flex-col items-center justify-center">
                  <span className="text-3xl font-bold text-green-400">{stats.ok}</span>
                  <span className="text-xs text-green-400/70 uppercase tracking-wider mt-1">Processados</span>
                </div>
                <div className="bg-[#101828] border border-red-500/30 rounded-xl p-4 flex flex-col items-center justify-center">
                  <span className="text-3xl font-bold text-red-400">{stats.errors}</span>
                  <span className="text-xs text-red-400/70 uppercase tracking-wider mt-1">Erros</span>
                </div>
              </div>

              {stats.extract_errors?.length > 0 && (
                <div className="p-4 bg-yellow-500/10 border border-yellow-500/20 rounded-xl">
                  <h4 className="text-sm font-semibold text-yellow-400 mb-2 flex items-center gap-2">
                    <AlertCircle size={16} /> Alertas de Extração
                  </h4>
                  <ul className="text-xs text-yellow-400/80 space-y-1 list-disc pl-4">
                    {stats.extract_errors.map((err, i) => <li key={i}>{err}</li>)}
                  </ul>
                </div>
              )}

              <div className="bg-[#101828] border border-[#344054] rounded-xl overflow-hidden flex flex-col">
                <div className="p-4 border-b border-[#344054] flex items-center justify-between">
                  <h3 className="font-medium text-white flex items-center gap-2">
                    <Folder className="text-[#00FFA7]" size={18} /> 
                    Grupos Identificados ({Object.keys(stats.groups).length})
                  </h3>
                  {sessionId && (
                    <a 
                      href={`/api/nfe/download/${sessionId}`} 
                      className="bg-[#344054] hover:bg-[#475467] text-white text-sm px-3 py-1.5 rounded-lg flex items-center gap-2 transition-colors"
                    >
                      <FileDown size={16} /> Baixar Todos (ZIP)
                    </a>
                  )}
                </div>
                <div className="divide-y divide-[#344054] max-h-[500px] overflow-y-auto">
                  {Object.entries(stats.groups).map(([folder, info]) => (
                    <div key={folder} className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4 hover:bg-white/5 transition-colors">
                      <div>
                        <h4 className="text-[#00FFA7] font-semibold text-sm">{folder}</h4>
                        <div className="flex gap-4 mt-1 text-xs text-[#667085]">
                          <span>CNPJ: {info.cnpj || 'N/A'}</span>
                          <span>IE: {info.ie || 'N/A'}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-4">
                        <span className="text-sm text-[#D0D5DD] bg-[#182230] px-2 py-1 rounded">
                          {info.count} arquivo(s)
                        </span>
                        <a 
                          href={`/api/nfe/download/${sessionId}/${folder}`}
                          className="text-[#00FFA7] hover:text-white transition-colors"
                          title="Baixar este grupo"
                        >
                          <FileDown size={20} />
                        </a>
                      </div>
                    </div>
                  ))}
                  {Object.keys(stats.groups).length === 0 && (
                    <div className="p-8 text-center text-[#667085] text-sm">
                      Nenhuma Inscrição Estadual encontrada nos XMLs processados.
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
