import { useState, useRef, memo } from "react";
import {
  useGenerateDocument,
  useDocumentsForJob,
  useDeleteDocument,
} from "../hooks/useDocuments";

const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
  { code: "fr", label: "Français" },
  { code: "it", label: "Italiano" },
];

function MarkdownRenderer({ content }) {
  const html = content
    .replace(
      /^### (.+)$/gm,
      '<h3 class="text-base font-semibold mt-4 mb-1">$1</h3>',
    )
    .replace(
      /^## (.+)$/gm,
      '<h2 class="text-lg font-bold mt-5 mb-2 border-b border-border pb-1">$1</h2>',
    )
    .replace(
      /^# (.+)$/gm,
      '<h1 class="text-xl font-bold mt-6 mb-3">$1</h1>',
    )
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>")
    .replace(
      /^- (.+)$/gm,
      '<li class="ml-4 list-disc text-sm text-text-primary">$1</li>',
    )
    .replace(
      /\n\n/g,
      '</p><p class="text-sm text-text-primary mt-2 leading-relaxed">',
    )
    .replace(/\n/g, "<br/>");

  return (
    <div
      className="max-w-none"
      dangerouslySetInnerHTML={{
        __html: `<p class="text-sm text-text-primary leading-relaxed">${html}</p>`,
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
        margin: [10, 10, 10, 10],
        filename,
        html2canvas: { scale: 2 },
        jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
      })
      .from(element)
      .save();
  }

  const docs = existingDocs?.data || [];

  return (
    <div className="mt-6 bg-surface rounded-xl shadow-card p-4">
      <h3 className="mb-3 text-base font-bold text-text-primary">
        AI Document Generator
      </h3>

      {/* Language selector */}
      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs text-text-secondary">Language:</span>
        {LANGUAGES.map((lang) => (
          <button
            key={lang.code}
            onClick={() => setLanguage(lang.code)}
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              language === lang.code
                ? "bg-swiss-red text-white"
                : "bg-surface-tertiary text-text-secondary hover:bg-swiss-red-light hover:text-swiss-red transition-all duration-200"
            }`}
          >
            {lang.label}
          </button>
        ))}
      </div>

      {/* Generate buttons */}
      <div className="mb-4 flex gap-2">
        <button
          onClick={() => handleGenerate("cv")}
          disabled={generateDoc.isPending}
          className="flex-1 bg-swiss-red hover:bg-swiss-red-hover rounded-xl px-4 py-2 text-sm font-semibold text-white transition-all duration-200 disabled:opacity-50"
        >
          {generateDoc.isPending && generateDoc.variables?.docType === "cv"
            ? "Generating CV..."
            : "Generate Tailored CV"}
        </button>
        <button
          onClick={() => handleGenerate("cover_letter")}
          disabled={generateDoc.isPending}
          className="flex-1 bg-text-primary hover:bg-text-primary/90 rounded-xl px-4 py-2 text-sm font-semibold text-white transition-all duration-200 disabled:opacity-50"
        >
          {generateDoc.isPending &&
          generateDoc.variables?.docType === "cover_letter"
            ? "Generating..."
            : "Generate Cover Letter"}
        </button>
      </div>

      {/* Error */}
      {generateDoc.isError && (
        <div className="mb-3 bg-error-light text-error rounded-xl p-3 text-sm">
          {generateDoc.error.message}
        </div>
      )}

      {/* Preview */}
      {activeDoc && (
        <div className="bg-surface-secondary rounded-xl p-4">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-xs font-medium uppercase text-text-secondary">
              {activeDoc.doc_type === "cv" ? "Tailored CV" : "Cover Letter"}{" "}
              Preview
            </span>
            <button
              onClick={handleDownloadPDF}
              className="flex items-center gap-1 bg-swiss-red hover:bg-swiss-red-hover rounded-xl px-3 py-1 text-xs font-medium text-white transition-all duration-200"
            >
              Download PDF
            </button>
          </div>
          <div ref={printRef} className="rounded bg-white p-6 shadow-sm">
            <MarkdownRenderer content={activeDoc.content} />
          </div>
        </div>
      )}

      {/* Previously generated documents */}
      {docs.length > 0 && (
        <div className="mt-4">
          <h4 className="mb-2 text-xs font-medium uppercase text-text-secondary">
            Previously Generated
          </h4>
          <div className="space-y-1">
            {docs.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center justify-between rounded-lg bg-surface-secondary px-3 py-2 border border-border-light"
              >
                <button
                  onClick={() => setActiveDoc(doc)}
                  className="text-xs text-swiss-red hover:underline"
                >
                  {doc.doc_type === "cv" ? "CV" : "Cover Letter"}
                  {doc.language && ` (${doc.language.toUpperCase()})`}
                  {" — "}
                  {new Date(doc.created_at).toLocaleDateString("de-CH")}
                </button>
                <button
                  onClick={() => deleteDoc.mutate(doc.id)}
                  className="text-xs text-error hover:text-error/80"
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(DocumentGenerator);
