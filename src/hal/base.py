"""Definição de interfaces e eventos da Camada de Abstração de Hardware (HAL)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import queue
from typing import Union
from ..config import MotorDirection

# Tabela de transição de quadratura 4x ajustada para a polaridade da bancada física:
# Movimento de subida incrementa a contagem de pulsos; descida decrementa.
# Índice: (prev_a << 3) | (prev_b << 2) | (curr_a << 1) | curr_b
QUADRATURE_TABLE = (
     0,  1, -1,  0,
    -1,  0,  0,  1,
     1,  0,  0, -1,
     0, -1,  1,  0,
)


@dataclass(frozen=True)
class CortinaEvent:
    """Evento da cortina de luz (True = obstruída, False = liberada)."""
    obstruida: bool


@dataclass(frozen=True)
class BandeirolaEvent:
    """Evento de transição do sensor de andar (bandeirola)."""
    entrada: bool  # True se entrou na bandeirola, False se saiu
    pos_encoder: int  # Posição do encoder em pulsos/mm no instante exato


HardwareEvent = Union[CortinaEvent, BandeirolaEvent]


class ElevatorHardware(ABC):
    """Interface unificada de controle de hardware da Cabine 1."""

    @abstractmethod
    def set_motor(self, direction: MotorDirection, duty_percent: float) -> None:
        """Aciona o motor de tração com a direção e o duty cycle especificados (0.0 a 100.0%)."""
        pass

    @abstractmethod
    def get_position(self) -> int:
        """Lê a posição atual acumulada do encoder de quadratura (em pulsos / mm)."""
        pass

    @abstractmethod
    def is_curtain_obstructed(self) -> bool:
        """Verifica se a cortina de luz está obstruída atualmente."""
        pass

    @abstractmethod
    def is_floor_sensor_active(self) -> bool:
        """Verifica se o sensor de andar está ativo (dentro da bandeirola)."""
        pass

    @abstractmethod
    def get_event_queue(self) -> queue.Queue:
        """Retorna a fila de eventos assíncronos (cortina e bandeirola)."""
        pass

    def emergency_brake(self) -> None:
        """Para o motor imediatamente e aciona o freio elétrico (DIR1=1, DIR2=1, PWM=0)."""
        self.set_motor(MotorDirection.FREIO, 0.0)

    @abstractmethod
    def cleanup(self) -> None:
        """Libera recursos de hardware antes de encerrar."""
        pass
