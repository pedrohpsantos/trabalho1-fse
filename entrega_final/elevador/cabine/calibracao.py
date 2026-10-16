"""Calibração das posições dos 6 andares (Seção 3.3).

As posições vêm EXCLUSIVAMENTE das bordas do Sensor de Andar, entregues por interrupção de
GPIO junto com a contagem do encoder. O registrador MODBUS `andar_atual` só identifica a qual
andar pertence cada bandeirola; o registrador `nivelado` não é consultado.

Procedimento: (1) descida até a base do poço (cruzando a bandeirola do andar 0);
(2) varredura completa de SUBIDA atravessando as 6 bandeirolas; (3) varredura completa de
DESCIDA. O centro de cada andar é a média das bordas de entrada e saída em cada sentido,
e depois a média entre os dois sentidos.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

from ..constantes import DUTY_ATRITO_ESTATICO, NUM_ANDARES, Direcao
from .movimento import FREIO, Comando

log = logging.getLogger(__name__)

LIMITE_DESLOCAMENTO_SEM_ID_MM = 5000.0
TICKS_ESTOL = 24  # ~1,2 s sem variar a contagem com o motor acionado => fim de curso


class FaseCalibracao(Enum):
    HOMING = "homing"
    SUBIDA = "subida"
    DESCIDA = "descida"
    CONCLUIDA = "concluida"
    FALHA = "falha"


@dataclass(frozen=True)
class ResultadoCalibracao:
    centros_mm: list[float]
    larguras_mm: list[float]


@dataclass
class _Passagem:
    entrada_pos: int | None
    entrada_ts: float | None
    saida_pos: int
    saida_ts: float
    andar: int | None = None


@dataclass
class Calibrador:
    duty: float = 40.0
    margem_extremo_mm: float = 60.0
    fase: FaseCalibracao = FaseCalibracao.HOMING
    motivo_falha: str = ""
    _entrada_pos: int | None = None
    _entrada_ts: float | None = None
    _pendentes: list[_Passagem] = field(default_factory=list)
    _leitura_andar: tuple[int, float] | None = None  # (andar_atual, ts da leitura MODBUS)
    _registros: dict[str, dict[int, tuple[int, int]]] = field(default_factory=lambda: {"subida": {}, "descida": {}})
    _proximo_id: int = 0
    _alvo_parada: float | None = None
    _parando: bool = False
    _ticks_imovel: int = 0
    _pos_anterior: int | None = None
    _pos_inicio_fase: int | None = None
    resultado: ResultadoCalibracao | None = None

    # --- eventos ---------------------------------------------------------------------
    def ao_borda(self, entrada: bool, pos: int, agora: float) -> None:
        """Interrupção do Sensor de Andar (as duas bordas)."""
        if entrada:
            self._entrada_pos, self._entrada_ts = pos, agora
            return
        self._pendentes.append(_Passagem(self._entrada_pos, self._entrada_ts, pos, agora))
        self._entrada_pos = self._entrada_ts = None

    def ao_modbus(self, andar_atual: int, ts: float) -> None:
        self._leitura_andar = (andar_atual, ts)

    # --- laço periódico -----------------------------------------------------------------
    def passo(self, pos: int, duty_max: float, agora: float) -> Comando:
        if self.fase in (FaseCalibracao.CONCLUIDA, FaseCalibracao.FALHA):
            return FREIO
        self._resolver_passagens()
        if self.fase == FaseCalibracao.FALHA:
            return FREIO
        imovel = self._pos_anterior is not None and pos == self._pos_anterior
        self._pos_anterior = pos
        if self._pos_inicio_fase is None:
            self._pos_inicio_fase = pos
        sentido = Direcao.DESCER if self.fase in (FaseCalibracao.HOMING, FaseCalibracao.DESCIDA) else Direcao.SUBIR
        if self._parando:
            return self._aguardar_parada(imovel)
        if self._alvo_parada is not None and self._passou(sentido, pos, self._alvo_parada):
            self._parando = True
            self._ticks_imovel = 0
            return FREIO
        if self.fase == FaseCalibracao.HOMING:
            self._ticks_imovel = self._ticks_imovel + 1 if imovel else 0
            if self._ticks_imovel >= TICKS_ESTOL:
                log.info("calibração: base do poço alcançada por estol da contagem")
                self._parando = True
                self._ticks_imovel = 0
                return FREIO
            if abs(pos - self._pos_inicio_fase) > LIMITE_DESLOCAMENTO_SEM_ID_MM:
                return self._falhar("andar 0 não identificado durante a descida inicial")
        duty = min(self.duty, duty_max)
        if duty < DUTY_ATRITO_ESTATICO and not self._em_movimento(imovel):
            return FREIO  # sem Cota suficiente para vencer o atrito estático: aguarda
        return Comando(sentido, duty)

    @staticmethod
    def _passou(sentido: Direcao, pos: int, alvo: float) -> bool:
        return pos <= alvo if sentido == Direcao.DESCER else pos >= alvo

    def _em_movimento(self, imovel: bool) -> bool:
        return not imovel

    def _aguardar_parada(self, imovel: bool) -> Comando:
        self._ticks_imovel = self._ticks_imovel + 1 if imovel else 0
        if self._ticks_imovel >= 3:
            self._parando = False
            self._alvo_parada = None
            self._ticks_imovel = 0
            self._pos_inicio_fase = None
            self._avancar_fase()
        return FREIO

    def _avancar_fase(self) -> None:
        self._pendentes.clear()
        self._entrada_pos = self._entrada_ts = None
        if self.fase == FaseCalibracao.HOMING:
            self.fase, self._proximo_id = FaseCalibracao.SUBIDA, 0
        elif self.fase == FaseCalibracao.SUBIDA:
            self.fase, self._proximo_id = FaseCalibracao.DESCIDA, NUM_ANDARES - 1
        elif self.fase == FaseCalibracao.DESCIDA:
            self._concluir()

    # --- identificação das bandeirolas ---------------------------------------------------------
    def _resolver_passagens(self) -> None:
        if not self._pendentes or self._leitura_andar is None:
            return
        andar, ts = self._leitura_andar
        restantes: list[_Passagem] = []
        for p in self._pendentes:
            referencia = p.entrada_ts if p.entrada_ts is not None else p.saida_ts
            if ts < referencia:
                restantes.append(p)  # leitura anterior ao cruzamento: ainda não vale
                continue
            p.andar = andar
            self._registrar(p)
            if self.fase == FaseCalibracao.FALHA:
                return
        self._pendentes = restantes

    def _registrar(self, p: _Passagem) -> None:
        sentido = "descida" if self.fase in (FaseCalibracao.HOMING, FaseCalibracao.DESCIDA) else "subida"
        if self.fase == FaseCalibracao.HOMING:
            self._pos_inicio_fase = p.saida_pos
            if p.andar == 0 and self._alvo_parada is None:
                self._alvo_parada = p.saida_pos - self.margem_extremo_mm
            return
        if p.entrada_pos is None or abs(p.saida_pos - p.entrada_pos) < 20:
            self._falhar(f"bandeirola do andar {p.andar} sem bordas válidas")
            return
        if p.andar != self._proximo_id:
            self._falhar(f"andar {p.andar} fora de sequência (esperado {self._proximo_id})")
            return
        self._registros[sentido][p.andar] = (p.entrada_pos, p.saida_pos)
        if self.fase == FaseCalibracao.SUBIDA:
            self._proximo_id += 1
            if p.andar == NUM_ANDARES - 1:
                self._alvo_parada = p.saida_pos + self.margem_extremo_mm
        else:
            self._proximo_id -= 1
            if p.andar == 0:
                self._alvo_parada = p.saida_pos - self.margem_extremo_mm

    def _falhar(self, motivo: str) -> Comando:
        log.error("calibração falhou: %s", motivo)
        self.fase, self.motivo_falha = FaseCalibracao.FALHA, motivo
        return FREIO

    def _concluir(self) -> None:
        centros, larguras = [], []
        for andar in range(NUM_ANDARES):
            medidas = []
            for sentido in ("subida", "descida"):
                entrada, saida = self._registros[sentido][andar]
                medidas.append(((entrada + saida) / 2.0, abs(saida - entrada)))
            centros.append(sum(m[0] for m in medidas) / 2.0)
            larguras.append(sum(m[1] for m in medidas) / 2.0)
        self.resultado = ResultadoCalibracao(centros, larguras)
        self.fase = FaseCalibracao.CONCLUIDA
