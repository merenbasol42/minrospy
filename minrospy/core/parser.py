"""Parser: gelen byte akışını frame'lere ayrıştıran durum makinesi.

Checksum: CRC-8/SMBUS (polinom 0x07, init 0x00) — wireframe ile paylaşılır.

Kullanım:
    parser = Parser()
    parser.on_frame_completed = lambda data: ...   # data: bytes (CH_ID + PAYLOAD)
    parser.on_error           = lambda err: ...
    parser.feed(incoming_bytes)
"""

import enum
from collections.abc import Callable

from . import wireframe


class Error(enum.Enum):
    INVALID_LENGTH = 0  # length alanı MIN_DATA_LEN'den küçük veya MAX_DATA sınırını aşıyor
    CRC_MISMATCH = 1    # alınan CRC hesaplanan CRC ile eşleşmiyor


class _State(enum.Enum):
    HEADER_WAIT = 0
    LENGTH_WAIT = 1
    DATA_READING = 2
    CRC_WAIT = 3


class Parser:
    def __init__(self, max_data: int = wireframe.MAX_DATA_LEN):
        if not (wireframe.MIN_DATA_LEN <= max_data <= wireframe.MAX_DATA_LEN):
            raise ValueError("max_data: MIN_DATA_LEN..MAX_DATA_LEN aralığında olmalı")
        self.max_data = max_data

        # data: bytes (CH_ID + PAYLOAD)
        self.on_frame_completed: Callable[[bytes], None] | None = None
        self.on_error: Callable[[Error], None] | None = None

        self._reset()

    def feed(self, data: bytes) -> None:
        """Gelen byte'ları durum makinesine işler; tam frame'lerde callback tetiklenir."""
        for byte in data:
            self._advance(byte)

    # ── İç durum makinesi ──────────────────────────────────────────────────

    def _advance(self, byte: int) -> None:
        if self._state is _State.HEADER_WAIT:
            if byte == wireframe.HEADER[self._header_matched]:
                self._header_matched += 1
                self._frame_buf.append(byte)
                if self._header_matched == wireframe.HEADER_SIZE:
                    self._state = _State.LENGTH_WAIT
            else:
                # Hatalı bayt yeni bir header başlangıcı olabilir. header_matched==0
                # iken frame_buf'ın boş olması değişmezdir (aşağıdaki ternary bunu
                # korur), bu yüzden match dalında ayrıca sıfırlamaya gerek yok.
                self._header_matched = 1 if byte == wireframe.HEADER[0] else 0
                self._frame_buf = bytearray([byte]) if self._header_matched else bytearray()

        elif self._state is _State.LENGTH_WAIT:
            self._frame_buf.append(byte)
            if byte < wireframe.MIN_DATA_LEN or byte > self.max_data:
                self._emit_error(Error.INVALID_LENGTH)
                self._resync()
                return
            self._data_len = byte
            self._data_remaining = byte
            self._data = bytearray()
            self._crc = 0
            self._state = _State.DATA_READING

        elif self._state is _State.DATA_READING:
            self._frame_buf.append(byte)
            self._data.append(byte)
            self._crc = wireframe.crc8_update(self._crc, byte)
            self._data_remaining -= 1
            if self._data_remaining == 0:
                self._state = _State.CRC_WAIT

        elif self._state is _State.CRC_WAIT:
            self._frame_buf.append(byte)
            if byte == self._crc:
                if self.on_frame_completed is not None:
                    self.on_frame_completed(bytes(self._data))
                self._finish_frame()
            else:
                self._emit_error(Error.CRC_MISMATCH)
                self._resync()

    def _emit_error(self, err: Error) -> None:
        if self.on_error is not None:
            self.on_error(err)

    def _finish_frame(self) -> None:
        """Bir frame başarıyla tamamlanınca çağrılır — _resync() ile simetrik
        isim: biri başarı, diğeri hata sonrası toparlanma yolunu temsil eder."""
        self._reset()

    def _resync(self) -> None:
        """Geçersiz LENGTH veya CRC_MISMATCH sonrası çağrılır.

        Eşleşmiş header'ı (wireframe.HEADER kendi içinde çakışmadığından "header
        değil" olduğu kesin ispatlanmış tek bölge) atar; LEN/DATA/CRC olarak
        tüketilmiş kalan baytları HEADER_WAIT'ten başlayarak yeniden tarar —
        içlerinde gömülü olabilecek gerçek bir frame'in header'ı kaçırılmaz.
        """
        remaining = self._frame_buf[wireframe.HEADER_SIZE:]
        self._reset()
        for byte in remaining:
            self._advance(byte)

    def _reset(self) -> None:
        self._state = _State.HEADER_WAIT
        self._header_matched = 0
        self._frame_buf = bytearray()
        self._data = bytearray()
        self._data_len = 0
        self._data_remaining = 0
        self._crc = 0
