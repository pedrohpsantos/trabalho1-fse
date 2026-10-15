"""Servidor TCP do Central: aceita as cabines em qualquer ordem e tolera reconexões."""

import hmac
import logging
import socket
import threading
from collections.abc import Callable

from ..protocolo.canal import CanalFechado, CanalJson

log = logging.getLogger(__name__)

TIMEOUT_HELLO_S = 5.0
TIMEOUT_SILENCIO_S = 4.0  # a cabine envia telemetria a cada 0,5 s


class ServidorCabines:
    def __init__(
        self,
        host: str,
        porta: int,
        token: str,
        ao_hello: Callable[[int, dict], None],
        ao_mensagem: Callable[[int, dict], None],
        ao_desconexao: Callable[[int], None],
    ) -> None:
        self.host, self.porta, self.token = host, porta, token
        self._ao_hello, self._ao_mensagem, self._ao_desconexao = ao_hello, ao_mensagem, ao_desconexao
        self._canais: dict[int, CanalJson] = {}
        self._trava = threading.Lock()
        self._parar = threading.Event()
        self._socket: socket.socket | None = None
        self._thread = threading.Thread(target=self._aceitar, name="tcp-accept", daemon=True)

    def iniciar(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.porta))
        sock.listen(8)
        sock.settimeout(0.5)
        self._socket = sock
        self.porta = sock.getsockname()[1]
        self._thread.start()
        log.info("escutando cabines em %s:%d", self.host, self.porta)

    def parar(self) -> None:
        self._parar.set()
        with self._trava:
            for canal in self._canais.values():
                canal.fechar()
        if self._socket:
            self._socket.close()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def conectada(self, cabine: int) -> bool:
        with self._trava:
            canal = self._canais.get(cabine)
        return canal is not None and not canal.fechado

    def enviar(self, cabine: int, tipo: str, /, **campos) -> bool:
        with self._trava:
            canal = self._canais.get(cabine)
        if canal is None:
            return False
        try:
            canal.enviar(tipo, **campos)
            return True
        except CanalFechado:
            return False

    # ------------------------------------------------------------------
    def _aceitar(self) -> None:
        while not self._parar.is_set():
            try:
                conexao, endereco = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._atender, args=(conexao, endereco), name="tcp-cabine", daemon=True).start()

    def _atender(self, conexao: socket.socket, endereco) -> None:
        canal = CanalJson(conexao, f"{endereco[0]}:{endereco[1]}")
        cabine = 0
        try:
            hello = canal.receber(TIMEOUT_HELLO_S)
            if hello["t"] != "hello" or not hmac.compare_digest(hello["token"].encode(), self.token.encode()):
                log.warning("conexão de %s rejeitada (hello/token inválido)", canal.nome)
                return
            cabine = hello["cabine"]
            with self._trava:
                anterior = self._canais.get(cabine)
                self._canais[cabine] = canal
            if anterior:
                anterior.fechar()  # a conexão nova substitui a antiga (cabine reiniciou)
            canal.enviar("boas_vindas", mensagem=f"cabine {cabine} registrada")
            self._ao_hello(cabine, hello)
            while not self._parar.is_set():
                self._ao_mensagem(cabine, canal.receber(TIMEOUT_SILENCIO_S))
        except (CanalFechado, TimeoutError):
            log.info("cabine %s desconectada", cabine or canal.nome)
        except Exception:  # noqa: BLE001 - uma sessão com defeito não pode derrubar o Central
            log.exception("erro na sessão da cabine %s", cabine)
        finally:
            canal.fechar()
            if cabine:
                with self._trava:
                    atual = self._canais.get(cabine)
                    substituida = atual is not canal
                    if not substituida:
                        del self._canais[cabine]
                if not substituida:
                    self._ao_desconexao(cabine)
