import pytest
from pypdf import PdfWriter

from creditcard.parsers.base import PdfPasswordError, decrypt_pdf, extract_text


def _make_encrypted_pdf(path, password):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(password)
    with open(path, "wb") as fh:
        writer.write(fh)


def test_decrypt_with_correct_password_returns_reader(tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf, "secret123")
    reader = decrypt_pdf(pdf, "secret123")
    assert len(reader.pages) == 1


def test_decrypt_with_wrong_password_raises_named_error(tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf, "secret123")
    with pytest.raises(PdfPasswordError) as exc:
        decrypt_pdf(pdf, "wrong")
    assert "enc.pdf" in str(exc.value)
    assert "wrong" not in str(exc.value)  # never leak the attempted password


def test_unencrypted_pdf_opens_without_password(tmp_path):
    pdf = tmp_path / "plain.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    assert len(decrypt_pdf(pdf, "").pages) == 1


def test_extract_text_returns_string(tmp_path):
    pdf = tmp_path / "plain.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    assert isinstance(extract_text(pdf, ""), str)


def test_extract_text_reads_an_encrypted_pdf(tmp_path):
    """The real case: every statement this pipeline sees is encrypted."""
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf, "secret123")
    assert isinstance(extract_text(pdf, "secret123"), str)


def test_extract_text_with_wrong_password_raises_named_error(tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf, "secret123")
    with pytest.raises(PdfPasswordError) as exc:
        extract_text(pdf, "nope")
    assert "enc.pdf" in str(exc.value)
    assert "nope" not in str(exc.value)
