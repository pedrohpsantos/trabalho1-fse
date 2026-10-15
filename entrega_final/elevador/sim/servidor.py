"""Servidor TCP da bancada virtual: canais "uart" (quadros RTU) e "gpio" (uma cabine)."""

import json
import logging
import queue
import socket
import struct
import threading

from ..constantes import Direcao
from .bancada import Bancada
from .fisica import SEQUENCIA_QUADRATURA
from .modbus_sim import processar_quadro

log = logging.getLogger(__name__)


class ServidorBancada:
    def __init__(self, bancada: Bancada, host: str = "127.0.0.1", porta: int = 5600) -> None:
        self.bancada = bancada
        self.host, self.porta = host, porta
        self._parar = threading.Event()
        self._sock: socket.socket | None = None
        self._thread = threading.Thread(target=self._aceitar, name="bancada-accept", daemon=True)

    def iniciar(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.porta))
        sock.listen(16)
        sock.settimeout(0.5)
        self._sock = sock
        self.porta = sock.getsockname()[1]
        self._thread.start()
        log.info("bancada virtual em %s:%d", self.host, self.porta)

    def parar(self) -> None:
        self._parar.set()
        if self._sock:
            self._sock.close()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _aceitar(self) -> None:
        while not self._parar.is_set():
            try:
                conexao, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._atender, args=(conexao,), name="bancada-conn", daemon=True).start()

    def _atender(self, conexao: socket.socket) -> None:
        conexao.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            leitor = conexao.makefile("rb")
            saudacao = json.loads(leitor.readline(1024))
            if saudacao.get("canal") == "uart":
                self._canal_uart(conexao, leitor)
            elif saudacao.get("canal") == "gpio" and saudacao.get("cabine") in self.bancada.cabines:
                self._canal_gpio(conexao, leitor, int(saudacao["cabine"]))
        except (OSError, ValueError):
            log.debug("conexão da bancada encerrada", exc_info=True)
        finally:
            conexao.close()

    def _canal_uart(self, conexao: socket.socket, leitor) -> None:
        while not self._parar.is_set():
            cab = leitor.read(2)
            if len(cab) < 2:
                return
            (tamanho,) = struct.unpack(">H", cab)
            quadro = leitor.read(tamanho)
            if len(quadro) < tamanho:
                return
            resposta = processar_quadro(self.bancada, quadro)
            conexao.sendall(struct.pack(">H", len(resposta)) + resposta)

    def _canal_gpio(self, conexao: socket.socket, leitor, numero: int) -> None:
        fisica = self.bancada.cabines[numero]
        saida: queue.Queue = queue.Queue(maxsize=4096)

        def publicar(msg: dict) -> None:
            try:
                saida.put_nowait(msg)
            except queue.Full:
                pass

        with self.bancada.lock:
            a, b = SEQUENCIA_QUADRATURA[fisica._quad]
            publicar(
                {"k": "init", "a": a, "b": b, "c": fisica.contagem, "s": int(fisica.sensor), "x": int(fisica.cortina)}
            )
            fisica.ao_quadratura = lambda a, b: publicar({"k": "q", "a": a, "b": b})
            fisica.ao_sensor = lambda n, c: publicar({"k": "s", "n": int(n), "c": c})
            fisica.ao_cortina = lambda n: publicar({"k": "c", "n": int(n)})

        def escrever() -> None:
            while not self._parar.is_set():
                try:
                    msg = saida.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    conexao.sendall((json.dumps(msg, separators=(",", ":")) + "\n").encode())
                except OSError:
                    return

        threading.Thread(target=escrever, name=f"bancada-tx{numero}", daemon=True).start()
        try:
            while not self._parar.is_set():
                linha = leitor.readline(1024)
                if not linha:
                    return
                msg = json.loads(linha)
                if msg.get("k") == "motor":
                    with self.bancada.lock:
                        fisica.definir_motor(Direcao(msg["d"]), float(msg["duty"]))
        finally:
            with self.bancada.lock:  # cabine caiu: freia (estado seguro) e solta os callbacks
                fisica.definir_motor(Direcao.FREIO, 0.0)
                fisica.ao_quadratura = fisica.ao_sensor = fisica.ao_cortina = None
