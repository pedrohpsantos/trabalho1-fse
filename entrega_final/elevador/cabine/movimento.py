"""Controlador de posição: PID + rampa de aceleração + saturação por Cota + parada por freio.

Máquina de estados: PARADO -> MOVENDO -> FREANDO -> CHEGOU. A saída é sempre o par
(direção da ponte H, duty %). A posição de controle vem exclusivamente do encoder.

Parada (ajustada com os CSVs da bancada real): o freio é acionado quando o ponto de parada
PREVISTO (`erro − v·LATENCIA_FREIO_S`) cai a <= FAIXA_PARADA_MM do alvo, e a chegada só é
aceita a <= ACEITE_CHEGADA_MM (metade da tolerância de ±10 mm). A margem absorve o erro
residual entre o encoder e o andar real: na rasp32 uma cabine parada a 10 mm pelo encoder
estava fora do nível para o simulador, que recusou abrir a porta.
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
    FAIXA_PARADA_MM = 2.5  # |ponto de parada previsto − alvo| que aciona o freio
    LATENCIA_FREIO_S = 0.06  # deslocamento após o freio ≈ v·60 ms (medido na bancada)
    ACEITE_CHEGADA_MM = 5.0  # chegada aceita; acima disso, nova aproximação fina
    VELOCIDADE_PARADA_MM_S = 200.0
    DUTY_ARRANQUE = DUTY_ATRITO_ESTATICO + 1.0  # vence o atrito e cabe na Cota mínima (950 mA => 11,4 %)
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
        self.reaproximacoes = 0
        self.aceite_mm = self.ACEITE_CHEGADA_MM

    def ir_para(self, alvo: float, aceite_mm: float | None = None) -> None:
        """`aceite_mm`: margem de chegada mais estrita (recentralização para a porta)."""
        self.alvo = float(alvo)
        self.aceite_mm = self.ACEITE_CHEGADA_MM if aceite_mm is None else aceite_mm
        self.fase = FaseMovimento.MOVENDO
        self.pid.reiniciar()
        self._limite_rampa = 0.0
        self._saturado = 0
        self._ticks_parado = 0
        self._ticks_imovel = 0
        self.reaproximacoes = 0

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
            if abs(self.erro) <= self.aceite_mm:
                self.fase = FaseMovimento.CHEGOU
            else:  # parou fora da margem: nova aproximação fina (a porta nunca abre aqui)
                self.fase = FaseMovimento.MOVENDO
                self.reaproximacoes += 1
                self.pid.reiniciar()
                self._limite_rampa = 0.0
        return self._emitir(FREIO)

    def _passo_movendo(self, velocidade: float, parado: bool, duty_max: float, dt: float) -> Comando:
        erro = self.erro
        previsto = erro - velocidade * self.LATENCIA_FREIO_S  # erro onde a cabine pararia se freasse já
        ultrapassaria = previsto * erro < 0 and abs(erro) <= 3 * TOLERANCIA_NIVELAMENTO_MM
        if (abs(previsto) <= self.FAIXA_PARADA_MM or ultrapassaria) and abs(velocidade) <= self.VELOCIDADE_PARADA_MM_S:
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
