"""src/parte3_simulador.py - Driver MODBUS RTU para os registradores do simulador de elevadores.

Implementa as funções MODBUS 0x03 e 0x10 sobre os mapas oficiais das Seções 5.1 a 5.3:
- Cabines: 0x11, 0x12, 0x13
- Prédio: 0x20
- Inversão de endianness rigorosamente validada:
    * Requisição 0x03 e 0x10: reg e qtd em Little-Endian;
    * Requisição 0x10: valores enviados em Little-Endian;
    * Resposta 0x03: valores recebidos em Big-Endian;
    * Resposta 0x10: eco de reg e qtd em Big-Endian;
    * CRC-16: Little-Endian;
- Política de até 3 retentativas somente para timeout e CRC inválido;
- Exceções MODBUS (0x01, 0x02, 0x03) não são repetidas;
- Posição da cabine (offset 7) convertida para int16 com sinal (valores negativos no poço);
- Módulo independente e importável pela Entrega Final.
"""

import struct
import time
from typing import Any

from .config import (
    ADDR_PREDIO,
    EXCECOES_MODBUS,
    validar_e_obter_matricula,
)
from .crc import anexar_crc16, verificar_crc16
from .uart_driver import BaseUartDriver


class SimuladorModbusClient:
    """Cliente MODBUS RTU para interface com as cabines e o controlador do prédio."""

    def __init__(
        self,
        driver: BaseUartDriver,
        matricula: list[int] | str | bytes | None = None,
        max_tentativas: int = 3,
        intervalo_retry_s: float = 0.05,
    ):
        self.driver = driver
        self.matricula_bytes = validar_e_obter_matricula(matricula)
        self.max_tentativas = max_tentativas
        self.intervalo_retry_s = intervalo_retry_s

    # -------------------------------------------------------------------------
    # Primitivas Genéricas MODBUS (0x03 e 0x10)
    # -------------------------------------------------------------------------

    def read_holding_registers(
        self, addr: int, reg_inicial: int, qtd: int
    ) -> list[int]:
        """Função 0x03: Lê 'qtd' registradores consecutivos a partir de 'reg_inicial'.

        Ordem de bytes:
        - reg e qtd da requisição: LITTLE-ENDIAN;
        - valores recebidos na resposta: BIG-ENDIAN;
        - CRC-16: LITTLE-ENDIAN.

        Política de retentativas: até 3 tentativas apenas para timeout ou falha de CRC.
        Respostas de exceção (MSB ativo) são lançadas imediatamente como RuntimeError.
        """
        if qtd <= 0:
            raise ValueError(
                f"Quantidade de registradores deve ser maior que zero (fornecido: {qtd})"
            )

        # Requisição: [addr][0x03][reg_LE: 2B][qtd_LE: 2B][matrícula: 6B][CRC: 2B] (12 bytes)
        req_sem_crc = (
            bytes([addr, 0x03])
            + struct.pack("<HH", reg_inicial, qtd)
            + self.matricula_bytes
        )
        pacote_tx = anexar_crc16(req_sem_crc)
        tam_esperado = 5 + 2 * qtd  # [addr][0x03][byte_count][2*qtd bytes][CRC: 2]

        ultimo_erro = "Desconhecido"

        for tentativa in range(1, self.max_tentativas + 1):
            resp = self.driver.enviar_e_receber(
                pacote_tx, tamanho_esperado=tam_esperado
            )

            # Timeout ou resposta incompleta
            if len(resp) < 5:
                ultimo_erro = (
                    f"Timeout ou resposta incompleta ({len(resp)} bytes recebidos)"
                )
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            # Verificação do CRC
            if not verificar_crc16(resp):
                ultimo_erro = (
                    f"Falha de CRC na resposta (recebido: {resp.hex(' ').upper()})"
                )
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            # Verificação de Exceção MODBUS (bit 7 ativo na função: 0x83)
            # Exceções NUNCA são repetidas!
            if resp[1] == (0x03 | 0x80):
                cod_exc = resp[2]
                msg_exc = EXCECOES_MODBUS.get(cod_exc, "Exceção não mapeada")
                raise RuntimeError(
                    f"Exceção MODBUS 0x{cod_exc:02X} no dispositivo 0x{addr:02X}: {msg_exc}"
                )

            # Validação de integridade do cabeçalho
            if resp[0] != addr:
                ultimo_erro = f"Endereço divergente: esperado 0x{addr:02X}, recebido 0x{resp[0]:02X}"
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            if resp[1] != 0x03:
                ultimo_erro = f"Código de função divergente: esperado 0x03, recebido 0x{resp[1]:02X}"
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            byte_count = resp[2]
            if byte_count != 2 * qtd:
                ultimo_erro = f"byte_count inconsistente: esperado {2 * qtd}, recebido {byte_count}"
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            if len(resp) < 5 + byte_count:
                ultimo_erro = (
                    f"Tamanho insuficiente para o payload declarado ({len(resp)} B)"
                )
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            # Decodificação dos registradores em BIG-ENDIAN
            valores = []
            for i in range(qtd):
                offset = 3 + 2 * i
                val_be = struct.unpack(">H", resp[offset : offset + 2])[0]
                valores.append(val_be)

            return valores

        raise TimeoutError(
            f"Falha de comunicação 0x03 com dispositivo 0x{addr:02X} após {self.max_tentativas} tentativas. Último erro: {ultimo_erro}"
        )

    def write_multiple_registers(
        self, addr: int, reg_inicial: int, valores: list[int]
    ) -> None:
        """Função 0x10: Escreve múltiplos registradores consecutivos a partir de 'reg_inicial'.

        Ordem de bytes:
        - reg, qtd e valores da requisição: LITTLE-ENDIAN;
        - eco de reg e qtd na resposta: BIG-ENDIAN;
        - CRC-16: LITTLE-ENDIAN.

        Política de retentativas: até 3 tentativas apenas para timeout ou falha de CRC.
        Respostas de exceção (MSB ativo) são lançadas imediatamente como RuntimeError.
        """
        qtd = len(valores)
        if qtd <= 0:
            raise ValueError("Lista de valores a escrever não pode ser vazia")

        byte_count = 2 * qtd

        # Corpo da requisição com reg, qtd e byte_count em LE
        corpo = bytes([addr, 0x10]) + struct.pack("<HHB", reg_inicial, qtd, byte_count)

        # Valores escritos empacotados em LITTLE-ENDIAN
        for v in valores:
            corpo += struct.pack("<H", v & 0xFFFF)

        # Matrícula de 6 dígitos antes do CRC
        pacote_tx = anexar_crc16(corpo + self.matricula_bytes)
        tam_esperado = 8  # [addr][0x10][eco_reg_BE: 2][eco_qtd_BE: 2][CRC: 2]

        ultimo_erro = "Desconhecido"

        for tentativa in range(1, self.max_tentativas + 1):
            resp = self.driver.enviar_e_receber(
                pacote_tx, tamanho_esperado=tam_esperado
            )

            # Timeout ou resposta incompleta
            if len(resp) < 5:
                ultimo_erro = (
                    f"Timeout ou resposta incompleta ({len(resp)} bytes recebidos)"
                )
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            # Verificação de CRC
            if not verificar_crc16(resp):
                ultimo_erro = f"Falha de CRC no eco de escrita (recebido: {resp.hex(' ').upper()})"
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            # Verificação de Exceção MODBUS (bit 7 ativo na função: 0x90)
            # Exceções NUNCA são repetidas!
            if resp[1] == (0x10 | 0x80):
                cod_exc = resp[2]
                msg_exc = EXCECOES_MODBUS.get(cod_exc, "Exceção não mapeada")
                raise RuntimeError(
                    f"Exceção MODBUS 0x{cod_exc:02X} ao escrever no dispositivo 0x{addr:02X}: {msg_exc}"
                )

            # Validação do cabeçalho e eco da resposta (8 bytes)
            if len(resp) != 8:
                ultimo_erro = f"Tamanho incorreto para eco de escrita 0x10: esperado 8 bytes, recebido {len(resp)}"
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            if resp[0] != addr or resp[1] != 0x10:
                ultimo_erro = f"Cabeçalho inválido no eco 0x10 (addr=0x{resp[0]:02X}, func=0x{resp[1]:02X})"
                if tentativa < self.max_tentativas:
                    time.sleep(self.intervalo_retry_s)
                    continue
                break

            # Eco de reg_inicial e qtd retornam em BIG-ENDIAN
            eco_reg, eco_qtd = struct.unpack(">HH", resp[2:6])
            if eco_reg == reg_inicial and eco_qtd == qtd:
                return  # Escrita bem-sucedida e eco confirmado

            ultimo_erro = f"Eco divergente: esperado reg={reg_inicial}, qtd={qtd}; recebido reg={eco_reg}, qtd={eco_qtd}"
            if tentativa < self.max_tentativas:
                time.sleep(self.intervalo_retry_s)
                continue
            break

        raise TimeoutError(
            f"Falha ao escrever registradores no dispositivo 0x{addr:02X} após {self.max_tentativas} tentativas. Último erro: {ultimo_erro}"
        )

    # -------------------------------------------------------------------------
    # Funções de Domínio - Cabines (Seção 5.2)
    # -------------------------------------------------------------------------

    def le_estado_cabine(self, cabine: int = 1) -> dict[str, Any]:
        """Lê os 9 registradores de estado de uma cabine (offsets 0 a 8).

        Converte a posição (offset 7) para int16 com sinal (negativa no poço).
        """
        if cabine not in (1, 2, 3):
            raise ValueError(f"Cabine inválida: {cabine} (deve ser 1, 2 ou 3)")

        addr = 0x10 + cabine
        regs = self.read_holding_registers(addr, reg_inicial=0, qtd=9)

        # Converte offset 7 (posicao_mm) para int16 com sinal
        pos_raw = regs[7]
        pos_mm = struct.unpack(">h", struct.pack(">H", pos_raw))[0]

        nomes_porta = {
            0: "Fechada",
            1: "Abrindo",
            2: "Aberta",
            3: "Fechando",
            4: "Obstruída",
        }

        return {
            "cabine": cabine,
            "andar_atual": regs[0],
            "nivelado": bool(regs[1]),
            "porta_estado": nomes_porta.get(regs[2], f"Desconhecido ({regs[2]})"),
            "porta_estado_raw": regs[2],
            "porta_comando": regs[3],
            "corrente_ma": regs[4],
            "carga_kg": regs[5],
            "passageiros": regs[6],
            "posicao_mm": pos_mm,
            "falha": regs[8],
        }

    def comanda_porta(self, cabine: int, comando: int) -> None:
        """Escreve o comando de porta no registrador 3 da cabine.

        Comandos permitidos: 0=Nenhum, 1=Abrir, 2=Fechar.
        """
        if cabine not in (1, 2, 3):
            raise ValueError(f"Cabine inválida: {cabine} (deve ser 1, 2 ou 3)")
        if comando not in (0, 1, 2):
            raise ValueError(
                f"Comando de porta inválido: {comando} (permitidos: 0, 1 ou 2)"
            )

        addr = 0x10 + cabine
        self.write_multiple_registers(addr, reg_inicial=3, valores=[comando])

    # -------------------------------------------------------------------------
    # Funções de Domínio - Prédio (Seção 5.3)
    # -------------------------------------------------------------------------

    def le_estado_predio(self) -> dict[str, Any]:
        """Lê os 16 registradores de estado do Controlador do Prédio (0x20)."""
        regs = self.read_holding_registers(ADDR_PREDIO, reg_inicial=0, qtd=16)
        return {
            "chamadas_na_fila": regs[0],
            "chamada_origem": regs[1],
            "chamada_destino": regs[2],
            "chamada_id": regs[3],
            "barramento_max_ma": regs[7],
            "watchdog_ambiente": regs[8],
            "cenario_ativo": regs[11],
            "cenario_geradas": regs[12],
            "cenario_atendidas": regs[13],
            "espera_media_s": regs[14] / 10.0,
            "viagem_media_s": regs[15] / 10.0,
        }

    def escreve_condicao_contorno(self, temp_c: float, press_hpa: int) -> None:
        """Escreve a Condição de Contorno nos registradores 5 e 6 do Prédio (0x20).

        Temperatura em décimos de °C (int16) e pressão em hPa (uint16).
        """
        temp_x10 = round(temp_c * 10)
        self.write_multiple_registers(
            ADDR_PREDIO, reg_inicial=5, valores=[temp_x10, press_hpa]
        )

    def le_chamada_da_fila(self) -> dict[str, int]:
        """Lê a chamada que está na cabeça da fila de destino."""
        regs = self.read_holding_registers(ADDR_PREDIO, reg_inicial=0, qtd=4)
        return {
            "pendentes": regs[0],
            "origem": regs[1],
            "destino": regs[2],
            "id": regs[3],
        }

    def atribui_chamada(self, chamada_id: int, cabine: int) -> None:
        """Escreve a atribuição de uma chamada nos registradores 9 e 10 do Prédio (0x20).

        chamada_id: ID da chamada a ser atendida;
        cabine: Cabine escolhida (1, 2 ou 3).
        """
        if cabine not in (1, 2, 3):
            raise ValueError(f"Cabine deve ser 1, 2 ou 3 (fornecido: {cabine})")
        self.write_multiple_registers(
            ADDR_PREDIO, reg_inicial=9, valores=[chamada_id, cabine]
        )

    def remove_chamada_da_fila(self) -> None:
        """Escreve 1 no registrador 4 (chamada_pop) para remover a chamada atendida."""
        self.write_multiple_registers(ADDR_PREDIO, reg_inicial=4, valores=[1])
