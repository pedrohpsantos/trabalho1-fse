"""Planejador de trajetória e cálculo de perfis de velocidade da cabine."""

from dataclasses import dataclass
from typing import Union

from ..config import Fisica, MotorDirection


@dataclass
class ContinuarUpdate:
    direcao: MotorDirection
    duty_percent: float


@dataclass
class ChegouAoAndarUpdate:
    andar: int
    posicao: int
    erro_nivelamento: int


@dataclass
class LimiteCursoAtingidoUpdate:
    posicao: int


MotionUpdate = Union[ContinuarUpdate, ChegouAoAndarUpdate, LimiteCursoAtingidoUpdate]


@dataclass
class EstadoParado:
    pass


@dataclass
class EstadoMovendoParaAndar:
    andar_alvo: int
    pos_alvo: int
    duty_atual: float


@dataclass
class EstadoManual:
    direcao: MotorDirection
    duty: float


MotionState = Union[EstadoParado, EstadoMovendoParaAndar, EstadoManual]


class MotionPlanner:
    """Máquina de estados para cálculo de rampas e paradas de nivelamento."""

    def __init__(self) -> None:
        self.estado: MotionState = EstadoParado()

    def comandar_andar(self, andar_alvo: int, pos_alvo: int) -> None:
        """Inicia transição para o andar alvo."""
        self.estado = EstadoMovendoParaAndar(
            andar_alvo=andar_alvo,
            pos_alvo=pos_alvo,
            duty_atual=Fisica.MIN_DUTY_ARRANQUE,
        )

    def comandar_manual(self, direcao: MotorDirection, duty: float) -> None:
        """Configura acionamento em modo manual."""
        if direcao in (MotorDirection.FREIO, MotorDirection.LIVRE) or duty <= 0.0:
            self.estado = EstadoParado()
        else:
            self.estado = EstadoManual(direcao=direcao, duty=duty)

    def parar(self) -> None:
        """Interrompe qualquer movimento e comuta para repouso."""
        self.estado = EstadoParado()

    def update(self, pos_atual: int) -> MotionUpdate:
        """Calcula o ciclo de controle com base na cota atual."""
        if isinstance(self.estado, EstadoParado):
            return ContinuarUpdate(
                direcao=MotorDirection.FREIO,
                duty_percent=0.0,
            )

        elif isinstance(self.estado, EstadoManual):
            dir_atual = self.estado.direcao
            duty = self.estado.duty

            # Proteção de fim de curso em modo manual
            if dir_atual == MotorDirection.SUBIR and pos_atual >= Fisica.POS_MAXIMA_MM:
                self.estado = EstadoParado()
                return LimiteCursoAtingidoUpdate(posicao=pos_atual)
            if dir_atual == MotorDirection.DESCER and pos_atual <= Fisica.POS_MINIMA_MM:
                self.estado = EstadoParado()
                return LimiteCursoAtingidoUpdate(posicao=pos_atual)

            return ContinuarUpdate(
                direcao=dir_atual,
                duty_percent=duty,
            )

        elif isinstance(self.estado, EstadoMovendoParaAndar):
            alvo = self.estado.pos_alvo
            andar = self.estado.andar_alvo
            erro = alvo - pos_atual
            dist = abs(erro)

            # Parada por tolerância de nivelamento (+-10 mm)
            if dist <= Fisica.TOLERANCIA_NIVELAMENTO_MM:
                self.estado = EstadoParado()
                return ChegouAoAndarUpdate(
                    andar=andar,
                    posicao=pos_atual,
                    erro_nivelamento=erro,
                )

            # Sentido de movimentação
            direcao = MotorDirection.SUBIR if erro > 0 else MotorDirection.DESCER

            # Verificação de fim de curso
            if direcao == MotorDirection.SUBIR and pos_atual >= Fisica.POS_MAXIMA_MM:
                self.estado = EstadoParado()
                return LimiteCursoAtingidoUpdate(posicao=pos_atual)
            if direcao == MotorDirection.DESCER and pos_atual <= Fisica.POS_MINIMA_MM:
                self.estado = EstadoParado()
                return LimiteCursoAtingidoUpdate(posicao=pos_atual)

            # Cálculo de velocidade (aceleração progressiva ou desaceleração por aproximação)
            duty_atual = self.estado.duty_atual
            if dist > Fisica.DISTANCIA_DESACELERACAO_MM:
                duty_atual = min(duty_atual + 4.0, Fisica.CRUISE_DUTY)
            else:
                ratio = float(dist) / float(Fisica.DISTANCIA_DESACELERACAO_MM)
                target_duty = Fisica.MIN_DUTY_APROXIMACAO + ratio * (Fisica.CRUISE_DUTY - Fisica.MIN_DUTY_APROXIMACAO)
                duty_atual = max(Fisica.MIN_DUTY_APROXIMACAO, min(duty_atual, target_duty))

            self.estado.duty_atual = duty_atual

            return ContinuarUpdate(
                direcao=direcao,
                duty_percent=duty_atual,
            )
