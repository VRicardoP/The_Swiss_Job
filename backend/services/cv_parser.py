"""CV Parser — extract text from PDF and DOCX files."""

import io
import logging
import re
import zipfile

logger = logging.getLogger(__name__)

# MIME types
PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# --- A-13 / A-18: lo que la subida NO comprobaba ---------------------------
#
# El router acota el tamaño RECIBIDO (CV_MAX_SIZE_MB = 10) y comprueba el
# `content_type`. Las dos cosas eran insuficientes:
#
#  - A-13, bomba de descompresión: el límite era sólo sobre los bytes
#    COMPRIMIDOS. `python-docx` abre el fichero con `zipfile` y lee
#    `word/document.xml` COMPLETO en memoria antes de pasarlo a lxml, y en
#    ninguna capa había cota de lo descomprimido. Un ZIP válido de 10 MB con
#    ratio ~1000:1 expande a ~10 GB, y el contenedor de producción tiene
#    `mem_limit: 1536m`: UNA petición autenticada mataba al worker de gunicorn
#    por OOM, que además pierde la caché en proceso del recorrido del feed.
#  - A-18, tipo elegido por el cliente: `file.content_type` es la cabecera de la
#    parte multipart, o sea un valor arbitrario del atacante, y era la ÚNICA
#    validación de tipo. Así que bytes cualesquiera llegaban a MuPDF —una
#    biblioteca C— dentro del proceso que tiene la sesión de BD y la SECRET_KEY
#    en memoria.
#
# Las dos guardas viven aquí, en `extract_text`, porque es el cuello único por
# el que pasan los dos formatos: cualquier llamador futuro queda cubierto sin
# acordarse de nada.

# Firmas de fichero. El PDF se busca en los primeros 1024 bytes y no en el
# offset 0 porque la especificación permite basura delante (es lo que hace
# `file(1)`); el ZIP sí exige offset 0, y sólo `PK\x03\x04`: `PK\x05\x06`
# (archivo vacío) y `PK\x07\x08` (dividido) no son DOCX válidos.
_FIRMA_PDF = b"%PDF-"
_VENTANA_FIRMA_PDF = 1024
_FIRMA_ZIP = b"PK\x03\x04"

# Cota de lo DESCOMPRIMIDO. Generosa a propósito: un CV legítimo en DOCX son
# unos pocos MB de XML más imágenes, así que 100 MB deja pasar cualquier
# documento real y mata un 1000:1.
_MAX_DESCOMPRIMIDO_BYTES = 100 * 1024 * 1024
_TROZO = 1024 * 1024

# Un CV no tiene cien páginas. Acota el trabajo de MuPDF ante un PDF hecho para
# que el parseo no termine nunca.
_MAX_PAGINAS_PDF = 100


def _verificar_firma(file_bytes: bytes, content_type: str) -> None:
    """El contenido debe CONCORDAR con el tipo que declara el cliente."""
    if content_type == PDF_TYPE:
        if _FIRMA_PDF not in file_bytes[:_VENTANA_FIRMA_PDF]:
            raise ValueError("el contenido no es un PDF (falta la firma %PDF-)")
    elif content_type == DOCX_TYPE:
        if not file_bytes.startswith(_FIRMA_ZIP):
            raise ValueError("el contenido no es un DOCX (falta la firma ZIP)")


def _verificar_descompresion(file_bytes: bytes) -> None:
    """Rechaza un DOCX cuyo contenido descomprimido excede la cota.

    Se descomprime POR TROZOS, contando lo que sale del descompresor, en vez de
    sumar el `file_size` del directorio central.

    MEDIDO, porque la primera versión de este comentario razonaba mal: resulta
    que `zipfile` **sí** acota la lectura al tamaño declarado y **sí** detecta
    que el metadato miente, porque valida el CRC al terminar el miembro (un
    `file_size` falseado a 1 produce `BadZipFile: Bad CRC-32`, no una lectura
    desbordada). Así que sumar `file_size` habría bastado para este formato.
    Contar lo leído se queda por dos motivos que sí se sostienen: no depende de
    ese comportamiento de `zipfile` —que es un detalle de implementación, no un
    contrato— y acota igual los miembros STORED, donde no hay CRC que ayude
    hasta el final. El coste son decenas de ms sobre un fichero de ≤10 MB.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            total = 0
            for entrada in zf.infolist():
                if entrada.is_dir():
                    continue
                with zf.open(entrada) as miembro:
                    while trozo := miembro.read(_TROZO):
                        total += len(trozo)
                        if total > _MAX_DESCOMPRIMIDO_BYTES:
                            raise ValueError(
                                "el contenido descomprimido supera la cota "
                                f"({_MAX_DESCOMPRIMIDO_BYTES // (1024 * 1024)} MB)"
                            )
    except zipfile.BadZipFile as exc:
        raise ValueError("el DOCX no es un ZIP legible") from exc


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
            # A-13: un CV no tiene cien páginas. Sin esta cota, un PDF con
            # decenas de miles de páginas mantiene a MuPDF trabajando dentro del
            # threadpool de anyio, que el login COMPARTE con bcrypt: saturarlo
            # degrada la autenticación de todos.
            if doc.page_count > _MAX_PAGINAS_PDF:
                raise ValueError(
                    f"el PDF tiene {doc.page_count} páginas; el máximo es "
                    f"{_MAX_PAGINAS_PDF}"
                )
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

        Raises ValueError for unsupported types, para un contenido que no
        concuerda con el tipo declarado (A-18) y para un DOCX que se
        descomprime por encima de la cota (A-13). El router las convierte en
        422; lo que importa es que ninguno de los dos llegue al parser.
        """
        if content_type not in (PDF_TYPE, DOCX_TYPE):
            raise ValueError(f"Unsupported file type: {content_type}")

        # El ORDEN importa: la firma primero, porque es lo más barato y descarta
        # el caso de bytes que no son ni del formato declarado; y la cota de
        # descompresión antes de que `python-docx` toque el fichero.
        _verificar_firma(file_bytes, content_type)

        if content_type == PDF_TYPE:
            return CVParser.parse_pdf(file_bytes)
        _verificar_descompresion(file_bytes)
        return CVParser.parse_docx(file_bytes)

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
