"""Planejador de movimento e geração de perfis de rampa da Cabine 1."""

from dataclasses import dataclass
from typing import Optional, Union

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


class InalteradoUpdate:
    pass


MotionUpdate = Union[ContinuarUpdate, ChegouAoAndarUpdate, LimiteCursoAtingidoUpdate, InalteradoUpdate]


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
    """Gerador de perfil de movimento com rampas de aceleração e desaceleração."""

    def __init__(self) -> None:
        self.estado: MotionState = EstadoParado()

    def comandar_andar(self, andar_alvo: int, pos_alvo: int) -> None:
        """Comanda a viagem até um andar de destino."""
        self.estado = EstadoMovendoParaAndar(
            andar_alvo=andar_alvo,
            pos_alvo=pos_alvo,
            duty_atual=Fisica.MIN_DUTY_ARRANQUE,
        )

    def comandar_manual(self, direcao: MotorDirection, duty: float) -> None:
        """Comanda acionamento manual do motor."""
        if direcao in (MotorDirection.FREIO, MotorDirection.LIVRE) or duty <= 0.0:
            self.estado = EstadoParado()
        else:
            self.estado = EstadoManual(direcao=direcao, duty=duty)

    def parar(self) -> None:
        """Para qualquer movimento ativo."""
        self.estado = EstadoParado()

    def update(self, pos_atual: int) -> MotionUpdate:
        """Atualiza o perfil de movimento a cada ciclo da malha de controle (ex: 50 ms)."""
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

            # 1. Verificação da tolerância de nivelamento (±10 mm)
            if dist <= Fisica.TOLERANCIA_NIVELAMENTO_MM:
                self.estado = EstadoParado()
                return ChegouAoAndarUpdate(
                    andar=andar,
                    posicao=pos_atual,
                    erro_nivelamento=erro,
                )

            # 2. Determinação do sentido de movimento
            direcao = MotorDirection.SUBIR if erro > 0 else MotorDirection.DESCER

            # 3. Proteção de fim de curso
            if direcao == MotorDirection.SUBIR and pos_atual >= Fisica.POS_MAXIMA_MM:
                self.estado = EstadoParado()
                return LimiteCursoAtingidoUpdate(posicao=pos_atual)
            if direcao == MotorDirection.DESCER and pos_atual <= Fisica.POS_MINIMA_MM:
                self.estado = EstadoParado()
                return LimiteCursoAtingidoUpdate(posicao=pos_atual)

            # 4. Perfil de velocidade (Rampas de Aceleração e Desaceleração)
            duty_atual = self.estado.duty_atual
            if dist > Fisica.DISTANCIA_DESACELERACAO_MM:
                # Rampa de aceleração suave: incrementa até atingir CRUISE_DUTY (+4% por ciclo de 50ms)
                duty_atual = min(duty_atual + 4.0, Fisica.CRUISE_DUTY)
            else:
                # Rampa de desaceleração progressiva aproximando do andar
                ratio = float(dist) / float(Fisica.DISTANCIA_DESACELERACAO_MM)
                target_duty = Fisica.MIN_DUTY_APROXIMACAO + ratio * (Fisica.CRUISE_DUTY - Fisica.MIN_DUTY_APROXIMACAO)
                duty_atual = max(Fisica.MIN_DUTY_APROXIMACAO, min(duty_atual, target_duty))

            self.estado.duty_atual = duty_atual

            return ContinuarUpdate(
                direcao=direcao,
                duty_percent=duty_atual,
            )

        return InalteradoUpdate()
