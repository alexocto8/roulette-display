"""app/reports/branding.py: validação/redimensionamento/armazenamento do logo do cliente. Nunca
guarda a imagem no SQLite (só o path) e nunca deixa uma imagem inválida quebrar a geração do
relatório (validate_and_store recusa antes; is_valid_stored_logo é o fallback de segurança)."""
from __future__ import annotations

from PIL import Image

from app.reports import branding


def _make_image(path, size=(300, 200), mode="RGB", color=(255, 0, 0)):
    Image.new(mode, size, color).save(path)


def test_valid_png_is_stored_as_logo_png(tmp_path):
    source = tmp_path / "source.png"
    _make_image(source)
    dest = branding.validate_and_store(source, tmp_path / "branding")
    assert dest.name == "logo.png"
    assert dest.exists()
    with Image.open(dest) as img:
        assert img.format == "PNG"


def test_valid_jpg_is_normalized_to_png(tmp_path):
    source = tmp_path / "source.jpg"
    _make_image(source, mode="RGB")
    dest = branding.validate_and_store(source, tmp_path / "branding")
    with Image.open(dest) as img:
        assert img.format == "PNG"


def test_oversized_image_is_resized_proportionally(tmp_path):
    source = tmp_path / "big.png"
    _make_image(source, size=(4000, 2000))  # 2:1 aspect ratio
    dest = branding.validate_and_store(source, tmp_path / "branding")
    with Image.open(dest) as img:
        assert img.width <= branding.MAX_DIMENSION
        assert img.height <= branding.MAX_DIMENSION
        assert abs(img.width / img.height - 2.0) < 0.01  # proporção preservada


def test_unsupported_format_is_rejected(tmp_path):
    source = tmp_path / "logo.bmp"
    _make_image(source)
    try:
        branding.validate_and_store(source, tmp_path / "branding")
        assert False, "deveria ter recusado"
    except branding.InvalidLogoError:
        pass


def test_missing_source_file_is_rejected(tmp_path):
    try:
        branding.validate_and_store(tmp_path / "nope.png", tmp_path / "branding")
        assert False, "deveria ter recusado"
    except branding.InvalidLogoError:
        pass


def test_corrupted_image_data_is_rejected(tmp_path):
    source = tmp_path / "corrupt.png"
    source.write_bytes(b"isso nao e um png de verdade")
    try:
        branding.validate_and_store(source, tmp_path / "branding")
        assert False, "deveria ter recusado"
    except branding.InvalidLogoError:
        pass


def test_oversized_file_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "MAX_SOURCE_BYTES", 100)  # limite artificialmente baixo pro teste
    source = tmp_path / "source.png"
    _make_image(source, size=(500, 500))  # bem maior que 100 bytes depois de codificado
    try:
        branding.validate_and_store(source, tmp_path / "branding")
        assert False, "deveria ter recusado"
    except branding.InvalidLogoError:
        pass


def test_remove_deletes_the_stored_logo(tmp_path):
    source = tmp_path / "source.png"
    _make_image(source)
    dest = branding.validate_and_store(source, tmp_path / "branding")
    assert dest.exists()
    branding.remove(tmp_path / "branding")
    assert not dest.exists()


def test_remove_on_missing_logo_does_not_raise(tmp_path):
    branding.remove(tmp_path / "branding")  # nunca existiu — não deve levantar


def test_is_valid_stored_logo_true_for_real_logo(tmp_path):
    source = tmp_path / "source.png"
    _make_image(source)
    dest = branding.validate_and_store(source, tmp_path / "branding")
    assert branding.is_valid_stored_logo(dest) is True


def test_is_valid_stored_logo_false_for_none_or_missing():
    assert branding.is_valid_stored_logo(None) is False
    assert branding.is_valid_stored_logo("") is False
    assert branding.is_valid_stored_logo("/nao/existe/logo.png") is False


def test_is_valid_stored_logo_false_for_corrupted_stored_file(tmp_path):
    bogus = tmp_path / "logo.png"
    bogus.write_bytes(b"nao e uma imagem")
    assert branding.is_valid_stored_logo(bogus) is False
