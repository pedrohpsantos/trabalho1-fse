"""Transportes UART virtuais: em processo (testes) e via TCP (processos separados)."""

import socket
import struct
import threading

from ..modbus.uart import UartBase
from .bancada import Bancada
from .modbus_sim import processar_quadro


class UartVirtual(UartBase):
    """Liga o cliente MODBUS direto à bancada, na mesma thread/processo."""

    def __init__(self, bancada: Bancada, falhas: "InjetorFalhas | None" = None) -> None:
        self.bancada = bancada
        self.falhas = falhas

    def abrir(self) -> None:
        return None

    def fechar(self) -> None:
        return None

    def transacionar(self, quadro: bytes, tamanho_esperado: int) -> bytes:
        resposta = processar_quadro(self.bancada, quadro)
        if self.falhas:
            return self.falhas.aplicar(resposta)
        return resposta


class InjetorFalhas:
    """Corrompe ou descarta respostas, para testar retentativas (CRC/timeout)."""

    def __init__(self) -> None:
        self.descartar = 0
        self.corromper = 0

    def aplicar(self, resposta: bytes) -> bytes:
        if self.descartar > 0:
            self.descartar -= 1
            return b""
        if self.corromper > 0 and resposta:
            self.corromper -= 1
            return resposta[:-1] + bytes((resposta[-1] ^ 0xFF,))
        return resposta


class UartViaTcp(UartBase):
    """Cliente do servidor da bancada: quadros RTU com prefixo de 2 bytes de tamanho."""

    def __init__(self, host: str, porta: int, timeout_s: float = 0.5) -> None:
        self.host, self.porta, self.timeout_s = host, porta, timeout_s
        self._sock: socket.socket | None = None
        self._trava = threading.Lock()

    def abrir(self) -> None:
        if self._sock is None:
            sock = socket.create_connection((self.host, self.porta), timeout=2.0)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.sendall(b'{"canal":"uart"}\n')
            self._sock = sock

    def fechar(self) -> None:
        with self._trava:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None

    def transacionar(self, quadro: bytes, tamanho_esperado: int) -> bytes:
        with self._trava:
            try:
                self.abrir()
                self._sock.settimeout(self.timeout_s)
                self._sock.sendall(struct.pack(">H", len(quadro)) + quadro)
                cabecalho = self._receber_exato(2)
                (tamanho,) = struct.unpack(">H", cabecalho)
                return self._receber_exato(tamanho) if tamanho else b""
            except (OSError, ConnectionError):
                # Sem resposta == timeout MODBUS; reabre na próxima tentativa.
                if self._sock is not None:
                    self._sock.close()
                    self._sock = None
                return b""

    def _receber_exato(self, n: int) -> bytes:
        dados = b""
        while len(dados) < n:
            pedaco = self._sock.recv(n - len(dados))
            if not pedaco:
                raise ConnectionError("conexão encerrada")
            dados += pedaco
        return dados
