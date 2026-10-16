"""CRC, enquadramento MODBUS (endianness, matrícula), retentativas e exceções."""

import struct

import pytest

from elevador.constantes import ENDERECO_PREDIO, PortaComando
from elevador.modbus.cliente import ClienteModbus, ErroModbus, ExcecaoModbus, matricula_em_bytes
from elevador.modbus.crc import anexar_crc16, calcular_crc16, verificar_crc16
from elevador.modbus.simulador import ApiSimulador
from elevador.sim.bancada import Bancada
from elevador.sim.uart_sim import InjetorFalhas, UartVirtual


class UartEspiao(UartVirtual):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.enviados: list[bytes] = []

    def transacionar(self, quadro, tamanho_esperado):
        self.enviados.append(quadro)
        return super().transacionar(quadro, tamanho_esperado)


@pytest.fixture
def bancada():
    return Bancada()


def cliente_para(bancada, falhas=None, tentativas=3):
    uart = UartEspiao(bancada, falhas)
    return ClienteModbus(uart, "038442", tentativas=tentativas, intervalo_retry_s=0), uart


def test_crc16_vetor_conhecido():
    assert calcular_crc16(b"123456789") == 0xBB3D  # CRC-16/ARC (init 0x0000), não CRC-16/MODBUS


@pytest.mark.parametrize(
    "quadro",
    [
        # Exemplos literais do enunciado (Entrega 2, Seção 3.3), matrícula 654321.
        "11 03 00 00 09 00 06 05 04 03 02 01 0E E4",
        "20 10 05 00 02 00 04 FD 00 F5 03 06 05 04 03 02 01 56 C3",
        "20 10 00 05 00 02 57 63",
        "11 83 02 B0 F4",
    ],
)
def test_crc16_bate_com_os_quadros_do_enunciado(quadro):
    assert verificar_crc16(bytes.fromhex(quadro))


def test_quadros_gerados_pelo_cliente_identicos_ao_enunciado(bancada):
    uart = UartEspiao(bancada)
    cliente = ClienteModbus(uart, "654321", intervalo_retry_s=0)
    cliente.ler_registradores(0x11, 0, 9)
    cliente.escrever_registradores(ENDERECO_PREDIO, 5, [253, 1013])
    assert uart.enviados[0] == bytes.fromhex("11 03 00 00 09 00 06 05 04 03 02 01 0E E4")
    assert uart.enviados[1] == bytes.fromhex("20 10 05 00 02 00 04 FD 00 F5 03 06 05 04 03 02 01 56 C3")


def test_crc_anexo_little_endian_e_verificacao():
    quadro = anexar_crc16(b"\x11\x03\x00\x00")
    crc = calcular_crc16(b"\x11\x03\x00\x00")
    assert quadro[-2:] == bytes((crc & 0xFF, crc >> 8))
    assert verificar_crc16(quadro)
    assert not verificar_crc16(quadro[:-1] + b"\x00")
    assert not verificar_crc16(b"\x01")


def test_matricula_bytes_crus_nao_ascii():
    assert matricula_em_bytes("038442") == bytes([0, 3, 8, 4, 4, 2])
    for invalida in ("12345", "1234567", "12a456", [1, 2, 3, 4, 5, 10]):
        with pytest.raises(ValueError):
            matricula_em_bytes(invalida)


def test_leitura_0x03_formato_da_requisicao_e_valores_big_endian(bancada):
    cliente, uart = cliente_para(bancada)
    regs = cliente.ler_registradores(0x11, 0, 9)
    req = uart.enviados[0]
    assert req[:2] == b"\x11\x03"
    assert struct.unpack("<HH", req[2:6]) == (0, 9)  # reg/qtd em little-endian
    assert req[6:12] == bytes([0, 3, 8, 4, 4, 2])  # matrícula antes do CRC
    assert verificar_crc16(req) and len(req) == 14
    assert len(regs) == 9 and regs[0] == 0  # cabine começa no andar 0


def test_escrita_0x10_formato_e_efeito(bancada):
    fis = bancada.cabines[1]
    fis.x = fis.geometria.andares_mm[0]  # nivelada: o simulador só abre a porta no nível
    cliente, uart = cliente_para(bancada)
    cliente.escrever_registradores(0x11, 3, [int(PortaComando.ABRIR)])
    req = uart.enviados[0]
    assert req[:2] == b"\x11\x10"
    assert struct.unpack("<HHB", req[2:7]) == (3, 1, 2)
    assert struct.unpack("<H", req[7:9]) == (1,)  # valor em little-endian
    assert req[9:15] == bytes([0, 3, 8, 4, 4, 2])
    assert bancada.cabines[1].porta != 0


def test_retentativa_por_crc_invalido_e_timeout(bancada):
    falhas = InjetorFalhas()
    cliente, uart = cliente_para(bancada, falhas)
    falhas.corromper, falhas.descartar = 1, 1
    assert cliente.ler_registradores(0x11, 0, 3)  # 1 corrompida + 1 muda + 1 boa
    assert len(uart.enviados) == 3


def test_falha_apos_tres_tentativas(bancada):
    falhas = InjetorFalhas()
    falhas.descartar = 10
    cliente, uart = cliente_para(bancada, falhas)
    with pytest.raises(ErroModbus):
        cliente.ler_registradores(0x11, 0, 3)
    assert len(uart.enviados) == 3


def test_excecao_nao_e_repetida(bancada):
    cliente, uart = cliente_para(bancada)
    with pytest.raises(ExcecaoModbus) as erro:
        cliente.escrever_registradores(0x11, 0, [1])  # registrador somente leitura
    assert erro.value.codigo == 0x02
    assert len(uart.enviados) == 1
    with pytest.raises(ExcecaoModbus) as erro:
        cliente.escrever_registradores(0x11, 3, [7])  # comando de porta inválido
    assert erro.value.codigo == 0x03
    assert len(uart.enviados) == 2


def test_dispositivo_ignora_quadro_sem_matricula(bancada):
    from elevador.sim.modbus_sim import processar_quadro

    sem = anexar_crc16(bytes((0x11, 0x03)) + struct.pack("<HH", 0, 1))
    assert processar_quadro(bancada, sem) == b""


def test_api_condicao_de_contorno_e_atribuicao(bancada):
    cliente, _ = cliente_para(bancada)
    api = ApiSimulador(cliente)
    api.escrever_condicao_contorno(31.4, 1008.6)
    predio = api.ler_predio()
    assert bancada.predio.temp_x10 == 314 and bancada.predio.pressao_hpa == 1009
    assert predio.barramento_max_ma == 10720  # 12000 - 200 * (31,4 - 25)
    id_ = bancada.registrar_chamada(0, 3)
    assert api.ler_predio().chamadas_na_fila == 1
    api.atribuir_chamada(id_, 2)
    api.remover_chamada_da_fila()
    assert api.ler_predio().chamadas_na_fila == 0
    assert bancada.predio.chamadas[id_].cabine == 2
    with pytest.raises(ValueError):
        api.atribuir_chamada(id_, 4)


def test_leitura_predio_endereco_correto(bancada):
    cliente, uart = cliente_para(bancada)
    ApiSimulador(cliente).ler_predio()
    assert uart.enviados[0][0] == ENDERECO_PREDIO


def test_uart_serial_reabre_a_porta_apos_erro_de_e_s(monkeypatch):
    serial = pytest.importorskip("serial")
    from elevador.modbus import uart as modulo_uart

    resposta = anexar_crc16(b"\x20\x10\x00\x05\x00\x02")
    aberturas = []

    class SerialFalsa:
        def __init__(self, **_k):
            aberturas.append(self)
            self.is_open = True
            self._buf = b""

        def reset_input_buffer(self):
            self._buf = b""

        def reset_output_buffer(self):
            pass

        def write(self, _quadro):
            if len(aberturas) == 1:
                raise serial.SerialException("device reports readiness to read but returned no data")
            self._buf = resposta

        def flush(self):
            pass

        def read(self, n):
            dados, self._buf = self._buf[:n], self._buf[n:]
            return dados

        def close(self):
            self.is_open = False

    monkeypatch.setattr(modulo_uart.serial, "Serial", SerialFalsa)
    monkeypatch.setattr(modulo_uart.time, "sleep", lambda _s: None)
    cliente = ClienteModbus(modulo_uart.UartSerial("/dev/null", 0.2), "038442", intervalo_retry_s=0)
    cliente.escrever_registradores(ENDERECO_PREDIO, 5, [253, 1013])  # 1ª tentativa falha, 2ª reabre
    assert len(aberturas) == 2 and not aberturas[0].is_open
