"""Fetch the non-GUI runtime payload of an upstream Zapret 2 GUI release.

The public zapretgui repository does not contain the engine binaries, lists,
lua scripts, presets and catalogs that the installer ships (they live in the
upstream private build repo). Upstream publishes every file of a release in a
content-addressed store next to an Ed25519-signed ``update-manifest.json``.

This script downloads that manifest + signature for one upstream release,
verifies the signature with the upstream public keys that are already in
``src/updater/release/signed.py``, then downloads every file outside
``_internal/`` (the GUI itself is rebuilt from this fork's source) and checks
each file's SHA-256 against the signed manifest. Nothing unverified is kept.

Usage: python fetch_payload.py <upstream-version> <output-dir>
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from zapret_update.manifest import load_signed  # noqa: E402  (stdlib-only module)

OWNER = "zapretdiscordyoutube"
REPO = "zapretgui"
PRODUCT = "zapret-gui"
HOSTS = ["git.zapret.moe", *(f"git{n}.zapret.moe" for n in range(2, 9))]
# Files of _internal/ we still take from upstream (not produced by PyInstaller).
KEEP_INTERNAL = {"_internal/install_manifest.json"}
UA = {"User-Agent": "guincalo-zapretgui-fork-build"}


def trusted_keys() -> dict[str, bytes]:
    tree = ast.parse((SRC / "updater/release/signed.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TRUSTED_KEYS":
            keys = {}
            for k, v in zip(node.value.keys, node.value.values):
                keys[k.value] = base64.b64decode(v.args[0].value)
            return keys
    raise SystemExit("TRUSTED_KEYS not found in signed.py")


def get(path: str, *, tries: int = 3) -> bytes:
    last: Exception | None = None
    for host in HOSTS:
        for attempt in range(tries):
            url = f"https://{host}{path}"
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                    return r.read()
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(1 + attempt)
    raise SystemExit(f"download failed for {path}: {last}")


def zstd_decompress(data: bytes) -> bytes:
    try:
        from compression import zstd  # Python 3.14+
        return zstd.decompress(data)
    except ImportError:
        import zstandard  # type: ignore
        return zstandard.ZstdDecompressor().decompressobj().decompress(data)


def main() -> None:
    version, out = sys.argv[1], Path(sys.argv[2])
    rel = f"/{OWNER}/{REPO}/releases/download/{version}"
    raw = get(f"{rel}/update-manifest.json")
    sig = get(f"{rel}/update-manifest.json.sig")
    payload = json.loads(raw)
    manifest = load_signed(raw, sig, trusted=trusted_keys(), product=PRODUCT, channel=payload.get("channel", ""))
    print(f"signed manifest OK: {manifest.product} {manifest.channel} {manifest.version} key={manifest.key}")
    out.mkdir(parents=True, exist_ok=True)
    wanted = [e for e in payload["files"] if not e["path"].startswith("_internal/") or e["path"] in KEEP_INTERNAL]

    def fetch(entry: dict) -> None:
        path = entry["path"]
        target = out / path
        if not target.resolve().is_relative_to(out.resolve()):
            raise SystemExit(f"unsafe path {path}")
        data = zstd_decompress(get(f"/api/packages/{OWNER}/generic/{PRODUCT}-store/1/{entry['sha256']}.zst"))
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != int(entry["size"]):
            raise SystemExit(f"hash/size mismatch for {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(fetch, wanted))
    count = len(wanted)
    (out / "_internal").mkdir(exist_ok=True)
    (out.parent / "upstream-update-manifest.json").write_bytes(raw)
    (out.parent / "upstream-update-manifest.json.sig").write_bytes(sig)
    print(f"payload: {count} files verified into {out}")


if __name__ == "__main__":
    main()
