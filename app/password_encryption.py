import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from google.cloud.firestore_v1.transaction import transactional

from app.security import APP_SECRET_KEY

CHALLENGE_TTL_SECONDS = 180
CHALLENGE_COLLECTION = "password_encryption_challenges"
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
HKDF_INFO = b"cycle-sense-password-payload-v1"


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str, expected_length: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 24576:
        raise ValueError("Invalid encrypted credential")
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Invalid encrypted credential") from exc
    if expected_length is not None and len(decoded) != expected_length:
        raise ValueError("Invalid encrypted credential")
    return decoded


@lru_cache(maxsize=1)
def _server_private_key() -> ec.EllipticCurvePrivateKey:
    key_material = HKDF(
        algorithm=hashes.SHA256(),
        length=48,
        salt=b"cycle-sense-password-transport-v1",
        info=b"server-ecdh-private-key",
    ).derive(APP_SECRET_KEY.encode("utf-8"))
    private_value = int.from_bytes(key_material, "big") % (P256_ORDER - 1) + 1
    return ec.derive_private_key(private_value, ec.SECP256R1())


def get_password_encryption_public_key() -> dict[str, str | bool]:
    numbers = _server_private_key().public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _encode_base64url(numbers.x.to_bytes(32, "big")),
        "y": _encode_base64url(numbers.y.to_bytes(32, "big")),
        "ext": True,
    }


def decrypt_credential_payload(payload: dict) -> dict:
    try:
        challenge = payload["challenge"]
        ephemeral_key = payload["ephemeral_public_key"]
        if (
            not isinstance(challenge, str)
            or not 32 <= len(challenge) <= 128
            or ephemeral_key.get("kty") != "EC"
            or ephemeral_key.get("crv") != "P-256"
        ):
            raise ValueError("Invalid encrypted credential")

        x = int.from_bytes(_decode_base64url(ephemeral_key["x"], 32), "big")
        y = int.from_bytes(_decode_base64url(ephemeral_key["y"], 32), "big")
        client_public_key = ec.EllipticCurvePublicNumbers(
            x, y, ec.SECP256R1()
        ).public_key()

        salt = _decode_base64url(payload["salt"], 16)
        iv = _decode_base64url(payload["iv"], 12)
        ciphertext = _decode_base64url(payload["ciphertext"])
        if len(ciphertext) < 16 or len(ciphertext) > 16384:
            raise ValueError("Invalid encrypted credential")

        shared_secret = _server_private_key().exchange(ec.ECDH(), client_public_key)
        encryption_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=HKDF_INFO,
        ).derive(shared_secret)
        plaintext = AESGCM(encryption_key).decrypt(
            iv, ciphertext, challenge.encode("ascii")
        )
        decoded = json.loads(plaintext)
        if not isinstance(decoded, dict):
            raise ValueError("Invalid encrypted credential")
        return decoded
    except (InvalidTag, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid encrypted credential") from exc


def _challenge_reference(challenge: str):
    from app.user_service import db

    challenge_hash = hashlib.sha256(challenge.encode("ascii")).hexdigest()
    return db.collection(CHALLENGE_COLLECTION).document(challenge_hash)


def issue_password_encryption_challenge() -> dict:
    from app.user_service import db

    now = datetime.now(timezone.utc)
    collection = db.collection(CHALLENGE_COLLECTION)
    for expired in collection.where("expires_at", "<=", now).limit(100).stream():
        expired.reference.delete()

    challenge = secrets.token_urlsafe(32)
    expires_at = now + timedelta(seconds=CHALLENGE_TTL_SECONDS)
    collection.document(hashlib.sha256(challenge.encode("ascii")).hexdigest()).create(
        {"expires_at": expires_at}
    )
    return {
        "challenge": challenge,
        "expires_at": int(expires_at.timestamp()),
        "public_key": get_password_encryption_public_key(),
    }


def consume_password_encryption_challenge(challenge: str) -> bool:
    if not isinstance(challenge, str) or not 32 <= len(challenge) <= 128:
        return False

    from app.user_service import db

    reference = _challenge_reference(challenge)
    now = datetime.now(timezone.utc)

    @transactional
    def consume(transaction):
        snapshot = reference.get(transaction=transaction)
        if not snapshot.exists:
            return False
        expires_at = (snapshot.to_dict() or {}).get("expires_at")
        transaction.delete(reference)
        return isinstance(expires_at, datetime) and expires_at > now

    return consume(db.transaction())