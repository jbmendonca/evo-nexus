import type { Extension } from '@codemirror/state'
import { EditorState, StateEffect } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, drawSelection, highlightActiveLine } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'
import { json } from '@codemirror/lang-json'
import { python } from '@codemirror/lang-python'
import { javascript } from '@codemirror/lang-javascript'
import { syntaxHighlighting, defaultHighlightStyle, HighlightStyle } from '@codemirror/language'
import { tags } from '@lezer/highlight'

// Evolution dark theme — matches --bg-primary, --evo-green palette
const evoTheme = EditorView.theme({
  '&': {
    backgroundColor: '#0C111D',
    color: '#D0D5DD',
    height: '100%',
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
    fontSize: '13px',
  },
  '.cm-content': {
    caretColor: '#00FFA7',
    padding: '12px 0',
  },
  '.cm-cursor': {
    borderLeftColor: '#00FFA7',
    borderLeftWidth: '2px',
  },
  '.cm-selectionBackground, ::selection': {
    backgroundColor: '#1a2744',
  },
  '&.cm-focused .cm-selectionBackground': {
    backgroundColor: '#1a2744',
  },
  '.cm-gutters': {
    backgroundColor: '#0a0f1a',
    color: '#667085',
    border: 'none',
    borderRight: '1px solid #344054',
  },
  '.cm-gutter': {
    minWidth: '48px',
  },
  '.cm-activeLineGutter': {
    backgroundColor: '#182230',
    color: '#D0D5DD',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(0, 255, 167, 0.04)',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 12px 0 8px',
  },
  '.cm-scroller': {
    overflow: 'auto',
  },
}, { dark: true })

// Syntax highlighting for Evolution theme
const evoHighlight = HighlightStyle.define([
  { tag: tags.keyword, color: '#00FFA7' },
  { tag: tags.operator, color: '#00FFA7' },
  { tag: tags.string, color: '#7ee787' },
  { tag: tags.number, color: '#79c0ff' },
  { tag: tags.bool, color: '#79c0ff' },
  { tag: tags.null, color: '#79c0ff' },
  { tag: tags.comment, color: '#667085', fontStyle: 'italic' },
  { tag: tags.function(tags.variableName), color: '#d2a8ff' },
  { tag: tags.variableName, color: '#D0D5DD' },
  { tag: tags.typeName, color: '#ffa657' },
  { tag: tags.className, color: '#ffa657' },
  { tag: tags.propertyName, color: '#79c0ff' },
  { tag: tags.heading, color: '#00FFA7', fontWeight: 'bold' },
  { tag: tags.emphasis, fontStyle: 'italic' },
  { tag: tags.strong, fontWeight: 'bold' },
  { tag: tags.link, color: '#00FFA7', textDecoration: 'underline' },
  { tag: tags.url, color: '#7ee787' },
  { tag: tags.meta, color: '#667085' },
  { tag: tags.tagName, color: '#7ee787' },
  { tag: tags.attributeName, color: '#79c0ff' },
  { tag: tags.attributeValue, color: '#a5d6ff' },
  { tag: tags.punctuation, color: '#8b949e' },
  { tag: tags.bracket, color: '#D0D5DD' },
])

export function languageForPath(path: string | unknown): Extension[] {
  if (typeof path !== 'string' || !path) return []
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  switch (ext) {
    case 'md':
    case 'markdown':
      return [markdown()]
    case 'json':
    case 'jsonc':
      return [json()]
    case 'py':
      return [python()]
    case 'js':
    case 'mjs':
    case 'cjs':
      return [javascript()]
    case 'ts':
    case 'tsx':
    case 'jsx':
      return [javascript({ typescript: true, jsx: true })]
    default:
      return []
  }
}

export function baseExtensions(readOnly: boolean, onSave?: () => void): Extension[] {
  const saveKeymap = onSave
    ? keymap.of([{
      key: 'Mod-s',
      run: () => { onSave(); return true },
    }])
    : []

  return [
    lineNumbers(),
    history(),
    drawSelection(),
    highlightActiveLine(),
    syntaxHighlighting(evoHighlight),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    keymap.of([...defaultKeymap, ...historyKeymap]),
    evoTheme,
    EditorState.readOnly.of(readOnly),
    EditorView.lineWrapping,
    saveKeymap,
  ]
}

export { EditorView, EditorState, StateEffect }
