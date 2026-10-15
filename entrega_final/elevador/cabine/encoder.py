"""Decodificador de encoder em quadratura 4x (acionado por interrupção em A e B)."""

import threading

# Índice: (estado_anterior << 2) | estado_atual, com estado = (A << 1) | B.
TABELA_QUADRATURA = (0, 1, -1, 0, -1, 0, 0, 1, 1, 0, 0, -1, 0, -1, 1, 0)

_INT32_MIN = -(2**31)
_INT32_SPAN = 2**32


def envolver_int32(valor: int) -> int:
    """Mantém o contador em inteiro de 32 bits com sinal."""
    return ((valor + 2**31) % _INT32_SPAN) + _INT32_MIN


class DecodificadorQuadratura:
    """Contador relativo, com sinal, de 32 bits; seguro para chamadas de várias threads."""

    def __init__(self, a: int = 0, b: int = 0) -> None:
        self._trava = threading.Lock()
        self._estado = ((a & 1) << 1) | (b & 1)
        self._contagem = 0

    def atualizar(self, a: int, b: int) -> int:
        """Processa uma transição (chamada pelas ISRs de A e B); devolve a contagem."""
        atual = ((a & 1) << 1) | (b & 1)
        with self._trava:
            delta = TABELA_QUADRATURA[(self._estado << 2) | atual]
            self._estado = atual
            if delta:
                self._contagem = envolver_int32(self._contagem + delta)
            return self._contagem

    @property
    def contagem(self) -> int:
        with self._trava:
            return self._contagem

    def definir(self, a: int, b: int, contagem: int) -> None:
        """Sincroniza canais e contagem (usado ao conectar à bancada virtual)."""
        with self._trava:
            self._estado = ((a & 1) << 1) | (b & 1)
            self._contagem = envolver_int32(contagem)

    def deslocar(self, delta: int) -> None:
        """Soma `delta` atomicamente (reancoragem sem perder pulsos concorrentes)."""
        with self._trava:
            self._contagem = envolver_int32(self._contagem + int(delta))
