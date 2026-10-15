"""Fontes de tempo injetáveis, para permitir testes determinísticos em tempo virtual."""

import time
from collections.abc import Callable

Relogio = Callable[[], float]


def relogio_real() -> float:
    """Tempo de parede (epoch, segundos). Os quatro processos rodam na mesma Raspberry Pi."""
    return time.time()


class RelogioVirtual:
    """Relógio manual: só avança quando `avancar` é chamado."""

    def __init__(self, inicio: float = 1_000_000.0) -> None:
        self.t = inicio

    def __call__(self) -> float:
        return self.t

    def avancar(self, dt: float) -> None:
        self.t += dt
