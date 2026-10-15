"""Grupo completo em tempo virtual: bancada + 3 cabines reais + o `ServicoCentral` real.

Mesmo código de produção (runtime das cabines, Despacho, repartição de Cota, persistência),
com rede e gerente MODBUS substituídos por equivalentes síncronos e o relógio avançado
manualmente. Permite rodar um Cenário de Tráfego de 5 min em segundos, de forma
determinística, e verificar os invariantes da Seção 7.5.
"""

import tempfile
from concurrent.futures import Future
from pathlib import Path

from ..cabine.controlador import CabineRuntime
from ..cabine.hal.sim import GpioVirtual
from ..central.bmp280 import Bmp280Simulado
from ..central.config import ConfigCentral
from ..central.persistencia import Persistencia
from ..central.servico import ServicoCentral
from ..constantes import NUM_CABINES
from ..modbus.cliente import ClienteModbus
from ..modbus.simulador import ApiSimulador
from ..protocolo.mensagens import VERSAO, validar
from .bancada import Bancada
from .harness import EPOCH, config_padrao
from .uart_sim import UartVirtual


class GerenteSincrono:
    """Executa as transações MODBUS na hora (a fila prioritária real é testada à parte)."""

    def __init__(self, api: ApiSimulador) -> None:
        self.api = api

    def _rodar(self, funcao) -> Future:
        futuro: Future = Future()
        try:
            futuro.set_result(funcao(self.api))
        except Exception as erro:  # noqa: BLE001
            futuro.set_exception(erro)
        return futuro

    def submeter(self, funcao, prioridade: int = 0) -> Future:
        return self._rodar(funcao)

    def executar(self, funcao, prioridade: int = 0, timeout: float = 0.0):
        return self._rodar(funcao).result()


class RedeVirtual:
    """Entrega as mensagens do Central direto aos runtimes, validando o esquema do protocolo."""

    def __init__(self, runtimes: dict[int, CabineRuntime]) -> None:
        self.runtimes = runtimes
        self.conectadas = dict.fromkeys(range(1, NUM_CABINES + 1), True)
        self.enviadas: list[tuple[int, str, dict]] = []

    def conectada(self, cabine: int) -> bool:
        return self.conectadas.get(cabine, False)

    def enviar(self, cabine: int, tipo: str, /, **campos) -> bool:
        if not self.conectadas.get(cabine):
            return False
        msg = validar({"t": tipo, "v": VERSAO, **campos})
        self.enviadas.append((cabine, tipo, campos))
        self.runtimes[cabine].receber(msg)
        return True


class EnlaceVirtual:
    """Enlace de uma cabine: leva suas mensagens ao `ServicoCentral`."""

    def __init__(self, grupo: "GrupoVirtual", cabine: int) -> None:
        self.grupo, self.cabine = grupo, cabine

    @property
    def conectado(self) -> bool:
        return self.grupo.rede.conectadas[self.cabine]

    def enviar(self, tipo: str, /, **campos) -> bool:
        if not self.conectado:
            return False
        msg = validar({"t": tipo, "v": VERSAO, **campos})
        self.grupo.servico._ao_mensagem(self.cabine, msg)
        return True


class GrupoVirtual:
    def __init__(
        self, semente: int = 1, pasta_dados: str | Path | None = None, temperatura_c: float = 25.0, pid=None
    ) -> None:
        from ..cabine.config import ParametrosPid

        self.bancada = Bancada(semente=semente)
        self.runtimes: dict[int, CabineRuntime] = {}
        self.rede = RedeVirtual(self.runtimes)
        pid = pid or ParametrosPid(0.5, 0.02, 5.0)
        for n in range(1, NUM_CABINES + 1):
            hal = GpioVirtual(self.bancada, n)
            rt = CabineRuntime(config_padrao(n, pid), hal, EnlaceVirtual(self, n), relogio=self.agora)
            hal.registrar_callbacks(rt.ao_sensor, rt.ao_cortina)
            self.runtimes[n] = rt
        self._pasta = Path(pasta_dados) if pasta_dados else Path(tempfile.mkdtemp(prefix="grupo_virtual_"))
        cliente = ClienteModbus(UartVirtual(self.bancada), "038442", intervalo_retry_s=0)
        self.sensor = Bmp280Simulado(temperatura_c)
        self.servico = ServicoCentral(
            ConfigCentral(pasta_dados=str(self._pasta)),
            GerenteSincrono(ApiSimulador(cliente)),  # type: ignore[arg-type]
            self.sensor,
            Persistencia(self._pasta),
            relogio=self.agora,
        )
        self.servico.rede = self.rede  # type: ignore[assignment]
        self.cotas_violadas = 0
        self.max_soma_cotas = 0
        self._prox = {"chamadas": 0.0, "cotas": 0.0, "poll": 0.0, "ambiente": 0.0, "estado": 2.0}
        for n, rt in self.runtimes.items():
            self.servico._ao_hello(n, rt.mensagem_hello())

    def agora(self) -> float:
        return EPOCH + self.bancada.t

    def passo(self, dt: float = 0.05) -> None:
        self.bancada.avancar(dt)
        t, s = self.bancada.t, self.servico
        if t >= self._prox["ambiente"]:
            self._prox["ambiente"] = t + s.cfg.periodo_ambiente_s
            s._ciclo_ambiente()
        if t >= self._prox["poll"]:
            self._prox["poll"] = t + s.cfg.periodo_poll_cabine_s
            for n in self.runtimes:
                s._ler_e_empurrar(n, 0)
        if t >= self._prox["chamadas"]:
            self._prox["chamadas"] = t + s.cfg.periodo_chamadas_s
            estado = s.gerente.executar(lambda api: api.ler_predio())
            s.predio, s._t_predio = estado, self.agora()
            s._ultimo_cenario = estado.cenario_ativo
            s._processar_fila(estado)
        if t >= self._prox["cotas"]:
            self._prox["cotas"] = t + 1.0
            s._renovar_cotas()
            soma = sum(s._cotas.values())
            self.max_soma_cotas = max(self.max_soma_cotas, soma)
            if soma > s.barramento_atual():
                self.cotas_violadas += 1
        if t >= self._prox["estado"]:
            self._prox["estado"] = t + 2.0
            s._estacionar()
        for rt in self.runtimes.values():
            rt.atender_interrupcoes()
            rt.tick()

    def rodar(self, segundos: float, ate=None) -> bool:
        for _ in range(int(round(segundos / 0.05))):
            self.passo()
            if ate is not None and ate():
                return True
        return ate is None or bool(ate())
