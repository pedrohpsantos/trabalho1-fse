"""tests/test_parte3.py - Testes unitários do Simulador MODBUS RTU (Parte 3)."""

import struct

import pytest

from src.crc import anexar_crc16, verificar_crc16
from src.parte3_simulador import SimuladorModbusClient
from src.uart_driver import FakeUartDriver


@pytest.fixture
def fake_driver():
    return FakeUartDriver()


@pytest.fixture
def client(fake_driver):
    return SimuladorModbusClient(
        driver=fake_driver,
        matricula=[6, 5, 4, 3, 2, 1],
        max_tentativas=3,
        intervalo_retry_s=0.001,
    )


def test_read_holding_registers_endianness_e_quadro(client, fake_driver):
    """Primitiva 0x03: valida envio de reg/qtd em LE e decodificação de valores em BE."""
    # Lê 2 registradores da Cabine 1 (offsets 0 e 1: andar_atual e nivelado)
    vals = client.read_holding_registers(addr=0x11, reg_inicial=0, qtd=2)
    assert len(vals) == 2
    assert vals[0] == 0  # andar_atual = 0
    assert vals[1] == 1  # nivelado = 1

    tx = fake_driver.historico_tx[-1]
    # Tamanho do quadro 0x03: addr(1) + func(1) + reg(2) + qtd(2) + mat(6) + crc(2) = 14 bytes
    assert len(tx) == 14
    assert tx[:2] == bytes([0x11, 0x03])
    # reg_inicial e qtd em Little-Endian na requisição:
    assert tx[2:4] == struct.pack("<H", 0)
    assert tx[4:6] == struct.pack("<H", 2)
    assert tx[6:12] == bytes([6, 5, 4, 3, 2, 1])
    assert verificar_crc16(tx) is True


def test_write_multiple_registers_endianness_e_eco(client, fake_driver):
    """Primitiva 0x10: valida reg/qtd/valores em LE no envio e eco conferido em BE."""
    # Escreve nos registradores 5 e 6 do prédio: temp=253 e press=1013
    client.write_multiple_registers(addr=0x20, reg_inicial=5, valores=[253, 1013])

    tx = fake_driver.historico_tx[-1]
    # Tamanho do quadro 0x10: addr(1) + func(1) + reg(2) + qtd(2) + bc(1) + 2*qtd(4) + mat(6) + crc(2) = 19 bytes
    assert len(tx) == 19
    assert tx[:2] == bytes([0x20, 0x10])
    # reg e qtd em LE:
    assert tx[2:4] == struct.pack("<H", 5)
    assert tx[4:6] == struct.pack("<H", 2)
    assert tx[6] == 4  # byte_count = 2 * 2
    # Valores gravados em LE na requisição:
    assert tx[7:9] == struct.pack("<H", 253)
    assert tx[9:11] == struct.pack("<H", 1013)
    # Matrícula antes do CRC:
    assert tx[11:17] == bytes([6, 5, 4, 3, 2, 1])
    assert verificar_crc16(tx) is True


def test_posicao_mm_int16_com_sinal_inclusive_negativo(client, fake_driver):
    """Garante que a posição da cabine (offset 7) é convertida para int16 com sinal (poço negativo)."""
    # Define posição positiva na cabine 1
    fake_driver.cabines[0x11][7] = 120
    estado = client.le_estado_cabine(1)
    assert estado["posicao_mm"] == 120

    # Define posição negativa no poço (ex: -350 mm)
    # Em uint16: 65536 - 350 = 65186 (0xFEA2)
    pos_negativa_u16 = struct.unpack(">H", struct.pack(">h", -350))[0]
    fake_driver.cabines[0x11][7] = pos_negativa_u16
    estado_neg = client.le_estado_cabine(1)
    assert estado_neg["posicao_mm"] == -350


def test_retentativas_sucesso_na_segunda_tentativa(client, fake_driver):
    """Política de retentativas: recupera de 1 falha de CRC com sucesso na 2ª tentativa."""
    # Injeta falha de CRC na primeira resposta
    resp_corrompida = bytes([0x11, 0x03, 0x02, 0x00, 0x00, 0xDE, 0xAD])
    fake_driver.resposta_injetada = resp_corrompida

    # A função deve tentar novamente e obter sucesso com a resposta simulada padrão
    vals = client.read_holding_registers(0x11, reg_inicial=0, qtd=1)
    assert vals == [0]
    # Verificamos que foram feitas 2 transmissões
    assert len(fake_driver.historico_tx) >= 2


def test_retentativas_esgotadas_lanca_timeout_error(client, fake_driver):
    """Política de retentativas: esgota 3 tentativas de timeout e lança TimeoutError."""
    fake_driver.simular_timeout = True
    with pytest.raises(TimeoutError, match="após 3 tentativas"):
        client.read_holding_registers(0x11, reg_inicial=0, qtd=1)


def test_excecao_nao_deve_ser_repetida(client, fake_driver):
    """Respostas de exceção (MSB ativo) NÃO sofrem retentativa e lançam RuntimeError de imediato."""
    # Simula resposta de exceção 0x02 (Endereço inválido)
    resp_excecao = anexar_crc16(bytes([0x11, 0x83, 0x02]))
    fake_driver.resposta_injetada = resp_excecao

    historico_antes = len(fake_driver.historico_tx)
    with pytest.raises(RuntimeError, match="Exceção MODBUS 0x02"):
        client.read_holding_registers(0x11, reg_inicial=0, qtd=1)

    # Garante que houve APENAS 1 tentativa (não repetiu)
    assert len(fake_driver.historico_tx) == historico_antes + 1


def test_funcoes_de_dominio_cabine_e_porta(client):
    """Valida as funções le_estado_cabine e comanda_porta."""
    estado = client.le_estado_cabine(1)
    assert estado["cabine"] == 1
    assert estado["andar_atual"] == 0
    assert estado["nivelado"] is True
    assert estado["porta_estado"] == "Fechada"

    # Comanda abertura da porta (comando 1)
    client.comanda_porta(1, 1)
    estado_aberta = client.le_estado_cabine(1)
    assert estado_aberta["porta_estado"] == "Aberta"

    # Comanda fechamento da porta (comando 2)
    client.comanda_porta(1, 2)
    estado_fechada = client.le_estado_cabine(1)
    assert estado_fechada["porta_estado"] == "Fechada"

    # Comando inválido
    with pytest.raises(ValueError, match="Comando de porta inválido"):
        client.comanda_porta(1, 99)


def test_funcoes_de_dominio_predio_e_contorno(client):
    """Valida le_estado_predio e escreve_condicao_contorno."""
    predio = client.le_estado_predio()
    assert predio["chamadas_na_fila"] == 3
    assert predio["chamada_origem"] == 0
    assert predio["chamada_destino"] == 3
    assert predio["chamada_id"] == 101
    assert predio["barramento_max_ma"] == 12000
    assert predio["watchdog_ambiente"] == 0

    # Escreve nova condição de contorno (30.0 °C e 1010 hPa)
    client.escreve_condicao_contorno(30.0, 1010)
    predio_apos = client.le_estado_predio()
    # Derating térmico: 12000 - 200 * (30 - 25) = 11000 mA
    assert predio_apos["barramento_max_ma"] == 11000


def test_funcoes_de_dominio_fila_e_despacho(client):
    """Valida fluxo de consumo de chamadas: leitura, atribuição e remoção (pop)."""
    # 1. Lê chamada da cabeça da fila
    chamada = client.le_chamada_da_fila()
    assert chamada["pendentes"] == 3
    assert chamada["id"] == 101

    # 2. Atribui chamada à Cabine 2
    client.atribui_chamada(101, cabine=2)

    # 3. Remove chamada atendida da fila
    client.remove_chamada_da_fila()

    # Verifica que a fila avançou para a próxima chamada
    chamada_segunda = client.le_chamada_da_fila()
    assert chamada_segunda["pendentes"] == 2
    assert chamada_segunda["id"] == 102
