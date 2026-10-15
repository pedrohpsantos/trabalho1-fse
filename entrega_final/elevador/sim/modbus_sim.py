"""Decodificação de quadros MODBUS RTU do lado do "ESP32" virtual.

Reproduz o comportamento do simulador real validado na Entrega 2: CRC inválido ou
matrícula ausente => silêncio (timeout); função diferente de 0x03/0x10 => exceção 0x01.
"""

import struct

from ..modbus.crc import anexar_crc16, verificar_crc16
from .bancada import Bancada

TAMANHO_MATRICULA = 6


def _excecao(endereco: int, funcao: int, codigo: int) -> bytes:
    return anexar_crc16(bytes((endereco, funcao | 0x80, codigo)))


def _matricula_valida(trecho: bytes) -> bool:
    return len(trecho) == TAMANHO_MATRICULA and all(b <= 9 for b in trecho)


def processar_quadro(bancada: Bancada, quadro: bytes) -> bytes:
    """Devolve a resposta RTU ou b'' (dispositivo mudo) para um quadro recebido."""
    if len(quadro) < 6 or not verificar_crc16(quadro):
        return b""
    endereco, funcao = quadro[0], quadro[1]
    corpo = quadro[:-2]
    if funcao == 0x03:
        if len(corpo) != 6 + TAMANHO_MATRICULA or not _matricula_valida(corpo[6:]):
            return b""
        inicio, qtd = struct.unpack("<HH", corpo[2:6])
        regs = bancada.ler(endereco, inicio, qtd)
        if regs is None:
            return _excecao(endereco, funcao, 0x02)
        dados = bytes((endereco, 0x03, 2 * qtd)) + struct.pack(f">{qtd}H", *regs)
        return anexar_crc16(dados)
    if funcao == 0x10:
        if len(corpo) < 7 + TAMANHO_MATRICULA:
            return b""
        inicio, qtd, nbytes = struct.unpack("<HHB", corpo[2:7])
        if len(corpo) != 7 + nbytes + TAMANHO_MATRICULA or not _matricula_valida(corpo[-TAMANHO_MATRICULA:]):
            return b""
        if qtd == 0 or nbytes != 2 * qtd:
            return _excecao(endereco, funcao, 0x02)
        valores = list(struct.unpack(f"<{qtd}H", corpo[7 : 7 + nbytes]))
        codigo = bancada.escrever(endereco, inicio, valores)
        if codigo:
            return _excecao(endereco, funcao, codigo)
        return anexar_crc16(bytes((endereco, 0x10)) + struct.pack(">HH", inicio, qtd))
    if endereco in (0x11, 0x12, 0x13, 0x20):
        return _excecao(endereco, funcao, 0x01)
    return b""
