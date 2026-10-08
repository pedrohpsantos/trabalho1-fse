"""src/config.py - Configurações centrais do sistema da Entrega 2.

Centraliza a matrícula de 6 dígitos, parâmetros da UART e constantes de protocolo.
"""

# Matrícula padrão: 6 últimos dígitos numéricos da matrícula do estudante.
# Cada dígito DEVE ser um inteiro de 0 a 9, transmitido como byte bruto (ex: 0x06, não '6'/0x36).
MATRICULA_PADRAO: list[int] = [6, 5, 4, 3, 2, 1]

# Parâmetros padrão da UART
UART_PORTA_PADRAO: str = "/dev/serial0"
UART_BAUDRATE: int = 115200
UART_TIMEOUT_PADRAO: float = (
    0.5  # 500 ms (faixa de 200 a 500 ms conforme especificação)
)

# Endereços dos dispositivos MODBUS
ADDR_DISPOSITIVO_DIDATICO: int = 0x01
ADDR_CABINE_1: int = 0x11
ADDR_CABINE_2: int = 0x12
ADDR_CABINE_3: int = 0x13
ADDR_PREDIO: int = 0x20

# Códigos de Exceção MODBUS
EXCECOES_MODBUS = {
    0x01: "Função inválida (função diferente de 0x03 e 0x10 em dispositivo real)",
    0x02: "Endereço inválido / registrador somente-leitura / quantidade zero",
    0x03: "Valor inválido (ex: comando de porta ou atribuição fora da faixa permitida)",
}


def validar_e_obter_matricula(
    matricula: list[int] | str | bytes | None = None,
) -> bytes:
    """Valida que a matrícula possui exatamente 6 dígitos numéricos inteiros (0 a 9)

    e retorna os 6 bytes brutos correspondentes (nunca como caracteres ASCII).

    Args:
        matricula: Lista de 6 inteiros, string de 6 dígitos ou bytes. Se None, usa MATRICULA_PADRAO.

    Returns:
        bytes: Sequência exata de 6 bytes com valores entre 0x00 e 0x09.

    Raises:
        ValueError: Caso a matrícula não possua 6 dígitos ou contenha caracteres inválidos.
    """
    if matricula is None:
        digitos = list(MATRICULA_PADRAO)
    elif isinstance(matricula, (list, tuple)):
        digitos = list(matricula)
    elif isinstance(matricula, str):
        matricula_limpa = matricula.strip()
        if not matricula_limpa.isdigit():
            raise ValueError(
                f"Matrícula deve conter apenas dígitos numéricos, recebido: '{matricula}'"
            )
        digitos = [int(c) for c in matricula_limpa]
    elif isinstance(matricula, (bytes, bytearray)):
        digitos = list(matricula)
    else:
        raise ValueError(f"Tipo inválido para matrícula: {type(matricula)}")

    if len(digitos) != 6:
        raise ValueError(
            f"A matrícula deve conter exatamente 6 dígitos, recebido: {len(digitos)}"
        )

    for idx, d in enumerate(digitos):
        if not isinstance(d, int) or d < 0 or d > 9:
            raise ValueError(
                f"Dígito #{idx} inválido: {d} (cada dígito deve ser um inteiro entre 0 e 9, sem codificação ASCII)"
            )

    return bytes(digitos)
