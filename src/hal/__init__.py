"""Camada de Abstração de Hardware (HAL) para o controle da Cabine 1."""

from .base import ElevatorHardware, HardwareEvent, CortinaEvent, BandeirolaEvent, QUADRATURE_TABLE

__all__ = [
    "ElevatorHardware",
    "HardwareEvent",
    "CortinaEvent",
    "BandeirolaEvent",
    "QUADRATURE_TABLE",
]
