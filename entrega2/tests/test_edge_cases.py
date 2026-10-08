"""tests/test_edge_cases.py - Testes de casos de borda, validações negativas e importabilidade."""

import subprocess
import sys

import pytest

from src.crc import anexar_crc16
from src.parte3_simulador import SimuladorModbusClient
from src.uart_driver import FakeUartDriver


@pytest.fixture
def fake_driver():
    return FakeUartDriver()


@pytest.fixture
def client(fake_driver):
    return SimuladorModbusClient(
        driver=fake_driver, matricula=[6, 5, 4, 3, 2, 1], max_tentativas=1
    )


def test_importabilidade_parte3():
    """Garante que a Parte 3 pode ser importada e instanciada isoladamente para a Entrega Final."""
    from src import SimuladorModbusClient as ImportedClient
    from src.uart_driver import FakeUartDriver as ImportedDriver

    drv = ImportedDriver()
    c = ImportedClient(drv)
    assert c is not None
    assert hasattr(c, "read_holding_registers")
    assert hasattr(c, "write_multiple_registers")
    assert hasattr(c, "le_estado_cabine")
    assert hasattr(c, "le_estado_predio")


def test_quantidade_zero_lanca_value_error(client):
    """Garante que solicitar quantidade zero em 0x03 lança ValueError."""
    with pytest.raises(ValueError, match="maior que zero"):
        client.read_holding_registers(0x11, reg_inicial=0, qtd=0)


def test_lista_valores_vazia_em_0x10_lanca_value_error(client):
    """Garante que escrever lista vazia em 0x10 lança ValueError."""
    with pytest.raises(ValueError, match="não pode ser vazia"):
        client.write_multiple_registers(0x11, reg_inicial=3, valores=[])


def test_endereco_invalido_na_resposta_0x03(client, fake_driver):
    """Garante que resposta com endereço diferente da requisição é rejeitada."""
    # Requisição foi para 0x11, mas resposta injetada vem com endereço 0x12
    resp_outro_addr = anexar_crc16(bytes([0x12, 0x03, 0x02, 0x00, 0x05]))
    fake_driver.resposta_injetada = resp_outro_addr
    with pytest.raises(TimeoutError, match="Endereço divergente"):
        client.read_holding_registers(0x11, reg_inicial=0, qtd=1)


def test_funcao_invalida_na_resposta_0x03(client, fake_driver):
    """Garante que resposta com código de função inesperado é rejeitada."""
    resp_outra_func = anexar_crc16(bytes([0x11, 0x04, 0x02, 0x00, 0x05]))
    fake_driver.resposta_injetada = resp_outra_func
    with pytest.raises(TimeoutError, match="Código de função divergente"):
        client.read_holding_registers(0x11, reg_inicial=0, qtd=1)


def test_byte_count_incorreto_na_resposta_0x03(client, fake_driver):
    """Garante que resposta com byte_count diferente de 2*qtd é rejeitada."""
    # qtd=2 exige byte_count=4, mas resposta envia byte_count=2
    resp_bc_errado = anexar_crc16(bytes([0x11, 0x03, 0x02, 0x00, 0x05]))
    fake_driver.resposta_injetada = resp_bc_errado
    with pytest.raises(TimeoutError, match="byte_count inconsistente"):
        client.read_holding_registers(0x11, reg_inicial=0, qtd=2)


def test_eco_incorreto_na_escrita_0x10(client, fake_driver):
    """Garante que eco com reg ou qtd divergente é rejeitado."""
    # Escrita pediu reg=5, qtd=1, mas eco retorna reg=6, qtd=1 em BE
    resp_eco_errado = anexar_crc16(bytes([0x20, 0x10, 0x00, 0x06, 0x00, 0x01]))
    fake_driver.resposta_injetada = resp_eco_errado
    with pytest.raises(TimeoutError, match="Eco divergente"):
        client.write_multiple_registers(0x20, reg_inicial=5, valores=[250])


def test_excecao_0x01_funcao_invalida(client, fake_driver):
    """Garante tratamento da exceção 0x01 (Função inválida)."""
    resp_exc = anexar_crc16(bytes([0x11, 0x83, 0x01]))
    fake_driver.resposta_injetada = resp_exc
    with pytest.raises(RuntimeError, match="Função inválida"):
        client.read_holding_registers(0x11, reg_inicial=0, qtd=1)


def test_excecao_0x03_valor_invalido(client, fake_driver):
    """Garante tratamento da exceção 0x03 (Valor inválido)."""
    resp_exc = anexar_crc16(bytes([0x20, 0x90, 0x03]))
    fake_driver.resposta_injetada = resp_exc
    with pytest.raises(RuntimeError, match="Valor inválido"):
        client.write_multiple_registers(0x20, reg_inicial=10, valores=[99])


def test_cli_ajuda():
    """Garante que main.py pode ser executado com --help e exibe a descrição."""
    res = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "CLI da Entrega 2" in res.stdout
    assert "--mock" in res.stdout
    assert "--matricula" in res.stdout
