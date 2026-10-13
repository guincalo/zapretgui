"""Умный движок hosts: проверенные адреса вместо вшитых намертво.

Повод (2026-10-13): у тумблера YouTube стояла оговорка «иногда может не работать».
Один из шести вшитых адресов умер, а Chrome из нескольких строк одного имени берёт
только первую — стоит первому адресу перестать отвечать, и сайт не открывается.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from hosts import address_engine as engine
from hosts import address_probe as probe_module
from hosts.address_probe import ALIVE, DEAD, UNKNOWN, Probe
from hosts.catalog_overlay import apply_overlay, clean_overlay
from hosts.catalog_repository import HOSTS_PROFILE_ID, HostsCatalog

SHA = "catalog-sha"


def _catalog(**services) -> HostsCatalog:
    """Каталог из словаря «service_id: (название, профиль, [(имя, адрес), ...])»."""
    profiles = ["relay", HOSTS_PROFILE_ID]
    entries: dict[str, list[tuple[str, list[str]]]] = {}
    ids: dict[str, str] = {}
    for service_id, (name, profile, rows) in services.items():
        ids[name] = service_id.replace("_", ".")
        column = profiles.index(profile)
        entries[name] = [(host, [address if index == column else "" for index in range(len(profiles))]) for host, address in rows]
    return HostsCatalog(
        dns_profiles=profiles,
        dns_profile_names={},
        services={name: {host: list(values) for host, values in rows} for name, rows in entries.items()},
        service_entries=entries,
        service_order=list(entries),
        service_modes={},
        service_categories={},
        service_icons={},
        service_id_by_name=ids,
        service_name_by_id={value: key for key, value in ids.items()},
        catalog_version="test",
        content_sha256=SHA,
    )


YOUTUBE = _catalog(
    hosts_youtube=(
        "YouTube",
        HOSTS_PROFILE_ID,
        [
            ("www.youtube.com", "1.1.1.1"),
            ("www.youtube.com", "1.1.1.2"),
            ("www.youtube.com", "1.1.1.3"),
            ("www.youtube.com", "2001:db8::1"),
            ("youtube.com", "1.1.1.1"),
        ],
    )
)


# Как в настоящем каталоге: у каждого имени YouTube один и тот же набор адресов.
YOUTUBE_EVEN = _catalog(
    hosts_youtube=(
        "YouTube",
        HOSTS_PROFILE_ID,
        [(host, address) for host in ("www.youtube.com", "youtube.com") for address in ("1.1.1.1", "1.1.1.2", "1.1.1.3")],
    )
)


def _check(catalog, selection, verdicts, *, fresh=None, previous=None, ipv6=True, strict=True, network=True, recheck=None, relays=()):
    asked: list[tuple[str, str, str]] = []
    resolved: list[str] = []

    def probe(address, host, front):
        asked.append((address, host, front))
        return verdicts.get(address, Probe(UNKNOWN))

    overlay, report = engine.check_selection(
        selection,
        catalog=catalog,
        previous=previous,
        probe=probe,
        resolve=lambda host, want_v6: resolved.append(host) or list((fresh or {}).get(host, ())),
        network_ok=lambda: network,
        ipv6=ipv6,
        strict=strict,
        recheck=recheck,
        relays=relays,
        now=lambda: datetime(2026, 10, 13, 14, 20),
    )
    _check.resolved = resolved
    return overlay, report, asked


def _rows(overlay, service_id="hosts.youtube", profile=HOSTS_PROFILE_ID):
    return [tuple(row) for row in overlay["services"][service_id][profile]]


class EngineTests(unittest.TestCase):
    def test_dead_first_address_gives_way_to_a_living_one(self) -> None:
        verdicts = {"1.1.1.1": Probe(DEAD), "1.1.1.2": Probe(ALIVE, 80.0), "1.1.1.3": Probe(ALIVE, 30.0), "2001:db8::1": Probe(ALIVE, 50.0)}

        overlay, report, asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, verdicts)

        # Первым стоит самый быстрый живой; мёртвый убран, пока есть чем заменить. У имени, где он
        # единственный, он остаётся: без адреса имя не оставляют.
        self.assertEqual(
            _rows(overlay),
            [
                ("www.youtube.com", "1.1.1.3"),
                ("www.youtube.com", "1.1.1.2"),
                ("www.youtube.com", "2001:db8::1"),
                ("youtube.com", "1.1.1.1"),
            ],
        )
        self.assertEqual(report.dropped, {"YouTube": ("1.1.1.1",)})
        self.assertTrue(report.changed)
        # У YouTube приветствие идёт с нейтральным именем владельца: имя самого сайта фильтр режет.
        self.assertIn(("1.1.1.1", "www.youtube.com", "www.google.com"), asked)
        # Адрес проверяется один раз на сервис, даже если стоит у нескольких имён.
        self.assertEqual(len([item for item in asked if item[0] == "1.1.1.1"]), 1)

    def test_name_with_no_living_address_keeps_the_catalog_and_is_reported(self) -> None:
        verdicts = {address: Probe(DEAD) for address in ("1.1.1.1", "1.1.1.2", "1.1.1.3", "2001:db8::1")}

        overlay, report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, verdicts)

        # Сервис не остаётся совсем без адреса: как мёртвый не убран ни один, и об этом сказано.
        self.assertEqual(overlay["services"]["hosts.youtube"][HOSTS_PROFILE_ID][-1], ["youtube.com", "1.1.1.1"])
        self.assertIn(["www.youtube.com", "1.1.1.1"], overlay["services"]["hosts.youtube"][HOSTS_PROFILE_ID])
        self.assertEqual(report.silent, ("YouTube",))
        self.assertEqual(report.dropped, {})

    def test_fresh_addresses_from_encrypted_dns_are_taken_only_when_they_pass_the_probe(self) -> None:
        verdicts = {"1.1.1.1": Probe(DEAD), "1.1.1.2": Probe(DEAD), "1.1.1.3": Probe(DEAD), "9.9.9.1": Probe(ALIVE, 20.0), "9.9.9.2": Probe(UNKNOWN)}
        fresh = {"www.youtube.com": ["9.9.9.1", "9.9.9.2"], "youtube.com": ["9.9.9.1"]}

        overlay, report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, verdicts, fresh=fresh, ipv6=False)

        rows = _rows(overlay)
        self.assertEqual(rows[0], ("www.youtube.com", "9.9.9.1"))
        # Свежий адрес без подтверждения в hosts не идёт: о нём ничего не известно.
        self.assertNotIn(("www.youtube.com", "9.9.9.2"), rows)
        self.assertIn(("youtube.com", "9.9.9.1"), rows)
        self.assertEqual(report.added, {"YouTube": ("9.9.9.1",)})

    def test_new_addresses_are_looked_for_at_every_check_and_the_fastest_goes_first(self) -> None:
        """Просьба владельца 2026-10-13: «соберём адреса и улучшим, чтобы новые искать», «самый быстрый по пингу?»."""
        verdicts = {
            "1.1.1.1": Probe(ALIVE, 400.0, connect_ms=60.0),
            "1.1.1.2": Probe(ALIVE, 400.0, connect_ms=45.0),
            "1.1.1.3": Probe(ALIVE, 400.0, connect_ms=80.0),
            "9.9.9.1": Probe(ALIVE, 900.0, connect_ms=18.0),
            "9.9.9.2": Probe(ALIVE, 100.0, connect_ms=70.0),
            "9.9.9.3": Probe(DEAD),
        }
        fresh = {"www.youtube.com": ["9.9.9.1", "9.9.9.2", "9.9.9.3"]}

        overlay, report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, verdicts, fresh=fresh, ipv6=False)

        # Спрошены все имена, хотя адресов каталога хватало.
        self.assertEqual(_check.resolved, ["www.youtube.com", "youtube.com"])
        # Сравнение — по времени соединения («пинг»), а не по длительности всей пробы. Найденный
        # адрес втрое быстрее — он первый; прежний первый остаётся запасным.
        self.assertEqual(_rows(overlay)[:2], [("www.youtube.com", "9.9.9.1"), ("www.youtube.com", "1.1.1.1")])
        # У второго имени свежих нет, а адреса каталога отличаются на 15 мс — порядок каталога.
        self.assertEqual(_rows(overlay)[2:], [("youtube.com", "1.1.1.1"), ("youtube.com", "1.1.1.2")])
        self.assertEqual(report.added, {"YouTube": ("9.9.9.1",)})
        # Копилка: живые находки от быстрой к медленной; отказавший адрес в неё не попадает.
        self.assertEqual(overlay["pool"], {"hosts.youtube": {"www.youtube.com": ["9.9.9.1", "9.9.9.2"]}})

    def test_collected_addresses_are_tried_again_even_when_dns_is_silent(self) -> None:
        first, _report, _asked = _check(
            YOUTUBE_EVEN,
            {"YouTube": HOSTS_PROFILE_ID},
            {"1.1.1.1": Probe(ALIVE, 1.0, connect_ms=50.0), "9.9.9.2": Probe(ALIVE, 1.0, connect_ms=55.0)},
            fresh={"www.youtube.com": ["9.9.9.2"]},
            ipv6=False,
        )
        self.assertEqual(first["pool"], {"hosts.youtube": {"www.youtube.com": ["9.9.9.2"]}})

        # Адреса каталога умерли, шифрованный DNS не ответил — выручает копилка.
        dead = {address: Probe(DEAD) for address in ("1.1.1.1", "1.1.1.2", "1.1.1.3")}
        second, report, asked = _check(
            YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, {**dead, "9.9.9.2": Probe(ALIVE, 1.0, connect_ms=55.0)}, previous=first, ipv6=False
        )

        self.assertIn("9.9.9.2", [address for address, _host, _front in asked])
        self.assertEqual(_rows(second)[0], ("www.youtube.com", "9.9.9.2"))
        self.assertEqual(second["pool"], first["pool"])

        # Адрес из копилки, который сам перестал отвечать, из неё уходит.
        third, _report, _asked = _check(
            YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, {"1.1.1.1": Probe(ALIVE, 1.0, connect_ms=50.0), "9.9.9.2": Probe(DEAD)}, previous=second, ipv6=False
        )
        self.assertEqual(third["pool"], {})

    def test_ipv6_addresses_are_looked_for_only_where_ipv6_works(self) -> None:
        """«И IPv6 искать тоже»: свежий IPv6 берётся в пробу, когда IPv6 на компьютере есть."""
        verdicts = {"1.1.1.1": Probe(ALIVE, 1.0, connect_ms=40.0), "2001:db8::77": Probe(ALIVE, 1.0, connect_ms=20.0)}
        fresh = {"www.youtube.com": ["2001:db8::77"]}

        with_v6, _report, asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, verdicts, fresh=fresh, ipv6=True)
        self.assertIn(("www.youtube.com", "2001:db8::77"), _rows(with_v6))
        self.assertEqual(with_v6["pool"], {"hosts.youtube": {"www.youtube.com": ["2001:db8::77"]}})

        without, _report, asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, verdicts, fresh=fresh, ipv6=False)
        self.assertNotIn("2001:db8::77", [address for address, _host, _front in asked])
        self.assertNotIn(("www.youtube.com", "2001:db8::77"), _rows(without))
        # Найденный раньше IPv6 при этом из копилки не теряется: IPv6 мог пропасть на время.
        kept, _report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, verdicts, previous=with_v6, ipv6=False)
        self.assertEqual(kept["pool"], with_v6["pool"])

    def test_wrong_catalog_address_is_replaced_by_a_found_one(self) -> None:
        """На стенде 2026-10-13 у camo.githubassets.com в каталоге стоял адрес с чужим сертификатом."""
        catalog = _catalog(hosts_github=("GitHub", HOSTS_PROFILE_ID, [("github.com", "2.2.2.1"), ("camo.example", "2.2.2.9")]))
        verdicts = {
            "2.2.2.1": Probe(ALIVE, 1.0, connect_ms=40.0),
            "2.2.2.9": Probe(DEAD, reason="чужой сертификат"),
            "7.7.7.7": Probe(ALIVE, 1.0, connect_ms=35.0),
        }

        overlay, report, _asked = _check(
            catalog, {"GitHub": HOSTS_PROFILE_ID}, verdicts, fresh={"github.com": ["7.7.7.7"], "camo.example": ["7.7.7.7"]}, ipv6=False
        )

        # Неверный адрес заменён найденным; у рабочего имени найденный встал запасным, вторым.
        self.assertEqual(
            _rows(overlay, "hosts.github"),
            [("github.com", "2.2.2.1"), ("github.com", "7.7.7.7"), ("camo.example", "7.7.7.7")],
        )
        self.assertEqual(report.dropped, {"GitHub": ("2.2.2.9",)})

    def test_service_without_a_passport_never_asks_dns(self) -> None:
        # У Discord в hosts обходной адрес: честный DNS вернул бы настоящий, который в России не открывается.
        catalog = _catalog(hosts_discord=("Discord", HOSTS_PROFILE_ID, [("discord.com", "23.227.38.74")]))
        resolved: list[str] = []

        overlay, _report = engine.check_selection(
            {"Discord": HOSTS_PROFILE_ID},
            catalog=catalog,
            previous=None,
            probe=lambda address, host, front: Probe(ALIVE, 10.0) if not front else Probe(DEAD),
            resolve=lambda host, want_v6: resolved.append(host) or ["162.159.138.232"],
            network_ok=lambda: True,
            ipv6=False,
            strict=True,
        )

        self.assertEqual(resolved, [])
        # Строки как в каталоге — в набор они не попадают вовсе.
        self.assertEqual(overlay["services"], {})

    def test_silence_on_port_443_never_removes_an_address_of_a_service_without_a_passport(self) -> None:
        """У игр и обходных адресов порт 443 может молчать по устройству сервиса (стенд 2026-10-13:
        game.brawlstarsgame.com слушает другой порт). Такой адрес не убирается — только уступает первое место."""
        catalog = _catalog(
            hosts_x=("x.com", HOSTS_PROFILE_ID, [("x.com", "3.3.3.1"), ("x.com", "3.3.3.2"), ("x.com", "3.3.3.3"), ("api.x.com", "3.3.3.9")])
        )
        verdicts = {"3.3.3.1": Probe(DEAD), "3.3.3.2": Probe(UNKNOWN), "3.3.3.3": Probe(ALIVE, 10.0)}

        overlay, report, asked = _check(catalog, {"x.com": HOSTS_PROFILE_ID}, verdicts)

        self.assertEqual(
            _rows(overlay, "hosts.x"),
            [("x.com", "3.3.3.3"), ("x.com", "3.3.3.1"), ("x.com", "3.3.3.2"), ("api.x.com", "3.3.3.9")],
        )
        self.assertEqual((report.dropped, report.silent), ({}, ()))
        # У имени с одним адресом выбирать не из чего — оно и не проверяется.
        self.assertNotIn("3.3.3.9", [address for address, _host, _front in asked])
        # Ответ зависит от имени: такая проба идёт на каждую пару «имя — адрес» со своим именем.
        self.assertEqual({(host, front) for _address, host, front in asked}, {("x.com", "")})

    def test_all_silent_addresses_keep_their_order(self) -> None:
        catalog = _catalog(hosts_game=("Game", HOSTS_PROFILE_ID, [("game.example", "4.4.4.1"), ("game.example", "4.4.4.2")]))

        overlay, report, _asked = _check(catalog, {"Game": HOSTS_PROFILE_ID}, {"4.4.4.1": Probe(DEAD), "4.4.4.2": Probe(DEAD)})

        self.assertEqual(overlay["services"], {})
        self.assertFalse(report.changed)
        self.assertEqual(report.silent, ())

    def test_relay_profile_puts_the_answering_relay_first(self) -> None:
        catalog = _catalog(
            dns_chatgpt=("ChatGPT", "relay", [("chatgpt.com", "5.5.5.1"), ("chatgpt.com", "5.5.5.2"), ("api.openai.com", "5.5.5.1"), ("api.openai.com", "5.5.5.2")])
        )
        # Первый посредник принимает соединение, но на приветствие молчит (так было у GeoHide 2026-10-13).
        verdicts = {"5.5.5.1": Probe(UNKNOWN), "5.5.5.2": Probe(ALIVE, 40.0)}

        overlay, _report, asked = _check(catalog, {"ChatGPT": "relay"}, verdicts, relays={"5.5.5.1", "5.5.5.2"})

        self.assertEqual(
            _rows(overlay, "dns.chatgpt", "relay"),
            [("chatgpt.com", "5.5.5.2"), ("chatgpt.com", "5.5.5.1"), ("api.openai.com", "5.5.5.2"), ("api.openai.com", "5.5.5.1")],
        )
        # Посредник пропускает только имена своего списка: нейтральное имя ему не подходит.
        self.assertEqual({front for _address, _host, front in asked}, {""})
        # Посредник один на все имена сервиса: проба на адрес, а не на каждое имя.
        self.assertEqual(len(asked), 2)

    def test_dns_profile_rows_that_are_not_relays_are_left_alone(self) -> None:
        """В профиле DNS рядом с посредником лежат обычные адреса сайтов (почта, внутренние серверы):
        на стенде 2026-10-13 их пробы дали сотню ложных «мёртв»."""
        catalog = _catalog(
            dns_naukri=("Naukri", "relay", [("naukri.com", "5.5.5.1"), ("smtp.naukri.com", "8.8.1.1"), ("smtp.naukri.com", "8.8.1.2")])
        )

        overlay, report, asked = _check(catalog, {"Naukri": "relay"}, {"8.8.1.1": Probe(DEAD)}, relays={"5.5.5.1"})

        self.assertEqual(asked, [])
        self.assertEqual(overlay["services"], {})
        self.assertFalse(report.changed)

    def test_without_internet_nothing_is_changed(self) -> None:
        everything_silent = {address: Probe(DEAD) for address in ("1.1.1.1", "1.1.1.2", "1.1.1.3", "2001:db8::1")}

        overlay, report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, everything_silent, network=False)

        self.assertIsNone(overlay)
        self.assertFalse(report.network)

    def test_address_that_loses_packets_gives_way_to_a_steady_one(self) -> None:
        """Жалоба владельца 2026-10-13: ping первого адреса YouTube — два ответа из четырёх,
        «не самое лучшее он нашёл». Одно удачное соединение потерь не показывает."""
        steady = {"1.1.1.1": Probe(ALIVE, 1.0, connect_ms=18.0), "1.1.1.2": Probe(ALIVE, 1.0, connect_ms=25.0)}
        first, _report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, steady, ipv6=False)
        self.assertEqual(_rows(first)[0], ("www.youtube.com", "1.1.1.1"))

        # Одна потеря из шести замеров бывает и на здоровой линии — порядок прежний.
        blip = {**steady, "1.1.1.1": Probe(ALIVE, 1.0, connect_ms=18.0, lost=1, samples=6)}
        same, report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, blip, ipv6=False, previous=first)
        self.assertFalse(report.changed)

        # Половина замеров без ответа: первым становится адрес без потерь, хотя он и не быстрее.
        lossy = {**steady, "1.1.1.1": Probe(ALIVE, 1.0, connect_ms=18.0, lost=3, samples=6)}
        moved, report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, lossy, ipv6=False, previous=first)
        self.assertEqual(_rows(moved)[:2], [("www.youtube.com", "1.1.1.2"), ("www.youtube.com", "1.1.1.1")])
        self.assertTrue(report.changed)

        # Быстрый адрес с потерями не вытесняет устойчивый.
        fast_but_lossy = {"1.1.1.1": Probe(ALIVE, 1.0, connect_ms=5.0, lost=3, samples=6), "1.1.1.2": Probe(ALIVE, 1.0, connect_ms=60.0)}
        kept, report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, fast_but_lossy, ipv6=False, previous=moved)
        self.assertEqual(_rows(kept)[0], ("www.youtube.com", "1.1.1.2"))

    def test_spare_address_is_a_steady_one_when_there_is_a_choice(self) -> None:
        verdicts = {
            "1.1.1.1": Probe(ALIVE, 1.0, connect_ms=20.0),
            "1.1.1.2": Probe(ALIVE, 1.0, connect_ms=20.0, lost=4, samples=6),
            "1.1.1.3": Probe(ALIVE, 1.0, connect_ms=22.0),
        }

        overlay, _report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, verdicts, ipv6=False)

        self.assertEqual(_rows(overlay)[:2], [("www.youtube.com", "1.1.1.1"), ("www.youtube.com", "1.1.1.3")])

    def test_one_address_is_not_hit_by_all_names_at_once(self) -> None:
        import threading
        import time

        # Двенадцать разных семейств имён на одном адресе: проба нужна каждому.
        hosts = [(f"cdn.site{index}.example", "6.6.6.6") for index in range(12)]
        catalog = _catalog(hosts_github=("GitHub", HOSTS_PROFILE_ID, hosts))
        lock = threading.Lock()
        running = peak = 0

        def probe(address, host, front):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.02)
            with lock:
                running -= 1
            return Probe(ALIVE, 1.0)

        engine.check_selection(
            {"GitHub": HOSTS_PROFILE_ID},
            catalog=catalog,
            previous=None,
            probe=probe,
            resolve=lambda host, want_v6: [],
            network_ok=lambda: True,
            ipv6=False,
            strict=True,
        )

        self.assertLessEqual(peak, engine.MAX_PER_ADDRESS)
        self.assertGreater(peak, 1)

    def test_names_of_one_family_on_one_address_are_probed_once(self) -> None:
        """У «GitHub загрузки/картинки» восемнадцать имён на одном адресе; проба на каждое била по
        серверу так, что он отвечал через раз (стенд 2026-10-13), и рабочий адрес заменялся свежим."""
        rows = [(f"avatars{index}.githubusercontent.com", "6.6.6.6") for index in range(9)] + [("github.githubassets.com", "6.6.6.6")]
        catalog = _catalog(hosts_github=("GitHub", HOSTS_PROFILE_ID, rows))

        overlay, _report, asked = _check(catalog, {"GitHub": HOSTS_PROFILE_ID}, {"6.6.6.6": Probe(ALIVE, 5.0)}, ipv6=False)

        # Одна проба на семейство имён: у другого семейства может быть свой сертификат.
        self.assertEqual(sorted(host for _address, host, _front in asked), ["avatars0.githubusercontent.com", "github.githubassets.com"])
        self.assertEqual(overlay["services"], {})

    def test_internet_is_checked_only_when_nobody_answered(self) -> None:
        """Отдельная проверка связи на стенде занимала три секунды на каждое нажатие тумблера."""
        calls: list[str] = []

        def network_ok() -> bool:
            calls.append("спросили")
            return True

        def run(verdicts):
            return engine.check_selection(
                {"YouTube": HOSTS_PROFILE_ID},
                catalog=YOUTUBE_EVEN,
                previous=None,
                probe=lambda address, host, front: verdicts.get(address, Probe(DEAD)),
                resolve=lambda host, want_v6: [],
                network_ok=network_ok,
                ipv6=False,
                strict=True,
            )

        run({"1.1.1.2": Probe(UNKNOWN)})
        self.assertEqual(calls, [])
        run({})
        self.assertEqual(calls, ["спросили"])

    def test_background_check_drops_an_address_only_after_two_failures_in_a_row(self) -> None:
        verdicts = {"1.1.1.1": Probe(DEAD), "1.1.1.2": Probe(ALIVE, 10.0), "1.1.1.3": Probe(ALIVE, 20.0)}

        first, report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, verdicts, strict=False, ipv6=False)
        self.assertEqual(first["suspects"], ["1.1.1.1"])
        self.assertEqual(report.dropped, {})
        # Но первым уже стоит живой: именно первый адрес берёт браузер.
        self.assertEqual(_rows(first)[0], ("www.youtube.com", "1.1.1.2"))

        second, report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, verdicts, strict=False, ipv6=False, previous=first)
        self.assertNotIn(("www.youtube.com", "1.1.1.1"), _rows(second))
        self.assertEqual(report.dropped, {"YouTube": ("1.1.1.1",)})

    def test_order_stays_put_while_the_first_address_is_alive(self) -> None:
        """Иначе блок в hosts переписывался бы при каждой проверке из-за пары миллисекунд разницы."""
        hiccup = {"1.1.1.1": Probe(UNKNOWN), "1.1.1.2": Probe(ALIVE, 10.0), "1.1.1.3": Probe(ALIVE, 50.0)}
        first, _report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, hiccup, ipv6=False)
        self.assertEqual(_rows(first)[0], ("www.youtube.com", "1.1.1.2"))

        # Первый адрес каталога снова отвечает, и чуть быстрее — но менять рабочий порядок незачем.
        all_fine = {"1.1.1.1": Probe(ALIVE, 55.0), "1.1.1.2": Probe(ALIVE, 70.0), "1.1.1.3": Probe(ALIVE, 60.0)}
        second, report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, all_fine, ipv6=False, previous=first)

        self.assertEqual(_rows(second), _rows(first))
        self.assertFalse(report.changed)

    def test_first_address_gives_way_only_to_a_clearly_faster_one(self) -> None:
        first, _report, _asked = _check(
            YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, {"1.1.1.1": Probe(ALIVE, 80.0), "1.1.1.2": Probe(ALIVE, 90.0)}, ipv6=False
        )
        self.assertEqual(_rows(first)[:2], [("www.youtube.com", "1.1.1.1"), ("www.youtube.com", "1.1.1.2")])

        # Быстрее на 25 мс — шум замера, порядок прежний.
        noise = {"1.1.1.1": Probe(ALIVE, 80.0), "1.1.1.2": Probe(ALIVE, 55.0)}
        same, report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, noise, ipv6=False, previous=first)
        self.assertFalse(report.changed)

        # Вчетверо быстрее — это уже другой сервер ближе; он и становится первым.
        closer = {"1.1.1.1": Probe(ALIVE, 80.0), "1.1.1.2": Probe(ALIVE, 20.0)}
        moved, report, _asked = _check(YOUTUBE_EVEN, {"YouTube": HOSTS_PROFILE_ID}, closer, ipv6=False, previous=first)
        self.assertEqual(_rows(moved)[:2], [("www.youtube.com", "1.1.1.2"), ("www.youtube.com", "1.1.1.1")])
        self.assertTrue(report.changed)

    def test_ipv6_rows_are_written_only_when_ipv6_really_answers(self) -> None:
        """Вопрос владельца «почему не IPv6»: у адаптера адрес есть, а связи по IPv6 нет."""
        broken_v6 = {"1.1.1.1": Probe(ALIVE, 10.0), "2001:db8::1": Probe(DEAD, reason="не отвечает")}
        overlay, report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, broken_v6, ipv6=True)
        self.assertNotIn(("www.youtube.com", "2001:db8::1"), _rows(overlay))
        self.assertEqual(report.dropped, {"YouTube": ("2001:db8::1",)})

        # На компьютере вовсе без IPv6 такие адреса не проверяются и остаются в наборе: запись их и так пропустит.
        overlay, _report, asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, {"1.1.1.1": Probe(ALIVE, 10.0)}, ipv6=False)
        self.assertNotIn("2001:db8::1", [address for address, _host, _front in asked])
        self.assertIn(("www.youtube.com", "2001:db8::1"), _rows(overlay))


    def test_toggling_one_service_does_not_recheck_the_others(self) -> None:
        """Иначе каждое нажатие тумблера ждало бы, пока перепроверятся все включённые сервисы."""
        catalog = _catalog(
            hosts_youtube=("YouTube", HOSTS_PROFILE_ID, [("www.youtube.com", "1.1.1.1"), ("www.youtube.com", "1.1.1.2")]),
            hosts_github=("GitHub", HOSTS_PROFILE_ID, [("github.com", "2.2.2.1"), ("github.com", "2.2.2.2")]),
        )
        both = {"YouTube": HOSTS_PROFILE_ID, "GitHub": HOSTS_PROFILE_ID}
        first, _report, _asked = _check(catalog, both, {"1.1.1.2": Probe(ALIVE, 5.0), "2.2.2.1": Probe(ALIVE, 5.0)}, ipv6=False)
        self.assertEqual(_rows(first), [("www.youtube.com", "1.1.1.2"), ("www.youtube.com", "1.1.1.1")])

        second, report, asked = _check(
            catalog, both, {"2.2.2.1": Probe(DEAD), "2.2.2.2": Probe(ALIVE, 9.0)}, ipv6=False, previous=first, recheck={"GitHub"}
        )

        self.assertEqual({address for address, _host, _front in asked}, {"2.2.2.1", "2.2.2.2"})
        # YouTube остался с адресами прошлой проверки, GitHub получил живой адрес первым.
        self.assertEqual(_rows(second), _rows(first))
        self.assertEqual(_rows(second, "hosts.github"), [("github.com", "2.2.2.2")])
        self.assertTrue(report.changed)
        # Отметка «когда проверяли» — про проверку всех сервисов, частичная её не двигает.
        self.assertEqual(second["checked_at"], first["checked_at"])

    def test_service_that_was_not_named_waits_for_the_background_check(self) -> None:
        overlay, report, asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, {"1.1.1.1": Probe(DEAD)}, ipv6=False, recheck=set())

        self.assertEqual(asked, [])
        self.assertEqual(overlay["services"], {})
        self.assertFalse(report.changed)

    def test_nothing_to_recheck_needs_no_internet_check(self) -> None:
        first, _report, _asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, {"1.1.1.1": Probe(ALIVE, 5.0)}, ipv6=False)

        overlay, report, asked = _check(YOUTUBE, {"YouTube": HOSTS_PROFILE_ID}, {}, previous=first, recheck=set(), network=False)

        self.assertEqual(asked, [])
        self.assertTrue(report.network)
        self.assertFalse(report.changed)
        self.assertEqual(_rows(overlay), _rows(first))


class CommandTests(unittest.TestCase):
    """Команда проверки: сеть подменена, набор адресов лежит в словаре вместо настроек."""

    def setUp(self) -> None:
        from hosts import commands, proxy_domains

        self.commands = commands
        self.stored: dict = {}
        self.probes: dict[str, Probe] = {}
        commands._PROBE_CACHE.clear()
        commands._OVERLAY_BEFORE_REFRESH = None
        for target, replacement in (
            (patch.object(proxy_domains, "load_shipped_catalog", lambda: YOUTUBE_EVEN), None),
            (patch.object(proxy_domains, "get_engine_overlay", lambda: clean_overlay(self.stored)), None),
            (patch.object(proxy_domains, "set_engine_overlay", self._store), None),
            (patch("hosts.relay_check.internet_reachable", lambda: True), None),
            (patch("hosts.ipv6_detection.is_ipv6_available", lambda: False), None),
            (patch("hosts.address_probe.check_address", self._probe), None),
            (patch.object(commands, "_fresh_addresses", lambda host, want_ipv6: []), None),
            (patch.object(commands, "load_user_selection", lambda: {"YouTube": HOSTS_PROFILE_ID}), None),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.asked: list[str] = []

    def _store(self, overlay) -> bool:
        self.stored = clean_overlay(overlay)
        return True

    def _probe(self, address, host, *, front=""):
        self.asked.append(address)
        return self.probes.get(address, Probe(UNKNOWN))

    def test_check_saves_the_overlay_and_reports_in_plain_words(self) -> None:
        self.probes = {"1.1.1.1": Probe(DEAD), "1.1.1.2": Probe(ALIVE, 10.0), "1.1.1.3": Probe(ALIVE, 20.0)}

        report = self.commands.check_addresses({"YouTube": HOSTS_PROFILE_ID, "Discord": "off"}, strict=True)

        self.assertEqual(
            self.stored["services"]["hosts.youtube"][HOSTS_PROFILE_ID],
            [["www.youtube.com", "1.1.1.2"], ["www.youtube.com", "1.1.1.3"], ["youtube.com", "1.1.1.2"], ["youtube.com", "1.1.1.3"]],
        )
        self.assertEqual(self.commands.describe_address_check(report), "Неработающие адреса убраны: YouTube.")

    def test_living_address_is_not_probed_again_for_a_while_but_a_dead_one_is(self) -> None:
        self.probes = {"1.1.1.1": Probe(DEAD), "1.1.1.2": Probe(ALIVE, 10.0), "1.1.1.3": Probe(ALIVE, 20.0)}
        self.commands.check_addresses(None)
        self.asked.clear()

        self.commands.check_addresses(None)

        self.assertEqual(self.asked, ["1.1.1.1"])

    def test_background_change_is_rolled_back_when_hosts_could_not_be_rewritten(self) -> None:
        """Страница узнаёт включённый сервис по адресам в файле: набор не должен уйти вперёд файла."""
        self.probes = {"1.1.1.1": Probe(ALIVE, 10.0)}
        self.commands.check_addresses(None)
        self.commands._settle_overlay_after_refresh(write_failed=False)
        written = self.stored

        self.probes = {"1.1.1.1": Probe(UNKNOWN), "1.1.1.2": Probe(ALIVE, 10.0)}
        self.commands._PROBE_CACHE.clear()
        self.commands.check_addresses(None)
        self.assertNotEqual(self.stored["services"], written["services"])

        self.commands._settle_overlay_after_refresh(write_failed=True)
        self.assertEqual(self.stored["services"], written["services"])
        # Второй раз возвращать нечего.
        self.assertIsNone(self.commands._OVERLAY_BEFORE_REFRESH)

    def test_refresh_command_tells_the_engine_whether_the_write_failed(self) -> None:
        class Manager:
            refresh_write_failed = True

            def refresh_applied_service_selection(self, selection, *, has_saved_selection):
                return False, "нет прав"

        with (
            patch("hosts.proxy_domains.has_saved_user_hosts_selection", lambda: True),
            patch.object(self.commands, "_settle_overlay_after_refresh") as settle,
        ):
            result = self.commands.refresh_applied_selection(Manager())

        settle.assert_called_once_with(write_failed=True)
        self.assertFalse(result.changed)

    def test_words_for_each_outcome(self) -> None:
        describe = self.commands.describe_address_check
        report = engine.EngineReport

        self.assertEqual(describe(report(network=False)), "")
        self.assertEqual(describe(report(checked=5)), "")
        self.assertEqual(describe(report(added={"YouTube": ("9.9.9.9",)})), "Взяты свежие адреса: YouTube.")
        self.assertEqual(
            describe(report(silent=("ChatGPT (OpenAI)",))),
            "Ни один адрес не ответил: ChatGPT — оставлены прежние адреса.",
        )


class PageTests(unittest.TestCase):
    def test_time_of_the_last_check_in_words(self) -> None:
        from hosts.page_plans import describe_checked_at

        now = datetime(2026, 10, 13, 20, 0)
        self.assertEqual(describe_checked_at("2026-10-13T14:20:05", now=now), ("today", "13.10", "14:20"))
        self.assertEqual(describe_checked_at("2026-10-12T23:05:00", now=now), ("date", "12.10", "23:05"))
        self.assertEqual(describe_checked_at("", now=now), ("", "", ""))
        self.assertEqual(describe_checked_at("мусор", now=now), ("", "", ""))

    def test_snapshot_carries_the_time_of_the_last_check(self) -> None:
        from hosts.page_snapshot import build_page_snapshot

        snapshot = build_page_snapshot(
            hosts_text="",
            hosts_path="hosts",
            hosts_exists=True,
            readable=True,
            read_only=False,
            ipv6_available=False,
            addresses_checked_at="2026-10-13T14:20:05",
        )

        self.assertEqual(snapshot.addresses_checked_at, "2026-10-13T14:20:05")

    def test_settings_keep_only_well_formed_engine_data(self) -> None:
        from settings.normalize import normalize_hosts
        from settings.schema import default_hosts

        self.assertEqual(normalize_hosts({})["engine"], default_hosts()["engine"])
        cleaned = normalize_hosts(
            {
                "engine": {
                    "catalog": " sha ",
                    "checked_at": "2026-10-13T14:20:05",
                    "suspects": ["1.1.1.1", "1.1.1.1"],
                    "services": {"hosts.youtube": {"hosts": [["www.youtube.com", "1.1.1.1"], ["один"], "строка"], "пусто": []}, "": {}},
                }
            }
        )["engine"]
        self.assertEqual(cleaned["catalog"], "sha")
        self.assertEqual(cleaned["suspects"], ["1.1.1.1"])
        self.assertEqual(cleaned["services"], {"hosts.youtube": {"hosts": [["www.youtube.com", "1.1.1.1"]]}})


    def test_checked_addresses_survive_a_restart_and_do_not_touch_the_selection(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from settings import store as settings_store

        overlay = clean_overlay(
            {
                "catalog": SHA,
                "checked_at": "2026-10-13T14:20:05",
                "suspects": ["1.1.1.9"],
                "services": {"hosts.youtube": {HOSTS_PROFILE_ID: [["www.youtube.com", "1.1.1.2"], ["www.youtube.com", "2001:db8::1"]]}},
            }
        )
        with TemporaryDirectory() as temp_dir, patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
            try:
                settings_store.set_hosts_selection({"hosts.youtube": HOSTS_PROFILE_ID})
                settings_store.set_hosts_engine(overlay)
                settings_store.close_settings_database()

                self.assertEqual(clean_overlay(settings_store.get_hosts_engine()), overlay)
                self.assertEqual(settings_store.get_hosts_selection(), {"hosts.youtube": HOSTS_PROFILE_ID})
            finally:
                settings_store.close_settings_database()


class OverlayTests(unittest.TestCase):
    def test_overlay_replaces_only_its_own_profile_and_keeps_the_rest(self) -> None:
        overlay = {"catalog": SHA, "services": {"hosts.youtube": {HOSTS_PROFILE_ID: [["www.youtube.com", "9.9.9.9"], ["youtube.com", "9.9.9.9"]]}}}

        catalog = apply_overlay(YOUTUBE, overlay)

        column = catalog.dns_profiles.index(HOSTS_PROFILE_ID)
        self.assertEqual(
            [(host, values[column]) for host, values in catalog.service_entries["YouTube"]],
            [("www.youtube.com", "9.9.9.9"), ("youtube.com", "9.9.9.9")],
        )

    def test_overlay_from_another_catalog_or_with_a_missing_name_is_ignored(self) -> None:
        missing_name = {"catalog": SHA, "services": {"hosts.youtube": {HOSTS_PROFILE_ID: [["www.youtube.com", "9.9.9.9"]]}}}
        foreign = {"catalog": "old", "services": {"hosts.youtube": {HOSTS_PROFILE_ID: [["www.youtube.com", "9.9.9.9"], ["youtube.com", "9.9.9.9"]]}}}

        self.assertIs(apply_overlay(YOUTUBE, missing_name), YOUTUBE)
        self.assertIs(apply_overlay(YOUTUBE, foreign), YOUTUBE)
        self.assertIs(apply_overlay(YOUTUBE, {}), YOUTUBE)

    def test_garbage_in_stored_overlay_is_dropped(self) -> None:
        cleaned = clean_overlay(
            {"catalog": SHA, "suspects": ["1.1.1.1", "не адрес"], "services": {"hosts.youtube": {HOSTS_PROFILE_ID: [["a.example", "1.1.1.1"], ["b.example", "мусор"], "строка"]}}}
        )

        self.assertEqual(cleaned["services"], {"hosts.youtube": {HOSTS_PROFILE_ID: [["a.example", "1.1.1.1"]]}})
        self.assertEqual(cleaned["suspects"], ["1.1.1.1"])


class ProbeTests(unittest.TestCase):
    def test_answer_counts_only_inside_the_family_of_the_service_name(self) -> None:
        ok = b"HTTP/1.1 200 OK\r\nServer: ESF\r\n\r\n"
        to_www = b"HTTP/1.1 301 Moved\r\nLocation: https://www.youtube.com/\r\n\r\n"
        # Так на запрос с именем YouTube отвечает 8.8.8.8: адрес Google, но сервис не тот.
        to_dns = b"HTTP/1.1 302 Found\r\nLocation: https://dns.google/\r\n\r\n"

        self.assertTrue(probe_module._serves("www.youtube.com", ok))
        self.assertTrue(probe_module._serves("youtube.com", to_www))
        self.assertFalse(probe_module._serves("www.youtube.com", to_dns))
        self.assertFalse(probe_module._serves("www.youtube.com", b"HTTP/1.1 403 Forbidden\r\n\r\n"))
        self.assertFalse(probe_module._serves("www.youtube.com", b""))

    def test_silent_port_is_dead_only_after_a_second_try(self) -> None:
        tries: list[str] = []

        def connect(address):
            tries.append(address)
            return None, "timeout"

        with patch.object(probe_module, "_connect", connect), patch.object(probe_module.time, "sleep", lambda _s: None):
            result = probe_module.check_address("203.0.113.1", "example.com")

        self.assertEqual((result.verdict, result.reason), (DEAD, "не отвечает"))
        self.assertEqual(len(tries), 2)

    def test_living_address_is_measured_several_times_for_speed_and_losses(self) -> None:
        outcomes = iter(["ok", "timeout", "ok", "timeout", "ok", "ok"])
        # Время берётся перед каждым соединением и ещё раз — после удачного.
        clock = iter([0.0, 0.030, 1.0, 2.0, 2.010, 3.0, 4.0, 4.020, 5.0, 5.016])

        class _Sock:
            def close(self):
                pass

        def connect(address, timeout_s):
            outcome = next(outcomes)
            return (_Sock() if outcome == "ok" else None), outcome

        with (
            patch.object(probe_module, "_connect", connect),
            patch.object(probe_module.time, "perf_counter", lambda: next(clock)),
            patch.object(probe_module.time, "sleep", lambda _s: None),
        ):
            middle, lost, samples = probe_module._quality("203.0.113.1", 18.0)

        # Два соединения из шести без ответа; время — середина удачных замеров
        # (18, 30, 10, 20 и 16 мс), потерянные в него не входят.
        self.assertEqual((lost, samples), (2, 6))
        self.assertEqual(middle, 18.0)

    def test_closed_port_is_dead_at_once(self) -> None:
        with patch.object(probe_module, "_connect", lambda address: (None, "refused")) as _connect:
            result = probe_module.check_address("203.0.113.1", "example.com")

        self.assertEqual((result.verdict, result.reason), (DEAD, "порт закрыт"))


if __name__ == "__main__":
    unittest.main()
