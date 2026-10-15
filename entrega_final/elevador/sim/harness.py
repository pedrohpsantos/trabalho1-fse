"""Harness em tempo virtual: uma cabine real (runtime) + bancada + Central mínimo.

Executa o MESMO código dos Servidores Distribuídos, mas com o relógio avançado manualmente,
tornando testes e experimentos de PID determinísticos e muito mais rápidos que o tempo real.
"""

from ..cabine.config import ConfigCabine, ParametrosCabine, ParametrosPid, carregar_config
from ..cabine.controlador import CabineRuntime
from ..cabine.hal.sim import GpioVirtual
from ..constantes import ENDERECOS_CABINES, PortaComando
from .bancada import Bancada

EPOCH = 1_700_000_000.0
PERIODO_MODBUS_S = 0.2


class CentralMinimo:
    """Enlace de teste: espelha o MODBUS da bancada e concede Cota fixa (ou nenhuma)."""

    def __init__(self, bancada: Bancada, cabine: int, cota_ma: int | None = 4000) -> None:
        self.bancada = bancada
        self.cabine = cabine
        self.cota_ma = cota_ma  # None => Central "mudo" (sem renovar a Cota)
        self.conectado = True
        self.runtime: CabineRuntime | None = None
        self.eventos: list[dict] = []
        self.telemetrias: list[dict] = []
        self.comandos_porta: list[int] = []
        self._t_modbus = float("-inf")
        self._t_cota = float("-inf")
        self._seq = 0

    @property
    def agora(self) -> float:
        return EPOCH + self.bancada.t

    def enviar(self, tipo: str, /, **campos) -> bool:
        if not self.conectado:
            return False
        if tipo == "porta_cmd":
            self.comandos_porta.append(campos["cmd"])
            self.bancada.escrever(ENDERECOS_CABINES[self.cabine], 3, [int(PortaComando(campos["cmd"]))])
        elif tipo == "pedir_modbus":
            self._empurrar_modbus()
        elif tipo == "evento":
            self.eventos.append(campos)
        elif tipo == "telemetria":
            self.telemetrias.append(campos)
        return True

    def _empurrar_modbus(self) -> None:
        regs = self.bancada.ler(ENDERECOS_CABINES[self.cabine], 0, 9)
        self._t_modbus = self.bancada.t
        self.runtime.receber(
            {
                "t": "modbus",
                "andar_atual": regs[0],
                "nivelado": regs[1],
                "porta_estado": regs[2],
                "corrente_ma": regs[4],
                "falha": regs[8],
                "ts": self.agora,
            }
        )

    def passo(self) -> None:
        if not self.conectado:
            return
        if self.bancada.t - self._t_modbus >= PERIODO_MODBUS_S:
            self._empurrar_modbus()
        if self.cota_ma is not None and self.bancada.t - self._t_cota >= 1.0:
            self._t_cota = self.bancada.t
            self._seq += 1
            self.runtime.receber(
                {"t": "cota", "cota_ma": self.cota_ma, "ts": self.agora, "validade_s": 2.0, "seq": self._seq}
            )

    def desconectar(self) -> None:
        self.conectado = False
        self.runtime.conexao_perdida()


def config_padrao(cabine: int, pid: ParametrosPid | None = None) -> ConfigCabine:
    base = {
        "cabine": cabine,
        "central": {"host": "127.0.0.1", "porta": 5000},
    }
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / "cfg.json"
        caminho.write_text(json.dumps(base), encoding="utf-8")
        cfg = carregar_config(caminho)
    if pid is not None:
        cfg = ConfigCabine(**{**cfg.__dict__, "pid": pid})
    return cfg


class HarnessCabine:
    def __init__(
        self,
        cabine: int = 1,
        bancada: Bancada | None = None,
        cota_ma: int | None = 4000,
        pid: ParametrosPid | None = None,
        params: ParametrosCabine | None = None,
    ) -> None:
        self.bancada = bancada or Bancada()
        cfg = config_padrao(cabine, pid)
        if params is not None:
            cfg = ConfigCabine(**{**cfg.__dict__, "params": params})
        self.central = CentralMinimo(self.bancada, cabine, cota_ma)
        self.hal = GpioVirtual(self.bancada, cabine)
        self.runtime = CabineRuntime(cfg, self.hal, self.central, relogio=lambda: EPOCH + self.bancada.t)
        self.central.runtime = self.runtime
        self.hal.registrar_callbacks(self.runtime.ao_sensor, self.runtime.ao_cortina)
        self.fisica = self.bancada.cabines[cabine]
        self.cabine = cabine

    def passo(self, periodo_s: float = 0.05) -> None:
        self.bancada.avancar(periodo_s)
        self.central.passo()
        self.runtime.atender_interrupcoes()
        self.runtime.tick()

    def rodar(self, segundos: float, ate=None) -> bool:
        """Avança `segundos` de tempo virtual; devolve True se `ate()` ficou verdadeiro."""
        for _ in range(int(round(segundos / 0.05))):
            self.passo()
            if ate is not None and ate():
                return True
        return ate is None or ate()
