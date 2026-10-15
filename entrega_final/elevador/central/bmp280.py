"""Sensor BMP280 (I2C 0x76): driver real via smbus2, compensação de Bosch e simulador."""

import logging
import math
import struct
import threading
from dataclasses import dataclass
from typing import Protocol

try:
    from smbus2 import SMBus
except ImportError:  # opcional fora da Raspberry Pi
    SMBus = None

log = logging.getLogger(__name__)

ENDERECO_I2C = 0x76
REG_ID, REG_RESET, REG_CTRL_MEAS, REG_CONFIG, REG_DADOS, REG_CALIB = 0xD0, 0xE0, 0xF4, 0xF5, 0xF7, 0x88
ID_BMP280 = (0x56, 0x57, 0x58)


@dataclass(frozen=True)
class Leitura:
    temperatura_c: float
    pressao_hpa: float


class SensorAmbiente(Protocol):
    def ler(self) -> Leitura: ...

    def fechar(self) -> None: ...


@dataclass(frozen=True)
class Calibracao:
    t1: int
    t2: int
    t3: int
    p1: int
    p2: int
    p3: int
    p4: int
    p5: int
    p6: int
    p7: int
    p8: int
    p9: int

    @classmethod
    def de_bytes(cls, dados: bytes) -> "Calibracao":
        return cls(*struct.unpack("<HhhHhhhhhhhh", dados[:24]))


def compensar(cal: Calibracao, adc_t: int, adc_p: int) -> Leitura:
    """Algoritmos de ponto fixo do datasheet (temperatura 32 bits, pressão 64 bits)."""
    v1 = (((adc_t >> 3) - (cal.t1 << 1)) * cal.t2) >> 11
    v2 = (((((adc_t >> 4) - cal.t1) * ((adc_t >> 4) - cal.t1)) >> 12) * cal.t3) >> 14
    t_fine = v1 + v2
    temperatura = ((t_fine * 5 + 128) >> 8) / 100.0

    var1 = t_fine - 128000
    var2 = var1 * var1 * cal.p6
    var2 += (var1 * cal.p5) << 17
    var2 += cal.p4 << 35
    var1 = ((var1 * var1 * cal.p3) >> 8) + ((var1 * cal.p2) << 12)
    var1 = (((1 << 47) + var1) * cal.p1) >> 33
    if var1 == 0:
        raise ValueError("calibração inválida (divisão por zero)")
    pressao = 1048576 - adc_p
    pressao = (((pressao << 31) - var2) * 3125) // var1
    var1 = (cal.p9 * (pressao >> 13) * (pressao >> 13)) >> 25
    var2 = (cal.p8 * pressao) >> 19
    pressao = ((pressao + var1 + var2) >> 8) + (cal.p7 << 4)
    return Leitura(temperatura, pressao / 256.0 / 100.0)


class Bmp280(SensorAmbiente):
    def __init__(self, barramento: int = 1, endereco: int = ENDERECO_I2C) -> None:
        if SMBus is None:
            raise RuntimeError("smbus2 não instalado: use `pip install smbus2` ou --mock")
        self.endereco = endereco
        self._bus = SMBus(barramento)
        chip = self._bus.read_byte_data(endereco, REG_ID)
        if chip not in ID_BMP280:
            self._bus.close()
            raise RuntimeError(f"dispositivo 0x{endereco:02X} não é um BMP280 (id=0x{chip:02X})")
        self._cal = Calibracao.de_bytes(bytes(self._bus.read_i2c_block_data(endereco, REG_CALIB, 24)))

    def ler(self) -> Leitura:
        # Modo forçado, oversampling x1 para T e P: uma conversão por leitura.
        self._bus.write_byte_data(self.endereco, REG_CTRL_MEAS, 0b001_001_01)
        threading.Event().wait(0.012)  # conversão típica: 6,4 ms
        d = self._bus.read_i2c_block_data(self.endereco, REG_DADOS, 6)
        adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        return compensar(self._cal, adc_t, adc_p)

    def fechar(self) -> None:
        self._bus.close()


class Bmp280Simulado(SensorAmbiente):
    """Sensor virtual: temperatura ajustável (derating) com pequena oscilação realista."""

    def __init__(self, temperatura_c: float = 25.0, pressao_hpa: float = 1013.0) -> None:
        self.temperatura_c = temperatura_c
        self.pressao_hpa = pressao_hpa
        self.falhar = False
        self._n = 0

    def ler(self) -> Leitura:
        if self.falhar:
            raise OSError("BMP280 simulado indisponível")
        self._n += 1
        ruido = 0.05 * math.sin(self._n / 7.0)
        return Leitura(round(self.temperatura_c + ruido, 2), round(self.pressao_hpa + 10 * ruido, 2))

    def fechar(self) -> None:
        return None
