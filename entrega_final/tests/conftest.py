"""Fixtures compartilhadas: harness em tempo virtual (a calibração leva ~1 s de CPU)."""

import pytest

from elevador.cabine.config import ParametrosPid
from elevador.cabine.controlador import EstadoCabine
from elevador.sim.bancada import Bancada
from elevador.sim.harness import HarnessCabine

PID_TESTE = ParametrosPid(0.5, 0.02, 5.0)


def criar_harness_calibrado(**kwargs) -> HarnessCabine:
    kwargs.setdefault("pid", PID_TESTE)
    bancada = kwargs.pop("bancada", None) or Bancada()
    h = HarnessCabine(bancada=bancada, **kwargs)
    assert h.rodar(400, lambda: h.runtime.estado == EstadoCabine.OCIOSA), "calibração não terminou"
    return h


def ir_para(h: HarnessCabine, andar: int, limite_s: float = 120.0) -> bool:
    """Comanda uma parada manual e espera a porta abrir nivelada."""
    abertas = h.runtime.aberturas
    h.runtime.receber({"t": "ir_andar", "andar": andar})
    return h.rodar(limite_s, lambda: h.runtime.aberturas > abertas)


@pytest.fixture
def harness() -> HarnessCabine:
    return criar_harness_calibrado()


@pytest.fixture
def harness_sem_calibrar() -> HarnessCabine:
    return HarnessCabine(pid=PID_TESTE)
