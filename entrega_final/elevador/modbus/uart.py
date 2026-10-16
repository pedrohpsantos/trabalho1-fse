"""Drivers da porta serial. Somente o Servidor Central instancia um driver de hardware."""

import abc
import logging
import time

try:
    import serial
except ImportError:  # pyserial é opcional no desenvolvimento (modo --mock)
    serial = None

log = logging.getLogger(__name__)

BAUDRATE = 115200


class UartBase(abc.ABC):
    """Interface de transporte: envia um quadro RTU e devolve a resposta (ou b'' em timeout)."""

    @abc.abstractmethod
    def abrir(self) -> None: ...

    @abc.abstractmethod
    def fechar(self) -> None: ...

    @abc.abstractmethod
    def transacionar(self, quadro: bytes, tamanho_esperado: int) -> bytes: ...


class UartSerial(UartBase):
    """`/dev/serial0` a 115200 bps, 8N1, via pyserial."""

    def __init__(self, porta: str = "/dev/serial0", timeout_s: float = 0.3) -> None:
        if serial is None:
            raise RuntimeError("pyserial não instalado: use `pip install pyserial` ou --mock")
        self.porta = porta
        self.timeout_s = timeout_s
        self._conn = None

    def abrir(self) -> None:
        if self._conn is not None and self._conn.is_open:
            return
        self._conn = serial.Serial(
            port=self.porta,
            baudrate=BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
        )
        self._conn.reset_input_buffer()
        self._conn.reset_output_buffer()
        time.sleep(0.05)

    def fechar(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def transacionar(self, quadro: bytes, tamanho_esperado: int) -> bytes:
        try:
            self.abrir()
            conn = self._conn
            conn.reset_input_buffer()
            conn.write(quadro)
            conn.flush()
            log.debug("TX %s", quadro.hex(" "))
            # Lê o cabeçalho; uma resposta de exceção (MSB da função) tem apenas 5 bytes.
            resposta = conn.read(3)
            if len(resposta) == 3:
                falta = 2 if resposta[1] & 0x80 else tamanho_esperado - 3
                if falta > 0:
                    resposta += conn.read(falta)
        except OSError as erro:  # serial.SerialException deriva de OSError
            # Porta caiu/ocupada: fecha para reabrir na próxima tentativa e devolve "timeout",
            # de modo que o cliente aplique as mesmas retentativas de uma falta de resposta.
            log.warning("UART %s: %s", self.porta, erro)
            self.fechar()
            return b""
        log.debug("RX %s", resposta.hex(" "))
        return resposta
