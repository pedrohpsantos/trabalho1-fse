"""Ponto de entrada do Servidor Distribuído: `python -m elevador.cabine --config ...`."""

import argparse
import logging
import threading
import time

from ..constantes import PERIODO_PID_S
from ..util.logs import configurar_logs
from ..util.sinais import EncerramentoSeguro
from .config import ConfigCabine, carregar_config
from .controlador import CabineRuntime
from .hal.base import GpioCabine
from .rede import RedeCentral

log = logging.getLogger(__name__)


def criar_hal(cfg: ConfigCabine, mock: bool, bancada: str | None) -> GpioCabine:
    if mock:
        from .hal.sim import GpioRemoto

        host, _, porta = (bancada or "127.0.0.1:5600").partition(":")
        return GpioRemoto(host, int(porta or 5600), cfg.cabine)
    from .hal.rpi import GpioRaspberry

    return GpioRaspberry(cfg.pinos, cfg.params.debounce_cortina_s)


class ServidorDistribuido:
    def __init__(self, cfg: ConfigCabine, hal: GpioCabine) -> None:
        self.cfg = cfg
        self.hal = hal
        self.rede = RedeCentral(cfg.central_host, cfg.central_porta)
        self.runtime = CabineRuntime(cfg, hal, self.rede)
        self.rede.runtime = self.runtime
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._laco, name=f"controle-c{cfg.cabine}")

    def iniciar(self) -> None:
        self.hal.registrar_callbacks(self.runtime.ao_sensor, self.runtime.ao_cortina)
        self.rede.iniciar()
        self._thread.start()

    def _laco(self) -> None:
        """Malha de 50 ms agendada por prazo absoluto; dorme em Event.wait (sem busy-wait)."""
        proximo = time.monotonic()
        while not self._parar.is_set():
            agora = time.monotonic()
            if agora >= proximo:
                self.runtime.tick()
                proximo += PERIODO_PID_S
                if proximo < agora:  # atrasou mais que um período: ressincroniza
                    proximo = agora + PERIODO_PID_S
                continue
            if self.runtime.acordar.wait(timeout=proximo - agora):
                self.runtime.atender_interrupcoes()

    def encerrar(self) -> None:
        """Parada segura: PWM 0 e freio, depois libera sockets e GPIO."""
        self._parar.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            self.runtime.parada_segura()
        finally:
            self.rede.parar()
            self.hal.liberar()
        log.info("cabine %d encerrada com segurança", self.cfg.cabine)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Servidor Distribuído de uma cabine")
    ap.add_argument("--config", required=True, help="arquivo JSON da cabine (config/cabineN.json)")
    ap.add_argument("--mock", action="store_true", help="usa a bancada virtual no lugar do GPIO real")
    ap.add_argument("--bancada", default=None, help="host:porta da bancada virtual (padrão 127.0.0.1:5600)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    cfg = carregar_config(args.config)
    configurar_logs(f"cabine{cfg.cabine}", args.log_level)
    encerramento = EncerramentoSeguro()
    encerramento.instalar()
    servidor = ServidorDistribuido(cfg, criar_hal(cfg, args.mock, args.bancada))
    servidor.iniciar()
    log.info("cabine %d iniciada (%s)", cfg.cabine, "mock" if args.mock else "hardware")
    try:
        encerramento.aguardar()
    finally:
        servidor.encerrar()
    return 0
