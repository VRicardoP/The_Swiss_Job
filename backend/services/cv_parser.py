"""CV Parser — extract text from PDF and DOCX files."""

import io
import logging
import re

logger = logging.getLogger(__name__)

# MIME types
PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class CVParser:
    """Stateless CV text extraction. All methods are static."""

    SKILL_PATTERNS: list[str] = [
        # Programming languages
        r"\b(Python|Java|JavaScript|TypeScript|C\+\+|C#|Go|Rust|Ruby|PHP|Swift|Kotlin|Scala|R)\b",
        # Frameworks
        r"\b(React|Angular|Vue|Django|FastAPI|Spring|Node\.js|\.NET|Flask|Rails|Express)\b",
        # Data / Cloud / Infra
        r"\b(SQL|PostgreSQL|MongoDB|Redis|Elasticsearch|Kafka|Docker|Kubernetes|AWS|Azure|GCP|Terraform)\b",
        # AI / ML
        r"\b(Machine Learning|Deep Learning|NLP|Computer Vision|TensorFlow|PyTorch|Pandas|Spark)\b",
        # Swiss-relevant
        r"\b(SAP|ABAP|Fiori|UBS|Credit Suisse|Swisscom)\b",
        # Languages (natural)
        r"\b(Deutsch|Fran[cç]ais|English|Italiano|German|French|Italian|Romansch)\b",
        # Methodologies
        r"\b(Scrum|Agile|Kanban|ITIL|Prince2|PMP|Lean|Six Sigma|DevOps|CI/CD)\b",
    ]

    @staticmethod
    def parse_pdf(file_bytes: bytes) -> str:
        """Extract plain text from a PDF file using PyMuPDF."""
        import fitz

        text_parts = []
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for page in doc:
                text_parts.append(page.get_text())
        return "\n".join(text_parts).strip()

    @staticmethod
    def parse_docx(file_bytes: bytes) -> str:
        """Extract plain text from a DOCX file using python-docx."""
        from docx import Document

        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs).strip()

    @staticmethod
    def extract_text(file_bytes: bytes, content_type: str) -> str:
        """Route to the correct parser based on content type.

        Raises ValueError for unsupported types.
        """
        if content_type == PDF_TYPE:
            return CVParser.parse_pdf(file_bytes)
        if content_type == DOCX_TYPE:
            return CVParser.parse_docx(file_bytes)
        raise ValueError(f"Unsupported file type: {content_type}")

    @staticmethod
    def extract_skills(text: str) -> list[str]:
        """Extract skill keywords from parsed CV text.

        Returns a deduplicated, sorted list of matched skills.
        """
        if not text:
            return []
        seen: dict[str, str] = {}
        for pattern in CVParser.SKILL_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                skill = match.group(0)
                key = skill.lower()
                if key not in seen:
                    seen[key] = skill
        return sorted(seen.values())

    @staticmethod
    def clean_text(text: str) -> str:
        """Clean extracted text: normalize whitespace, remove non-printable chars."""
        # Remove non-printable characters except newlines and common accented chars
        text = re.sub(r"[^\x20-\x7E\n\xC0-\xFF\u0100-\u017F]", " ", text)
        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Collapse multiple spaces
        text = re.sub(r" {2,}", " ", text)
        return text.strip()
