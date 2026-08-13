"""Licensing: device fingerprint determinism + Ed25519 verification (all statuses).

Uses a throwaway Ed25519 keypair generated in-process — never touches the real keypair in
license-generator/keys/ or the public key embedded in app/license/public_key.py, so these tests
stay hermetic and don't care whether that directory even exists on the machine running pytest.
"""
from __future__ import annotations

import base64
import json
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.license import hardware
from app.license.verify import LicenseStatus, verify_license


@pytest.fixture
def keypair():
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return private_key, public_bytes


def _sign(private_key, payload: dict) -> dict:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(canonical)
    return {"payload": payload, "signature": base64.b64encode(signature).decode("ascii")}


def _base_payload(device_id: str, expires_at: str | None = None) -> dict:
    return {
        "schema_version": 1,
        "license_id": "test-license-id",
        "device_id": device_id,
        "customer": "Cliente Teste",
        "casino": "Cassino Teste",
        "table": "ROLETA 01",
        "issued_at": "2026-01-01",
        "expires_at": expires_at,
        "features": ["core"],
    }


# -- device fingerprint ---------------------------------------------------------


def test_device_id_is_deterministic():
    assert hardware.device_id() == hardware.device_id()


def test_device_id_matches_client_example_format():
    assert hardware.device_id().count("-") == 2
    prefix, a, b = hardware.device_id().split("-")
    assert prefix == "RLT"
    assert len(a) == 4 and len(b) == 4


def test_fingerprint_full_digest_is_64_hex_chars():
    assert len(hardware.fingerprint()) == 64
    int(hardware.fingerprint(), 16)  # não levanta ValueError -> é hex válido


# -- verify_license: cada status ---------------------------------------------------


def test_missing_license_file(tmp_path, keypair):
    _, public_bytes = keypair
    result = verify_license(tmp_path / "nope.dat", public_bytes)
    assert result.status is LicenseStatus.MISSING


def test_corrupt_json(tmp_path, keypair):
    _, public_bytes = keypair
    path = tmp_path / "license.dat"
    path.write_text("{ isso não é json válido")
    result = verify_license(path, public_bytes)
    assert result.status is LicenseStatus.CORRUPT


def test_empty_file(tmp_path, keypair):
    _, public_bytes = keypair
    path = tmp_path / "license.dat"
    path.write_text("")
    result = verify_license(path, public_bytes)
    assert result.status is LicenseStatus.CORRUPT
    assert not result.ok


def test_json_missing_required_envelope_fields(tmp_path, keypair):
    """JSON sintaticamente válido, mas sem "payload"/"signature" (estrutura errada) — não pode
    passar disso pra frente achando que tem algo pra verificar."""
    _, public_bytes = keypair
    path = tmp_path / "license.dat"
    path.write_text('{"algo": "irrelevante"}')
    result = verify_license(path, public_bytes)
    assert result.status is LicenseStatus.CORRUPT


def test_wrong_public_key_embedded_in_the_app_rejects_an_otherwise_valid_license(tmp_path, keypair):
    """Se o binário embarcado apontar pra chave pública errada (erro de build/deploy, não ataque),
    o fail-closed continua valendo: nada passa, mesmo com uma licença genuína do par de chaves
    certo. Silencioso e permissivo aqui seria o pior tipo de bug de segurança."""
    private_key, _correct_public_bytes = keypair
    other_public_bytes = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    envelope = _sign(private_key, _base_payload(hardware.device_id()))
    path = tmp_path / "license.dat"
    path.write_text(json.dumps(envelope))

    result = verify_license(path, other_public_bytes)  # chave pública ERRADA
    assert result.status is LicenseStatus.INVALID_SIGNATURE
    assert not result.ok


def test_valid_perpetual_license(tmp_path, keypair):
    private_key, public_bytes = keypair
    device = hardware.device_id()
    envelope = _sign(private_key, _base_payload(device))
    path = tmp_path / "license.dat"
    path.write_text(json.dumps(envelope))

    result = verify_license(path, public_bytes)
    assert result.ok
    assert result.status is LicenseStatus.VALID
    assert result.payload["device_id"] == device


def test_tampered_payload_fails_signature(tmp_path, keypair):
    """Payload edited after signing (e.g. hand-editing the JSON) must fail, even though the
    signature bytes are syntactically well-formed."""
    private_key, public_bytes = keypair
    envelope = _sign(private_key, _base_payload(hardware.device_id()))
    envelope["payload"]["customer"] = "NOME TROCADO"  # não re-assina
    path = tmp_path / "license.dat"
    path.write_text(json.dumps(envelope))

    result = verify_license(path, public_bytes)
    assert result.status is LicenseStatus.INVALID_SIGNATURE


def test_signature_from_wrong_key_fails(tmp_path, keypair):
    """A license signed by a *different* private key (someone without the real key trying to
    forge one) must be rejected even with an otherwise well-formed envelope."""
    _, public_bytes = keypair
    other_key = Ed25519PrivateKey.generate()
    envelope = _sign(other_key, _base_payload(hardware.device_id()))
    path = tmp_path / "license.dat"
    path.write_text(json.dumps(envelope))

    result = verify_license(path, public_bytes)
    assert result.status is LicenseStatus.INVALID_SIGNATURE


def test_device_mismatch(tmp_path, keypair):
    """The SD-card-cloning scenario: a genuinely valid signature, issued for a different Device
    ID than this machine's — must be rejected, not silently accepted."""
    private_key, public_bytes = keypair
    envelope = _sign(private_key, _base_payload("RLT-0000-0000"))
    path = tmp_path / "license.dat"
    path.write_text(json.dumps(envelope))

    result = verify_license(path, public_bytes)
    assert result.status is LicenseStatus.DEVICE_MISMATCH


# -- item 16 da auditoria: os três cenários de clonagem de cartão SD, nomeados explicitamente ------
# Por que isso funciona sem precisar de dois Raspberry Pi de verdade: `verify_license` nunca lê
# nada do disco pra decidir de qual equipamento se trata — ele chama `hardware.device_id()`, que lê
# o serial do SoC (fora do cartão SD). "Clonar o SD" é, do ponto de vista deste teste, usar o MESMO
# arquivo `license.dat` (bytes idênticos) contra dois valores diferentes de `compute_device_id()` —
# exatamente a diferença que existe entre um clone rodando no Pi original vs. num Pi diferente.


def test_clone_scenario_original_sd_on_original_pi_works(tmp_path, keypair, monkeypatch):
    private_key, public_bytes = keypair
    monkeypatch.setattr("app.license.verify.compute_device_id", lambda: "RLT-AAAA-0001")
    envelope = _sign(private_key, _base_payload("RLT-AAAA-0001"))
    path = tmp_path / "license.dat"
    path.write_text(json.dumps(envelope))

    assert verify_license(path, public_bytes).ok


def test_clone_scenario_cloned_sd_back_on_the_same_original_pi_still_works(tmp_path, keypair, monkeypatch):
    """Cenário legítimo de manutenção: trocar o cartão SD (ex.: cartão antigo com defeito) no
    MESMO Raspberry Pi físico. O Device ID não muda porque ele vem do SoC, não do cartão."""
    private_key, public_bytes = keypair
    monkeypatch.setattr("app.license.verify.compute_device_id", lambda: "RLT-AAAA-0001")
    envelope = _sign(private_key, _base_payload("RLT-AAAA-0001"))
    license_bytes = json.dumps(envelope)

    original_sd = tmp_path / "original_sd_license.dat"
    original_sd.write_text(license_bytes)
    cloned_sd = tmp_path / "cloned_sd_license.dat"  # dd do cartão original, byte a byte
    cloned_sd.write_text(license_bytes)

    assert verify_license(original_sd, public_bytes).ok
    assert verify_license(cloned_sd, public_bytes).ok  # mesmo Pi -> mesmo device_id -> ok


def test_clone_scenario_cloned_sd_on_a_different_pi_is_blocked(tmp_path, keypair, monkeypatch):
    """O cenário antipirataria de verdade: o MESMO arquivo license.dat (cartão clonado bit a bit)
    rodando num Raspberry Pi FISICAMENTE diferente. compute_device_id() muda porque o serial do
    SoC é outro — a licença tem que ser recusada."""
    private_key, public_bytes = keypair
    envelope = _sign(private_key, _base_payload("RLT-AAAA-0001"))  # emitida para o Pi A
    cloned_sd = tmp_path / "cloned_sd_license.dat"
    cloned_sd.write_text(json.dumps(envelope))

    monkeypatch.setattr("app.license.verify.compute_device_id", lambda: "RLT-BBBB-0002")  # agora é o Pi B
    result = verify_license(cloned_sd, public_bytes)
    assert result.status is LicenseStatus.DEVICE_MISMATCH
    assert not result.ok


def test_expired_license(tmp_path, keypair):
    private_key, public_bytes = keypair
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    envelope = _sign(private_key, _base_payload(hardware.device_id(), expires_at=yesterday))
    path = tmp_path / "license.dat"
    path.write_text(json.dumps(envelope))

    result = verify_license(path, public_bytes)
    assert result.status is LicenseStatus.EXPIRED


def test_not_yet_expired_license_is_valid(tmp_path, keypair):
    private_key, public_bytes = keypair
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    envelope = _sign(private_key, _base_payload(hardware.device_id(), expires_at=tomorrow))
    path = tmp_path / "license.dat"
    path.write_text(json.dumps(envelope))

    result = verify_license(path, public_bytes)
    assert result.ok


def test_clock_rollback_does_not_bypass_expiry(tmp_path, keypair):
    """If the state file already recorded a timestamp AFTER the license's expiry, rolling the
    system clock back before that point must not resurrect an expired license."""
    private_key, public_bytes = keypair
    already_expired = (date.today() - timedelta(days=10)).isoformat()
    envelope = _sign(private_key, _base_payload(hardware.device_id(), expires_at=already_expired))
    license_path = tmp_path / "license.dat"
    license_path.write_text(json.dumps(envelope))

    state_path = tmp_path / ".license_state"
    from datetime import datetime, timezone
    # Simula que o app já rodou "hoje" antes (relógio real) — isso persiste no state file.
    state_path.write_text(datetime.now(timezone.utc).isoformat())

    result = verify_license(license_path, public_bytes, state_path=state_path)
    assert result.status is LicenseStatus.EXPIRED
