"""minros parameters overlay — protokol sabitleri.

Rezerve kanal bloğu:
    249 = reliability ACK
    248 = logging
    247 = parameters REQ  (host → düğüm : GET, SET)
    246 = parameters RES  (düğüm → host : VALUE, ERR)

İki kanal, reliability'nin "kanal başına tek publisher" sözleşmesini sağlar.

Tip wire'da TAŞINMAZ — değer, tipin sabit boyutlu little-endian byte
gösterimidir. Bir PARAM_ID'nin hangi tipe karşılık geldiği host'ta statik bir
manifest'te (ParamClient'a verilen type_map) yaşar.

Frame'ler (payload):
    GET   : [OP=0x01][PARAM_ID]
    SET   : [OP=0x02][PARAM_ID][value bytes...]
    VALUE : [OP=0x03][PARAM_ID][value bytes...]
    ERR   : [OP=0x04][PARAM_ID][CODE]

Ayrıntı: lib/minros/minros/overlays/parameters/parameters-protocol.md

C++ tarafındaki parameters_protocol.hpp ile birebir uyumludur.
"""

import enum

PARAM_REQ_CHANNEL_ID = 247  # host → düğüm
PARAM_RES_CHANNEL_ID = 246  # düğüm → host


class OpCode(enum.IntEnum):
    GET = 0x01
    SET = 0x02
    VALUE = 0x03
    ERR = 0x04


class ErrCode(enum.IntEnum):
    UNKNOWN_ID = 0x00  # bu ID'de kayıtlı parametre yok
    READ_ONLY = 0x01  # parametre salt-okunur, yazılamaz
    BAD_LENGTH = 0x02  # value bytes uzunluğu tipin SIZE'ından kısa
    REJECTED = 0x03  # event handler (BEFORE_SET) değişikliği reddetti


class Event(enum.IntEnum):
    """SET akışında event handler'a verilen faz (bkz. ParamServer.set_event_handler)."""

    BEFORE_SET = 0  # önerilen değer; handler False dönerse reddedilir (yazılmaz)
    AFTER_SET = 1  # değer storage'a yazıldı; bildirim (dönüş yok sayılır)
