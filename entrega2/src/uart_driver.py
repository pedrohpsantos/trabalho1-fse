"""src/uart_driver.py - Driver de comunicação UART com suporte a hardware e simulação.

Gerencia o tráfego de baixo nível, formatação hexadecimal obrigatória e timeouts.
"""

import abc
import struct
import time

from .crc import anexar_crc16, verificar_crc16

# Tentativa condicional de importar pyserial
try:
    import serial

    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    SERIAL_AVAILABLE = False


class BaseUartDriver(abc.ABC):
    """Interface abstrata para o driver UART."""

    def __init__(
        self, porta: str = "/dev/serial0", baudrate: int = 115200, timeout: float = 0.5
    ):
        self.porta = porta
        self.baudrate = baudrate
        self.timeout = timeout
        self.aberto = False

    @abc.abstractmethod
    def conectar(self) -> None:
        """Abre a conexão com o dispositivo serial."""

    @abc.abstractmethod
    def fechar(self) -> None:
        """Fecha a conexão com o dispositivo serial."""

    @abc.abstractmethod
    def enviar_e_receber(
        self, pacote: bytes, tamanho_esperado: int | None = None
    ) -> bytes:
        """Transmite um pacote e aguarda a resposta."""


class HardwareUartDriver(BaseUartDriver):
    """Driver de comunicação física com a porta serial da Raspberry Pi via pyserial."""

    def __init__(
        self, porta: str = "/dev/serial0", baudrate: int = 115200, timeout: float = 0.5
    ):
        super().__init__(porta, baudrate, timeout)
        if not SERIAL_AVAILABLE:
            raise RuntimeError(
                "O pacote 'pyserial' não está instalado. Instale com 'pip install pyserial' ou execute com --mock."
            )
        self.serial_conn: serial.Serial | None = None

    def conectar(self) -> None:
        if self.serial_conn is None or not self.serial_conn.is_open:
            self.serial_conn = serial.Serial(
                port=self.porta,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
            )
            self.serial_conn.reset_input_buffer()
            self.serial_conn.reset_output_buffer()
            self.aberto = True

    def fechar(self) -> None:
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            self.serial_conn = None
        self.aberto = False

    def enviar_e_receber(
        self, pacote: bytes, tamanho_esperado: int | None = None
    ) -> bytes:
        self.conectar()
        try:
            self.serial_conn.reset_input_buffer()
            print(f"[TX] ({len(pacote):02d} B): {pacote.hex(' ').upper()}")
            self.serial_conn.write(pacote)
            self.serial_conn.flush()

            if tamanho_esperado is not None:
                resposta = self.serial_conn.read(tamanho_esperado)
            else:
                resposta = bytearray()
                t0 = time.time()
                while time.time() - t0 < self.timeout:
                    esperando = self.serial_conn.in_waiting
                    if esperando > 0:
                        resposta.extend(self.serial_conn.read(esperando))
                        time.sleep(0.02)
                    elif len(resposta) > 0:
                        break
                    time.sleep(0.01)
                resposta = bytes(resposta)

            if resposta:
                print(f"[RX] ({len(resposta):02d} B): {resposta.hex(' ').upper()}")
            else:
                print("[RX] (00 B): <TIMEOUT>")
            return resposta
        except Exception as e:
            print(f"[ERRO UART] Falha na comunicação física: {e}")
            raise


class FakeUartDriver(BaseUartDriver):
    """Driver simulador que replica com fidelidade o comportamento da ESP32 do laboratório.

    Permite testar e executar toda a CLI e os testes unitários sem Raspberry Pi física.
    """

    def __init__(self, porta: str = "FAKE_UART", timeout: float = 0.5):
        super().__init__(porta=porta, baudrate=115200, timeout=timeout)
        self.aberto = True

        # Hook para injeção de falhas em testes unitários
        self.resposta_injetada: bytes | None = None
        self.simular_timeout: bool = False
        self.historico_tx: list[bytes] = []

        # Estado simulado das cabines (0x11, 0x12, 0x13)
        # Offsets 0..8:
        # [andar_atual, nivelado, porta_estado, porta_comando, corrente_ma, carga_kg, passageiros, posicao_mm, falha]
        self.cabines: dict[int, list[int]] = {
            0x11: [0, 1, 0, 0, 2400, 450, 3, 150, 0],  # Cabine 1 (posicao 150 mm)
            0x12: [2, 1, 0, 0, 0, 0, 0, 6000, 0],  # Cabine 2
            0x13: [5, 1, 0, 0, 1200, 150, 1, 15000, 0],  # Cabine 3
        }

        # Fila de chamadas do prédio: lista de tuplas (origem, destino, id)
        self.fila_chamadas: list[tuple] = [
            (0, 3, 101),
            (2, 0, 102),
            (4, 1, 103),
        ]

        # Estado simulado do prédio (0x20)
        # Offsets 0..15:
        # 0: chamadas_na_fila, 1: origem, 2: destino, 3: id, 4: pop (W), 5: temp (W), 6: press (W),
        # 7: barramento, 8: wd, 9: atribuicao_id (W), 10: atribuicao_cabine (W),
        # 11: cenario, 12: geradas, 13: atendidas, 14: espera_x10, 15: viagem_x10
        self.temp_c_x10 = 250
        self.press_hpa = 1013
        self.watchdog_expirado = 0
        self.cenario_ativo = 1
        self.cenario_geradas = 20
        self.cenario_atendidas = 16
        self.espera_media_s_x10 = 145  # 14.5 s
        self.viagem_media_s_x10 = 230  # 23.0 s

    def conectar(self) -> None:
        self.aberto = True

    def fechar(self) -> None:
        self.aberto = False

    def _obter_registradores_predio(self) -> list[int]:
        qtd_pendentes = len(self.fila_chamadas)
        if qtd_pendentes > 0:
            origem, destino, cid = self.fila_chamadas[0]
        else:
            origem, destino, cid = 0, 0, 0

        # Barramento nominal: 12000 mA menos derating térmico
        temp_c = self.temp_c_x10 / 10.0
        if self.watchdog_expirado:
            barramento = 3000
        else:
            barramento = int(max(3000, 12000 - 200 * max(0.0, temp_c - 25.0)))

        return [
            qtd_pendentes,  # 0: chamadas_na_fila
            origem,  # 1: chamada_origem
            destino,  # 2: chamada_destino
            cid,  # 3: chamada_id
            0,  # 4: chamada_pop (somente escrita)
            self.temp_c_x10,  # 5: ambiente_temp_c_x10
            self.press_hpa,  # 6: ambiente_press_hpa
            barramento,  # 7: barramento_max_ma
            self.watchdog_expirado,  # 8: watchdog_ambiente
            0,  # 9: atribuicao_chamada_id
            0,  # 10: atribuicao_cabine
            self.cenario_ativo,  # 11: cenario_ativo
            self.cenario_geradas,  # 12: cenario_geradas
            self.cenario_atendidas,  # 13: cenario_atendidas
            self.espera_media_s_x10,  # 14: espera_media_s_x10
            self.viagem_media_s_x10,  # 15: viagem_media_s_x10
        ]

    def enviar_e_receber(
        self, pacote: bytes, tamanho_esperado: int | None = None
    ) -> bytes:
        self.historico_tx.append(pacote)
        print(f"[TX] ({len(pacote):02d} B): {pacote.hex(' ').upper()}")

        if self.simular_timeout:
            print("[RX] (00 B): <TIMEOUT>")
            return b""

        if self.resposta_injetada is not None:
            resp = self.resposta_injetada
            self.resposta_injetada = None
            if resp:
                print(f"[RX] ({len(resp):02d} B): {resp.hex(' ').upper()}")
            else:
                print("[RX] (00 B): <TIMEOUT>")
            return resp

        resp = self._processar_pacote(pacote)
        if resp:
            print(f"[RX] ({len(resp):02d} B): {resp.hex(' ').upper()}")
        else:
            print("[RX] (00 B): <TIMEOUT>")
        return resp

    def _processar_pacote(self, pkt: bytes) -> bytes:
        if not pkt:
            return b""

        # Identificação de Parte 1 (comandos 0xA1..0xA3 e 0xB1..0xB3 sem MODBUS)
        cmd = pkt[0]
        if cmd in (0xA1, 0xA2, 0xA3, 0xB1, 0xB2, 0xB3):
            return self._processar_parte1(pkt)

        # Identificação de MODBUS (Parte 2 ou Parte 3 com endereço no 1º byte)
        addr = pkt[0]
        if len(pkt) < 4:
            return b""

        # Validação prévia de CRC nas mensagens MODBUS recebidas
        if not verificar_crc16(pkt):
            # ESP32 descarta silenciosamente mensagens com CRC inválido
            return b""

        corpo = pkt[:-2]  # Remove CRC

        # Parte 2: Endereço 0x01 didático com funções 0x23 ou 0x16
        if addr == 0x01 and len(corpo) >= 2 and corpo[1] in (0x23, 0x16):
            return self._processar_parte2(corpo)

        # Parte 3: Endereços 0x11..0x13 (Cabines) e 0x20 (Prédio)
        if addr in (0x11, 0x12, 0x13, 0x20):
            return self._processar_parte3(addr, corpo)

        # Endereço desconhecido
        return b""

    def _processar_parte1(self, pkt: bytes) -> bytes:
        cmd = pkt[0]
        # Último dígito da matrícula: último byte da requisição
        ult_digito = pkt[-1] if len(pkt) >= 7 else 1

        if cmd == 0xA1:
            # Solicita inteiro constante (ex: 42)
            return struct.pack("<i", 42)
        elif cmd == 0xA2:
            # Solicita float constante (ex: 3.14159)
            return struct.pack("<f", 3.14159)
        elif cmd == 0xA3:
            # Solicita string constante
            texto = b"FSE_2026_2"
            return bytes([len(texto)]) + texto
        elif cmd == 0xB1:
            # Envia inteiro: resposta = int_recebido * ultimo_digito
            if len(pkt) < 5:
                return b""
            val = struct.unpack("<i", pkt[1:5])[0]
            resp_val = (val * ult_digito) & 0xFFFFFFFF
            # Garante representação com sinal no range de 32 bits
            if resp_val >= 0x80000000:
                resp_val -= 0x100000000
            return struct.pack("<i", resp_val)
        elif cmd == 0xB2:
            # Envia float: resposta = float_recebido * ultimo_digito
            if len(pkt) < 5:
                return b""
            val = struct.unpack("<f", pkt[1:5])[0]
            resp_val = val * ult_digito
            return struct.pack("<f", resp_val)
        elif cmd == 0xB3:
            # Envia string: resposta = "Resposta da UART: " + string_enviada
            tam = pkt[1]
            txt_enviado = pkt[2 : 2 + tam].decode("utf-8", errors="replace")
            txt_resp = f"Resposta da UART: {txt_enviado}".encode()
            return bytes([len(txt_resp)]) + txt_resp

        return b""

    def _processar_parte2(self, corpo: bytes) -> bytes:
        # corpo: [addr=0x01][func][sub][dados...][matricula: 6]
        func = corpo[1]
        sub = corpo[2]
        ult_digito = corpo[-1]  # Último dígito da matrícula

        if func == 0x23:  # Solicitações
            if sub == 0xA1:
                resp_payload = bytes([0x01, 0x23]) + struct.pack("<i", 999)
                return anexar_crc16(resp_payload)
            elif sub == 0xA2:
                resp_payload = bytes([0x01, 0x23]) + struct.pack("<f", 27.85)
                return anexar_crc16(resp_payload)
            elif sub == 0xA3:
                txt = b"MODBUS_OK"
                resp_payload = bytes([0x01, 0x23, len(txt)]) + txt
                return anexar_crc16(resp_payload)
        elif func == 0x16:  # Envios
            if sub == 0xB1:
                val = struct.unpack("<i", corpo[3:7])[0]
                resp_payload = bytes([0x01, 0x16]) + struct.pack("<i", val * ult_digito)
                return anexar_crc16(resp_payload)
            elif sub == 0xB2:
                val = struct.unpack("<f", corpo[3:7])[0]
                resp_payload = bytes([0x01, 0x16]) + struct.pack("<f", val * ult_digito)
                return anexar_crc16(resp_payload)
            elif sub == 0xB3:
                tam = corpo[3]
                txt_enviado = corpo[4 : 4 + tam].decode("utf-8", errors="replace")
                txt_resp = f"MODBUS Eco: {txt_enviado}".encode()
                resp_payload = bytes([0x01, 0x16, len(txt_resp)]) + txt_resp
                return anexar_crc16(resp_payload)

        # Exceção função/sub inválido
        return anexar_crc16(bytes([0x01, func | 0x80, 0x01]))

    def _processar_parte3(self, addr: int, corpo: bytes) -> bytes:
        func = corpo[1]

        # Apenas funções 0x03 e 0x10 suportadas
        if func not in (0x03, 0x10):
            return anexar_crc16(
                bytes([addr, func | 0x80, 0x01])
            )  # 0x01: Função inválida

        if func == 0x03:
            # Leitura: corpo = [addr, 0x03, reg_le: 2, qtd_le: 2, mat: 6]
            if len(corpo) < 6:
                return anexar_crc16(bytes([addr, 0x83, 0x02]))
            reg_ini, qtd = struct.unpack("<HH", corpo[2:6])

            if qtd == 0:
                return anexar_crc16(
                    bytes([addr, 0x83, 0x02])
                )  # 0x02: Endereço inválido

            # Obtenção da tabela de registradores do dispositivo
            if addr in (0x11, 0x12, 0x13):
                tabela = self.cabines[addr]
            else:
                tabela = self._obter_registradores_predio()

            if reg_ini + qtd > len(tabela):
                return anexar_crc16(bytes([addr, 0x83, 0x02]))  # 0x02: Fora do mapa

            # Montagem da resposta 0x03: valores retornam em BIG-ENDIAN
            byte_count = 2 * qtd
            dados_resp = bytearray([addr, 0x03, byte_count])
            for i in range(qtd):
                val = tabela[reg_ini + i]
                dados_resp.extend(struct.pack(">H", val & 0xFFFF))
            return anexar_crc16(bytes(dados_resp))

        elif func == 0x10:
            # Escrita: corpo = [addr, 0x10, reg_le: 2, qtd_le: 2, byte_count: 1, val_1..val_qtd: LE, mat: 6]
            if len(corpo) < 7:
                return anexar_crc16(bytes([addr, 0x90, 0x02]))
            reg_ini, qtd, byte_count = struct.unpack("<HHB", corpo[2:7])

            if byte_count != 2 * qtd or qtd == 0:
                return anexar_crc16(bytes([addr, 0x90, 0x02]))

            offset_valores = 7
            valores_escrita = []
            for i in range(qtd):
                val = struct.unpack(
                    "<H", corpo[offset_valores + 2 * i : offset_valores + 2 * i + 2]
                )[0]
                valores_escrita.append(val)

            # Validações de escrita e aplicação de regras de negócio
            if addr in (0x11, 0x12, 0x13):
                # Cabines: SOMENTE registrador 3 (porta_comando) é gravável!
                if reg_ini != 3 or qtd != 1:
                    return anexar_crc16(
                        bytes([addr, 0x90, 0x02])
                    )  # 0x02: Escrita em RO ou fora da faixa
                cmd_porta = valores_escrita[0]
                if cmd_porta not in (0, 1, 2):
                    return anexar_crc16(
                        bytes([addr, 0x90, 0x03])
                    )  # 0x03: Valor inválido
                # Aplica comando de porta e simula transição de estado
                self.cabines[addr][3] = cmd_porta
                if cmd_porta == 1:
                    self.cabines[addr][2] = 2  # Aberta
                elif cmd_porta == 2:
                    self.cabines[addr][2] = 0  # Fechada
            elif addr == 0x20:
                # Prédio: Registradores graváveis são 4, 5, 6, 9 e 10
                # Verifica limites do mapa
                if reg_ini + qtd > 16:
                    return anexar_crc16(bytes([addr, 0x90, 0x02]))

                # Checa se está tentando escrever em registradores apenas de leitura
                ro_regs = {0, 1, 2, 3, 7, 8, 11, 12, 13, 14, 15}
                for r in range(reg_ini, reg_ini + qtd):
                    if r in ro_regs:
                        return anexar_crc16(bytes([addr, 0x90, 0x02]))

                # Aplica valores gravados
                for idx, r in enumerate(range(reg_ini, reg_ini + qtd)):
                    v = valores_escrita[idx]
                    if r == 4:  # chamada_pop
                        if v == 1 and self.fila_chamadas:
                            self.fila_chamadas.pop(0)
                    elif r == 5:  # ambiente_temp_c_x10
                        self.temp_c_x10 = v
                        self.watchdog_expirado = 0
                    elif r == 6:  # ambiente_press_hpa
                        self.press_hpa = v
                        self.watchdog_expirado = 0
                    elif r == 9:  # atribuicao_chamada_id
                        pass
                    elif r == 10 and v not in (1, 2, 3):
                        return anexar_crc16(
                            bytes([addr, 0x90, 0x03])
                        )  # 0x03: Cabine inválida

            # Resposta de sucesso 0x10: Eco de reg_ini e qtd em BIG-ENDIAN!
            # [addr, 0x10, reg_ini_BE: 2, qtd_BE: 2, CRC: 2] (8 bytes)
            eco = struct.pack(">HH", reg_ini, qtd)
            resp_eco = bytes([addr, 0x10]) + eco
            return anexar_crc16(resp_eco)


def criar_uart_driver(
    porta: str = "/dev/serial0", timeout: float = 0.5, forcar_mock: bool = False
) -> BaseUartDriver:
    """Cria e retorna o driver UART apropriado (físico ou simulado).

    Se 'forcar_mock' for True, ou se o pacote 'serial' não estiver disponível, ou se a porta
    física não puder ser aberta, retorna o FakeUartDriver com simulação integrada.
    """
    if forcar_mock or not SERIAL_AVAILABLE:
        print("[INFO] Usando FakeUartDriver (modo simulador offline).")
        return FakeUartDriver(porta=porta, timeout=timeout)

    try:
        driver = HardwareUartDriver(porta=porta, timeout=timeout)
        driver.conectar()
        return driver
    except Exception as e:  # noqa: BLE001
        print(
            f"[AVISO] Não foi possível abrir porta física '{porta}' ({e}). Ativando FakeUartDriver."
        )
        return FakeUartDriver(porta=porta, timeout=timeout)
