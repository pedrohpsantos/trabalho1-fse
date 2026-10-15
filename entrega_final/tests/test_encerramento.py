"""SIGINT/SIGTERM: parada segura dos atuadores e encerramento ordenado dos processos."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from elevador.cabine.app import ServidorDistribuido
from elevador.cabine.config import carregar_config
from elevador.cabine.hal.sim import GpioVirtual
from elevador.constantes import Direcao
from elevador.sim.bancada import Bancada
from elevador.util.sinais import EncerramentoSeguro

RAIZ = Path(__file__).resolve().parent.parent


def test_encerramento_seguro_aciona_callbacks_uma_unica_vez():
    chamadas = []
    enc = EncerramentoSeguro()
    enc.ao_encerrar(lambda: chamadas.append(1))
    enc.solicitar()
    enc.solicitar()
    assert enc.evento.is_set() and chamadas == [1]


def test_encerrar_cabine_zera_pwm_e_aplica_freio():
    bancada = Bancada()
    hal = GpioVirtual(bancada, 1)
    servidor = ServidorDistribuido(carregar_config(RAIZ / "config" / "cabine1.json"), hal)
    servidor.rede.iniciar = lambda: None  # sem TCP neste teste
    servidor.rede.parar = lambda: None
    hal.definir_motor(Direcao.SUBIR, 70.0)  # motor em plena marcha
    assert bancada.cabines[1].duty == 70.0
    servidor.iniciar()
    servidor.encerrar()
    fisica = bancada.cabines[1]
    assert fisica.duty == 0.0 and fisica.dir == Direcao.FREIO  # PWM 0% e DIR1=DIR2=1


def _iniciar(modulo_args, env):
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, "-m", *modulo_args],
        cwd=RAIZ,
        env=env,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def _sinalizar(proc):
    proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)


def test_processos_encerram_com_sinal_e_central_salva_estado(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(RAIZ)}
    cfg = tmp_path / "central.json"
    cfg.write_text(json.dumps({"escuta": {"host": "127.0.0.1", "porta": 5917}, "pasta_dados": str(tmp_path / "dados")}))
    bancada = _iniciar(["elevador.sim", "--porta", "5918"], env)
    time.sleep(1.5)
    central = _iniciar(
        ["elevador.central", "--config", str(cfg), "--mock", "--bancada", "127.0.0.1:5918", "--sem-painel"], env
    )
    cab_cfg = tmp_path / "cabine1.json"
    cab_cfg.write_text('{"cabine": 1, "central": {"host": "127.0.0.1", "porta": 5917}}')
    cabine = _iniciar(["elevador.cabine", "--config", str(cab_cfg), "--mock", "--bancada", "127.0.0.1:5918"], env)
    procs = [cabine, central, bancada]
    try:
        time.sleep(4.0)
        assert all(p.poll() is None for p in procs), "algum processo morreu na partida"
        for p in procs:
            _sinalizar(p)
        codigos = []
        for p in procs:
            try:
                codigos.append(p.wait(timeout=10))
            except subprocess.TimeoutExpired:
                pytest.fail("processo não encerrou após o sinal")
        assert codigos == [0, 0, 0]
        assert (tmp_path / "dados" / "estado.json").exists()  # Central persistiu o estado ao sair
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
