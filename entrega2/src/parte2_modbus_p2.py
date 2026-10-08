"""src/parte2_modbus_p2.py - Wrapper MODBUS Modificado (Parte 2).

Implementa os 6 comandos com formato MODBUS didático:
- Endereço padrão 0x01;
- Códigos de função 0x23 (solicitação) e 0x16 (envio);
- Subcódigos 0xA1, 0xA2, 0xA3, 0xB1, 0xB2, 0xB3;
- Matrícula de 6 dígitos numéricos inserida imediatamente antes do CRC-16;
- Cálculo e validação do CRC-16 em Little-Endian;
- Tratamento de exceções com bit MSB setado na função (func | 0x80);
- Formatação e exibição dos campos decodificados.
"""

import struct

from .config import (
    ADDR_DISPOSITIVO_DIDATICO,
    EXCECOES_MODBUS,
    validar_e_obter_matricula,
)
from .crc import anexar_crc16, verificar_crc16
from .uart_driver import BaseUartDriver


class ProtocoloModbusP2:
    """Implementa as funções independentes dos seis comandos da Parte 2."""

    def __init__(
        self,
        driver: BaseUartDriver,
        matricula: list[int] | str | bytes | None = None,
        endereco: int = ADDR_DISPOSITIVO_DIDATICO,
    ):
        self.driver = driver
        self.matricula_bytes = validar_e_obter_matricula(matricula)
        self.endereco = endereco

    def _executar_transacao(
        self, payload: bytes, funcao_esperada: int, tamanho_minimo: int = 5
    ) -> bytes:
        """Adiciona a matrícula de 6 dígitos e CRC ao payload, transmite e valida a resposta.

        Args:
            payload: Bytes da mensagem MODBUS antes da matrícula.
            funcao_esperada: Código da função esperada na resposta (0x23 ou 0x16).
            tamanho_minimo: Tamanho mínimo aceitável para a resposta com CRC.

        Returns:
            bytes: Resposta completa e validada com CRC.

        Raises:
            TimeoutError: Caso ocorra timeout na resposta.
            ValueError: Se o CRC for inválido ou tamanho insuficiente.
            RuntimeError: Se o servidor responder com exceção MODBUS (MSB ativo).
        """
        # Monta quadro com matrícula e anexa CRC-16 em Little-Endian
        pacote_tx = anexar_crc16(payload + self.matricula_bytes)
        resp = self.driver.enviar_e_receber(pacote_tx)

        if len(resp) < 5:
            raise TimeoutError(
                f"Resposta MODBUS incompleta ou timeout: recebido {len(resp)} bytes (mínimo 5 bytes)"
            )

        # Validação do CRC-16
        if not verificar_crc16(resp):
            raise ValueError(
                f"CRC-16 inválido na resposta recebida: {resp.hex(' ').upper()}"
            )

        # Verificação do endereço. A ESP32 da bancada responde ao wrapper
        # didático com 0x00 (endereço de retorno do bridge UART), embora a
        # requisição seja corretamente endereçada a 0x01. Preserve 0x01 para
        # implementações MODBUS convencionais e aceite 0x00 somente neste
        # cliente, cuja função/subcódigo e CRC continuam sendo validados abaixo.
        addr_resp = resp[0]
        enderecos_resposta_validos = (self.endereco, 0x00)
        if addr_resp not in enderecos_resposta_validos:
            raise ValueError(
                f"Endereço divergente na resposta: esperado 0x{self.endereco:02X} ou 0x00, recebido 0x{addr_resp:02X}"
            )

        # Verificação de bit de erro/exceção (MSB ativo na função)
        func_resp = resp[1]
        if func_resp & 0x80:
            cod_excecao = resp[2] if len(resp) >= 3 else 0
            msg_exc = EXCECOES_MODBUS.get(cod_excecao, "Exceção MODBUS não mapeada")
            raise RuntimeError(
                f"Exceção MODBUS recebida: Função 0x{func_resp:02X}, Código 0x{cod_excecao:02X} ({msg_exc})"
            )

        if func_resp != funcao_esperada:
            raise ValueError(
                f"Função divergente na resposta: esperada 0x{funcao_esperada:02X}, recebida 0x{func_resp:02X}"
            )

        # A resposta da ESP32 ecoa o subcódigo da Parte 2 antes dos dados.
        # Ele também protege contra a associação acidental de uma resposta de
        # outro comando que use a mesma função MODBUS.
        subcodigo_esperado = payload[2]
        if len(resp) < 6:
            raise ValueError(
                f"Resposta MODBUS sem subcódigo: recebido {len(resp)} bytes"
            )
        if resp[2] != subcodigo_esperado:
            raise ValueError(
                f"Subcódigo divergente na resposta: esperado 0x{subcodigo_esperado:02X}, recebido 0x{resp[2]:02X}"
            )

        if len(resp) < tamanho_minimo:
            raise ValueError(
                f"Tamanho da resposta insuficiente: recebido {len(resp)} bytes, esperado no mínimo {tamanho_minimo}"
            )

        return resp

    def solicita_inteiro(self) -> int:
        """Solicita valor inteiro via MODBUS (Função 0x23, Subcódigo 0xA1)."""
        payload = bytes([self.endereco, 0x23, 0xA1])
        resp = self._executar_transacao(payload, funcao_esperada=0x23, tamanho_minimo=9)

        # Formato de resposta: [addr][0x23][sub][int32 LE: 4B][CRC: 2B]
        val = struct.unpack("<i", resp[3:7])[0]
        print(f"-> [Parte 2 - MODBUS 0x23/0xA1] Inteiro recebido: {val}")
        return val

    def solicita_float(self) -> float:
        """Solicita valor float via MODBUS (Função 0x23, Subcódigo 0xA2)."""
        payload = bytes([self.endereco, 0x23, 0xA2])
        resp = self._executar_transacao(payload, funcao_esperada=0x23, tamanho_minimo=9)

        # Formato de resposta: [addr][0x23][sub][float LE: 4B][CRC: 2B]
        val = struct.unpack("<f", resp[3:7])[0]
        print(f"-> [Parte 2 - MODBUS 0x23/0xA2] Float recebido: {val:.4f}")
        return val

    def solicita_string(self) -> str:
        """Solicita valor string via MODBUS (Função 0x23, Subcódigo 0xA3)."""
        payload = bytes([self.endereco, 0x23, 0xA3])
        resp = self._executar_transacao(payload, funcao_esperada=0x23, tamanho_minimo=6)

        # Formato de resposta: [addr][0x23][sub][tam: 1B][string: tam B][CRC: 2B]
        tam = resp[3]
        if len(resp) < 6 + tam:
            raise ValueError(
                f"Payload de string MODBUS incompleto: esperado {tam} bytes de dados"
            )

        texto = resp[4 : 4 + tam].decode("utf-8", errors="replace").rstrip("\x00")
        print(f"-> [Parte 2 - MODBUS 0x23/0xA3] String recebida ({len(texto)} chars / {tam} B declarados): '{texto}'")
        return texto

    def envia_inteiro(self, valor: int) -> int:
        """Envia valor inteiro via MODBUS (Função 0x16, Subcódigo 0xB1)."""
        payload_dados = struct.pack("<i", valor)
        payload = bytes([self.endereco, 0x16, 0xB1]) + payload_dados
        resp = self._executar_transacao(payload, funcao_esperada=0x16, tamanho_minimo=9)

        # Formato de resposta: [addr][0x16][sub][int32 LE: 4B][CRC: 2B]
        val = struct.unpack("<i", resp[3:7])[0]
        ult_digito = self.matricula_bytes[-1]
        print(
            f"-> [Parte 2 - MODBUS 0x16/0xB1] Retorno inteiro: {val} "
            f"(enviado {valor} × último dígito {ult_digito} = {val})"
        )
        return val

    def envia_float(self, valor: float) -> float:
        """Envia valor float via MODBUS (Função 0x16, Subcódigo 0xB2)."""
        payload_dados = struct.pack("<f", valor)
        payload = bytes([self.endereco, 0x16, 0xB2]) + payload_dados
        resp = self._executar_transacao(payload, funcao_esperada=0x16, tamanho_minimo=9)

        # Formato de resposta: [addr][0x16][sub][float LE: 4B][CRC: 2B]
        val = struct.unpack("<f", resp[3:7])[0]
        ult_digito = self.matricula_bytes[-1]
        print(
            f"-> [Parte 2 - MODBUS 0x16/0xB2] Retorno float: {val:.4f} "
            f"(enviado {valor:.4f} × último dígito {ult_digito} = {val:.4f})"
        )
        return val

    def envia_string(self, texto: str) -> str:
        """Envia valor string via MODBUS (Função 0x16, Subcódigo 0xB3)."""
        dados_str = texto.encode("utf-8")
        if len(dados_str) > 255:
            raise ValueError(
                f"String muito longa (máximo 255 bytes, fornecido {len(dados_str)})"
            )

        payload = bytes([self.endereco, 0x16, 0xB3, len(dados_str)]) + dados_str
        resp = self._executar_transacao(payload, funcao_esperada=0x16, tamanho_minimo=6)

        # Formato de resposta: [addr][0x16][sub][tam: 1B][string: tam B][CRC: 2B]
        tam = resp[3]
        if len(resp) < 6 + tam:
            raise ValueError(
                f"Payload de string MODBUS incompleto: esperado {tam} bytes de dados"
            )

        retorno = resp[4 : 4 + tam].decode("utf-8", errors="replace").rstrip("\x00")
        print(f"-> [Parte 2 - MODBUS 0x16/0xB3] Retorno string ({len(retorno)} chars / {tam} B declarados): '{retorno}'")
        return retorno
