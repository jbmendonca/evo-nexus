import { useState, useEffect, useRef, useCallback } from 'react'
import { Upload, X } from 'lucide-react'

const API_BASE = import.meta.env.DEV ? 'http://localhost:8080' : ''

interface UploadFile {
  file: File
  progress: number
  status: 'pending' | 'uploading' | 'done' | 'error'
  error?: string
}

interface UploadDropzoneProps {
  currentPath: string
  onUploadComplete: () => void
  onClose: () => void
  onConfirmOverwrite: (filename: string) => Promise<boolean>
}

export default function UploadDropzone({
  currentPath,
  onUploadComplete,
  onClose,
  onConfirmOverwrite,
}: UploadDropzoneProps) {
  const [uploads, setUploads] = useState<UploadFile[]>([])
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const uploadFile = useCallback(async (file: File) => {
    const targetPath = currentPath ? `${currentPath}/${file.name}` : `workspace/${file.name}`

    // Check if exists (HEAD request)
    const checkRes = await fetch(`${API_BASE}/api/workspace/file?path=${encodeURIComponent(targetPath)}`, {
      method: 'HEAD',
      credentials: 'include',
    }).catch(() => null)

    if (checkRes?.ok) {
      const confirmed = await onConfirmOverwrite(file.name)
      if (!confirmed) return
    }

    const formData = new FormData()
    formData.append('file', file)
    formData.append('path', currentPath || 'workspace')

    return new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${API_BASE}/api/workspace/upload`)
      xhr.withCredentials = true
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest')

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const pct = Math.round((e.loaded / e.total) * 100)
          setUploads(prev =>
            prev.map(u => u.file === file ? { ...u, progress: pct, status: 'uploading' } : u)
          )
        }
      })

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          setUploads(prev =>
            prev.map(u => u.file === file ? { ...u, progress: 100, status: 'done' } : u)
          )
          resolve()
        } else {
          setUploads(prev =>
            prev.map(u => u.file === file ? { ...u, status: 'error', error: `Erro ${xhr.status}` } : u)
          )
          reject(new Error(`Upload failed: ${xhr.status}`))
        }
      })

      xhr.addEventListener('error', () => {
        setUploads(prev =>
          prev.map(u => u.file === file ? { ...u, status: 'error', error: 'Falha na conexão' } : u)
        )
        reject(new Error('Network error'))
      })

      xhr.send(formData)
    })
  }, [currentPath, onConfirmOverwrite])

  const processFiles = useCallback(async (files: File[]) => {
    const newUploads: UploadFile[] = files.map(f => ({
      file: f,
      progress: 0,
      status: 'pending',
    }))
    setUploads(newUploads)

    await Promise.allSettled(files.map(uploadFile))
    onUploadComplete()
  }, [uploadFile, onUploadComplete])

  const handleDrop = useCallback((e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const files = Array.from(e.dataTransfer?.files ?? [])
    if (files.length > 0) processFiles(files)
  }, [processFiles])

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault()
    setDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: DragEvent) => {
    if ((e.target as Element)?.closest?.('.dropzone-inner')) return
    setDragOver(false)
  }, [])

  useEffect(() => {
    window.addEventListener('drop', handleDrop)
    window.addEventListener('dragover', handleDragOver)
    window.addEventListener('dragleave', handleDragLeave)
    return () => {
      window.removeEventListener('drop', handleDrop)
      window.removeEventListener('dragover', handleDragOver)
      window.removeEventListener('dragleave', handleDragLeave)
    }
  }, [handleDrop, handleDragOver, handleDragLeave])

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length > 0) processFiles(files)
  }

  const allDone = uploads.length > 0 && uploads.every(u => u.status === 'done' || u.status === 'error')

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.7)' }}
    >
      <div
        className="dropzone-inner relative w-full max-w-lg mx-4 rounded-2xl p-8 text-center"
        style={{
          background: 'var(--bg-card)',
          border: `2px dashed ${dragOver ? 'var(--evo-green)' : 'var(--border)'}`,
          transition: 'border-color 150ms ease',
        }}
      >
        {/* Close */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 p-1.5 rounded-lg transition-colors"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-secondary)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
          aria-label="Fechar"
        >
          <X size={16} />
        </button>

        {uploads.length === 0 ? (
          <>
            <div
              className="mx-auto mb-4 flex items-center justify-center w-16 h-16 rounded-2xl"
              style={{
                background: dragOver ? 'rgba(0,255,167,0.15)' : 'var(--bg-sidebar)',
                border: '1px solid var(--border)',
                transition: 'background 150ms ease',
              }}
            >
              <Upload size={28} style={{ color: dragOver ? 'var(--evo-green)' : 'var(--text-muted)' }} />
            </div>
            <p className="text-base font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>
              Soltar para fazer upload
            </p>
            <p className="text-sm mb-4" style={{ color: 'var(--text-muted)' }}>
              em <code className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-sidebar)', color: 'var(--evo-green)' }}>{currentPath || 'workspace'}</code>
            </p>
            <button
              onClick={() => inputRef.current?.click()}
              className="px-4 py-2 text-sm rounded-lg transition-colors"
              style={{
                background: 'rgba(0,255,167,0.1)',
                border: '1px solid rgba(0,255,167,0.3)',
                color: 'var(--evo-green)',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'rgba(0,255,167,0.2)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'rgba(0,255,167,0.1)')}
            >
              Selecionar arquivos
            </button>
            <input
              ref={inputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFileInput}
            />
          </>
        ) : (
          <div className="space-y-3 text-left">
            <p className="text-sm font-medium mb-3" style={{ color: 'var(--text-primary)' }}>
              {allDone ? 'Upload concluido' : 'Enviando arquivos...'}
            </p>
            {uploads.map((u, i) => (
              <div key={i}>
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="truncate" style={{ color: 'var(--text-secondary)' }}>{u.file.name}</span>
                  <span style={{ color: u.status === 'error' ? 'var(--danger)' : u.status === 'done' ? 'var(--evo-green)' : 'var(--text-muted)' }}>
                    {u.status === 'error' ? u.error : u.status === 'done' ? 'Pronto' : `${u.progress}%`}
                  </span>
                </div>
                <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-sidebar)' }}>
                  <div
                    className="h-full rounded-full transition-all duration-300"
                    style={{
                      width: `${u.progress}%`,
                      background: u.status === 'error' ? 'var(--danger)' : 'var(--evo-green)',
                    }}
                  />
                </div>
              </div>
            ))}
            {allDone && (
              <button
                onClick={onClose}
                className="mt-2 px-4 py-2 text-sm rounded-lg transition-colors w-full"
                style={{
                  background: 'rgba(0,255,167,0.1)',
                  border: '1px solid rgba(0,255,167,0.3)',
                  color: 'var(--evo-green)',
                }}
              >
                Fechar
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
