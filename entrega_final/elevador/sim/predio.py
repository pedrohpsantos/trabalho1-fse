"""Controlador do prédio na bancada virtual: fila de chamadas, cenários de tráfego, watchdog."""

import random
from collections import deque
from dataclasses import dataclass

from ..constantes import (
    NUM_ANDARES,
    WATCHDOG_AMBIENTE_S,
    Cenario,
    barramento_com_derating,
)

DURACAO_CENARIO_S = 300.0


@dataclass
class ChamadaSim:
    id: int
    origem: int
    destino: int
    t_registro: float
    cabine: int = 0
    t_embarque: float | None = None
    t_desembarque: float | None = None


def gerar_chamadas_cenario(
    cenario: Cenario, semente: int, duracao_s: float = DURACAO_CENARIO_S, intervalo_medio_s: float = 8.0
) -> list[tuple[float, int, int]]:
    """Sequência determinística (instante, origem, destino) de um cenário (Seção 7.6)."""
    rng = random.Random(semente * 17 + int(cenario))
    chamadas: list[tuple[float, int, int]] = []
    t = rng.expovariate(1.0 / intervalo_medio_s)
    while t < duracao_s:
        if cenario == Cenario.UP_PEAK and rng.random() < 0.8:
            origem, destino = 0, rng.randint(1, NUM_ANDARES - 1)
        elif cenario == Cenario.DOWN_PEAK and rng.random() < 0.8:
            origem, destino = rng.randint(1, NUM_ANDARES - 1), 0
        else:
            origem = rng.randrange(NUM_ANDARES)
            destino = rng.choice([a for a in range(NUM_ANDARES) if a != origem])
        chamadas.append((t, origem, destino))
        t += rng.expovariate(1.0 / intervalo_medio_s)
    return chamadas


class PredioSimulado:
    def __init__(self) -> None:
        self.fila: deque[ChamadaSim] = deque()
        self.chamadas: dict[int, ChamadaSim] = {}
        self._proximo_id = 1
        self.temp_x10 = 250
        self.pressao_hpa = 1013
        self._ultima_escrita_ambiente: float | None = None
        self._ultima_atribuicao: tuple[int, int] | None = None
        self._atrib_id = 0
        self.cenario = Cenario.NENHUM
        self._agenda_cenario: deque[tuple[float, int, int]] = deque()
        self._inicio_cenario = 0.0
        self.geradas = 0
        self.atendidas = 0
        self._soma_espera = 0.0
        self._soma_viagem = 0.0
        self.chamadas_perdidas = 0

    # --- chamadas ---------------------------------------------------------------
    def registrar_chamada(self, origem: int, destino: int, t: float) -> ChamadaSim:
        chamada = ChamadaSim(self._proximo_id, origem, destino, t)
        self._proximo_id += 1
        self.chamadas[chamada.id] = chamada
        self.fila.append(chamada)
        return chamada

    def iniciar_cenario(self, cenario: Cenario, t: float, semente: int = 1, intervalo_medio_s: float = 8.0) -> None:
        self.cenario = cenario
        self._inicio_cenario = t
        self._agenda_cenario = deque(gerar_chamadas_cenario(cenario, semente, DURACAO_CENARIO_S, intervalo_medio_s))
        self.geradas = self.atendidas = 0
        self._soma_espera = self._soma_viagem = 0.0

    def atualizar(self, t: float) -> None:
        while self._agenda_cenario and self._agenda_cenario[0][0] <= t - self._inicio_cenario:
            _, origem, destino = self._agenda_cenario.popleft()
            self.registrar_chamada(origem, destino, t)
            self.geradas += 1
        if self.cenario != Cenario.NENHUM and not self._agenda_cenario and self.atendidas >= self.geradas:
            self.cenario = Cenario.NENHUM

    def porta_aberta(self, cabine: int, andar: int, t: float) -> None:
        """Chamado quando a porta de `cabine` abre nivelada em `andar`."""
        for c in self.chamadas.values():
            if c.cabine != cabine:
                continue
            if c.t_embarque is None and c.origem == andar:
                c.t_embarque = t
                self._soma_espera += t - c.t_registro
            elif c.t_embarque is not None and c.t_desembarque is None and c.destino == andar:
                c.t_desembarque = t
                self._soma_viagem += t - c.t_embarque
                self.atendidas += 1

    # --- escrita de registradores --------------------------------------------------
    def escrever(self, registrador: int, valor: int, t: float) -> bool:
        """Aplica uma escrita; devolve False para valor inválido."""
        if registrador == 4:
            if valor == 1 and self.fila:
                chamada = self.fila.popleft()
                if self._ultima_atribuicao and self._ultima_atribuicao[0] == chamada.id:
                    chamada.cabine = self._ultima_atribuicao[1]
                else:
                    self.chamadas_perdidas += 1
            return True
        if registrador == 5:
            self.temp_x10 = valor if valor < 0x8000 else valor - 0x10000
            self._ultima_escrita_ambiente = t
            return True
        if registrador == 6:
            self.pressao_hpa = valor
            self._ultima_escrita_ambiente = t
            return True
        if registrador == 9:
            self._atrib_id = valor
            return True
        if registrador == 10:
            if valor not in (1, 2, 3):
                return False
            self._ultima_atribuicao = (self._atrib_id, valor)
            return True
        return False

    # --- leitura ---------------------------------------------------------------------
    def watchdog_expirado(self, t: float) -> bool:
        referencia = self._ultima_escrita_ambiente if self._ultima_escrita_ambiente is not None else 0.0
        return t - referencia > WATCHDOG_AMBIENTE_S

    def barramento_max_ma(self, t: float) -> int:
        return barramento_com_derating(self.temp_x10 / 10.0, self.watchdog_expirado(t))

    def registradores(self, t: float) -> list[int]:
        cab = self.fila[0] if self.fila else None
        n_emb = sum(1 for c in self.chamadas.values() if c.t_embarque is not None)
        espera = self._soma_espera / n_emb if n_emb else 0.0
        viagem = self._soma_viagem / self.atendidas if self.atendidas else 0.0
        return [
            len(self.fila),
            cab.origem if cab else 0,
            cab.destino if cab else 0,
            cab.id if cab else 0,
            0,
            self.temp_x10 & 0xFFFF,
            self.pressao_hpa,
            self.barramento_max_ma(t),
            int(self.watchdog_expirado(t)),
            0,
            0,
            int(self.cenario),
            self.geradas,
            self.atendidas,
            int(espera * 10),
            int(viagem * 10),
        ]
