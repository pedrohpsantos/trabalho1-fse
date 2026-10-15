"""Bancada virtual: substitui a ESP32 (cabines + prédio + dashboard) e o hardware de GPIO.

A `Bancada` é determinística: o tempo só avança em `avancar(dt)`. Em testes e nos
experimentos de PID é avançada manualmente (tempo virtual); em execução normal um
`ExecutorTempoReal` a avança em tempo de parede.
"""

import threading
import time
from dataclasses import dataclass, field

from ..constantes import (
    ENDERECO_PREDIO,
    ENDERECOS_CABINES,
    NUM_ANDARES,
    NUM_CABINES,
    Cenario,
    PortaEstado,
    RegCabine,
)
from .fisica import CabineFisica, Geometria, ParametrosPlanta, gerar_geometria
from .predio import PredioSimulado


@dataclass
class MetricasBancada:
    max_soma_corrente_ma: int = 0
    tempo_sobrecarga_s: float = 0.0
    amostras: list[tuple[float, int, int]] = field(default_factory=list)  # (t, soma, barramento)


class Bancada:
    def __init__(
        self,
        semente: int = 1,
        params: ParametrosPlanta | None = None,
        posicoes_iniciais: dict[int, float] | None = None,
        geometrias: dict[int, Geometria] | None = None,
    ) -> None:
        self.t = 0.0
        self.lock = threading.RLock()
        self.params = params or ParametrosPlanta()
        self.cabines: dict[int, CabineFisica] = {}
        for n in range(1, NUM_CABINES + 1):
            geo = (geometrias or {}).get(n) or gerar_geometria(n, semente)
            inicial = (posicoes_iniciais or {}).get(n)
            self.cabines[n] = CabineFisica(n, geo, self.params, inicial, semente)
        self.predio = PredioSimulado()
        self.metricas = MetricasBancada()
        self._pos_reg = {n: c.posicao_absoluta_mm for n, c in self.cabines.items()}
        self._proxima_amostra_pos = 1.0
        self._porta_anterior = {n: c.porta for n, c in self.cabines.items()}

    # --- tempo ------------------------------------------------------------------
    def avancar(self, dt: float) -> None:
        with self.lock:
            passo_max = 0.005
            restante = dt
            while restante > 1e-9:
                passo = min(passo_max, restante)
                self._passo(passo)
                restante -= passo

    def _passo(self, dt: float) -> None:
        self.t += dt
        for cabine in self.cabines.values():
            cabine.avancar(dt)
        self.predio.atualizar(self.t)
        for n, cabine in self.cabines.items():
            anterior = self._porta_anterior[n]
            if cabine.porta == PortaEstado.ABERTA and anterior != PortaEstado.ABERTA and cabine.nivelado:
                self.predio.porta_aberta(n, cabine.andar_proximo, self.t)
            self._porta_anterior[n] = cabine.porta
        if self.t >= self._proxima_amostra_pos:
            self._proxima_amostra_pos += 1.0
            self._pos_reg = {n: c.posicao_absoluta_mm for n, c in self.cabines.items()}
        soma = sum(c.corrente_ma for c in self.cabines.values())
        limite = self.predio.barramento_max_ma(self.t)
        m = self.metricas
        m.max_soma_corrente_ma = max(m.max_soma_corrente_ma, soma)
        if soma > limite:
            m.tempo_sobrecarga_s += dt

    # --- MODBUS -----------------------------------------------------------------------
    def ler(self, endereco: int, inicio: int, qtd: int) -> list[int] | None:
        with self.lock:
            if endereco == ENDERECO_PREDIO:
                tabela = self.predio.registradores(self.t)
            else:
                cabine = self._cabine_por_endereco(endereco)
                if cabine is None:
                    return None
                tabela = cabine.registradores(self._pos_reg[cabine.numero])
            if qtd <= 0 or inicio + qtd > len(tabela):
                return None
            return tabela[inicio : inicio + qtd]

    def escrever(self, endereco: int, inicio: int, valores: list[int]) -> int:
        """Aplica escrita; devolve 0 (ok) ou o código de exceção MODBUS."""
        with self.lock:
            if endereco == ENDERECO_PREDIO:
                if inicio + len(valores) > 16:
                    return 0x02
                for i, valor in enumerate(valores):
                    if not self.predio.escrever(inicio + i, valor, self.t):
                        return 0x03 if inicio + i == 10 else 0x02
                return 0
            cabine = self._cabine_por_endereco(endereco)
            if cabine is None:
                return 0x02
            if inicio != RegCabine.PORTA_COMANDO or len(valores) != 1:
                return 0x02
            if valores[0] not in (0, 1, 2):
                return 0x03
            cabine.comandar_porta(valores[0])
            return 0

    def _cabine_por_endereco(self, endereco: int) -> CabineFisica | None:
        for n, addr in ENDERECOS_CABINES.items():
            if addr == endereco:
                return self.cabines[n]
        return None

    # --- cenários e consulta -------------------------------------------------------------
    def iniciar_cenario(self, cenario: Cenario, semente: int = 1, intervalo_medio_s: float = 8.0) -> None:
        with self.lock:
            self.predio.iniciar_cenario(cenario, self.t, semente, intervalo_medio_s)

    def registrar_chamada(self, origem: int, destino: int) -> int:
        if not (0 <= origem < NUM_ANDARES and 0 <= destino < NUM_ANDARES) or origem == destino:
            raise ValueError("chamada inválida")
        with self.lock:
            return self.predio.registrar_chamada(origem, destino, self.t).id

    def violacoes(self) -> dict[str, int]:
        with self.lock:
            return {
                "porta_fora_nivel": sum(c.violacoes_porta_fora_nivel for c in self.cabines.values()),
                "movimento_porta_aberta": sum(c.violacoes_movimento_porta_aberta for c in self.cabines.values()),
                "chamadas_perdidas": self.predio.chamadas_perdidas,
                "fim_de_curso": sum(1 for c in self.cabines.values() if c.falha),
            }


class ExecutorTempoReal:
    """Avança a bancada em tempo de parede (passos de ~5 ms) numa thread própria."""

    def __init__(self, bancada: Bancada, passo_s: float = 0.005, fator: float = 1.0) -> None:
        self.bancada = bancada
        self.passo_s = passo_s
        self.fator = fator
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._executar, name="bancada", daemon=True)

    def iniciar(self) -> None:
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _executar(self) -> None:
        anterior = time.monotonic()
        while not self._parar.wait(self.passo_s):
            agora = time.monotonic()
            dt = min(agora - anterior, 0.1)
            anterior = agora
            self.bancada.avancar(dt * self.fator)
