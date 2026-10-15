"""Serialização do acesso à UART: uma thread dona da serial e fila com prioridades.

Vários componentes do Central (polling das cabines, despacho, BMP280, comandos de porta)
precisam falar MODBUS. Como o barramento é half-duplex mestre/escravo, todas as transações
passam por uma única thread consumidora de `PriorityQueue`; comandos urgentes (porta)
ultrapassam leituras periódicas.
"""

import itertools
import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import TypeVar

from .simulador import ApiSimulador

log = logging.getLogger(__name__)

T = TypeVar("T")

PRIO_URGENTE = 0  # comandos de porta
PRIO_CONTROLE = 1  # fila de chamadas, atribuições, barramento
PRIO_CABINES = 2  # polling de estado das cabines
PRIO_AMBIENTE = 3  # condição de contorno (período de segundos)

_ENCERRAR = (99, 0, None, None)


class GerenteModbus:
    def __init__(self, api: ApiSimulador) -> None:
        self.api = api
        self._fila: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._thread = threading.Thread(target=self._executar, name="modbus", daemon=True)
        self._ativo = False

    def iniciar(self) -> None:
        self._ativo = True
        self._thread.start()

    def parar(self) -> None:
        self._ativo = False
        self._fila.put((_ENCERRAR[0], next(self._seq), None, None))
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def submeter(self, funcao: Callable[[ApiSimulador], T], prioridade: int = PRIO_CABINES) -> "Future[T]":
        futuro: Future = Future()
        if not self._ativo:
            futuro.set_exception(RuntimeError("gerente MODBUS parado"))
            return futuro
        self._fila.put((prioridade, next(self._seq), funcao, futuro))
        return futuro

    def executar(self, funcao: Callable[[ApiSimulador], T], prioridade: int = PRIO_CONTROLE, timeout: float = 5.0) -> T:
        """Submete e aguarda o resultado (propaga exceções do MODBUS)."""
        return self.submeter(funcao, prioridade).result(timeout=timeout)

    def _executar(self) -> None:
        while True:
            _prio, _seq, funcao, futuro = self._fila.get()
            if funcao is None:
                break
            if not futuro.set_running_or_notify_cancel():
                continue
            try:
                futuro.set_result(funcao(self.api))
            except Exception as erro:  # noqa: BLE001 - devolvida ao chamador via Future
                futuro.set_exception(erro)
        # Descarta pendências para não deixar chamadores bloqueados.
        while True:
            try:
                _, _, funcao, futuro = self._fila.get_nowait()
            except queue.Empty:
                break
            if futuro is not None:
                futuro.set_exception(RuntimeError("gerente MODBUS encerrado"))
