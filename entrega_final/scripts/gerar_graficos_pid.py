"""Experimentos de sintonia do PID, rampa, Cota e reancoragem (gráficos da Seção 7.1 / README).

Executa o MESMO runtime dos Servidores Distribuídos em tempo virtual contra a bancada
virtual e grava os PNGs em docs/graficos/.  Uso: python scripts/gerar_graficos_pid.py
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from elevador.cabine.config import ParametrosPid  # noqa: E402
from elevador.cabine.controlador import EstadoCabine  # noqa: E402
from elevador.sim.bancada import Bancada  # noqa: E402
from elevador.sim.harness import HarnessCabine  # noqa: E402

SAIDA = RAIZ / "docs" / "graficos"

PID_PADRAO = ParametrosPid(0.5, 0.05, 40.0)
PID_SINTONIZADO = ParametrosPid(0.5, 0.02, 5.0)


@dataclass
class Amostras:
    t: list[float]
    pos: list[float]
    alvo: list[float]
    duty: list[float]
    corrente: list[float]
    erro_real: list[float]  # posição verdadeira - centro real do andar de destino


def preparar(pid: ParametrosPid, cota_ma: int = 4000, semente: int = 1) -> HarnessCabine:
    h = HarnessCabine(bancada=Bancada(semente=semente), pid=pid, cota_ma=cota_ma)
    if not h.rodar(400, lambda: h.runtime.estado == EstadoCabine.OCIOSA):
        raise RuntimeError("calibração não terminou")
    return h


def viagem(h: HarnessCabine, destino: int, limite_s: float = 90.0) -> Amostras:
    a = Amostras([], [], [], [], [], [])
    h.runtime.receber({"t": "ir_andar", "andar": destino})
    t0 = h.bancada.t
    geo = h.fisica.geometria
    aberturas = h.runtime.aberturas
    for _ in range(int(limite_s / 0.05)):
        h.passo()
        a.t.append(h.bancada.t - t0)
        a.pos.append(h.hal.posicao())
        a.alvo.append(h.runtime.ctrl.alvo if h.runtime.estado == EstadoCabine.MOVENDO else float("nan"))
        a.duty.append(h.fisica.duty * (1 if h.fisica.v >= 0 else -1))
        a.corrente.append(h.fisica.corrente_ma)
        a.erro_real.append(h.fisica.x - geo.andares_mm[destino])
        if h.runtime.aberturas > aberturas:
            break
    return a


def grafico_degrau() -> None:
    """Resposta de posição e duty para diferentes ganhos (viagem 0 -> 1)."""
    conjuntos = [
        ("Padrão do enunciado (0,5 / 0,05 / 40)", PID_PADRAO),
        ("Kd menor (0,5 / 0,05 / 10)", ParametrosPid(0.5, 0.05, 10.0)),
        ("Sintonizado (0,5 / 0,02 / 5)", PID_SINTONIZADO),
        ("Kp alto (1,0 / 0,02 / 5)", ParametrosPid(1.0, 0.02, 5.0)),
    ]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for rotulo, pid in conjuntos:
        h = preparar(pid)
        a = viagem(h, 1)
        ref = a.alvo[1] if a.alvo[1] == a.alvo[1] else a.pos[-1]
        ax1.plot(a.t, a.pos, label=f"{rotulo}: {a.t[-1]:.1f} s")
        ax2.plot(a.t, a.duty, label=rotulo)
    ax1.axhline(ref, color="k", ls="--", lw=0.8, label="referência (andar 1)")
    ax1.set_ylabel("posição do encoder [mm]")
    ax1.set_title("Resposta ao degrau de posição (andar 0 → 1): efeito dos ganhos")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    ax2.set_ylabel("duty comandado [%]")
    ax2.set_xlabel("tempo [s]")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(SAIDA / "01_degrau_ganhos.png", dpi=130)
    plt.close(fig)


def grafico_nivelamento() -> None:
    """Erro real de nivelamento após 30 viagens aleatórias com o PID sintonizado."""
    import random

    h = preparar(PID_SINTONIZADO)
    rng = random.Random(7)
    erros, atual = [], 0
    for _ in range(30):
        destino = rng.choice([f for f in range(6) if f != atual])
        a = viagem(h, destino)
        erros.append(a.erro_real[-1])
        atual = destino
        h.rodar(8, lambda: h.runtime.estado == EstadoCabine.OCIOSA)
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(range(1, len(erros) + 1), erros, color="tab:blue")
    ax.axhspan(-10, 10, color="tab:green", alpha=0.2, label="tolerância ±10 mm")
    ax.set_xlabel("viagem")
    ax.set_ylabel("erro real de nivelamento [mm]")
    ax.set_title(f"Nivelamento na abertura da porta (máx. {max(abs(e) for e in erros):.1f} mm)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(SAIDA / "02_nivelamento.png", dpi=130)
    plt.close(fig)


def grafico_rampa() -> None:
    """Rampa de aceleração (25 %/s) versus partida em degrau, vencendo o atrito estático."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for rotulo, rampa in (("rampa 25 %/s", 25.0), ("sem rampa (degrau)", 10_000.0)):
        pid = ParametrosPid(0.5, 0.02, 5.0, 15.0, rampa)
        h = preparar(pid, cota_ma=6000)
        a = viagem(h, 2)
        n = min(len(a.t), 120)
        ax1.plot(a.t[:n], a.duty[:n], label=rotulo)
        ax2.plot(a.t[:n], a.corrente[:n], label=rotulo)
    ax1.axhline(10, color="r", ls=":", label="atrito estático (10 %)")
    ax1.set_ylabel("duty [%]")
    ax1.legend(fontsize=8)
    ax1.set_title("Partida: rampa de aceleração e atrito estático")
    ax2.set_ylabel("corrente [mA]")
    ax2.set_xlabel("tempo [s]")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(SAIDA / "03_rampa.png", dpi=130)
    plt.close(fig)


def grafico_cota() -> None:
    """Saturação do PID pela Cota: duty e corrente respeitam duty_max = cota/6000*100."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for cota in (1000, 3000, 6000):
        h = preparar(PID_SINTONIZADO, cota_ma=cota)
        a = viagem(h, 2, limite_s=150)
        ax1.plot(a.t, [abs(d) for d in a.duty], label=f"Cota {cota} mA (duty_max {cota / 60:.1f} %)")
        ax2.plot(a.t, a.corrente, label=f"Cota {cota} mA")
        ax2.axhline(cota, ls=":", lw=0.8, color="gray")
    ax1.set_ylabel("duty [%]")
    ax1.set_title("Saturação do PID pela Cota de Potência (viagem 0 → 2)")
    ax1.legend(fontsize=8)
    ax2.set_ylabel("corrente [mA]")
    ax2.set_xlabel("tempo [s]")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(SAIDA / "04_cota.png", dpi=130)
    plt.close(fig)


def grafico_reancoragem() -> None:
    """Deriva do encoder (verdadeira - contada) com e sem reancoragem nas bandeirolas."""
    import random

    fig, ax = plt.subplots(figsize=(9, 4))
    for rotulo, ativa in (("com reancoragem", True), ("sem reancoragem", False)):
        h = preparar(PID_SINTONIZADO)
        if not ativa:
            h.runtime._reancorar = lambda *a, **k: None
            h.runtime._reancorar_borda = lambda *a, **k: 0
        rng = random.Random(3)
        atual, deriva = 0, []
        for _ in range(25):
            destino = rng.choice([f for f in range(6) if f != atual])
            viagem(h, destino)
            h.rodar(8, lambda h=h: h.runtime.estado == EstadoCabine.OCIOSA)
            geo = h.fisica.geometria
            contado = h.hal.posicao() - h.runtime.centros[destino]
            real = h.fisica.x - geo.andares_mm[destino]
            deriva.append(real - contado)
            atual = destino
        ax.plot(range(1, 26), deriva, marker="o", ms=3, label=rotulo)
    ax.axhspan(-10, 10, color="tab:green", alpha=0.15, label="±10 mm")
    ax.set_xlabel("viagem")
    ax.set_ylabel("posição real − posição contada [mm]")
    ax.set_title("Deriva do contador de quadratura e efeito da reancoragem")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(SAIDA / "05_reancoragem.png", dpi=130)
    plt.close(fig)


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    for funcao in (grafico_degrau, grafico_nivelamento, grafico_rampa, grafico_cota, grafico_reancoragem):
        print(f"gerando {funcao.__name__} ...", flush=True)
        funcao()
    print(f"gráficos em {SAIDA}")


if __name__ == "__main__":
    main()
