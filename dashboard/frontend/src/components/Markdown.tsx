import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function Markdown({ children }: { children?: string | null }) {
  const safe = typeof children === 'string' ? children : ''
  return (
    <div className="markdown-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{safe}</ReactMarkdown>
    </div>
  )
}
