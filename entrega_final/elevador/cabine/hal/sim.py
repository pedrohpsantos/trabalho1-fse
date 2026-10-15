"""GPIO virtual: liga uma cabine ao modelo físico da bancada (em processo ou via TCP)."""

import json
import logging
import queue
import socket
import threading

from ...constantes import Direcao
from ...protocolo.canal import LeitorLinhas
from ...sim.bancada import Bancada
from ..encoder import DecodificadorQuadratura
from .base import AoCortina, AoSensor, GpioCabine

log = logging.getLogger(__name__)


class GpioVirtual(GpioCabine):
    """Em processo: as interrupções são callbacks disparados pela física da bancada."""

    def __init__(self, bancada: Bancada, cabine: int) -> None:
        self.bancada = bancada
        self.fisica = bancada.cabines[cabine]
        self._dec = DecodificadorQuadratura(0, 0)
        self._desloc = 0  # reancoragens acumuladas: as bordas da bancada usam a contagem bruta
        self.fisica.ao_quadratura = self._dec.atualizar

    def registrar_callbacks(self, ao_sensor: AoSensor, ao_cortina: AoCortina) -> None:
        self.fisica.ao_sensor = lambda entrou, contagem: ao_sensor(entrou, contagem + self._desloc)
        self.fisica.ao_cortina = ao_cortina

    def definir_motor(self, direcao: Direcao, duty: float) -> None:
        with self.bancada.lock:
            self.fisica.definir_motor(direcao, duty)

    def posicao(self) -> int:
        return self._dec.contagem

    def deslocar_posicao(self, delta: int) -> None:
        self._desloc += delta
        self._dec.deslocar(delta)

    def cortina_obstruida(self) -> bool:
        return self.fisica.cortina

    def sensor_andar_ativo(self) -> bool:
        return self.fisica.sensor

    def liberar(self) -> None:
        self.definir_motor(Direcao.FREIO, 0.0)


class GpioRemoto(GpioCabine):
    """Cliente TCP da bancada: recebe quadratura/bordas e envia o acionamento do motor."""

    def __init__(self, host: str, porta: int, cabine: int) -> None:
        self.host, self.porta, self.cabine = host, porta, cabine
        self._dec = DecodificadorQuadratura(0, 0)
        self._desloc = 0
        self._sensor = False
        self._cortina = False
        self._ao_sensor: AoSensor | None = None
        self._ao_cortina: AoCortina | None = None
        self._parar = threading.Event()
        self._fila_tx: queue.Queue = queue.Queue(maxsize=64)
        self._thread = threading.Thread(target=self._executar, name="gpio-remoto", daemon=True)
        self._thread.start()

    def registrar_callbacks(self, ao_sensor: AoSensor, ao_cortina: AoCortina) -> None:
        self._ao_sensor, self._ao_cortina = ao_sensor, ao_cortina

    def definir_motor(self, direcao: Direcao, duty: float) -> None:
        try:
            self._fila_tx.put_nowait({"k": "motor", "d": direcao.value, "duty": duty})
        except queue.Full:
            pass  # o próximo comando (50 ms) substitui este

    def posicao(self) -> int:
        return self._dec.contagem

    def deslocar_posicao(self, delta: int) -> None:
        self._desloc += delta
        self._dec.deslocar(delta)

    def cortina_obstruida(self) -> bool:
        return self._cortina

    def sensor_andar_ativo(self) -> bool:
        return self._sensor

    def liberar(self) -> None:
        self.definir_motor(Direcao.FREIO, 0.0)
        self._parar.wait(0.2)  # dá tempo de a thread de rede enviar o freio
        self._parar.set()
        self._thread.join(timeout=1.0)

    def _executar(self) -> None:
        while not self._parar.is_set():
            try:
                sock = socket.create_connection((self.host, self.porta), timeout=2.0)
            except OSError:
                self._parar.wait(0.5)
                continue
            sock.sendall((json.dumps({"canal": "gpio", "cabine": self.cabine}) + "\n").encode())
            try:
                self._laco(sock, LeitorLinhas(sock))
            except OSError:
                log.info("conexão com a bancada perdida; reconectando")
            finally:
                sock.close()

    def _laco(self, sock: socket.socket, leitor: LeitorLinhas) -> None:
        while not self._parar.is_set():
            try:
                while True:
                    item = self._fila_tx.get_nowait()
                    sock.sendall((json.dumps(item) + "\n").encode())
            except queue.Empty:
                pass
            try:
                linha = leitor.ler(0.05)
            except TimeoutError:
                continue
            self._tratar(json.loads(linha))

    def _tratar(self, msg: dict) -> None:
        tipo = msg.get("k")
        if tipo == "init":
            self._dec.definir(int(msg["a"]), int(msg["b"]), int(msg["c"]) + self._desloc)
            self._sensor, self._cortina = bool(msg["s"]), bool(msg["x"])
        elif tipo == "q":
            self._dec.atualizar(int(msg["a"]), int(msg["b"]))
        elif tipo == "s":
            self._sensor = bool(msg["n"])
            if self._ao_sensor:
                self._ao_sensor(self._sensor, int(msg["c"]) + self._desloc)
        elif tipo == "c":
            self._cortina = bool(msg["n"])
            if self._ao_cortina:
                self._ao_cortina(self._cortina)
