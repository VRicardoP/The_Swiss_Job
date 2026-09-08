import DocumentGenerator from "../components/DocumentGenerator";

export default function DocumentsPage() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <h1 className="text-xl font-semibold">Document library</h1>
      <DocumentGenerator library />
    </div>
  );
}
