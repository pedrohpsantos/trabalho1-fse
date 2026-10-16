"""Física de uma cabine na bancada virtual.

Modelo (deliberadamente simples e documentado no README):
  * velocidade-alvo = ganho * duty * sentido, com lag de 1ª ordem (`tau_motor_s`);
  * atrito estático: parado, duty < 10% não move; em movimento, qualquer duty move;
  * freio (DIR1=DIR2=1) para quase de imediato; "livre" desacelera lentamente;
  * posição verdadeira em mm; o contador do encoder = posição relativa à partida + deriva
    (acumulada a cada inversão de sentido e por distância percorrida);
  * bandeirolas centradas nos andares reais, com semilargura diferente em cada andar;
  * corrente como no firmware medido: 300 mA de repouso + 57 mA por ponto de duty;
  * falhas como no firmware: 0x01 porta comandada fora do nível (não abre), 0x02 barramento
    em sobrecarga (marcado pela bancada), 0x04 fim de curso.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from ..constantes import (
    NUM_ANDARES,
    POCO_MM,
    SOBRECURSO_MM,
    TOLERANCIA_NIVELAMENTO_MM,
    VAO_NOMINAL_MM,
    Direcao,
    PortaComando,
    PortaEstado,
    corrente_estimada_ma,
)

FALHA_PORTA_FORA_NIVEL = 0x01
FALHA_SOBRECARGA = 0x02
FALHA_FIM_DE_CURSO = 0x04
SEQUENCIA_QUADRATURA = ((0, 0), (0, 1), (1, 1), (1, 0))  # sentido "subir" (contagem crescente)


@dataclass
class ParametrosPlanta:
    ganho_mm_s_por_pct: float = 8.0
    tau_motor_s: float = 0.12
    tau_freio_s: float = 0.03
    tau_livre_s: float = 1.2
    atrito_pct: float = 10.0
    latencia_irq_mm: float = 1.0
    deriva_inversao_mm: float = 3.0
    deriva_mm_por_metro: float = 0.3
    tempo_porta_s: float = 1.5


@dataclass
class Geometria:
    andares_mm: list[float]
    semilargura_mm: list[float]


def gerar_geometria(cabine: int, semente: int = 1) -> Geometria:
    """Andares fora de múltiplos exatos de 3000 mm e bandeirolas de larguras distintas."""
    rng = random.Random(semente * 1000 + cabine)
    andares = [i * VAO_NOMINAL_MM + rng.uniform(-60.0, 60.0) for i in range(NUM_ANDARES)]
    semi = [rng.uniform(50.0, 140.0) for _ in range(NUM_ANDARES)]
    return Geometria(andares, semi)


@dataclass
class CabineFisica:
    numero: int
    geometria: Geometria
    params: ParametrosPlanta = field(default_factory=ParametrosPlanta)
    posicao_inicial_mm: float | None = None
    semente: int = 1

    def __post_init__(self) -> None:
        g = self.geometria
        self.x = g.andares_mm[0] + 20.0 if self.posicao_inicial_mm is None else self.posicao_inicial_mm
        self.x0 = self.x
        self.v = 0.0
        self.dir = Direcao.FREIO
        self.duty = 0.0
        self.deriva = 0.0
        self._contagem = 0
        self._quad = 0
        self._sinal_anterior = 0
        self.sensor = self._dentro_bandeirola(self.x)
        self.cortina = False
        self.porta = PortaEstado.FECHADA
        self._timer_porta = 0.0
        self.falha = 0
        self.carga_kg = 0
        self.passageiros = 0
        self.violacoes_porta_fora_nivel = 0
        self.violacoes_movimento_porta_aberta = 0
        self.corrente_ma = 0
        self.max_corrente_ma = 0
        self._rng = random.Random(self.semente * 31 + self.numero)
        self.ao_quadratura: Callable[[int, int], None] | None = None
        self.ao_sensor: Callable[[bool, int], None] | None = None
        self.ao_cortina: Callable[[bool], None] | None = None

    # --- grandezas observáveis -------------------------------------------------
    @property
    def contagem(self) -> int:
        return self._contagem

    @property
    def xmin(self) -> float:
        return self.geometria.andares_mm[0] - POCO_MM

    @property
    def xmax(self) -> float:
        return self.geometria.andares_mm[-1] + SOBRECURSO_MM

    def _dentro_bandeirola(self, x: float) -> bool:
        g = self.geometria
        return any(abs(x - f) <= w for f, w in zip(g.andares_mm, g.semilargura_mm, strict=True))

    @property
    def andar_proximo(self) -> int:
        return min(range(NUM_ANDARES), key=lambda i: abs(self.x - self.geometria.andares_mm[i]))

    @property
    def nivelado(self) -> bool:
        return abs(self.x - self.geometria.andares_mm[self.andar_proximo]) <= TOLERANCIA_NIVELAMENTO_MM

    @property
    def posicao_absoluta_mm(self) -> int:
        return int(round(self.x - self.geometria.andares_mm[0]))

    # --- entradas (atuadores) ----------------------------------------------------
    def definir_motor(self, direcao: Direcao, duty: float) -> None:
        self.dir = direcao
        self.duty = max(0.0, min(100.0, duty))

    def definir_cortina(self, obstruida: bool) -> None:
        if obstruida != self.cortina:
            self.cortina = obstruida
            if self.ao_cortina:
                self.ao_cortina(obstruida)

    def comandar_porta(self, comando: int) -> None:
        if comando == PortaComando.ABRIR:
            if self.porta in (PortaEstado.FECHADA, PortaEstado.FECHANDO, PortaEstado.OBSTRUIDA):
                if not self.nivelado or abs(self.v) > 1.0:
                    self.violacoes_porta_fora_nivel += 1
                    self.falha |= FALHA_PORTA_FORA_NIVEL
                    return  # como o firmware: registra a falha e não abre
                self.porta = PortaEstado.ABRINDO
                self._timer_porta = self.params.tempo_porta_s
        elif comando == PortaComando.FECHAR and self.porta in (PortaEstado.ABERTA, PortaEstado.ABRINDO):
            self.porta = PortaEstado.FECHANDO
            self._timer_porta = self.params.tempo_porta_s

    # --- dinâmica -------------------------------------------------------------------
    def avancar(self, dt: float) -> None:
        self._avancar_porta(dt)
        p = self.params
        movendo = abs(self.v) >= 0.5
        if self.dir in (Direcao.SUBIR, Direcao.DESCER):
            sinal = 1.0 if self.dir == Direcao.SUBIR else -1.0
            alvo_v = sinal * p.ganho_mm_s_por_pct * self.duty
            if not movendo and self.duty < p.atrito_pct:
                alvo_v = 0.0
            tau = p.tau_motor_s
            self.corrente_ma = corrente_estimada_ma(self.duty)
        elif self.dir == Direcao.FREIO:
            alvo_v, tau = 0.0, p.tau_freio_s
            self.corrente_ma = corrente_estimada_ma(0.0, acionado=False)
        else:
            alvo_v, tau = 0.0, p.tau_livre_s
            self.corrente_ma = corrente_estimada_ma(0.0, acionado=False)
        self.max_corrente_ma = max(self.max_corrente_ma, self.corrente_ma)
        self.v += (alvo_v - self.v) * min(1.0, dt / tau)
        if abs(self.v) < 0.3 and alvo_v == 0.0:
            self.v = 0.0
        x_ant = self.x
        x_novo = min(self.xmax, max(self.xmin, self.x + self.v * dt))
        if x_novo in (self.xmin, self.xmax) and abs(self.v) > 0:
            self.falha |= FALHA_FIM_DE_CURSO
            self.v = 0.0
        if self.v != 0.0 and self.porta != PortaEstado.FECHADA:
            self.violacoes_movimento_porta_aberta += 1
        self._atualizar_deriva(x_ant, x_novo)
        self._emitir_bandeirolas(x_ant, x_novo)
        self.x = x_novo
        self._atualizar_contagem()

    def _avancar_porta(self, dt: float) -> None:
        if self.porta == PortaEstado.ABRINDO:
            self._timer_porta -= dt
            if self._timer_porta <= 0:
                self.porta = PortaEstado.ABERTA
        elif self.porta == PortaEstado.FECHANDO:
            if self.cortina:
                self.porta = PortaEstado.OBSTRUIDA
                return
            self._timer_porta -= dt
            if self._timer_porta <= 0:
                self.porta = PortaEstado.FECHADA

    def _atualizar_deriva(self, x_ant: float, x_novo: float) -> None:
        delta = x_novo - x_ant
        if delta == 0.0:
            return
        sinal = 1 if delta > 0 else -1
        if self._sinal_anterior and sinal != self._sinal_anterior:
            amp = self.params.deriva_inversao_mm
            self.deriva += self._rng.uniform(-amp, amp)
        self._sinal_anterior = sinal
        self.deriva += sinal * abs(delta) / 1000.0 * self.params.deriva_mm_por_metro

    def _contagem_em(self, x: float) -> int:
        return int(round(x - self.x0 + self.deriva))

    def _emitir_bandeirolas(self, x_ant: float, x_novo: float) -> None:
        if x_novo == x_ant:
            return
        g = self.geometria
        sinal = 1.0 if x_novo > x_ant else -1.0
        cruzamentos = []
        for f, w in zip(g.andares_mm, g.semilargura_mm, strict=True):
            for borda, entrando_subindo in ((f - w, True), (f + w, False)):
                if min(x_ant, x_novo) < borda <= max(x_ant, x_novo):
                    entrada = entrando_subindo if sinal > 0 else not entrando_subindo
                    cruzamentos.append((borda, entrada))
        for borda, entrada in sorted(cruzamentos, key=lambda c: c[0] * sinal):
            self.sensor = entrada
            if self.ao_sensor:
                lat = self.params.latencia_irq_mm * sinal
                self.ao_sensor(entrada, int(round(borda - self.x0 + self.deriva + lat)))

    def _atualizar_contagem(self) -> None:
        alvo = self._contagem_em(self.x)
        while self._contagem != alvo:
            passo = 1 if alvo > self._contagem else -1
            self._contagem += passo
            self._quad = (self._quad + passo) % 4
            if self.ao_quadratura:
                a, b = SEQUENCIA_QUADRATURA[self._quad]
                self.ao_quadratura(a, b)

    # --- registradores MODBUS -------------------------------------------------------
    def registradores(self, posicao_reg_mm: int) -> list[int]:
        return [
            self.andar_proximo,
            int(self.nivelado and abs(self.v) < 1.0),
            int(self.porta),
            0,
            self.corrente_ma,
            self.carga_kg,
            self.passageiros,
            posicao_reg_mm & 0xFFFF,
            self.falha,
        ]
