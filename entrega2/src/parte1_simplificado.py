"""src/parte1_simplificado.py - Implementação dos 6 comandos do Protocolo Simplificado.

Protocolo enxuto:
- Sem endereçamento MODBUS e sem CRC;
- Cada requisição é composta por: [CMD][Payload opcional][Matrícula de 6 bytes inteiros];
- int32 e float trafegam em Little-Endian conforme especificação de Entrega_2.md;
- Validação estrita de tamanhos, timeouts e exibição de valores decodificados.
"""

import struct

from .config import validar_e_obter_matricula
from .uart_driver import BaseUartDriver


class ProtocoloSimplificado:
    """Implementa as funções independentes dos seis comandos da Parte 1."""

    def __init__(
        self, driver: BaseUartDriver, matricula: list[int] | str | bytes | None = None
    ):
        self.driver = driver
        self.matricula_bytes = validar_e_obter_matricula(matricula)

    def solicita_inteiro(self) -> int:
        """Comando 0xA1: Solicita valor inteiro de 32 bits (int32_t little-endian)."""
        pacote = bytes([0xA1]) + self.matricula_bytes
        resp = self.driver.enviar_e_receber(pacote, tamanho_esperado=4)

        if len(resp) != 4:
            raise TimeoutError(
                f"Falha na resposta do comando 0xA1: esperado 4 bytes, recebido {len(resp)} bytes"
            )

        val = struct.unpack("<i", resp)[0]
        print(f"-> [Parte 1 - 0xA1] Inteiro recebido: {val}")
        return val

    def solicita_float(self) -> float:
        """Comando 0xA2: Solicita valor float de 32 bits (float IEEE 754 little-endian)."""
        pacote = bytes([0xA2]) + self.matricula_bytes
        resp = self.driver.enviar_e_receber(pacote, tamanho_esperado=4)

        if len(resp) != 4:
            raise TimeoutError(
                f"Falha na resposta do comando 0xA2: esperado 4 bytes, recebido {len(resp)} bytes"
            )

        val = struct.unpack("<f", resp)[0]
        print(f"-> [Parte 1 - 0xA2] Float recebido: {val:.4f}")
        return val

    def solicita_string(self) -> str:
        """Comando 0xA3: Solicita string (1 byte tamanho N seguido de N bytes)."""
        pacote = bytes([0xA3]) + self.matricula_bytes
        resp = self.driver.enviar_e_receber(pacote)

        if len(resp) < 1:
            raise TimeoutError(
                "Falha na resposta do comando 0xA3: nenhum byte recebido (timeout)"
            )

        tam = resp[0]
        if len(resp) < 1 + tam:
            raise ValueError(
                f"Tamanho de string inconsistente no comando 0xA3: declarado {tam} B, recebido {len(resp) - 1} B"
            )

        texto = resp[1 : 1 + tam].decode("utf-8", errors="replace")
        print(f"-> [Parte 1 - 0xA3] String recebida ({tam} B): '{texto}'")
        return texto

    def envia_inteiro(self, valor: int) -> int:
        """Comando 0xB1: Envia inteiro de 32 bits em little-endian e recebe o eco processado."""
        payload_int = struct.pack("<i", valor)
        pacote = bytes([0xB1]) + payload_int + self.matricula_bytes
        resp = self.driver.enviar_e_receber(pacote, tamanho_esperado=4)

        if len(resp) != 4:
            raise TimeoutError(
                f"Falha na resposta do comando 0xB1: esperado 4 bytes, recebido {len(resp)} bytes"
            )

        val = struct.unpack("<i", resp)[0]
        print(f"-> [Parte 1 - 0xB1] Eco inteiro processado: {val}")
        return val

    def envia_float(self, valor: float) -> float:
        """Comando 0xB2: Envia float de 32 bits em little-endian e recebe o eco processado."""
        payload_float = struct.pack("<f", valor)
        pacote = bytes([0xB2]) + payload_float + self.matricula_bytes
        resp = self.driver.enviar_e_receber(pacote, tamanho_esperado=4)

        if len(resp) != 4:
            raise TimeoutError(
                f"Falha na resposta do comando 0xB2: esperado 4 bytes, recebido {len(resp)} bytes"
            )

        val = struct.unpack("<f", resp)[0]
        print(f"-> [Parte 1 - 0xB2] Eco float processado: {val:.4f}")
        return val

    def envia_string(self, texto: str) -> str:
        """Comando 0xB3: Envia string com 1 byte de tamanho e recebe a resposta processada."""
        dados_str = texto.encode("utf-8")
        if len(dados_str) > 255:
            raise ValueError(
                f"String muito longa para envio (máximo 255 bytes, fornecido {len(dados_str)})"
            )

        pacote = bytes([0xB3, len(dados_str)]) + dados_str + self.matricula_bytes
        resp = self.driver.enviar_e_receber(pacote)

        if len(resp) < 1:
            raise TimeoutError(
                "Falha na resposta do comando 0xB3: nenhum byte recebido (timeout)"
            )

        tam = resp[0]
        if len(resp) < 1 + tam:
            raise ValueError(
                f"Tamanho de string inconsistente no comando 0xB3: declarado {tam} B, recebido {len(resp) - 1} B"
            )

        retorno = resp[1 : 1 + tam].decode("utf-8", errors="replace")
        print(f"-> [Parte 1 - 0xB3] Retorno de string ({tam} B): '{retorno}'")
        return retorno
