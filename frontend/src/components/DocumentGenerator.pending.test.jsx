/**
 * A20-08 — con una operación PENDIENTE (recuperada de sessionStorage) los DOS
 * botones de generación quedan deshabilitados. Antes solo lo estaba «Tailored
 * CV»: «Cover letter» seguía activo y, como `handleGenerate` reutiliza la
 * operación pendiente, el clic reenviaba la generación del CV.
 */
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, expect, it, vi } from 'vitest'
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
  default: (select) => select({ user: { id: 'owner' } }),
}))

const PENDING = { docType: 'cv', language: 'en', operationId: 'op-1' }

beforeEach(() => {
  const store = new Map([[`document-operation:owner:job-1`, JSON.stringify(PENDING)]])
  globalThis.sessionStorage = {
    getItem: (k) => store.get(k) ?? null,
    setItem: (k, v) => store.set(k, v),
    removeItem: (k) => store.delete(k),
  }
})

// Etiqueta de apertura del <button> cuyo contenido es `texto` (el último
// `<button` que precede al texto).
function boton(html, texto) {
  const antes = html.slice(0, html.indexOf(texto))
  const aperturas = [...antes.matchAll(/<button[^>]*>/g)]
  return aperturas.at(-1)[0]
}

function botones(html) {
  return [boton(html, 'Tailored CV'), boton(html, 'Cover letter')]
}

it('disables both generation buttons while an operation is pending', () => {
  const html = renderToStaticMarkup(createElement(DocumentGenerator, { jobHash: 'job-1' }))
  expect(html).toContain('A document operation is pending')
  const [cv, cover] = botones(html)
  expect(cv).toMatch(/\sdisabled(?:=""|[\s>])/)
  expect(cover).toMatch(/\sdisabled(?:=""|[\s>])/)
})

it('keeps both buttons enabled without a pending operation', () => {
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  const html = renderToStaticMarkup(createElement(DocumentGenerator, { jobHash: 'job-1' }))
  const [cv, cover] = botones(html)
  expect(cv).not.toMatch(/\sdisabled(?:=""|[\s>])/)
  expect(cover).not.toMatch(/\sdisabled(?:=""|[\s>])/)
})
