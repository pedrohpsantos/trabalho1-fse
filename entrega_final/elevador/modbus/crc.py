"""CRC-16 do simulador (polinômio refletido 0xA001, valor inicial 0x0000), em little-endian.

É o `crc16.c` de referência da disciplina (CRC-16/ARC). NÃO é o CRC-16/MODBUS padrão
(init 0xFFFF): com init 0xFFFF o simulador descarta todos os quadros. Os quadros literais
do enunciado (Entrega 2, Seção 3.3) são verificados em `tests/test_modbus.py`.
"""


def _gerar_tabela() -> tuple[int, ...]:
    tabela = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        tabela.append(crc)
    return tuple(tabela)


_TABELA = _gerar_tabela()


def calcular_crc16(dados: bytes) -> int:
    crc = 0x0000
    for byte in dados:
        crc = (crc >> 8) ^ _TABELA[(crc ^ byte) & 0xFF]
    return crc


def anexar_crc16(dados: bytes) -> bytes:
    crc = calcular_crc16(dados)
    return dados + bytes((crc & 0xFF, crc >> 8))


def verificar_crc16(quadro: bytes) -> bool:
    if len(quadro) < 3:
        return False
    return calcular_crc16(quadro[:-2]) == (quadro[-2] | (quadro[-1] << 8))
