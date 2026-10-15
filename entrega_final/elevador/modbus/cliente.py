"""Cliente MODBUS RTU (funções 0x03 e 0x10) adaptado do módulo `entrega2/parte3_simulador`.

Particularidades do simulador (validadas na Entrega 2):
  * requisição: registrador e quantidade em little-endian;
  * valores escritos (0x10) em little-endian, valores lidos (0x03) em big-endian;
  * eco da escrita em big-endian; CRC em little-endian;
  * os 6 últimos dígitos da matrícula, como bytes crus 0x00..0x09, vão antes do CRC.
Retentativas (até 3) só para timeout/CRC inválido; exceções 0x01/0x02/0x03 não se repetem.
"""

import logging
import struct
import time

from .crc import anexar_crc16, verificar_crc16
from .uart import UartBase

log = logging.getLogger(__name__)

MATRICULA_PADRAO = "038442"
EXCECOES = {
    0x01: "função inválida",
    0x02: "endereço inválido / registrador somente leitura",
    0x03: "valor inválido",
}


class ErroModbus(Exception):
    """Falha de comunicação (timeout, CRC) após esgotar as tentativas."""


class ExcecaoModbus(ErroModbus):
    """Resposta de exceção do dispositivo (não retentável)."""

    def __init__(self, endereco: int, codigo: int) -> None:
        self.codigo = codigo
        super().__init__(f"exceção 0x{codigo:02X} em 0x{endereco:02X}: {EXCECOES.get(codigo, 'desconhecida')}")


def matricula_em_bytes(matricula: str | list[int] | bytes) -> bytes:
    """Valida 6 dígitos 0..9 e devolve os bytes crus (nunca ASCII)."""
    if isinstance(matricula, str):
        if not matricula.isdigit():
            raise ValueError("matrícula deve conter apenas dígitos")
        digitos = [int(c) for c in matricula]
    else:
        digitos = list(matricula)
    if len(digitos) != 6 or any(not isinstance(d, int) or not 0 <= d <= 9 for d in digitos):
        raise ValueError("matrícula deve ter exatamente 6 dígitos (0..9)")
    return bytes(digitos)


class ClienteModbus:
    """Primitivas de leitura/escrita sobre um transporte UART."""

    def __init__(
        self,
        uart: UartBase,
        matricula: str | list[int] | bytes = MATRICULA_PADRAO,
        tentativas: int = 3,
        intervalo_retry_s: float = 0.03,
    ) -> None:
        self.uart = uart
        self.matricula = matricula_em_bytes(matricula)
        self.tentativas = max(1, tentativas)
        self.intervalo_retry_s = intervalo_retry_s

    def ler_registradores(self, endereco: int, inicio: int, quantidade: int) -> list[int]:
        if quantidade <= 0:
            raise ValueError("quantidade deve ser positiva")
        quadro = anexar_crc16(bytes((endereco, 0x03)) + struct.pack("<HH", inicio, quantidade) + self.matricula)
        resposta = self._transacionar(endereco, 0x03, quadro, 5 + 2 * quantidade)
        if resposta[2] != 2 * quantidade:
            raise ErroModbus(f"byte_count inesperado: {resposta[2]}")
        return list(struct.unpack(f">{quantidade}H", resposta[3 : 3 + 2 * quantidade]))

    def escrever_registradores(self, endereco: int, inicio: int, valores: list[int]) -> None:
        if not valores:
            raise ValueError("lista de valores vazia")
        qtd = len(valores)
        corpo = bytes((endereco, 0x10)) + struct.pack("<HHB", inicio, qtd, 2 * qtd)
        corpo += b"".join(struct.pack("<H", v & 0xFFFF) for v in valores)
        quadro = anexar_crc16(corpo + self.matricula)
        resposta = self._transacionar(endereco, 0x10, quadro, 8)
        eco_reg, eco_qtd = struct.unpack(">HH", resposta[2:6])
        if (eco_reg, eco_qtd) != (inicio, qtd):
            raise ErroModbus(f"eco divergente: reg={eco_reg} qtd={eco_qtd}")

    def _transacionar(self, endereco: int, funcao: int, quadro: bytes, tamanho: int) -> bytes:
        ultimo_erro = "sem resposta"
        for tentativa in range(1, self.tentativas + 1):
            resposta = self.uart.transacionar(quadro, tamanho)
            erro = self._validar(endereco, funcao, resposta, tamanho)
            if erro is None:
                return resposta
            ultimo_erro = erro
            log.debug("0x%02X f=%02X tentativa %d: %s", endereco, funcao, tentativa, erro)
            if tentativa < self.tentativas:
                time.sleep(self.intervalo_retry_s)
        raise ErroModbus(
            f"falha em 0x{endereco:02X} função 0x{funcao:02X} após {self.tentativas} tentativas: {ultimo_erro}"
        )

    @staticmethod
    def _validar(endereco: int, funcao: int, resposta: bytes, tamanho: int) -> str | None:
        """Devolve a descrição do erro retentável, None se válida; lança ExcecaoModbus."""
        if len(resposta) < 5:
            return f"timeout ou resposta curta ({len(resposta)} B)"
        if not verificar_crc16(resposta):
            return "CRC inválido"
        if resposta[0] != endereco:
            return f"endereço divergente 0x{resposta[0]:02X}"
        if resposta[1] == funcao | 0x80:
            raise ExcecaoModbus(endereco, resposta[2])
        if resposta[1] != funcao or len(resposta) != tamanho:
            return f"quadro malformado ({len(resposta)} B, função 0x{resposta[1]:02X})"
        return None
