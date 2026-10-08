"""tests/test_parte1.py - Testes unitários do Protocolo Simplificado (Parte 1)."""

import struct

import pytest

from src.parte1_simplificado import ProtocoloSimplificado
from src.uart_driver import FakeUartDriver


@pytest.fixture
def fake_driver():
    return FakeUartDriver()


@pytest.fixture
def protocolo(fake_driver):
    return ProtocoloSimplificado(driver=fake_driver, matricula=[6, 5, 4, 3, 2, 1])


def test_solicita_inteiro(protocolo, fake_driver):
    """Comando 0xA1: envia 7 bytes e decodifica int32 LE."""
    val = protocolo.solicita_inteiro()
    assert val == 42
    tx = fake_driver.historico_tx[-1]
    assert tx == bytes([0xA1, 6, 5, 4, 3, 2, 1])


def test_solicita_float(protocolo, fake_driver):
    """Comando 0xA2: envia 7 bytes e decodifica float LE."""
    val = protocolo.solicita_float()
    assert pytest.approx(val, rel=1e-4) == 3.14159
    tx = fake_driver.historico_tx[-1]
    assert tx == bytes([0xA2, 6, 5, 4, 3, 2, 1])


def test_solicita_string(protocolo, fake_driver):
    """Comando 0xA3: envia 7 bytes e decodifica string [len][chars]."""
    texto = protocolo.solicita_string()
    assert texto == "FSE_2026_2"
    tx = fake_driver.historico_tx[-1]
    assert tx == bytes([0xA3, 6, 5, 4, 3, 2, 1])


def test_envia_inteiro(protocolo, fake_driver):
    """Comando 0xB1: envia 11 bytes com int32 LE e decodifica eco multiplicado."""
    # Último dígito da matrícula é 1, então 3245 * 1 = 3245
    val = protocolo.envia_inteiro(3245)
    assert val == 3245
    tx = fake_driver.historico_tx[-1]
    assert len(tx) == 11
    assert tx[0] == 0xB1
    assert struct.unpack("<i", tx[1:5])[0] == 3245
    assert tx[5:] == bytes([6, 5, 4, 3, 2, 1])


def test_envia_float(protocolo, fake_driver):
    """Comando 0xB2: envia 11 bytes com float LE e decodifica eco multiplicado."""
    val = protocolo.envia_float(15.5)
    assert pytest.approx(val, rel=1e-4) == 15.5
    tx = fake_driver.historico_tx[-1]
    assert len(tx) == 11
    assert tx[0] == 0xB2
    assert pytest.approx(struct.unpack("<f", tx[1:5])[0], rel=1e-4) == 15.5
    assert tx[5:] == bytes([6, 5, 4, 3, 2, 1])


def test_envia_string(protocolo, fake_driver):
    """Comando 0xB3: envia tamanho + texto UTF-8 + matrícula."""
    texto_envio = "TesteUART"
    retorno = protocolo.envia_string(texto_envio)
    assert retorno == f"Resposta da UART: {texto_envio}"
    tx = fake_driver.historico_tx[-1]
    assert tx[0] == 0xB3
    assert tx[1] == len(texto_envio.encode("utf-8"))
    assert tx[2 : 2 + len(texto_envio)] == texto_envio.encode("utf-8")
    assert tx[2 + len(texto_envio) :] == bytes([6, 5, 4, 3, 2, 1])


def test_timeout_em_solicita_inteiro(protocolo, fake_driver):
    """Lança TimeoutError quando não recebe resposta."""
    fake_driver.simular_timeout = True
    with pytest.raises(TimeoutError, match="esperado 4 bytes"):
        protocolo.solicita_inteiro()


def test_string_com_tamanho_invalido(protocolo, fake_driver):
    """Lança ValueError quando o byte de tamanho é inconsistente com os bytes recebidos."""
    # Byte de tamanho declara 10 bytes, mas apenas 3 são enviados
    fake_driver.resposta_injetada = bytes([10, 0x41, 0x42, 0x43])
    with pytest.raises(ValueError, match="Tamanho de string inconsistente"):
        protocolo.solicita_string()
