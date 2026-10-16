from __future__ import annotations

import asyncio
import hashlib
import os
import struct
import time
import unittest
from unittest.mock import patch

from telegram_proxy.proxy import routes as routes_module
from telegram_proxy.proxy import session as session_module
from telegram_proxy.proxy import ws as ws_module
from telegram_proxy.proxy.aes_ctr import AesCtrStream, aes_ctr_keystream
from telegram_proxy.proxy.key_clusters import CLUSTER_MEDIA, CLUSTER_REGULAR, KEY_NOT_FOUND_PACKET
from telegram_proxy.proxy.obfs import ABRIDGED, PADDED, PacketReader, encode_packet
from telegram_proxy.proxy.routing import UpstreamProxyConfig
from telegram_proxy.wss_proxy import TelegramWSProxy


SECRET_HEX = "00112233445566778899aabbccddeeff"
RELAY_IP = "149.154.167.220"


def make_client_header(tag: bytes, dc: int, secret: bytes = b""):
    while True:
        header = bytearray(os.urandom(64))
        if header[0] != 0xEF and bytes(header[4:8]) != b"\0\0\0\0" and bytes(header[:4]) not in (b"\xdd" * 4, b"\xee" * 4):
            break
    prekey_iv = bytes(header[8:56])
    key, iv = prekey_iv[:32], prekey_iv[32:48]
    reverse = prekey_iv[::-1]
    back_key, back_iv = reverse[:32], reverse[32:48]
    if secret:
        key = hashlib.sha256(key + secret).digest()
        back_key = hashlib.sha256(back_key + secret).digest()
    tail = tag + struct.pack("<h", dc) + b"\0\0"
    keystream = aes_ctr_keystream(key, iv, 64)
    header[56:64] = bytes(a ^ b for a, b in zip(tail, keystream[56:64]))
    to_server = AesCtrStream(key, iv)
    to_server.update(b"\0" * 64)
    return bytes(header), to_server, AesCtrStream(back_key, back_iv)


def answer_for(packet: bytes) -> bytes:
    """Ответ «Telegram» того же вида: заголовок пакета тот же, тело перевёрнуто."""
    return packet[:20] + packet[20:][::-1]


def rpc_packet(body_len: int) -> bytes:
    """Незашифрованный MTProto-пакет: auth_key_id=0, msg_id, длина, тело."""
    return b"\0" * 8 + os.urandom(8) + struct.pack("<I", body_len) + os.urandom(body_len)


def keyed_packet(key_id: bytes, body_len: int) -> bytes:
    """Пакет, зашифрованный ключом key_id (номер ключа идёт открыто)."""
    return key_id + os.urandom(16) + os.urandom(body_len)


def route_kind(host: str) -> str:
    if host == RELAY_IP or host.endswith("web.telegram.org"):
        return "relay"
    return "tunnel" if host.endswith("workers.dev") else "front"


class FakeTelegramWs:
    """WebSocket, за которым «Telegram»: принимает заголовок и отвечает эхом пакетов."""

    def __init__(
        self,
        target: ws_module.WsTarget,
        *,
        answer: bool,
        answer_delay: float = 0.0,
        send_delay: float = 0.0,
        rejects_keys: bool = False,
    ):
        self.target = target
        # Кластер за этим сокетом не знает ни одного ключа: -404 на пакет с ключом.
        self.rejects_keys = rejects_keys
        self.answer = answer
        self.answer_delay = answer_delay
        # Медленный send(): пока кадр уходит, Desktop успевает прислать ещё.
        self.send_delay = send_delay
        # Сырой поток сокета: сессия смотрит в него, когда клиент ушёл раньше ответа.
        self.reader = asyncio.StreamReader()
        self.frames: list[bytes] = []
        self.packets: list[bytes] = []
        self.header_tail = b""
        self.opened_at = time.monotonic()
        self.is_closing = False
        self._inbox: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._decrypt: AesCtrStream | None = None
        self._encrypt: AesCtrStream | None = None
        self._reader = PacketReader(ABRIDGED)

    def has_unread_data(self) -> bool:
        return False

    async def send_many(self, chunks: list[bytes]) -> None:
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        for chunk in chunks:
            self.frames.append(chunk)
            if self._decrypt is None:
                header = chunk[:64]
                self._decrypt = AesCtrStream(header[8:40], header[40:56])
                self.header_tail = self._decrypt.update(header)[56:64]
                reverse = header[8:56][::-1]
                self._encrypt = AesCtrStream(reverse[:32], reverse[32:48])
                chunk = chunk[64:]
            packets = self._reader.feed(self._decrypt.update(chunk))
            self.packets.extend(packets)
            if self.answer:
                for packet in packets:
                    keyed = packet[:8] != b"\0" * 8
                    body = KEY_NOT_FOUND_PACKET if (self.rejects_keys and keyed) else answer_for(packet)
                    reply = self._encrypt.update(encode_packet(ABRIDGED, body))
                    asyncio.get_running_loop().call_later(self.answer_delay, self._deliver, reply)

    def _deliver(self, reply: bytes) -> None:
        if self.is_closing:
            return
        self.reader.feed_data(reply)
        self._inbox.put_nowait(reply)

    async def recv(self) -> bytes | None:
        return await self._inbox.get()

    async def close(self) -> None:
        self.is_closing = True
        self.reader.feed_eof()
        self._inbox.put_nowait(None)


async def serve_fake_socks_telegram(requests: list[tuple[str, int]]):
    """Внешний SOCKS5, за которым «Telegram» по обычному TCP."""

    async def handler(reader, writer):
        try:
            greeting = await reader.readexactly(2)
            await reader.readexactly(greeting[1])
            writer.write(b"\x05\x00")
            head = await reader.readexactly(4)
            host = ".".join(str(b) for b in await reader.readexactly(4)) if head[3] == 1 else ""
            port = struct.unpack("!H", await reader.readexactly(2))[0]
            requests.append((host, port))
            writer.write(b"\x05\x00\x00\x01" + b"\0" * 6)
            header = await reader.readexactly(64)
            decrypt = AesCtrStream(header[8:40], header[40:56])
            decrypt.update(header)
            reverse = header[8:56][::-1]
            encrypt = AesCtrStream(reverse[:32], reverse[32:48])
            packets = PacketReader(ABRIDGED)
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for packet in packets.feed(decrypt.update(data)):
                    writer.write(encrypt.update(encode_packet(ABRIDGED, answer_for(packet))))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handler, "127.0.0.1", 0)


class SessionScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sockets: list[FakeTelegramWs] = []
        self.logs: list[str] = []
        self.refuse_ws = False
        self.relay_answer_delay: float | None = None
        # Релей, до которого не доходит даже SYN: connect висит без конца.
        self.relay_hangs = False
        self.send_delay = 0.0
        # Виды путей (relay / front / tunnel), которые отказывают в TCP или отвечают -404.
        self.refuse_kinds: set[str] = set()
        self.reject_kinds: set[str] = set()
        self.quota_hosts: set[str] = set()
        self.dialled: list[str] = []

        async def fake_connect(target, *, on_tcp_connected=None, **_kwargs):
            self.dialled.append(target.connect_host)
            kind = route_kind(target.connect_host)
            if kind in self.refuse_kinds:
                raise ws_module.WsConnectError(ws_module.STAGE_TCP, "refused")
            if target.connect_host == RELAY_IP and self.relay_hangs:
                await asyncio.Event().wait()
            if self.refuse_ws:
                raise ws_module.WsConnectError(ws_module.STAGE_TCP, "refused")
            if target.connect_host in self.quota_hosts:
                raise ws_module.WsConnectError(
                    ws_module.STAGE_UPGRADE, "HTTP HTTP/1.1 429 Too Many Requests", status_code=429
                )
            if target.connect_host == RELAY_IP:
                delay = self.relay_answer_delay
                sock = FakeTelegramWs(
                    target,
                    answer=delay is not None,
                    answer_delay=delay or 0.0,
                    send_delay=self.send_delay,
                    rejects_keys=kind in self.reject_kinds,
                )
            else:
                sock = FakeTelegramWs(
                    target, answer=True, send_delay=self.send_delay, rejects_keys=kind in self.reject_kinds
                )
            if on_tcp_connected is not None:
                on_tcp_connected()
            self.sockets.append(sock)
            return sock

        patches = [
            patch.object(ws_module, "connect", fake_connect),
            patch.object(session_module, "WATCHDOG_SECONDS", 0.3),
            patch.object(session_module, "MEDIA_WATCHDOG_SECONDS", 0.3),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def _start(self, **kwargs) -> TelegramWSProxy:
        proxy = TelegramWSProxy(port=0, pool_size=0, on_log=self.logs.append, **kwargs)
        await proxy.start()
        self.addAsyncCleanup(proxy.stop)
        self.port = proxy._servers[0].sockets[0].getsockname()[1]
        return proxy

    async def _socks5_connect(self, host: str, port: int):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"\x05\x01\x00")
        self.assertEqual(await reader.readexactly(2), b"\x05\x00")
        writer.write(b"\x05\x01\x00\x01" + bytes(int(x) for x in host.split(".")) + struct.pack("!H", port))
        reply = await reader.readexactly(10)
        self.assertEqual(reply[1], 0)
        return reader, writer

    async def _read_packets(self, reader, cipher, framing: str, count: int) -> list[bytes]:
        packet_reader = PacketReader(framing)
        packets: list[bytes] = []
        while len(packets) < count:
            data = await asyncio.wait_for(reader.read(65536), timeout=3)
            self.assertTrue(data, "proxy closed before the answer")
            packets.extend(packet_reader.feed(cipher.update(data)))
        return packets

    async def test_silent_relay_is_replaced_by_front_without_client_noticing(self) -> None:
        proxy = await self._start(mode="socks5")
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        first, second = rpc_packet(40), rpc_packet(1200)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, first) + encode_packet(ABRIDGED, second)))
        await writer.drain()

        answers = await self._read_packets(reader, from_server, ABRIDGED, 2)
        self.assertEqual(answers, [answer_for(first), answer_for(second)])
        writer.close()

        relay, front = self.sockets[0], self.sockets[1]
        self.assertEqual((relay.target.connect_host, relay.target.sni), (RELAY_IP, "kws2.web.telegram.org"))
        self.assertTrue(front.target.sni.startswith("kws2.") and front.target.sni.endswith(".co.uk"))
        # Один пакет — один кадр; заголовок едет в кадре с первым пакетом.
        for sock in (relay, front):
            self.assertEqual(len(sock.frames), 2)
            self.assertGreater(len(sock.frames[0]), 64)
            self.assertEqual(sock.packets, [first, second])
        # Фронту — тег abridged и +dc, без секрета.
        self.assertEqual(front.header_tail[:4], b"\xef" * 4)
        self.assertEqual(struct.unpack("<h", front.header_tail[4:6])[0], 2)
        self.assertEqual(proxy.stats.cloudflare_connections, 1)
        self.assertTrue(any("route=WSS" in line and "result=error" in line for line in self.logs))
        self.assertTrue(any("route=Cloudflare" in line and "result=connected" in line for line in self.logs))

    async def test_mtproxy_padded_client_is_rewrapped_to_abridged(self) -> None:
        await self._start(mode="mtproxy", mtproxy_secret=SECRET_HEX)
        self.sockets.clear()
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        header, to_server, from_server = make_client_header(b"\xdd" * 4, -4, bytes.fromhex(SECRET_HEX))
        packet = rpc_packet(28)
        writer.write(header + to_server.update(encode_packet(PADDED, packet)))
        await writer.drain()

        answers = await self._read_packets(reader, from_server, PADDED, 1)
        self.assertEqual(answers, [answer_for(packet)])
        writer.close()

        front = next(sock for sock in self.sockets if sock.answer)
        self.assertEqual(front.packets, [packet])
        self.assertEqual(struct.unpack("<h", front.header_tail[4:6])[0], 4)
        relay = self.sockets[0]
        self.assertEqual(relay.target.sni, "kws4-1.web.telegram.org")

    async def _leave_early(self) -> TelegramWSProxy:
        proxy = await self._start(mode="socks5")
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, _from_server = make_client_header(b"\xef" * 4, 2)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, rpc_packet(16))))
        await writer.drain()
        # Desktop на первых попытках ждёт ответа всего ~1 с — уходит раньше сторожа.
        await asyncio.sleep(0.1)
        writer.close()
        await asyncio.sleep(0.5)
        return proxy

    async def test_silent_relay_is_blamed_even_if_client_left_early(self) -> None:
        proxy = await self._leave_early()
        self.assertTrue(
            any("route=WSS" in line and "result=error" in line and "client left early" in line for line in self.logs),
            self.logs,
        )
        self.assertEqual(proxy.stats.recv_zero_count, 1)

    async def test_relay_answering_after_client_left_is_not_blamed(self) -> None:
        self.relay_answer_delay = 0.2
        proxy = await self._leave_early()
        self.assertTrue(any("answered later" in line for line in self.logs), self.logs)
        self.assertFalse(any("result=error" in line for line in self.logs), self.logs)
        self.assertEqual(proxy.stats.recv_zero_count, 0)

    async def test_silent_pooled_socket_is_not_retried_fresh(self) -> None:
        proxy = await self._start(mode="socks5")
        pooled = FakeTelegramWs(ws_module.WsTarget(RELAY_IP, "kws2.web.telegram.org"), answer=False)
        proxy.ws_pool.take = lambda route: pooled if route.kind == "relay" else None
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        packet = rpc_packet(16)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, packet)))
        await writer.drain()

        self.assertEqual(await self._read_packets(reader, from_server, ABRIDGED, 1), [answer_for(packet)])
        writer.close()
        # Молчащий сокет из пула — это отказ релея: следующим идёт фронт,
        # а не второе 5,5-секундное ожидание свежего релея.
        self.assertFalse(any(sock.target.connect_host == RELAY_IP for sock in self.sockets))
        self.assertFalse(any("retry fresh" in line for line in self.logs))

    async def test_worker_out_of_daily_quota_is_dropped_at_once_for_the_next_one(self) -> None:
        hosts = ("a.workers.dev", "b.workers.dev")
        patcher = patch.object(routes_module, "TUNNEL_HOSTS", hosts)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.quota_hosts = {"a.workers.dev"}
        proxy = await self._start(mode="socks5")
        proxy.route_health.tunnel_start = 0
        # Фронты DC203 на паузе: проверяется именно перебор воркеров.
        proxy.route_health.note_failure("cdn-kws203", threshold=1)

        for _ in range(2):
            reader, writer = await self._socks5_connect("91.105.192.100", 443)
            header, to_server, from_server = make_client_header(b"\xef" * 4, 203)
            packet = rpc_packet(40)
            writer.write(header + to_server.update(encode_packet(ABRIDGED, packet)))
            await writer.drain()
            self.assertEqual(await self._read_packets(reader, from_server, ABRIDGED, 1), [answer_for(packet)])
            writer.close()

        # Первое соединение наткнулось на 429 и ушло на второй воркер; второе
        # к исчерпанному воркеру уже не ходит.
        self.assertEqual(self.dialled, ["a.workers.dev", "b.workers.dev", "b.workers.dev"])
        self.assertTrue(proxy.route_health.is_suppressed("a.workers.dev"))
        self.assertFalse(proxy.route_health.is_suppressed("b.workers.dev"))

    async def test_country_socks_carries_traffic_when_wss_is_closed(self) -> None:
        requests: list[tuple[str, int]] = []
        socks = await serve_fake_socks_telegram(requests)
        self.addAsyncCleanup(self._close_server, socks)
        socks_port = socks.sockets[0].getsockname()[1]
        upstream = UpstreamProxyConfig(
            enabled=True, host="127.0.0.1", port=socks_port, mode="fallback", preset_id="ee", preset_name="Estonia"
        )
        self.refuse_ws = True
        proxy = await self._start(mode="socks5", upstream_config=upstream)
        reader, writer = await self._socks5_connect("149.154.175.50", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 1)
        packets = [rpc_packet(20), rpc_packet(300)]
        writer.write(header + to_server.update(b"".join(encode_packet(ABRIDGED, item) for item in packets)))
        await writer.drain()

        answers = await self._read_packets(reader, from_server, ABRIDGED, 2)
        self.assertEqual(answers, [answer_for(item) for item in packets])
        self.assertEqual(requests, [("149.154.175.50", 443)])
        self.assertEqual(proxy.stats.upstream_connections, 1)
        writer.close()

    async def test_external_proxy_switched_off_on_the_fly_leaves_mtproxy_on_wss(self) -> None:
        requests: list[tuple[str, int]] = []
        socks = await serve_fake_socks_telegram(requests)
        self.addAsyncCleanup(self._close_server, socks)
        socks_port = socks.sockets[0].getsockname()[1]
        upstream = UpstreamProxyConfig(
            enabled=True, host="127.0.0.1", port=socks_port, mode="always", preset_id="ee", preset_name="Estonia"
        )
        proxy = await self._start(mode="mtproxy", mtproxy_secret=SECRET_HEX, upstream_config=upstream)

        # Пользователь выключил «Использовать внешний прокси»: настройки отдают None.
        proxy.apply_upstream_config(None)
        self.assertFalse(proxy._upstream.enabled)
        self.assertIsNone(proxy._upstream_runtime.current_endpoint())

        self.sockets.clear()
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        header, to_server, from_server = make_client_header(b"\xdd" * 4, -4, bytes.fromhex(SECRET_HEX))
        packet = rpc_packet(28)
        writer.write(header + to_server.update(encode_packet(PADDED, packet)))
        await writer.drain()

        answers = await self._read_packets(reader, from_server, PADDED, 1)
        self.assertEqual(answers, [answer_for(packet)])
        writer.close()
        self.assertEqual(requests, [])
        self.assertEqual(proxy.stats.upstream_connections, 0)

    async def test_stop_does_not_wait_for_live_telegram_connection(self) -> None:
        proxy = TelegramWSProxy(port=0, pool_size=0, mode="socks5", on_log=self.logs.append)
        await proxy.start()
        self.port = proxy._servers[0].sockets[0].getsockname()[1]
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, rpc_packet(16))))
        await writer.drain()
        await self._read_packets(reader, from_server, ABRIDGED, 1)

        await asyncio.wait_for(proxy.stop(), timeout=3)
        self.assertFalse(proxy.is_running)
        writer.close()

    def _race_timing(self, *, watchdog: float = 2.0, race_delay: float = 0.05, loss_wait: float = 0.1) -> None:
        for item in (
            patch.object(session_module, "WATCHDOG_SECONDS", watchdog),
            patch.object(session_module, "FRONT_RACE_DELAY", race_delay),
            patch.object(session_module, "RACE_LOSS_MIN_WAIT", loss_wait),
        ):
            item.start()
            self.addCleanup(item.stop)

    async def _one_dc2_exchange(self) -> float:
        started = time.monotonic()
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        packet = rpc_packet(40)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, packet)))
        await writer.drain()
        self.assertEqual(await self._read_packets(reader, from_server, ABRIDGED, 1), [answer_for(packet)])
        writer.close()
        return time.monotonic() - started

    async def test_relay_without_tcp_loses_race_to_front_at_once(self) -> None:
        self._race_timing(loss_wait=0.0)
        self.relay_hangs = True
        proxy = await self._start(mode="socks5")

        elapsed = await self._one_dc2_exchange()

        # Раньше фронт ждал, пока релей не истратит весь свой срок; теперь
        # он стартует рядом через FRONT_RACE_DELAY и сразу везёт трафик.
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self.dialled[0], RELAY_IP)
        self.assertNotEqual(self.dialled[1], RELAY_IP)
        self.assertEqual(proxy.stats.cloudflare_connections, 1)
        await asyncio.sleep(0.05)
        # Проигрыш без TCP — отказ релея: через несколько раундов он подавится.
        self.assertTrue(
            any("route=WSS" in line and "no TCP" in line and "answered first" in line for line in self.logs),
            self.logs,
        )

    async def test_relay_address_without_tcp_hands_dc_to_its_other_ingress(self) -> None:
        # Провайдер закрыл 149.154.167.220 по IP: следующее соединение DC2 идёт
        # на второй адрес входа 149.154.167.99, а не на DNS-имя или фронт.
        self._race_timing(loss_wait=0.0)
        self.relay_hangs = True
        proxy = await self._start(mode="socks5")
        await self._one_dc2_exchange()
        await asyncio.sleep(0.05)

        self.dialled.clear()
        await self._one_dc2_exchange()

        self.assertEqual(self.dialled[0], "149.154.167.99")
        self.assertEqual(proxy.stats.wss_connections, 1)
        order = proxy.route_health.relay_address_order(("149.154.167.220", "149.154.167.99"))
        self.assertEqual(order[0], "149.154.167.99")

    async def test_relay_is_not_blamed_when_front_answers_before_it_could_connect(self) -> None:
        # Фронт из пула отвечает почти сразу; полсекунды без SYN ещё не отказ.
        self._race_timing(loss_wait=5.0)
        self.relay_hangs = True
        proxy = await self._start(mode="socks5")

        await self._one_dc2_exchange()
        await asyncio.sleep(0.05)

        self.assertEqual(proxy.stats.cloudflare_connections, 1)
        self.assertFalse(any("route=WSS" in line and "result=error" in line for line in self.logs), self.logs)

    async def test_answering_relay_wins_and_front_is_never_dialled(self) -> None:
        self._race_timing(race_delay=0.3)
        self.relay_answer_delay = 0.0
        proxy = await self._start(mode="socks5")

        await self._one_dc2_exchange()

        self.assertEqual(self.dialled, [RELAY_IP])
        self.assertEqual(proxy.stats.wss_connections, 1)
        self.assertEqual(proxy.stats.cloudflare_connections, 0)

    async def test_relay_that_answered_once_is_not_raced_any_more(self) -> None:
        self.relay_answer_delay = 0.0
        await self._start(mode="socks5")
        await self._one_dc2_exchange()

        # Релей доказал себя в этой сети; следующий молчащий релей ждёт свой
        # срок сторожа как раньше, без фронта наперегонки.
        self._race_timing(watchdog=0.3, race_delay=0.05)
        self.relay_answer_delay = None
        self.dialled.clear()
        await self._one_dc2_exchange()
        await asyncio.sleep(0.05)

        self.assertFalse(any("answered first" in line for line in self.logs), self.logs)
        self.assertTrue(any("route=WSS" in line and "no answer in 0.3s" in line for line in self.logs), self.logs)

    async def test_packets_sent_by_client_during_send_are_not_lost(self) -> None:
        self.relay_answer_delay = 0.0
        self.send_delay = 0.2
        await self._start(mode="socks5")
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        first, second = rpc_packet(40), rpc_packet(60)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, first)))
        await writer.drain()
        # Второй пакет приходит, пока первый ещё уходит на сервер.
        await asyncio.sleep(0.05)
        writer.write(to_server.update(encode_packet(ABRIDGED, second)))
        await writer.drain()

        answers = await self._read_packets(reader, from_server, ABRIDGED, 2)
        self.assertEqual(answers, [answer_for(first), answer_for(second)])
        writer.close()

    # ---- ключ медиа-сессии и два кластера DC ----

    async def _media_exchange(self, key_id: bytes, *, dc: int = -2) -> list[bytes]:
        """Один пакет с ключом от Desktop; что получил Desktop в ответ ([] — закрыто молча)."""
        reader, writer = await self._socks5_connect("149.154.167.222", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, dc)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, keyed_packet(key_id, 40))))
        await writer.drain()
        packet_reader = PacketReader(ABRIDGED)
        packets: list[bytes] = []
        while not packets:
            data = await asyncio.wait_for(reader.read(65536), timeout=3)
            if not data:
                break
            packets.extend(packet_reader.feed(from_server.update(data)))
        writer.close()
        return packets

    async def test_media_key_unknown_on_relay_cluster_moves_to_front_silently(self) -> None:
        self._race_timing(race_delay=0.3)
        self.relay_answer_delay = 0.0
        self.reject_kinds = {"relay"}
        proxy = await self._start(mode="socks5")
        key = os.urandom(8)

        answers = await self._media_exchange(key)

        # Desktop не увидел -404: ответил фронт обычного кластера, где живёт ключ.
        self.assertEqual(len(answers), 1)
        self.assertNotEqual(answers[0], KEY_NOT_FOUND_PACKET)
        self.assertEqual(proxy.stats.key_cluster_switches, 1)
        self.assertEqual(proxy.key_clusters.home(key), CLUSTER_REGULAR)
        self.assertTrue(any("-404 hidden" in line for line in self.logs), self.logs)

        # Следующее медиа-соединение с этим ключом сразу идёт к фронтам.
        self.dialled.clear()
        self.assertNotEqual((await self._media_exchange(key))[0], KEY_NOT_FOUND_PACKET)
        self.assertEqual(route_kind(self.dialled[0]), "front")
        self.assertEqual(proxy.stats.key_cluster_switches, 1)

    async def test_media_key_unknown_on_both_clusters_is_passed_to_telegram(self) -> None:
        self.relay_answer_delay = 0.0
        self.reject_kinds = {"relay", "front", "tunnel"}
        proxy = await self._start(mode="socks5")

        self.assertEqual(await self._media_exchange(os.urandom(8)), [KEY_NOT_FOUND_PACKET])
        self.assertEqual(proxy.stats.key_not_found_passed, 1)
        self.assertTrue(any("unknown on both clusters" in line for line in self.logs), self.logs)

    async def test_front_404_with_only_fallbacks_to_media_cluster_lets_key_move(self) -> None:
        self.refuse_kinds = {"relay"}
        self.reject_kinds = {"front"}
        proxy = await self._start(mode="socks5")

        self.assertEqual(await self._media_exchange(os.urandom(8)), [KEY_NOT_FOUND_PACKET])
        # Ради кластера ключа медиа не уходит на воркеры с суточным лимитом.
        self.assertFalse(any(route_kind(host) == "tunnel" for host in self.dialled), self.dialled)
        self.assertEqual(proxy.stats.key_not_found_passed, 1)

    async def test_key_home_briefly_unreachable_closes_quietly_once_then_passes_404(self) -> None:
        self.refuse_kinds = {"relay"}
        proxy = await self._start(mode="socks5")
        key = os.urandom(8)
        await self._media_exchange(key)
        self.assertEqual(proxy.key_clusters.home(key), CLUSTER_REGULAR)

        # Фронты на минуту пропали, туннель ведёт на медиа-кластер, где ключа нет.
        self.refuse_kinds = {"relay", "front"}
        self.reject_kinds = {"tunnel"}
        self.assertEqual(await self._media_exchange(key), [])
        self.assertEqual(proxy.stats.key_quiet_closes, 1)
        # Второй раз подряд — дом не вернулся, ключу пора переехать.
        self.assertEqual(await self._media_exchange(key), [KEY_NOT_FOUND_PACKET])
        self.assertEqual(proxy.key_clusters.home(key), "")

    async def test_front_404_does_not_win_the_race_against_answering_relay(self) -> None:
        self._race_timing(race_delay=0.05)
        self.relay_answer_delay = 0.2
        self.reject_kinds = {"front"}
        proxy = await self._start(mode="socks5")
        key = os.urandom(8)

        answers = await self._media_exchange(key)

        self.assertNotEqual(answers[0], KEY_NOT_FOUND_PACKET)
        self.assertTrue(any(route_kind(host) == "front" for host in self.dialled))
        self.assertEqual(proxy.key_clusters.home(key), CLUSTER_MEDIA)
        self.assertEqual(proxy.stats.key_cluster_switches, 1)

    async def test_404_on_main_session_is_passed_as_before(self) -> None:
        self.relay_answer_delay = 0.0
        self.reject_kinds = {"relay"}
        proxy = await self._start(mode="socks5")

        self.assertEqual(await self._media_exchange(os.urandom(8), dc=2), [KEY_NOT_FOUND_PACKET])
        self.assertEqual(proxy.stats.key_cluster_switches, 0)

    @staticmethod
    async def _close_server(server) -> None:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
