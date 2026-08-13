"""Venue logo: validated, resized, and stored locally as a plain PNG file — never inside SQLite
(a blob column would bloat every backup/restore and complicate the online SQLite backup API for
no real benefit). The database only ever stores a path
(`installation_identity.venue_logo_path`), per the "não armazenar imagem no SQLite" requirement.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger("roulette.branding")

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
MAX_SOURCE_BYTES = 8 * 1024 * 1024  # 8MB — generoso para um logo, recusa arquivos absurdos
MAX_DIMENSION = 2000  # px por lado; redimensiona proporcionalmente se maior
STORED_FILENAME = "logo.png"


class InvalidLogoError(ValueError):
    """Mensagem já pronta para mostrar ao operador (curta, sem stack trace)."""


def validate_and_store(source_path: str | Path, branding_dir: str | Path) -> Path:
    """Valida o arquivo de origem, redimensiona proporcionalmente se necessário, e salva sempre
    como PNG em `branding_dir/logo.png` (normaliza o formato — o gerador de PDF e a tela de
    preview não precisam lidar com JPEG vs PNG depois). Levanta `InvalidLogoError` em qualquer
    problema — nunca deixa uma imagem quebrada ser aceita silenciosamente."""
    source = Path(source_path)
    if not source.exists():
        raise InvalidLogoError(f"Arquivo não encontrado: {source.name}")
    if source.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise InvalidLogoError(f"Formato não suportado ({source.suffix or '?'}) — use PNG, JPG ou JPEG.")
    size = source.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise InvalidLogoError(f"Arquivo muito grande ({size // 1024}KB) — máximo {MAX_SOURCE_BYTES // 1024}KB.")

    try:
        image = Image.open(source)
        image.load()  # força decodificar agora — pega arquivo corrompido/truncado aqui, não depois
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidLogoError(f"Imagem inválida ou corrompida: {exc}") from exc

    if image.mode not in ("RGBA", "RGB"):
        image = image.convert("RGBA")

    if image.width > MAX_DIMENSION or image.height > MAX_DIMENSION:
        ratio = min(MAX_DIMENSION / image.width, MAX_DIMENSION / image.height)
        new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        image = image.resize(new_size, Image.LANCZOS)

    branding_dir = Path(branding_dir)
    branding_dir.mkdir(parents=True, exist_ok=True)
    dest = branding_dir / STORED_FILENAME
    tmp = dest.with_suffix(".tmp")
    image.save(tmp, format="PNG")
    tmp.replace(dest)  # atômico — nunca deixa logo.png pela metade se faltar espaço no meio do save
    return dest


def remove(branding_dir: str | Path) -> None:
    dest = Path(branding_dir) / STORED_FILENAME
    if dest.exists():
        dest.unlink()


def is_valid_stored_logo(logo_path: str | Path | None) -> bool:
    """Fallback seguro: chamado antes de tentar carregar o logo num relatório/preview — nunca
    deixa um path ausente/inválido/corrompido derrubar a geração do relatório."""
    if not logo_path:
        return False
    path = Path(logo_path)
    if not path.exists():
        return False
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        logger.warning("Logo armazenado é inválido: %s", path, exc_info=True)
        return False
