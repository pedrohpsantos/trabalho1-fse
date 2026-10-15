"""Operações de domínio sobre os mapas de registradores do simulador (Seções 5.2 e 5.3)."""

import struct
from dataclasses import dataclass

from ..constantes import (
    ENDERECO_PREDIO,
    ENDERECOS_CABINES,
    NUM_REGS_CABINE,
    NUM_REGS_PREDIO,
    PortaComando,
    RegCabine,
    RegPredio,
)
from .cliente import ClienteModbus


def _int16(valor: int) -> int:
    return struct.unpack(">h", struct.pack(">H", valor & 0xFFFF))[0]


@dataclass(frozen=True)
class EstadoCabineModbus:
    """Registradores de uma cabine (campos de depuração incluídos)."""

    andar_atual: int
    nivelado: int
    porta_estado: int
    corrente_ma: int
    carga_kg: int
    passageiros: int
    posicao_mm: int  # conferência apenas: 1 Hz, nunca usar na malha de controle
    falha: int

    @classmethod
    def de_registradores(cls, regs: list[int]) -> "EstadoCabineModbus":
        return cls(
            andar_atual=regs[RegCabine.ANDAR_ATUAL],
            nivelado=regs[RegCabine.NIVELADO],
            porta_estado=regs[RegCabine.PORTA_ESTADO],
            corrente_ma=regs[RegCabine.CORRENTE_MA],
            carga_kg=regs[RegCabine.CARGA_KG],
            passageiros=regs[RegCabine.PASSAGEIROS],
            posicao_mm=_int16(regs[RegCabine.POSICAO_MM]),
            falha=regs[RegCabine.FALHA],
        )


@dataclass(frozen=True)
class EstadoPredio:
    chamadas_na_fila: int
    chamada_origem: int
    chamada_destino: int
    chamada_id: int
    barramento_max_ma: int
    watchdog_ambiente: int
    cenario_ativo: int
    cenario_geradas: int
    cenario_atendidas: int
    espera_media_s_x10: int
    viagem_media_s_x10: int

    @classmethod
    def de_registradores(cls, regs: list[int]) -> "EstadoPredio":
        return cls(
            chamadas_na_fila=regs[RegPredio.CHAMADAS_NA_FILA],
            chamada_origem=regs[RegPredio.CHAMADA_ORIGEM],
            chamada_destino=regs[RegPredio.CHAMADA_DESTINO],
            chamada_id=regs[RegPredio.CHAMADA_ID],
            barramento_max_ma=regs[RegPredio.BARRAMENTO_MAX_MA],
            watchdog_ambiente=regs[RegPredio.WATCHDOG_AMBIENTE],
            cenario_ativo=regs[RegPredio.CENARIO_ATIVO],
            cenario_geradas=regs[RegPredio.CENARIO_GERADAS],
            cenario_atendidas=regs[RegPredio.CENARIO_ATENDIDAS],
            espera_media_s_x10=regs[RegPredio.ESPERA_MEDIA_S_X10],
            viagem_media_s_x10=regs[RegPredio.VIAGEM_MEDIA_S_X10],
        )


class ApiSimulador:
    """Fachada de alto nível usada pelo Servidor Central."""

    def __init__(self, cliente: ClienteModbus) -> None:
        self.cliente = cliente

    def ler_cabine(self, cabine: int) -> EstadoCabineModbus:
        regs = self.cliente.ler_registradores(ENDERECOS_CABINES[cabine], 0, NUM_REGS_CABINE)
        return EstadoCabineModbus.de_registradores(regs)

    def comandar_porta(self, cabine: int, comando: PortaComando) -> None:
        self.cliente.escrever_registradores(ENDERECOS_CABINES[cabine], RegCabine.PORTA_COMANDO, [int(comando)])

    def ler_predio(self) -> EstadoPredio:
        regs = self.cliente.ler_registradores(ENDERECO_PREDIO, 0, NUM_REGS_PREDIO)
        return EstadoPredio.de_registradores(regs)

    def escrever_condicao_contorno(self, temp_c: float, pressao_hpa: float) -> None:
        """Offsets 5 (décimos de °C) e 6 (hPa) numa única escrita 0x10."""
        temp_x10 = int(round(temp_c * 10))
        pressao = max(0, min(0xFFFF, int(round(pressao_hpa))))
        self.cliente.escrever_registradores(ENDERECO_PREDIO, RegPredio.AMBIENTE_TEMP_X10, [temp_x10, pressao])

    def atribuir_chamada(self, chamada_id: int, cabine: int) -> None:
        if cabine not in ENDERECOS_CABINES:
            raise ValueError(f"cabine inválida: {cabine}")
        self.cliente.escrever_registradores(ENDERECO_PREDIO, RegPredio.ATRIBUICAO_CHAMADA_ID, [chamada_id, cabine])

    def remover_chamada_da_fila(self) -> None:
        self.cliente.escrever_registradores(ENDERECO_PREDIO, RegPredio.CHAMADA_POP, [1])
