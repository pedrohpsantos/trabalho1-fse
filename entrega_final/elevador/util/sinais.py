"""Tratamento de SIGINT/SIGTERM com parada segura."""

import signal
import threading
from collections.abc import Callable


class EncerramentoSeguro:
    """Converte sinais de término em um `threading.Event` aguardável (sem busy-wait)."""

    def __init__(self) -> None:
        self.evento = threading.Event()
        self._callbacks: list[Callable[[], None]] = []

    def instalar(self) -> None:
        """Instala os handlers; deve ser chamado da thread principal."""
        for nome in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sinal = getattr(signal, nome, None)
            if sinal is not None:
                signal.signal(sinal, self._tratar)

    def ao_encerrar(self, callback: Callable[[], None]) -> None:
        self._callbacks.append(callback)

    def _tratar(self, _signum: int, _frame: object) -> None:
        self.solicitar()

    def solicitar(self) -> None:
        if not self.evento.is_set():
            self.evento.set()
            for cb in self._callbacks:
                cb()

    def aguardar(self) -> None:
        # wait() com timeout mantém a thread principal responsiva a sinais no Windows.
        while not self.evento.wait(timeout=0.5):
            pass
