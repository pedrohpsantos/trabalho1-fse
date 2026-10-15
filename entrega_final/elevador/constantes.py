"""Constantes do domínio: geometria, limites elétricos, tempos e mapas de registradores.

Todas as grandezas vêm do enunciado do Trabalho 1 (Seções 3, 5 e 7). Os valores são
compartilhados pelos servidores Central e Distribuído e pela bancada virtual.
"""

from enum import Enum, IntEnum

# --- Geometria (Seção 3.2) -------------------------------------------------
NUM_ANDARES = 6
VAO_NOMINAL_MM = 3000
POCO_MM = 400
SOBRECURSO_MM = 400
PULSOS_POR_METRO = 1000  # contagem de quadratura 4x
TOLERANCIA_NIVELAMENTO_MM = 10

# --- Motor / PWM (Seção 3.1) ------------------------------------------------
PWM_FREQ_HZ = 1000
DUTY_ATRITO_ESTATICO = 10.0  # abaixo disto a cabine parada não arranca

# --- Potência (Seções 7.2 a 7.4) -------------------------------------------
CORRENTE_MAX_CABINE_MA = 6000
CAPACIDADE_NOMINAL_MA = 12000
PISO_BARRAMENTO_MA = 3000
COTA_VALIDADE_S = 2.0
COTA_DEGRADADA_MA = 1000
PERIODO_RENOVACAO_COTA_S = 1.0
WATCHDOG_AMBIENTE_S = 30.0

# --- Períodos de controle e comunicação -------------------------------------
PERIODO_PID_S = 0.05
PERIODO_TELEMETRIA_S = 0.5
PERIODO_AMBIENTE_S = 2.5  # o enunciado exige no máximo 5 s

# --- Despacho (Seção 7.5) ---------------------------------------------------
LIMITE_ESPERA_S = 90.0

# --- MODBUS (Seção 5) -------------------------------------------------------
ENDERECO_PREDIO = 0x20
ENDERECOS_CABINES = {1: 0x11, 2: 0x12, 3: 0x13}
NUM_CABINES = 3


class RegCabine(IntEnum):
    """Offsets dos holding registers das cabines (Seção 5.2)."""

    ANDAR_ATUAL = 0
    NIVELADO = 1
    PORTA_ESTADO = 2
    PORTA_COMANDO = 3
    CORRENTE_MA = 4
    CARGA_KG = 5
    PASSAGEIROS = 6
    POSICAO_MM = 7
    FALHA = 8


NUM_REGS_CABINE = 9


class RegPredio(IntEnum):
    """Offsets dos holding registers do Controlador do Prédio (Seção 5.3)."""

    CHAMADAS_NA_FILA = 0
    CHAMADA_ORIGEM = 1
    CHAMADA_DESTINO = 2
    CHAMADA_ID = 3
    CHAMADA_POP = 4
    AMBIENTE_TEMP_X10 = 5
    AMBIENTE_PRESS_HPA = 6
    BARRAMENTO_MAX_MA = 7
    WATCHDOG_AMBIENTE = 8
    ATRIBUICAO_CHAMADA_ID = 9
    ATRIBUICAO_CABINE = 10
    CENARIO_ATIVO = 11
    CENARIO_GERADAS = 12
    CENARIO_ATENDIDAS = 13
    ESPERA_MEDIA_S_X10 = 14
    VIAGEM_MEDIA_S_X10 = 15


NUM_REGS_PREDIO = 16


class PortaEstado(IntEnum):
    """Valores de `porta_estado` (offset 2)."""

    FECHADA = 0
    ABRINDO = 1
    ABERTA = 2
    FECHANDO = 3
    OBSTRUIDA = 4


class PortaComando(IntEnum):
    """Valores de `porta_comando` (offset 3)."""

    NENHUM = 0
    ABRIR = 1
    FECHAR = 2


class Direcao(Enum):
    """Estados da ponte H (Seção 3.1)."""

    LIVRE = "livre"
    SUBIR = "subir"
    DESCER = "descer"
    FREIO = "freio"

    def niveis_gpio(self) -> tuple[int, int]:
        """Níveis lógicos (DIR1, DIR2)."""
        return {
            Direcao.LIVRE: (0, 0),
            Direcao.SUBIR: (1, 0),
            Direcao.DESCER: (0, 1),
            Direcao.FREIO: (1, 1),
        }[self]

    @classmethod
    def de_niveis(cls, dir1: int, dir2: int) -> "Direcao":
        return {(0, 0): cls.LIVRE, (1, 0): cls.SUBIR, (0, 1): cls.DESCER}.get(
            (int(bool(dir1)), int(bool(dir2))), cls.FREIO
        )


class Cenario(IntEnum):
    """Valores de `cenario_ativo` (Seção 7.6)."""

    NENHUM = 0
    UP_PEAK = 1
    DOWN_PEAK = 2
    INTERFLOOR = 3


def duty_max_da_cota(cota_ma: float) -> float:
    """Converte a Cota de Potência em limite de duty (Seção 7.2)."""
    return max(0.0, min(100.0, cota_ma / CORRENTE_MAX_CABINE_MA * 100.0))


DUTY_MAX_DEGRADADO = duty_max_da_cota(COTA_DEGRADADA_MA)


def barramento_com_derating(temp_c: float, watchdog_expirado: bool = False) -> int:
    """Capacidade do barramento conforme a Seção 7.4."""
    if watchdog_expirado:
        return PISO_BARRAMENTO_MA
    capacidade = CAPACIDADE_NOMINAL_MA - 200 * max(0.0, temp_c - 25.0)
    return int(max(PISO_BARRAMENTO_MA, capacidade))
