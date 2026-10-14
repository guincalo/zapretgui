from __future__ import annotations

"""Установщик ставится, только если его подтверждает подписанный список выпуска."""

import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from updater.download.contracts import UpdateSignatureError
from updater.download.flow import resolve_artifact
from updater.release import signed
from updater.release.resolver import ReleaseLookup
from zapret_update import manifest
from zapret_update.ed25519 import public_key_from_seed, sign_ed25519

SEED = hashlib.sha256(b"test key").digest()
OTHER_SEED = hashlib.sha256(b"someone else").digest()
KEYS = {"test-a": public_key_from_seed(SEED)}


def _manifest_bytes(version="99.1.2.3", *, sha="a" * 64, size=12345, channel="dev", key="test-a") -> bytes:
    return manifest.dumps(
        {
            "schema": 1,
            "product": "zapret-gui",
            "channel": channel,
            "version": version,
            "created": 1792000000,
            "key": key,
            "files": [{"path": "_internal/Zapret.exe", "size": 3, "sha256": "b" * 64}],
            "installer": {"name": "Zapret2Setup.exe", "size": size, "sha256": sha},
        }
    )


def _release(version="99.1.2.3", *, with_manifest=True) -> dict:
    file_name = f"Zapret2Setup_DEV_{version.replace('.', '_')}.exe"
    base = f"https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download/{version}/"
    release = {
        "version": version,
        "update_url": base + file_name,
        "file_name": file_name,
        "file_size": 12345,
        "sha256": "a" * 64,
        "verify_ssl": True,
        "source": "Forgejo API",
    }
    if with_manifest:
        release["manifest_url"] = base + manifest.MANIFEST_NAME
        release["signature_url"] = base + manifest.SIGNATURE_NAME
    return release


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, _size):
        yield self.body

    def close(self) -> None:
        pass


class _Session:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def get(self, url, **_kwargs):
        return _Response(self.files[url.rsplit("/", 1)[1]])

    def close(self) -> None:
        pass


def _served(raw: bytes, seed: bytes = SEED):
    signature = base64.b64encode(sign_ed25519(seed, raw)) + b"\n"
    files = {manifest.MANIFEST_NAME: raw, manifest.SIGNATURE_NAME: signature}
    return lambda: _Session(files)


class SignedReleaseTests(unittest.TestCase):
    def _resolve(self, release: dict, session_factory, **kwargs):
        real = signed.fetch_signed_release

        def fetch(*args, **options):
            return real(*args, session_factory=session_factory, **options)

        with (
            patch.object(signed, "TRUSTED_KEYS", KEYS),
            patch("updater.download.flow.fetch_signed_release", fetch),
            patch("updater.download.flow.lookup_latest_release", return_value=ReleaseLookup(release)),
            patch("updater.download.flow.APP_VERSION", "21.1.5.79"),
            patch("updater.download.flow.CHANNEL", "dev"),
        ):
            return resolve_artifact(**kwargs)

    def test_signed_installer_is_taken_from_the_list(self) -> None:
        artifact = self._resolve(_release(), _served(_manifest_bytes()))

        self.assertEqual((artifact.expected_sha256, artifact.expected_size), ("a" * 64, 12345))

    def test_mirror_naming_another_checksum_is_refused(self) -> None:
        # Зеркало подменило файл и подставило его сумму: подпись этого не знает.
        release = _release()
        release["sha256"] = "c" * 64

        with self.assertRaises(UpdateSignatureError) as caught:
            self._resolve(release, _served(_manifest_bytes()))
        self.assertEqual(caught.exception.code, "sign")

    def test_list_signed_by_a_stranger_is_refused(self) -> None:
        with self.assertRaises(UpdateSignatureError) as caught:
            self._resolve(_release(), _served(_manifest_bytes(), seed=OTHER_SEED))
        self.assertEqual(caught.exception.code, "sign")

    def test_changed_list_breaks_the_signature(self) -> None:
        raw = _manifest_bytes()
        signature = base64.b64encode(sign_ed25519(SEED, raw))
        tampered = raw.replace(b'"a', b'"c', 1)
        session = lambda: _Session({manifest.MANIFEST_NAME: tampered, manifest.SIGNATURE_NAME: signature})  # noqa: E731

        with self.assertRaises(UpdateSignatureError):
            self._resolve(_release(), session)

    def test_source_without_a_signed_list_is_not_installed(self) -> None:
        with self.assertRaises(UpdateSignatureError) as caught:
            self._resolve(_release(with_manifest=False), _served(_manifest_bytes()))
        self.assertEqual(caught.exception.code, "nosig")

    def test_old_signed_release_cannot_pose_as_an_update(self) -> None:
        # Подлинный, но старый список под видом новой версии: версия в списке — своя.
        with self.assertRaises(UpdateSignatureError) as caught:
            self._resolve(_release(), _served(_manifest_bytes(version="21.1.5.70")))
        self.assertEqual(caught.exception.code, "stale")

    def test_list_of_another_channel_is_refused(self) -> None:
        with self.assertRaises(UpdateSignatureError) as caught:
            self._resolve(_release(), _served(_manifest_bytes(channel="stable")))
        self.assertEqual(caught.exception.code, "product")

    def test_repair_accepts_the_installed_version(self) -> None:
        artifact = self._resolve(_release("21.1.5.79"), _served(_manifest_bytes("21.1.5.79")), allow_same_version=True)

        self.assertEqual(artifact.version, "21.1.5.79")


class SourcesNameTheSignedListTests(unittest.TestCase):
    def test_forgejo_release_carries_its_signed_list(self) -> None:
        from updater.release import forgejo

        tag = "99.1.2.3"
        base = f"https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download/{tag}/"
        names = ["Zapret2Setup_DEV_99_1_2_3.exe", "Zapret2Setup_DEV_99_1_2_3.exe.sha256", manifest.MANIFEST_NAME, manifest.SIGNATURE_NAME]
        release = {
            "tag_name": tag,
            "prerelease": True,
            "assets": [{"name": name, "browser_download_url": base + name, "size": 1} for name in names],
        }

        candidate = forgejo._candidate(release, "dev")

        self.assertEqual(candidate["manifest_url"], base + manifest.MANIFEST_NAME)
        self.assertEqual(candidate["signature_url"], base + manifest.SIGNATURE_NAME)

    def test_mirror_names_the_list_next_to_the_installer(self) -> None:
        from updater.release import mirrors

        entry = {"version": "99.1.2.3", "file_name": "Zapret2Setup_DEV_99_1_2_3.exe", "file_size": 5, "sha256": "a" * 64}
        plain = mirrors.release_from_versions({"dev": dict(entry)}, "dev", base_url="https://m:888", verify_ssl=False, source="m")
        signed_entry = mirrors.release_from_versions(
            {"dev": {**entry, "manifest": True}}, "dev", base_url="https://m:888", verify_ssl=False, source="m"
        )

        self.assertNotIn("manifest_url", plain)
        self.assertEqual(
            signed_entry["manifest_url"], "https://m:888/download/Zapret2Setup_DEV_99_1_2_3.exe." + manifest.MANIFEST_NAME
        )


class ManifestFormatTests(unittest.TestCase):
    def test_paths_cannot_leave_the_program_folder(self) -> None:
        for bad in ("../evil.exe", "/abs.exe", "C:/x.exe", "a\\b.exe", "a//b", "./a"):
            payload = json.loads(_manifest_bytes())
            payload["files"] = [{"path": bad, "size": 1, "sha256": "b" * 64}]
            with self.assertRaises(manifest.ManifestError, msg=bad):
                manifest.parse(payload)

    def test_build_and_sign_round_trip(self) -> None:
        import tempfile
        from pathlib import Path

        from zapret_update import sign

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tree" / "_internal").mkdir(parents=True)
            (root / "tree" / "_internal" / "Zapret.exe").write_bytes(b"exe")
            (root / "tree" / "user").mkdir()
            (root / "tree" / "user" / "settings.sqlite3").write_bytes(b"mine")
            (root / "setup.exe").write_bytes(b"installer")
            key = root / "k.key"
            public = base64.b64decode(sign.generate_key("test-b", key))

            raw_path, sig_path = sign.make(
                key_path=key, product="zapret-gui", channel="dev", version="1.2.3.4",
                tree=root / "tree", installer=root / "setup.exe", exclude=("user",), out_dir=root / "out",
            )
            loaded = manifest.load_signed(
                raw_path.read_bytes(), sig_path.read_text(), trusted={"test-b": public},
                product="zapret-gui", channel="dev", newer_than="1.2.3.3",
            )

        self.assertEqual([entry.path for entry in loaded.files], ["_internal/Zapret.exe"])
        self.assertEqual(loaded.installer.sha256, hashlib.sha256(b"installer").hexdigest())

    def test_rfc8032_vector(self) -> None:
        from zapret_update.ed25519 import verify_ed25519_signature

        seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        signature = sign_ed25519(seed, b"")
        self.assertEqual(public_key_from_seed(seed).hex(), "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        self.assertTrue(signature.hex().startswith("e5564300c360ac729086e2cc806e828a"))
        self.assertTrue(verify_ed25519_signature(public_key_from_seed(seed), signature, b""))


if __name__ == "__main__":
    unittest.main()
