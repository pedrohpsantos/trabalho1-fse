"""tests/test_crc.py - Testes unitários do cálculo e validação do CRC-16."""

from src.crc import anexar_crc16, calcular_crc16, verificar_crc16


def test_crc16_vetores_oficiais_entrega2():
    """Valida o cálculo do CRC-16 contra todos os exemplos documentados em Entrega_2.md."""
    # Exemplo 1 (Linha 194 de Entrega_2.md):
    # 0x11 0x03 0x00 0x00 0x09 0x00 0x06 0x05 0x04 0x03 0x02 0x01 [0x0E 0xE4]
    p194 = bytes(
        [0x11, 0x03, 0x00, 0x00, 0x09, 0x00, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01]
    )
    crc194 = calcular_crc16(p194)
    assert crc194 == 0xE40E
    assert anexar_crc16(p194)[-2:] == bytes([0x0E, 0xE4])
    assert verificar_crc16(anexar_crc16(p194)) is True

    # Exemplo 2 (Linha 202 de Entrega_2.md):
    # 0x20 0x10 0x05 0x00 0x02 0x00 0x04 0xFD 0x00 0xF5 0x03 0x06 0x05 0x04 0x03 0x02 0x01 [0x56 0xC3]
    p202 = bytes(
        [
            0x20,
            0x10,
            0x05,
            0x00,
            0x02,
            0x00,
            0x04,
            0xFD,
            0x00,
            0xF5,
            0x03,
            0x06,
            0x05,
            0x04,
            0x03,
            0x02,
            0x01,
        ]
    )
    crc202 = calcular_crc16(p202)
    assert crc202 == 0xC356
    assert anexar_crc16(p202)[-2:] == bytes([0x56, 0xC3])
    assert verificar_crc16(anexar_crc16(p202)) is True

    # Exemplo 3 (Linha 204 de Entrega_2.md):
    # Resposta de eco: 0x20 0x10 0x00 0x05 0x00 0x02 [0x57 0x63]
    p204 = bytes([0x20, 0x10, 0x00, 0x05, 0x00, 0x02])
    crc204 = calcular_crc16(p204)
    assert crc204 == 0x6357
    assert anexar_crc16(p204)[-2:] == bytes([0x57, 0x63])
    assert verificar_crc16(anexar_crc16(p204)) is True

    # Exemplo 4 (Linha 211 de Entrega_2.md):
    # Resposta de exceção: 0x11 0x83 0x02 [0xB0 0xF4]
    p211 = bytes([0x11, 0x83, 0x02])
    crc211 = calcular_crc16(p211)
    assert crc211 == 0xF4B0
    assert anexar_crc16(p211)[-2:] == bytes([0xB0, 0xF4])
    assert verificar_crc16(anexar_crc16(p211)) is True


def test_verificar_crc16_corrompido():
    """Garante que qualquer alteração de 1 byte no pacote invalida a checagem."""
    payload = bytes([0x01, 0x23, 0xA1, 6, 5, 4, 3, 2, 1])
    pct_valido = anexar_crc16(payload)
    assert verificar_crc16(pct_valido) is True

    # Corrompe o payload
    pct_corrompido_payload = bytes([0x02]) + pct_valido[1:]
    assert verificar_crc16(pct_corrompido_payload) is False

    # Corrompe o byte menos significativo do CRC
    pct_corrompido_crc = pct_valido[:-2] + bytes(
        [pct_valido[-2] ^ 0xFF, pct_valido[-1]]
    )
    assert verificar_crc16(pct_corrompido_crc) is False


def test_verificar_crc16_tamanho_insuficiente():
    """Retorna False para dados com menos de 2 bytes."""
    assert verificar_crc16(b"") is False
    assert verificar_crc16(b"\x01") is False
