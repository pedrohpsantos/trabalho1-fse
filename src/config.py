"""Configurações e constantes do sistema de controle da Cabine 1 (Entrega 1)."""

from dataclasses import dataclass
from enum import Enum
from typing import Tuple, List


@dataclass(frozen=True)
class PinConfig:
    """Configuração dos pinos GPIO (BCM) da Cabine 1."""
    pwm: int
    dir1: int
    dir2: int
    enc_a: int
    enc_b: int
    cortina: int
    sensor_andar: int

    @classmethod
    def tabela_oficial(cls) -> "PinConfig":
        """Pinos da Cabine 1 verificados na bancada física (PWM=13, DIR1=22, DIR2=23, SENSOR=0)."""
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
        """Pinos conforme exibido no widget alternativo da bancada (DIR1=17, DIR2=27, SENSOR=11)."""
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
    """Direção de acionamento do motor de tração."""
    LIVRE = "Livre"    # DIR1=0, DIR2=0
    SUBIR = "Subir"    # DIR1=1, DIR2=0
    DESCER = "Descer"  # DIR1=0, DIR2=1
    FREIO = "Freio"    # DIR1=1, DIR2=1

    def to_gpio_levels(self) -> Tuple[bool, bool]:
        """Retorna os níveis lógicos (DIR1, DIR2) para o acionamento."""
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


# Módulo de constantes físicas e parâmetros de controle
class Fisica:
    """Parâmetros de movimentação e física da cabine."""
    # Frequência do PWM do motor de tração (1 kHz)
    PWM_FREQ_HZ: float = 1000.0

    # Atrito estático: motor não arranca com duty cycle abaixo de 10% (na bancada ajustado para 20%)
    MIN_DUTY_ARRANQUE: float = 20.0

    # Duty cycle de aproximação lenta para nivelamento preciso
    MIN_DUTY_APROXIMACAO: float = 20.0

    # Duty cycle padrão de cruzeiro
    CRUISE_DUTY: float = 60.0

    # Resolução do encoder: 1000 pulsos por metro (1 pulso = 1 mm)
    PULSOS_POR_METRO: int = 1000

    # Tolerância máxima de nivelamento no andar (±10 mm / ±10 pulsos)
    TOLERANCIA_NIVELAMENTO_MM: int = 10

    # Distância para início da desaceleração na aproximação (em mm)
    DISTANCIA_DESACELERACAO_MM: int = 400

    # Limites de curso da bancada reduzida (em mm)
    POS_MINIMA_MM: int = 0
    POS_MAXIMA_MM: int = 6000

    # Posições nominais dos andares (andar, posicao_mm)
    ANDARES_NOMINAIS: List[Tuple[int, int]] = [
        (0, 0),
        (1, 3000),
        (2, 6000),
    ]

    # Tempo mínimo para debounce da cortina de luz (em milissegundos)
    DEBOUNCE_CORTINA_MS: int = 40
