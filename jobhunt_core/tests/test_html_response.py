"""The shared HTML-response helpers the scraping adapters depend on."""

import pytest

from jobhunt_core.harvest.providers.html_response import (
    TagStackParser,
    job_posting_description,
    looks_like_soft_block,
)


class _Text(TagStackParser):
    """Collects the text of the element whose id is `target`."""

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.chunks = []
        self.images = []
        self._depth = 0

    def on_open(self, tag, classes, attrs, depth):
        if not self._depth and attrs.get("id") == self.target:
            self._depth = depth

    def on_void(self, tag, classes, attrs):
        if tag == "img" and self.has_ancestor_class("logo"):
            self.images.append(attrs.get("src"))

    def on_close(self, tag, depth):
        if self._depth and self._depth >= depth:
            self._depth = 0

    def on_text(self, data):
        if self._depth and data.strip():
            self.chunks.append(data.strip())


def _text(html, target="d"):
    parser = _Text(target)
    parser.feed(html)
    parser.close()
    return parser


def test_unclosed_paragraphs_do_not_end_the_region_early():
    """MUERDE sin el arreglo: una `<p>` sin cerrar desalineaba la pila y cortó
    una descripción real de myScience siete párrafos antes del final."""
    parser = _text("<div id='d'><p>one<p>two<p>three</div><p>after</p>")

    assert parser.chunks == ["one", "two", "three"]


def test_a_stray_end_tag_is_noise_not_a_close():
    parser = _text("<div id='d'></span><p>kept</p></div><p>after</p>")

    assert parser.chunks == ["kept"]


def test_script_and_style_text_is_never_content():
    parser = _text(
        "<div id='d'>real<script>var x = 'hidden';</script>"
        "<style>.a{color:red}</style>more</div>"
    )

    assert parser.chunks == ["real", "more"]


def test_void_and_self_closed_elements_never_enter_the_stack():
    parser = _text(
        "<div id='d'><div class='logo'><img src='/a.png'><br/></div>text</div>"
    )

    assert parser.images == ["/a.png"]
    assert parser.chunks == ["text"]


def test_an_unclosed_document_still_closes_its_regions():
    parser = _text("<div id='d'><p>tail")

    assert parser.chunks == ["tail"]


@pytest.mark.parametrize(
    "body",
    [
        "cf-chl-bypass",
        "Attention Required! | Cloudflare",
        "Please complete the captcha",
        "ACCESS DENIED",
    ],
)
def test_challenge_bodies_are_detected(body):
    assert looks_like_soft_block(body)


@pytest.mark.parametrize(
    "body",
    ['<script src="/recaptcha/api.js">', "h-captcha-response", "", None],
)
def test_legitimate_bodies_are_not_challenges(body):
    assert not looks_like_soft_block(body)


def test_job_posting_description_is_plain_text_and_capped():
    html = (
        '<script type="application/ld+json">'
        '{"@type": "JobPosting", "description": "<p>Ship <b>things</b></p>"}'
        "</script>"
    )

    assert job_posting_description(html, max_length=500) == "Ship things"
    assert job_posting_description(html, max_length=4) == "Ship"


def test_a_challenge_page_is_a_failure_not_an_empty_description():
    """DT-148 A8: `SoftBlocked`, no un `ValueError` cualquiera.

    Como `ValueError`, `_read` lo clasificaba como REINTENTABLE: cuatro
    peticiones con espera exponencial contra una página que nos pide parar, y
    al agotarse un «request failed (ValueError)» sin estatus — ningún bloqueo
    contabilizado. `SoftBlocked` sale sin reintento y vale 403.
    """
    from jobhunt_core.harvest.provider import (
        SOFT_BLOCK_STATUS,
        SoftBlocked,
        blocking_status,
    )

    with pytest.raises(SoftBlocked, match="soft block") as fallo:
        job_posting_description("unusual traffic detected", max_length=500)
    # Y vale como bloqueo para el kill-switch, pese a ser un HTTP 200.
    assert blocking_status(fallo.value) == SOFT_BLOCK_STATUS
