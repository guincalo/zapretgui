from __future__ import annotations

"""Общее ядро: заливка хранилища выпуска в реестр пакетов Forgejo."""

import hashlib

import pytest

from zapret_update import publish


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_only_missing_or_wrong_files_are_uploaded_and_all_are_checked(tmp_path, monkeypatch) -> None:
    for name in ("one.zst", "two.zst", "bad.zst"):
        (tmp_path / name).write_bytes(name.encode())
    digest = lambda name: hashlib.sha256(name.encode()).hexdigest()  # noqa: E731
    remote = {"one.zst": (7, digest("one.zst")), "bad.zst": (7, "0" * 64)}
    calls = []

    def urlopen(request, timeout):
        name = request.full_url.rsplit("/", 1)[1]
        calls.append((request.get_method(), name, request.headers.get("Authorization")))
        if request.get_method() == "PUT":
            remote[name] = (len(name), digest(name))
        return _Response()

    monkeypatch.setattr(publish.urllib.request, "urlopen", urlopen)

    uploaded = publish.upload(
        {"one.zst", "two.zst", "bad.zst"}, cache_dir=tmp_path, owner="o", product="p", token="secret",
        listing=lambda: dict(remote),
    )

    assert uploaded == 2
    assert calls == [("DELETE", "bad.zst", "token secret"), ("PUT", "bad.zst", "token secret"), ("PUT", "two.zst", "token secret")]


def test_upload_that_did_not_land_stops_the_release(tmp_path, monkeypatch) -> None:
    (tmp_path / "one.zst").write_bytes(b"x")
    monkeypatch.setattr(publish.urllib.request, "urlopen", lambda request, timeout: _Response())

    with pytest.raises(RuntimeError, match="не тот или нет файл"):
        publish.upload({"one.zst"}, cache_dir=tmp_path, owner="o", product="p", token="t", listing=dict)


def test_no_token_no_upload(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="нет ключа"):
        publish.upload({"x.zst"}, cache_dir=tmp_path, owner="o", product="p", token="")


def test_manifest_names_cover_files_and_patches() -> None:
    from zapret_update import manifest

    loaded = manifest.parse({
        "schema": 1, "product": "p", "channel": "dev", "version": "1.0.0.1", "created": 1, "key": "k",
        "files": [{"path": "a", "size": 1, "sha256": "a" * 64, "zsize": 1}],
        "patches": [{"from": "b" * 64, "to": "a" * 64, "size": 1}],
    })

    assert publish.manifest_names(loaded) == {"a" * 64 + ".zst", "b" * 64 + "-" + "a" * 64 + ".zst"}


def _listed(files, patches=()):
    from zapret_update import manifest

    return manifest.parse({
        "schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.2", "created": 1, "key": "k",
        "files": [{"path": f"f{n}", "size": 1, "sha256": sha, "zsize": 1} for n, sha in enumerate(files)],
        "patches": [{"from": a, "to": b, "size": 1} for a, b in patches],
    })


def test_prune_removes_only_store_files_nobody_needs(monkeypatch) -> None:
    a, b, c, d = "a" * 64, "b" * 64, "c" * 64, "d" * 64
    current, previous = _listed([a], [(d, a)]), _listed([b])
    remote = {name: (1, "") for name in (f"{a}.zst", f"{d}-{a}.zst", f"{b}.zst", f"{c}.zst", f"{d}-{b}.zst", "README")}
    deleted = []

    def urlopen(request, timeout):
        assert request.get_method() == "DELETE" and request.headers.get("Authorization") == "token t"
        deleted.append(request.full_url.rsplit("/", 1)[1])
        return _Response()

    monkeypatch.setattr(publish.urllib.request, "urlopen", urlopen)

    removed = publish.prune([current, previous], owner="o", product="p", token="t", listing=lambda: dict(remote))

    assert sorted(deleted) == sorted([f"{c}.zst", f"{d}-{b}.zst"])  # чужое имя README не трогаем
    assert removed == 2


def test_prune_without_manifests_or_before_upload_deletes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(publish.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("ничего не удаляем"))
    a = "a" * 64

    with pytest.raises(ValueError):
        publish.prune([], owner="o", product="p", token="t", listing=lambda: {f"{a}.zst": (1, "")})
    with pytest.raises(RuntimeError, match="ещё не весь"):
        publish.prune([_listed([a])], owner="o", product="p", token="t", listing=lambda: {"b" * 64 + ".zst": (1, "")})
