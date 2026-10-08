"""src - Pacote de comunicação UART e MODBUS RTU da Entrega 2 de FSE."""

from .config import MATRICULA_PADRAO, validar_e_obter_matricula
from .crc import anexar_crc16, calcular_crc16, verificar_crc16
from .parte1_simplificado import ProtocoloSimplificado
from .parte2_modbus_p2 import ProtocoloModbusP2
from .parte3_simulador import SimuladorModbusClient
from .uart_driver import (
    BaseUartDriver,
    FakeUartDriver,
    HardwareUartDriver,
    criar_uart_driver,
)

__all__ = [
    "MATRICULA_PADRAO",
    "BaseUartDriver",
    "FakeUartDriver",
    "HardwareUartDriver",
    "ProtocoloModbusP2",
    "ProtocoloSimplificado",
    "SimuladorModbusClient",
    "anexar_crc16",
    "calcular_crc16",
    "criar_uart_driver",
    "validar_e_obter_matricula",
    "verificar_crc16",
]
