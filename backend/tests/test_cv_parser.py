"""Tests for CVParser service."""

import io

import pytest

from services.cv_parser import CVParser, DOCX_TYPE, PDF_TYPE


def _make_pdf(text: str) -> bytes:
    """Create a minimal PDF with the given text."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_docx(text: str) -> bytes:
    """Create a minimal DOCX with the given text."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class TestParsePDF:
    def test_parse_pdf_extracts_text(self):
        pdf = _make_pdf("Senior Python Developer at Zurich")
        result = CVParser.parse_pdf(pdf)
        assert "Python" in result
        assert "Zurich" in result

    def test_parse_pdf_empty_document(self):
        import fitz

        doc = fitz.open()
        doc.new_page()
        pdf_bytes = doc.tobytes()
        doc.close()
        result = CVParser.parse_pdf(pdf_bytes)
        assert result == ""


class TestParseDocx:
    def test_parse_docx_extracts_text(self):
        docx = _make_docx("Full Stack Developer with React and Node.js experience")
        result = CVParser.parse_docx(docx)
        assert "React" in result
        assert "Node.js" in result


class TestExtractText:
    def test_routing_pdf(self):
        pdf = _make_pdf("Hello from PDF")
        result = CVParser.extract_text(pdf, PDF_TYPE)
        assert "Hello" in result

    def test_routing_docx(self):
        docx = _make_docx("Hello from DOCX")
        result = CVParser.extract_text(docx, DOCX_TYPE)
        assert "Hello" in result

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            CVParser.extract_text(b"data", "text/plain")


class TestExtractSkills:
    def test_finds_known_skills(self):
        # SKILL_PATTERNS (Fase 5) cubre idiomas, docencia, contenido, RRHH y gestión.
        skills = CVParser.extract_skills(
            "Fluent in English, certified in Agile and Project Management"
        )
        assert "English" in skills
        assert "Agile" in skills
        assert "Project Management" in skills

    def test_finds_multilingual_keywords(self):
        skills = CVParser.extract_skills("Languages: English, French, Spanish")
        assert "English" in skills
        assert "French" in skills
        assert "Spanish" in skills

    def test_deduplication(self):
        skills = CVParser.extract_skills("English english ENGLISH speaker")
        english_count = sum(1 for s in skills if s.lower() == "english")
        assert english_count == 1

    def test_empty_text_returns_empty(self):
        assert CVParser.extract_skills("") == []
        assert CVParser.extract_skills("no skills here at all") == []


class TestCleanText:
    def test_collapses_whitespace(self):
        result = CVParser.clean_text("Hello     World\n\n\n\n\nNew section")
        assert "  " not in result
        assert "\n\n\n" not in result
        assert "Hello World" in result


class TestFirmaDelFichero:
    """A-18: el tipo lo declaraba el CLIENTE y era la única validación.

    `file.content_type` es la cabecera de la parte multipart, o sea un valor
    arbitrario del atacante, y `extract_text` enrutaba sólo por ella. Así que
    bytes cualesquiera llegaban a MuPDF —biblioteca C— dentro del proceso que
    tiene la sesión de BD y la SECRET_KEY en memoria.

    Control negativo: quitando la llamada a `_verificar_firma`, los tres
    primeros casos dejan de lanzar.
    """

    def test_un_docx_declarado_como_pdf_se_rechaza(self):
        docx = _make_docx("soy un DOCX")
        with pytest.raises(ValueError, match="no es un PDF"):
            CVParser.extract_text(docx, PDF_TYPE)

    def test_un_pdf_declarado_como_docx_se_rechaza(self):
        pdf = _make_pdf("soy un PDF")
        with pytest.raises(ValueError, match="no es un DOCX"):
            CVParser.extract_text(pdf, DOCX_TYPE)

    def test_bytes_arbitrarios_no_llegan_al_parser(self):
        # Lo que de verdad importa: esto es lo que no debe alcanzar a MuPDF.
        with pytest.raises(ValueError, match="no es un PDF"):
            CVParser.extract_text(b"\x00\x01\x02 basura " * 100, PDF_TYPE)

    def test_un_pdf_con_basura_delante_SE_ACEPTA(self):
        # La especificación permite datos antes de la cabecera y `file(1)` los
        # tolera: la guarda no puede ser más estricta que el formato o
        # rechazaría CVs legítimos.
        pdf = _make_pdf("Desarrollador")
        assert b"%PDF-" in pdf[:16], "el PDF de prueba trae la firma al principio"
        con_basura = b"\n" * 200 + pdf
        assert "Desarrollador" in CVParser.extract_text(con_basura, PDF_TYPE)

    def test_los_formatos_legitimos_siguen_pasando(self):
        assert "PDF" in CVParser.extract_text(_make_pdf("soy PDF"), PDF_TYPE)
        assert "DOCX" in CVParser.extract_text(_make_docx("soy DOCX"), DOCX_TYPE)


class TestBombaDeDescompresion:
    """A-13: el límite de tamaño era sólo sobre los bytes COMPRIMIDOS.

    `python-docx` lee `word/document.xml` completo en memoria antes de pasarlo a
    lxml, y no había cota de lo descomprimido. Un ZIP válido de 10 MB con ratio
    ~1000:1 expande a ~10 GB, y el contenedor de producción tiene
    `mem_limit: 1536m`: UNA petición autenticada mataba al worker por OOM.

    Control negativo: quitando `_verificar_descompresion`, el primer caso deja
    de lanzar (y en un entorno sin cota de memoria se comería la RAM).
    """

    @staticmethod
    def _bomba(descomprimido_bytes: int) -> bytes:
        """Un ZIP válido y pequeño cuyo contenido descomprimido es enorme."""
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Ceros: comprimen a casi nada, que es exactamente el truco.
            zf.writestr("word/document.xml", b"\0" * descomprimido_bytes)
        return buf.getvalue()

    def test_se_rechaza_antes_de_llegar_a_python_docx(self):
        from services.cv_parser import _MAX_DESCOMPRIMIDO_BYTES

        bomba = self._bomba(_MAX_DESCOMPRIMIDO_BYTES + 1024 * 1024)
        # La prueba sólo vale si el fichero COMPRIMIDO es pequeño: es lo que
        # hace que el límite del router no lo detenga.
        assert len(bomba) < 1024 * 1024, f"comprimido: {len(bomba)} bytes"

        with pytest.raises(ValueError, match="supera la cota"):
            CVParser.extract_text(bomba, DOCX_TYPE)

    def test_un_docx_normal_pasa_la_cota(self):
        docx = _make_docx("Ingeniero de software con diez años de experiencia")
        assert "Ingeniero" in CVParser.extract_text(docx, DOCX_TYPE)

    def test_un_zip_ilegible_da_un_error_claro(self):
        # Firma ZIP correcta y contenido roto: no debe salir como un error de
        # lxml a cuatro capas de profundidad.
        with pytest.raises(ValueError, match="no es un ZIP legible"):
            CVParser.extract_text(b"PK\x03\x04" + b"roto" * 50, DOCX_TYPE)

    def test_una_bomba_con_el_TAMANO_FALSEADO_tambien_se_rechaza(self):
        """La variante que intenta esquivar un guard basado en el metadato.

        Se falsea `file_size` a 1 byte en la cabecera local Y en el directorio
        central, que es lo que engañaría a un `sum(e.file_size ...)`.

        MEDIDO, y corrige el razonamiento con el que se escribió esta guarda:
        `zipfile` acota la lectura al tamaño declarado y valida el CRC al
        terminar el miembro, así que la mentira sale como `BadZipFile: Bad
        CRC-32` y nunca llega a entregar los 100 MB. El rechazo ocurre por esa
        vía, no por la cota. Lo que este test fija es la PROPIEDAD que importa
        —que no pasa— sin afirmar por cuál de los dos caminos, porque eso es un
        detalle de CPython.
        """
        import struct

        from services.cv_parser import _MAX_DESCOMPRIMIDO_BYTES

        bomba = bytearray(self._bomba(_MAX_DESCOMPRIMIDO_BYTES + 1024 * 1024))

        # usize: offset 22 en la cabecera local, 24 en la entrada del directorio
        # central. Offsets fijos y fiables porque el ZIP lo construimos aquí:
        # sin campos extra y muy por debajo del umbral de zip64.
        struct.pack_into("<I", bomba, bomba.index(b"PK\x03\x04") + 22, 1)
        struct.pack_into("<I", bomba, bomba.index(b"PK\x01\x02") + 24, 1)

        import zipfile

        with zipfile.ZipFile(io.BytesIO(bytes(bomba))) as zf:
            assert sum(e.file_size for e in zf.infolist()) == 1, (
                "el tamaño declarado quedó falseado a 1 byte"
            )

        with pytest.raises(ValueError):
            CVParser.extract_text(bytes(bomba), DOCX_TYPE)


class TestCotaDePaginasPDF:
    """A-13: MuPDF comparte el threadpool de anyio con bcrypt, que es el camino
    del login. Un PDF de decenas de miles de páginas lo satura y degrada la
    autenticación de todos."""

    def test_un_pdf_con_demasiadas_paginas_se_rechaza(self):
        import fitz

        from services.cv_parser import _MAX_PAGINAS_PDF

        doc = fitz.open()
        for _ in range(_MAX_PAGINAS_PDF + 1):
            doc.new_page()
        pdf = doc.tobytes()
        doc.close()

        with pytest.raises(ValueError, match="páginas"):
            CVParser.extract_text(pdf, PDF_TYPE)

    def test_un_pdf_de_dos_paginas_pasa(self):
        import fitz

        doc = fitz.open()
        for _ in range(2):
            pagina = doc.new_page()
            pagina.insert_text((72, 72), "Experiencia")
        pdf = doc.tobytes()
        doc.close()

        assert "Experiencia" in CVParser.extract_text(pdf, PDF_TYPE)
