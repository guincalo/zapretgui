# donater/crypto.py

from __future__ import annotations

import base64
import json
from typing import Dict, Optional

from zapret_update.ed25519 import verify_ed25519_signature

# kid -> base64(raw 32-byte Ed25519 public key)
TRUSTED_PUBLIC_KEYS_B64: Dict[str, str] = {
    # Generated 2026-01-28 (must match server ACTIVE_SIGNING_KID)
    "v1": "YLsnaxGcYv1CHAQbiB2dAgRTXkX6Z6klsWh6mjPi9F8=",
}


def canonical_json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def b64url_decode(data: str) -> bytes:
    data = (data or "").strip()
    if not data:
        return b""
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def b64_decode(data: str) -> bytes:
    return base64.b64decode((data or "").strip())


def verify_signed_response(
    resp: Dict,
    *,
    expected_device_id: str,
    expected_nonce: Optional[str] = None,
    trusted_public_keys_b64: Optional[Dict[str, str]] = None,
) -> Optional[Dict]:
    """
    Verify server response signature.

    Accepts either:
      - full API response: {success, signed, kid, sig}
      - cached response: {signed, kid, sig}

    Returns the signed payload dict when valid; otherwise None.
    """
    try:
        if not isinstance(resp, dict):
            return None

        signed = resp.get("signed")
        kid = (resp.get("kid") or "").strip()
        sig = (resp.get("sig") or "").strip()

        if not isinstance(signed, dict) or not kid or not sig:
            return None

        keys = trusted_public_keys_b64 or TRUSTED_PUBLIC_KEYS_B64
        pub_b64 = (keys or {}).get(kid)
        if not pub_b64:
            return None

        pub_raw = b64_decode(pub_b64)
        if len(pub_raw) != 32:
            return None

        sig_bytes = b64url_decode(sig)
        if len(sig_bytes) != 64:
            return None

        if not verify_ed25519_signature(pub_raw, sig_bytes, canonical_json(signed)):
            return None

        if str(signed.get("device_id") or "") != str(expected_device_id):
            return None

        if expected_nonce is not None and str(signed.get("nonce") or "") != str(expected_nonce):
            return None

        return signed
    except Exception:
        return None
