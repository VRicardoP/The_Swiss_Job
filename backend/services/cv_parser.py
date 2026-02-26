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
        r"\b(Python|JavaScript|TypeScript|Java|C\+\+|C#|Go|Golang|Rust|Ruby|PHP|Swift|Kotlin|Scala|R|Dart|Lua|Perl|Haskell|Elixir|Clojure)\b",
        # Frontend
        r"\b(React|Angular|Vue\.js|Vue|Next\.js|Nuxt\.js|Svelte|TailwindCSS|Tailwind|HTML|CSS|Sass|SCSS|Webpack|Vite)\b",
        # Mobile
        r"\b(React Native|Flutter|iOS|Android|SwiftUI|Jetpack Compose)\b",
        # Backend / Frameworks
        r"\b(Node\.js|Django|FastAPI|Flask|Spring Boot|Spring|Laravel|Express|Rails|ASP\.NET|\.NET|Nest\.js|Gin|Fiber)\b",
        # Data / ML / AI
        r"\b(Machine Learning|Deep Learning|Data Science|NLP|Computer Vision|TensorFlow|PyTorch|Pandas|Spark|Hadoop|Airflow|dbt|Snowflake|Databricks|Power BI|Tableau|LLM|LangChain|OpenAI)\b",
        # Databases & Messaging
        r"\b(SQL|PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|Oracle|SQLite|Cassandra|DynamoDB|Neo4j|Kafka|RabbitMQ)\b",
        # Cloud & DevOps
        r"\b(Docker|Kubernetes|AWS|Azure|GCP|Terraform|Ansible|CI/CD|Jenkins|GitHub Actions|GitLab CI|ArgoCD|Helm|Prometheus|Grafana|Datadog|Cloudflare)\b",
        # Tools & Platforms
        r"\b(Git|Linux|Jira|Confluence|Figma|GraphQL|REST API|gRPC|Microservices|API Gateway)\b",
        # Swiss-relevant (enterprise & finance)
        r"\b(SAP|ABAP|Fiori|S/4HANA|ServiceNow|Salesforce)\b",
        # Security
        r"\b(Cybersecurity|Penetration Testing|SIEM|OAuth|SSO)\b",
        # Languages (natural)
        r"\b(Deutsch|Fran[cç]ais|English|Italiano|German|French|Italian|Romansch)\b",
        # Methodologies
        r"\b(Scrum|Agile|Kanban|ITIL|Prince2|PMP|Lean|Six Sigma|DevOps|SRE)\b",
        # Roles / Specialties
        r"\b(QA|Blockchain|Product Manager|Scrum Master|Data Engineer|ML Engineer|Solution Architect)\b",
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
