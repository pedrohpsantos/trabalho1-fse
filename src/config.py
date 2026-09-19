"""Configurações de pinos e parâmetros cinemáticos do sistema."""

from dataclasses import dataclass
from enum import Enum
from typing import Tuple, List


@dataclass(frozen=True)
class PinConfig:
    """Mapeamento de pinos GPIO (BCM) da Cabine 1."""
    pwm: int
    dir1: int
    dir2: int
    enc_a: int
    enc_b: int
    cortina: int
    sensor_andar: int

    @classmethod
    def tabela_oficial(cls) -> "PinConfig":
        """Configuração nominal da bancada física (Bancada 36)."""
        return cls(
            pwm=13,
            dir1=22,
            dir2=23,
            enc_a=20,
            enc_b=21,
            cortina=26,
            sensor_andar=0,
        )

    @classmethod
    def widget_bancada(cls) -> "PinConfig":
        """Configuração alternativa (ThingsBoard)."""
        return cls(
            pwm=13,
            dir1=17,
            dir2=27,
            enc_a=20,
            enc_b=21,
            cortina=26,
            sensor_andar=11,
        )


class MotorDirection(Enum):
    """Estados lógicos da ponte H de acionamento do motor."""
    LIVRE = "Livre"    # DIR1=0, DIR2=0
    SUBIR = "Subir"    # DIR1=1, DIR2=0
    DESCER = "Descer"  # DIR1=0, DIR2=1
    FREIO = "Freio"    # DIR1=1, DIR2=1

    def to_gpio_levels(self) -> Tuple[bool, bool]:
        """Retorna os níveis lógicos (DIR1, DIR2)."""
        if self == MotorDirection.LIVRE:
            return (False, False)
        elif self == MotorDirection.SUBIR:
            return (True, False)
        elif self == MotorDirection.DESCER:
            return (False, True)
        elif self == MotorDirection.FREIO:
            return (True, True)
        return (False, False)

    @property
    def as_str(self) -> str:
        return self.value


class Fisica:
    """Parâmetros físicos e limites cinemáticos da cabine."""
    PWM_FREQ_HZ: float = 1000.0

    # Limite inferior de duty cycle para compensação de atrito estático
    MIN_DUTY_ARRANQUE: float = 20.0
    MIN_DUTY_APROXIMACAO: float = 20.0
    CRUISE_DUTY: float = 60.0

    # Relação de pulsos do encoder (1000 pulsos = 1 metro)
    PULSOS_POR_METRO: int = 1000

    # Tolerância de nivelamento no andar alvo (mm)
    TOLERANCIA_NIVELAMENTO_MM: int = 10

    # Distância para início da desaceleração controlada (mm)
    DISTANCIA_DESACELERACAO_MM: int = 400

    # Limites físicos de curso (mm)
    POS_MINIMA_MM: int = 0
    POS_MAXIMA_MM: int = 6000

    # Posições nominais dos andares (andar, cota_mm)
    ANDARES_NOMINAIS: List[Tuple[int, int]] = [
        (0, 0),
        (1, 3000),
        (2, 6000),
    ]

    # Janela de debounce temporal da cortina de luz (ms)
    DEBOUNCE_CORTINA_MS: int = 40
