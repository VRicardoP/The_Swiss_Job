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
        # Idiomas y nivel
        r"\b(English|Spanish|Japanese|French|Russian|Bilingual|Multilingual|Native English|Native Speaker|IELTS)\b",
        # Certificaciones educativas y lingüísticas
        r"\b(CELTA|TEFL|TESOL|IPGCE|JLPT|Cambridge|Pearson|British Council|Google Educator|SEN)\b",
        # Contenido, editorial y localización
        r"\b(Content Editor|Content Writer|Copy Editor|Proofreader|Copywriter|Localisation Specialist|Localization Specialist|Linguistic Quality Assurance|LQA|MTPE|Post-editor|Post-editing|Technical Writer|Documentation Specialist|Blog Editor|Educational Content)\b",
        # IA — evaluación de datos y anotación
        r"\b(RLHF|AI Trainer|AI Evaluator|Content Evaluator|Data Annotator|Data Annotation|Search Quality Rater|Quality Rater|Prompt Engineer)\b",
        # RRHH, L&D y People Operations
        r"\b(Instructional Designer|Instructional Design|eLearning|e-learning|Learning and Development|L&D|Talent Acquisition|HR Coordinator|HR Administrator|HR Officer|Onboarding Specialist|Training Coordinator|People Operations|People Partner|Payroll|HRIS|Workday|BambooHR)\b",
        # Administración y operaciones
        r"\b(Virtual Assistant|Executive Assistant|Administrative Coordinator|Operations Coordinator|Project Coordinator|Office Manager|Event Coordinator|Remote Assistant)\b",
        # Plataformas EdTech y herramientas de autoría
        r"\b(Google Classroom|Education Perfect|Moodle|Canvas|Blackboard|Articulate Rise|Articulate Storyline|SDL Trados|CAT Tool|LMS)\b",
        # Customer Success y relaciones con clientes
        r"\b(Customer Success|Customer Support|Customer Experience|Client Relations|VIP Relations|Concierge|Guest Experience)\b",
        # Herramientas de productividad y negocio
        r"\b(Google Workspace|Microsoft Office|HubSpot|CRM|Asana|Trello|Notion|ClickUp|Zoom|Skype)\b",
        # Hostelería y gestión hotelera
        r"\b(Hospitality|Hotel Management|Front Office|Opera PMS|BOSS|Revenue Management|Health and Safety)\b",
        # Organismos internacionales y ONGs
        r"\b(United Nations|UNESCO|UNICEF|ILO|UNOG|OECD|NGO|Programme Assistant|Documentation Assistant|Language Assistant)\b",
        # Metodologías de gestión de proyectos
        r"\b(PMP|CAPM|Prince2|Agile|Scrum|Project Management)\b",
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
