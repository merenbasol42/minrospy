"""minros parameters overlay (Python).

İki taraf:
    ParamServer — düğüm tarafı registry (C++ Params'ın karşılığı). PARAM_REQ'e
                  abone olur, GET/SET işler, PARAM_RES'ten VALUE/ERR yollar.
    ParamClient — host tarafı. get()/set() ile REQ yollar, VALUE/ERR'i çözer.

Her ikisi de bir "kanal katmanı" (RawNode veya reliability.Reliable) üzerinden
çalışır; katman subscribe(ch, cb) ve publish(ch, payload) sağlar.

Tip wire'da TAŞINMAZ — değer, tipin sabit boyutlu little-endian byte
gösterimidir (bkz. protocol.py). ParamServer tipi register_param'da verilen
mesaj örneğinden bilir; ParamClient ise host manifest'i olan bir type_map
(id -> mesaj sınıfı) ile VALUE'yu çözer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import protocol
from .protocol import Event

EventHandler = Callable[[int, Event, bytes], "bool | None"]


# ── Düğüm tarafı ─────────────────────────────────────────────────────────────


@dataclass
class _Entry:
    cls: type
    msg: Any
    read_only: bool


class ParamServer:
    def __init__(self, channel):
        self._ch = channel
        self._entries: dict[int, _Entry] = {}
        self._on_event: EventHandler | None = None
        channel.subscribe(protocol.PARAM_REQ_CHANNEL_ID, self._on_req)

    def register_param(self, id: int, msg, read_only: bool = False) -> None:
        """id'yi bir mesaj örneğiyle kaydeder (mevcut değeri tutar)."""
        self._entries[id] = _Entry(type(msg), msg, read_only)

    def set_event_handler(self, handler: EventHandler | None) -> None:
        """Doğrulama (BEFORE_SET) + değişim-bildirimi (AFTER_SET) callback'i.

        İmza: fn(id, event, value_bytes) -> bool | None. BEFORE_SET fazında
        False dönmek SET'i reddeder (REJECTED); AFTER_SET'in dönüşü yok sayılır.
        """
        self._on_event = handler

    def value(self, id: int):
        """Kayıtlı parametrenin güncel değerini döndürür (mesaj örneği)."""
        return self._entries[id].msg

    # ── İç ──
    def _send_value(self, id: int, e: _Entry) -> None:
        head = bytes([protocol.OpCode.VALUE, id])
        self._ch.publish(protocol.PARAM_RES_CHANNEL_ID, head + e.msg.to_bytes())

    def _send_err(self, id: int, code: protocol.ErrCode) -> None:
        self._ch.publish(
            protocol.PARAM_RES_CHANNEL_ID, bytes([protocol.OpCode.ERR, id, code])
        )

    def _on_req(self, payload: bytes) -> None:
        if len(payload) < 2:
            return
        op, pid = payload[0], payload[1]
        e = self._entries.get(pid)

        if op == protocol.OpCode.GET:
            if e is None:
                self._send_err(pid, protocol.ErrCode.UNKNOWN_ID)
                return
            self._send_value(pid, e)

        elif op == protocol.OpCode.SET:
            if e is None:
                self._send_err(pid, protocol.ErrCode.UNKNOWN_ID)
                return
            if e.read_only:
                self._send_err(pid, protocol.ErrCode.READ_ONLY)
                return
            body = payload[2:]
            if len(body) < e.cls.SIZE:
                self._send_err(pid, protocol.ErrCode.BAD_LENGTH)
                return

            if self._on_event is not None:
                if self._on_event(pid, Event.BEFORE_SET, bytes(body)) is False:
                    self._send_err(pid, protocol.ErrCode.REJECTED)
                    return

            msg = e.cls.from_bytes(body)
            if msg is None:  # SIZE'dan kısa (yukarıdaki kontrolden sonra olmamalı)
                self._send_err(pid, protocol.ErrCode.BAD_LENGTH)
                return
            e.msg = msg

            if self._on_event is not None:
                self._on_event(pid, Event.AFTER_SET, msg.to_bytes())

            self._send_value(pid, e)
        # VALUE/ERR/bilinmeyen: düğümde yoksay


# ── Host tarafı ──────────────────────────────────────────────────────────────


class ParamClient:
    def __init__(self, channel, type_map: dict[int, type] | None = None):
        self._ch = channel
        self._types: dict[int, type] = dict(type_map) if type_map else {}
        self.on_value: Callable[[int, Any], None] | None = None
        self.on_error: Callable[[int, int], None] | None = None
        self.last_value: tuple[int, Any] | None = None
        self.last_error: tuple[int, int] | None = None
        channel.subscribe(protocol.PARAM_RES_CHANNEL_ID, self._on_res)

    def register_type(self, id: int, cls: type) -> None:
        """id için mesaj sınıfını kaydeder (host manifest); VALUE bununla çözülür."""
        self._types[id] = cls

    def get(self, id: int) -> None:
        self._ch.publish(protocol.PARAM_REQ_CHANNEL_ID, bytes([protocol.OpCode.GET, id]))

    def set(self, id: int, msg) -> None:
        head = bytes([protocol.OpCode.SET, id])
        self._ch.publish(protocol.PARAM_REQ_CHANNEL_ID, head + msg.to_bytes())

    # ── İç ──
    def _on_res(self, payload: bytes) -> None:
        if len(payload) < 2:
            return
        op, pid = payload[0], payload[1]

        if op == protocol.OpCode.VALUE:
            body = payload[2:]
            cls = self._types.get(pid)
            msg = cls.from_bytes(body) if cls is not None else bytes(body)
            self.last_value = (pid, msg)
            if self.on_value is not None:
                self.on_value(pid, msg)

        elif op == protocol.OpCode.ERR:
            if len(payload) < 3:
                return
            self.last_error = (pid, payload[2])
            if self.on_error is not None:
                self.on_error(pid, payload[2])
