import { memo, useRef, useState } from "react";
import {
  FileText,
  Mail,
  Download,
  Sparkles,
  Trash2,
  Clock,
} from "lucide-react";
import {
  useGenerateDocument,
  useDocumentsForJob,
  useDeleteDocument,
} from "../hooks/useDocuments";
import { Button, IconButton, cn } from "./ui";

const LANGUAGES = [
  { code: "en", label: "EN" },
  { code: "de", label: "DE" },
  { code: "fr", label: "FR" },
  { code: "it", label: "IT" },
];

function MarkdownRenderer({ content }) {
  // Renderer minimal de markdown — clase prose-like construida a mano.
  const html = content
    .replace(/^### (.+)$/gm, '<h3 class="mt-5 mb-1 text-sm font-semibold tracking-tight text-text-primary">$1</h3>')
    .replace(/^## (.+)$/gm,  '<h2 class="mt-6 mb-2 border-b border-border pb-1.5 text-base font-semibold tracking-tight text-text-primary">$1</h2>')
    .replace(/^# (.+)$/gm,   '<h1 class="mt-6 mb-3 text-lg font-semibold tracking-tight text-text-primary">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-text-primary">$1</strong>')
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em class="italic">$1</em>')
    .replace(/^- (.+)$/gm,   '<li class="ml-5 list-disc text-sm leading-relaxed text-text-primary">$1</li>')
    .replace(/\n\n/g,        '</p><p class="mt-3 text-sm leading-relaxed text-text-primary">')
    .replace(/\n/g, "<br/>");

  return (
    <div
      className="max-w-none font-sans"
      dangerouslySetInnerHTML={{
        __html: `<p class="text-sm leading-relaxed text-text-primary">${html}</p>`,
      }}
    />
  );
}

function DocumentGenerator({ jobHash, jobTitle, jobCompany }) {
  const [language, setLanguage] = useState("en");
  const [activeDoc, setActiveDoc] = useState(null);
  const printRef = useRef(null);

  const generateDoc = useGenerateDocument();
  const { data: existingDocs } = useDocumentsForJob(jobHash);
  const deleteDoc = useDeleteDocument();

  function handleGenerate(docType) {
    generateDoc.mutate(
      { jobHash, docType, language },
      { onSuccess: (data) => setActiveDoc(data) },
    );
  }

  async function handleDownloadPDF() {
    if (!printRef.current || !activeDoc) return;
    const html2pdf = (await import("html2pdf.js")).default;
    const element = printRef.current;
    const docLabel = activeDoc.doc_type === "cv" ? "CV" : "Cover_Letter";
    const filename = `${docLabel}_${jobCompany || "Company"}_${jobTitle || "Position"}.pdf`
      .replace(/\s+/g, "_")
      .replace(/[^a-zA-Z0-9_.-]/g, "");

    html2pdf()
      .set({
        margin: [12, 12, 12, 12],
        filename,
        html2canvas: { scale: 2 },
        jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
      })
      .from(element)
      .save();
  }

  const docs = existingDocs?.data || [];
  const isGenerating = generateDoc.isPending;
  const generatingType = generateDoc.variables?.docType;

  return (
    <section className="mt-6 rounded-xl border border-border bg-surface p-5">
      {/* Encabezado */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-ink-50 text-ink-700">
            <Sparkles className="h-4 w-4" aria-hidden="true" />
          </span>
          <div>
            <h3 className="text-base font-semibold tracking-tight text-text-primary">
              AI Document Generator
            </h3>
            <p className="text-sm text-text-secondary">
              Generate a tailored CV or cover letter for this position.
            </p>
          </div>
        </div>
      </header>

      {/* Selector de idioma — segmented */}
      <div className="mt-4 flex items-center gap-3">
        <span className="text-xs font-medium uppercase tracking-wider text-text-tertiary">
          Language
        </span>
        <div className="inline-flex rounded-lg border border-border bg-surface-secondary p-0.5">
          {LANGUAGES.map((lang) => (
            <button
              key={lang.code}
              type="button"
              onClick={() => setLanguage(lang.code)}
              aria-pressed={language === lang.code}
              className={cn(
                "h-7 rounded-md px-2.5 text-xs font-medium tracking-tight transition-all",
                language === lang.code
                  ? "bg-surface text-text-primary shadow-xs"
                  : "text-text-tertiary hover:text-text-primary",
              )}
            >
              {lang.label}
            </button>
          ))}
        </div>
      </div>

      {/* Acciones */}
      <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Button
          variant="primary"
          leftIcon={<FileText className="h-4 w-4" />}
          loading={isGenerating && generatingType === "cv"}
          disabled={isGenerating}
          fullWidth
          onClick={() => handleGenerate("cv")}
        >
          Tailored CV
        </Button>
        <Button
          variant="secondary"
          leftIcon={<Mail className="h-4 w-4" />}
          loading={isGenerating && generatingType === "cover_letter"}
          disabled={isGenerating}
          fullWidth
          onClick={() => handleGenerate("cover_letter")}
        >
          Cover letter
        </Button>
      </div>

      {/* Error */}
      {generateDoc.isError && (
        <div className="mt-3 rounded-lg border border-error-border bg-error-light p-3 text-sm text-error">
          {generateDoc.error.message}
        </div>
      )}

      {/* Preview tipo documento real */}
      {activeDoc && (
        <div className="mt-5">
          <div className="mb-3 flex items-center justify-between">
            <span className="inline-flex items-center gap-1.5 rounded-md bg-ink-50 px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-ink-700">
              {activeDoc.doc_type === "cv" ? (
                <FileText className="h-3 w-3" />
              ) : (
                <Mail className="h-3 w-3" />
              )}
              {activeDoc.doc_type === "cv" ? "CV" : "Cover Letter"} preview
            </span>
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Download className="h-3.5 w-3.5" />}
              onClick={handleDownloadPDF}
            >
              Download PDF
            </Button>
          </div>
          <div
            ref={printRef}
            className="rounded-lg border border-border bg-white p-8 shadow-card"
          >
            <MarkdownRenderer content={activeDoc.content} />
          </div>
        </div>
      )}

      {/* Historial */}
      {docs.length > 0 && (
        <div className="mt-5">
          <h4 className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-text-tertiary">
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            Previously generated
          </h4>
          <ul className="space-y-1">
            {docs.map((doc) => (
              <li
                key={doc.id}
                className="flex items-center justify-between rounded-lg border border-border-light bg-surface-secondary px-3 py-2"
              >
                <button
                  type="button"
                  onClick={() => setActiveDoc(doc)}
                  className="inline-flex items-center gap-2 text-sm text-text-primary hover:text-ink"
                >
                  {doc.doc_type === "cv" ? (
                    <FileText className="h-4 w-4 text-text-tertiary" aria-hidden="true" />
                  ) : (
                    <Mail className="h-4 w-4 text-text-tertiary" aria-hidden="true" />
                  )}
                  <span className="font-medium">
                    {doc.doc_type === "cv" ? "CV" : "Cover letter"}
                  </span>
                  {doc.language && (
                    <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[10px] font-medium text-ink-700">
                      {doc.language.toUpperCase()}
                    </span>
                  )}
                  <span className="text-xs text-text-tertiary">
                    {new Date(doc.created_at).toLocaleDateString("de-CH")}
                  </span>
                </button>
                <IconButton
                  aria-label="Delete document"
                  variant="danger"
                  size="sm"
                  onClick={() => deleteDoc.mutate(doc.id)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </IconButton>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default memo(DocumentGenerator);
