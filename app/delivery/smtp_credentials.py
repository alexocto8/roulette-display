"""SMTP password storage: deliberately NOT in config.yaml alongside the rest of the SMTP
settings (host/port/username/etc, which stay in Config as usual).

Honest tradeoff, stated plainly rather than implying more than it delivers: this is file-
permission isolation (0600, owned by the service user, kept out of the file that's most likely to
be casually copied/viewed/backed up as "the config"), not encryption. Real encryption-at-rest
would need a key to live *somewhere* on the same unattended, physically-accessible device — which
provides no real protection against anyone who already has filesystem access, the same threat
model this project already accepted for `admin_pin` in config.yaml. Layering fake encryption on
top would be exactly the "security through obscurity" this project's licensing design explicitly
rejected elsewhere; a separate, tightly-permissioned file is the honest version of the same idea.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger("roulette.delivery")

_KEY = "smtp_password"


def get_smtp_password(credentials_path: str | Path) -> str:
    path = Path(credentials_path)
    if not path.exists():
        return ""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return str(data.get(_KEY, ""))
    except (OSError, yaml.YAMLError):
        logger.warning("Não foi possível ler as credenciais SMTP em %s", path, exc_info=True)
        return ""


def set_smtp_password(credentials_path: str | Path, password: str) -> None:
    path = Path(credentials_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump({_KEY: password}), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
