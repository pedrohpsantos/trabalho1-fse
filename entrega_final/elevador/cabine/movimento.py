"""Controlador de posição: PID + rampa de aceleração + saturação por Cota + parada por freio.

Máquina de estados: PARADO -> MOVENDO -> FREANDO -> CHEGOU. A saída é sempre o par
(direção da ponte H, duty %). A posição de controle vem exclusivamente do encoder.
"""

from dataclasses import dataclass
from enum import Enum

from ..constantes import DUTY_ATRITO_ESTATICO, TOLERANCIA_NIVELAMENTO_MM, Direcao
from .pid import Pid


class FaseMovimento(Enum):
    PARADO = "parado"
    MOVENDO = "movendo"
    FREANDO = "freando"
    CHEGOU = "chegou"


@dataclass(frozen=True)
class Comando:
    direcao: Direcao
    duty: float


FREIO = Comando(Direcao.FREIO, 0.0)


class ControladorPosicao:
    FAIXA_PARADA_MM = 4.0  # erro máximo para acionar o freio
    VELOCIDADE_PARADA_MM_S = 120.0
    DUTY_ARRANQUE = DUTY_ATRITO_ESTATICO + 2.0  # primeiro duty da rampa: vence o atrito estático
    TICKS_PARADO = 2

    def __init__(self, pid: Pid, rampa_pct_por_s: float = 25.0) -> None:
        self.pid = pid
        self.rampa = rampa_pct_por_s
        self.fase = FaseMovimento.PARADO
        self.alvo = 0.0
        self.erro = 0.0
        self.duty_saida = 0.0
        self._pos_anterior: float | None = None
        self._limite_rampa = 0.0
        self._ticks_parado = 0
        self._ticks_imovel = 0
        self._saturado = 0

    def ir_para(self, alvo: float) -> None:
        self.alvo = float(alvo)
        self.fase = FaseMovimento.MOVENDO
        self.pid.reiniciar()
        self._limite_rampa = 0.0
        self._saturado = 0
        self._ticks_parado = 0
        self._ticks_imovel = 0

    def retargetar(self, alvo: float) -> None:
        """Muda o alvo em voo (mesmo sentido), preservando a rampa e a velocidade atual."""
        self.alvo = float(alvo)

    def cancelar(self) -> None:
        self.fase = FaseMovimento.PARADO
        self.pid.reiniciar()

    def reancorar(self, delta: float) -> None:
        """A posição medida foi deslocada de `delta`: mantém o PID coerente."""
        if self._pos_anterior is not None:
            self._pos_anterior += delta
        self.pid.deslocar_erro(-delta)

    @property
    def chegou(self) -> bool:
        return self.fase == FaseMovimento.CHEGOU

    def passo(self, pos: float, duty_max: float, dt: float) -> Comando:
        velocidade = 0.0 if self._pos_anterior is None else (pos - self._pos_anterior) / dt
        parado = self._pos_anterior is not None and pos == self._pos_anterior
        self._pos_anterior = pos
        self.erro = self.alvo - pos
        if self.fase in (FaseMovimento.PARADO, FaseMovimento.CHEGOU):
            return self._emitir(FREIO)
        if self.fase == FaseMovimento.FREANDO:
            return self._passo_freando(parado)
        return self._passo_movendo(velocidade, parado, duty_max, dt)

    def _passo_freando(self, parado: bool) -> Comando:
        self._ticks_parado = self._ticks_parado + 1 if parado else 0
        if self._ticks_parado >= self.TICKS_PARADO:
            if abs(self.erro) <= TOLERANCIA_NIVELAMENTO_MM:
                self.fase = FaseMovimento.CHEGOU
            else:  # parou fora da tolerância: nova aproximação fina
                self.fase = FaseMovimento.MOVENDO
                self.pid.reiniciar()
                self._limite_rampa = 0.0
        return self._emitir(FREIO)

    def _passo_movendo(self, velocidade: float, parado: bool, duty_max: float, dt: float) -> Comando:
        erro = self.erro
        if abs(erro) <= self.FAIXA_PARADA_MM and abs(velocidade) <= self.VELOCIDADE_PARADA_MM_S:
            self.fase = FaseMovimento.FREANDO
            self._ticks_parado = 0
            return self._emitir(FREIO)
        if duty_max < DUTY_ATRITO_ESTATICO and abs(velocidade) < 20.0:
            # Cota abaixo do atrito estático e cabine parada: não energiza o motor à toa.
            self._limite_rampa = 0.0
            return self._emitir(FREIO)
        saida = self.pid.calcular(erro, dt, self._saturado)
        sentido = 1 if saida >= 0 else -1
        if not parado and velocidade * sentido < -60.0:
            # inversão com a cabine ainda correndo no sentido oposto: freia antes
            self.fase = FaseMovimento.FREANDO
            self._ticks_parado = 0
            return self._emitir(FREIO)
        if self._limite_rampa == 0.0:
            self._limite_rampa = self.DUTY_ARRANQUE
        else:
            self._limite_rampa = min(100.0, self._limite_rampa + self.rampa * dt)
        self._ticks_imovel = self._ticks_imovel + 1 if parado else 0
        magnitude = abs(saida)
        teto = min(self._limite_rampa, duty_max)
        self._saturado = sentido if magnitude > teto else 0
        magnitude = min(magnitude, teto)
        if self._ticks_imovel >= self.TICKS_PARADO and magnitude < DUTY_ATRITO_ESTATICO:
            # Cabine imóvel com duty abaixo do atrito estático: um pulso de arranque
            # evita o "parado sem erro aparente"; sem folga de Cota, mantém o freio.
            magnitude = self.DUTY_ARRANQUE if duty_max >= self.DUTY_ARRANQUE else 0.0
            if magnitude == 0.0:
                return self._emitir(FREIO)
        direcao = Direcao.SUBIR if sentido > 0 else Direcao.DESCER
        return self._emitir(Comando(direcao, magnitude))

    def _emitir(self, comando: Comando) -> Comando:
        self.duty_saida = comando.duty
        return comando
