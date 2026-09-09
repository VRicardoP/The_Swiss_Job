import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it, vi } from 'vitest'
import DocumentGenerator from './DocumentGenerator'

vi.mock('../hooks/useDocuments', () => ({
  useGenerateDocument: () => ({}),
  useDocumentLibrary: () => ({ data: { data: [] } }),
  usePendingDocuments: () => ({}),
  useRetryDocument: () => ({}),
  useDocumentsForJob: () => ({ data: { data: [] } }),
  useDeleteDocument: () => ({}),
}))

vi.mock('../stores/authStore', () => ({
  default: (select) => select({ user: { id: 'synthetic-owner' } }),
}))

it('renders the generation controls without literal newline escape text', () => {
  const html = renderToStaticMarkup(createElement(DocumentGenerator, { jobHash: 'synthetic' }))
  expect(html).toContain('Language')
  expect(html).not.toContain('\\n')
})
