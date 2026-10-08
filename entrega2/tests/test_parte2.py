"""tests/test_parte2.py - Testes unitários do Wrapper MODBUS Modificado (Parte 2)."""

import struct

import pytest

from src.crc import anexar_crc16, verificar_crc16
from src.parte2_modbus_p2 import ProtocoloModbusP2
from src.uart_driver import FakeUartDriver


@pytest.fixture
def fake_driver():
    return FakeUartDriver()


@pytest.fixture
def protocolo(fake_driver):
    return ProtocoloModbusP2(
        driver=fake_driver, matricula=[6, 5, 4, 3, 2, 1], endereco=0x01
    )


def test_solicita_inteiro_modbus(protocolo, fake_driver):
    """Função 0x23 / Sub 0xA1: valida quadro com 11 bytes, matrícula antes do CRC e resposta validada."""
    val = protocolo.solicita_inteiro()
    assert val == 999
    tx = fake_driver.historico_tx[-1]
    assert len(tx) == 11
    assert tx[:3] == bytes([0x01, 0x23, 0xA1])
    assert tx[3:9] == bytes([6, 5, 4, 3, 2, 1])
    assert verificar_crc16(tx) is True


def test_solicita_float_modbus(protocolo, fake_driver):
    """Função 0x23 / Sub 0xA2: valida retorno de float."""
    val = protocolo.solicita_float()
    assert pytest.approx(val, rel=1e-4) == 27.85
    tx = fake_driver.historico_tx[-1]
    assert len(tx) == 11
    assert tx[:3] == bytes([0x01, 0x23, 0xA2])
    assert tx[3:9] == bytes([6, 5, 4, 3, 2, 1])
    assert verificar_crc16(tx) is True


def test_solicita_string_modbus(protocolo, fake_driver):
    """Função 0x23 / Sub 0xA3: valida retorno de string."""
    texto = protocolo.solicita_string()
    assert texto == "MODBUS_OK"
    tx = fake_driver.historico_tx[-1]
    assert len(tx) == 11
    assert tx[:3] == bytes([0x01, 0x23, 0xA3])
    assert tx[3:9] == bytes([6, 5, 4, 3, 2, 1])
    assert verificar_crc16(tx) is True


def test_envia_inteiro_modbus(protocolo, fake_driver):
    """Função 0x16 / Sub 0xB1: valida pacote com 15 bytes (dado int32 em Little-Endian)."""
    val = protocolo.envia_inteiro(3245)
    assert val == 3245
    tx = fake_driver.historico_tx[-1]
    assert len(tx) == 15
    assert tx[:3] == bytes([0x01, 0x16, 0xB1])
    # Valida Little-Endian do inteiro 3245: 0xAD 0x0C 0x00 0x00
    assert tx[3:7] == bytes([0xAD, 0x0C, 0x00, 0x00])
    assert tx[7:13] == bytes([6, 5, 4, 3, 2, 1])
    assert verificar_crc16(tx) is True


def test_envia_float_modbus(protocolo, fake_driver):
    """Função 0x16 / Sub 0xB2: valida pacote com float em Little-Endian."""
    val = protocolo.envia_float(10.5)
    assert pytest.approx(val, rel=1e-4) == 10.5
    tx = fake_driver.historico_tx[-1]
    assert len(tx) == 15
    assert tx[:3] == bytes([0x01, 0x16, 0xB2])
    assert pytest.approx(struct.unpack("<f", tx[3:7])[0], rel=1e-4) == 10.5
    assert tx[7:13] == bytes([6, 5, 4, 3, 2, 1])
    assert verificar_crc16(tx) is True


def test_envia_string_modbus(protocolo, fake_driver):
    """Função 0x16 / Sub 0xB3: valida pacote com string e eco processado."""
    retorno = protocolo.envia_string("Ping")
    assert retorno == "MODBUS Eco: Ping"
    tx = fake_driver.historico_tx[-1]
    assert tx[:4] == bytes([0x01, 0x16, 0xB3, 4])
    assert tx[4:8] == b"Ping"
    assert tx[8:14] == bytes([6, 5, 4, 3, 2, 1])
    assert verificar_crc16(tx) is True


def test_crc_invalido_na_resposta_modbus(protocolo, fake_driver):
    """Garante que resposta com CRC corrompido é rejeitada com ValueError."""
    resp_corrompida = bytes([0x01, 0x23, 0x00, 0x00, 0x00, 0x00, 0xAA, 0xBB])
    fake_driver.resposta_injetada = resp_corrompida
    with pytest.raises(ValueError, match="CRC-16 inválido"):
        protocolo.solicita_inteiro()


def test_resposta_excecao_modbus(protocolo, fake_driver):
    """Garante que resposta com MSB ativo (ex: 0xA3 = 0x23 | 0x80) lança RuntimeError com a exceção."""
    resp_excecao = anexar_crc16(bytes([0x01, 0x23 | 0x80, 0x02]))
    fake_driver.resposta_injetada = resp_excecao
    with pytest.raises(
        RuntimeError, match="Exceção MODBUS recebida: Função 0xA3, Código 0x02"
    ):
        protocolo.solicita_inteiro()


def test_timeout_modbus(protocolo, fake_driver):
    """Garante lançamento de TimeoutError quando não há resposta."""
    fake_driver.simular_timeout = True
    with pytest.raises(TimeoutError, match="timeout"):
        protocolo.solicita_inteiro()
