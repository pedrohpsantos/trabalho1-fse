"""Enlace TCP da cabine com o Servidor Central: conexão resiliente com reconexão automática."""

import logging
import queue
import socket
import threading

from ..protocolo.canal import CanalFechado, CanalJson
from .controlador import CabineRuntime

log = logging.getLogger(__name__)

ESPERA_MIN_S = 0.25
ESPERA_MAX_S = 2.0
TIMEOUT_SILENCIO_S = 5.0  # o Central renova a Cota a cada 1 s: silêncio maior => link morto


class RedeCentral:
    """Implementa o `Enlace` do runtime. O envio é assíncrono (fila + thread escritora), de
    modo que um Central lento nunca atrasa a malha de controle de 50 ms."""

    def __init__(self, host: str, porta: int) -> None:
        self.host, self.porta = host, porta
        self.runtime: CabineRuntime | None = None
        self._canal: CanalJson | None = None
        self._tx: queue.Queue = queue.Queue(maxsize=256)
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._executar, name="rede-central", daemon=True)

    @property
    def conectado(self) -> bool:
        canal = self._canal
        return canal is not None and not canal.fechado

    def iniciar(self) -> None:
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        canal = self._canal
        if canal:
            canal.fechar()
        self._thread.join(timeout=2.0)

    def enviar(self, tipo: str, /, **campos) -> bool:
        if not self.conectado:
            return False
        try:
            self._tx.put_nowait((tipo, campos))
        except queue.Full:
            return False
        return True

    # ------------------------------------------------------------------
    def _executar(self) -> None:
        espera = ESPERA_MIN_S
        while not self._parar.is_set():
            try:
                sock = socket.create_connection((self.host, self.porta), timeout=2.0)
            except OSError:
                self._parar.wait(espera)
                espera = min(espera * 2, ESPERA_MAX_S)
                continue
            espera = ESPERA_MIN_S
            canal = CanalJson(sock, f"central@{self.host}:{self.porta}")
            try:
                self._sessao(canal)
            except CanalFechado:
                log.warning("conexão com o Central perdida")
            except TimeoutError:
                log.warning("Central em silêncio; reconectando")
            finally:
                canal.fechar()
                self._canal = None
                self.runtime.conexao_perdida()

    def _sessao(self, canal: CanalJson) -> None:
        runtime = self.runtime
        while not self._tx.empty():  # descarta mensagens de uma sessão anterior
            self._tx.get_nowait()
        canal.enviar("hello", **runtime.mensagem_hello())
        escritora = threading.Thread(target=self._escrever, args=(canal,), name="rede-tx", daemon=True)
        self._canal = canal
        escritora.start()
        log.info("conectado ao Central")
        while not self._parar.is_set():
            runtime.receber(canal.receber(TIMEOUT_SILENCIO_S))
        canal.fechar()

    def _escrever(self, canal: CanalJson) -> None:
        while not canal.fechado and not self._parar.is_set():
            try:
                tipo, campos = self._tx.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                canal.enviar(tipo, **campos)
            except CanalFechado:
                return
