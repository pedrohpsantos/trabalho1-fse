"""Canal TCP orientado a linhas, com envio thread-safe e limite de tamanho de linha."""

import logging
import socket
import threading
from typing import Any

from .mensagens import MAX_LINHA, MensagemInvalida, codificar, decodificar

log = logging.getLogger(__name__)

TIMEOUT_ENVIO_S = 3.0


class CanalFechado(Exception):
    """A conexão foi encerrada (pelo par ou por erro de rede)."""


class LeitorLinhas:
    """Lê linhas de um socket com timeout, mantendo o estado do buffer entre chamadas.

    (`socket.makefile` não serve: após um timeout o leitor fica inutilizável.)
    """

    def __init__(self, sock: socket.socket, limite: int = MAX_LINHA) -> None:
        self._sock = sock
        self._limite = limite
        self._buf = bytearray()

    def ler(self, timeout_s: float | None) -> bytes:
        """Próxima linha sem o '\\n'. Lança TimeoutError, ConnectionError (EOF) ou OverflowError."""
        while True:
            fim = self._buf.find(b"\n")
            if fim >= 0:
                linha = bytes(self._buf[:fim])
                del self._buf[: fim + 1]
                return linha
            if len(self._buf) > self._limite:
                raise OverflowError("linha excede o limite")
            self._sock.settimeout(timeout_s)
            pedaco = self._sock.recv(4096)
            if not pedaco:
                raise ConnectionError("conexão encerrada pelo par")
            self._buf += pedaco


class CanalJson:
    def __init__(self, sock: socket.socket, nome: str = "") -> None:
        self.nome = nome
        self._sock = sock
        try:
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:  # opções opcionais; nem toda plataforma as aceita
            log.debug("não foi possível ajustar opções do socket", exc_info=True)
        self._leitor = LeitorLinhas(sock)
        self._trava_envio = threading.Lock()
        self._fechado = threading.Event()

    @property
    def fechado(self) -> bool:
        return self._fechado.is_set()

    def enviar(self, tipo: str, /, **campos: Any) -> None:
        dados = codificar(tipo, **campos)
        with self._trava_envio:
            if self.fechado:
                raise CanalFechado(self.nome)
            try:
                self._sock.settimeout(TIMEOUT_ENVIO_S)
                self._sock.sendall(dados)
            except OSError as erro:
                self.fechar()
                raise CanalFechado(self.nome) from erro

    def receber(self, timeout_s: float | None) -> dict[str, Any]:
        """Bloqueia até chegar uma mensagem válida. Lança TimeoutError ou CanalFechado.

        Mensagens malformadas são descartadas com aviso (o par não derruba o serviço).
        """
        while True:
            try:
                linha = self._leitor.ler(timeout_s)
            except TimeoutError as erro:
                raise TimeoutError(self.nome) from erro
            except (OSError, OverflowError) as erro:
                self.fechar()
                raise CanalFechado(self.nome) from erro
            try:
                return decodificar(linha.rstrip(b"\r"))
            except MensagemInvalida as erro:
                log.warning("[%s] mensagem descartada: %s", self.nome, erro)

    def fechar(self) -> None:
        if self._fechado.is_set():
            return
        self._fechado.set()
        for acao in (lambda: self._sock.shutdown(socket.SHUT_RDWR), self._sock.close):
            try:
                acao()
            except OSError:
                pass
