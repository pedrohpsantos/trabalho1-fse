"""Gráficos e métricas dos experimentos na bancada REAL (rasp32), a partir dos CSVs da malha.

Os CSVs vêm de `python -m elevador.cabine ... --registro-csv dados/hw/malha_cabN.csv` e as
posições calibradas, da linha "calibração concluída" de `dados/hw/cabineN.log`.

Uso: python scripts/graficos_bancada.py <pasta_com_csv_e_logs> [pasta_saida]
"""

import ast
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

TOLERANCIA_MM = 10
JANELA_SOMA_S = 0.5  # agregação da soma das correntes (o espelho MODBUS chega a ~5 Hz)


def ler_csv(caminho: Path) -> list[dict]:
    with open(caminho, encoding="utf-8") as arq:
        return list(csv.DictReader(arq))


def ler_centros(log: Path) -> list[float] | None:
    centros = None
    for linha in log.read_text(encoding="utf-8").splitlines():
        if "calibração concluída:" in linha:
            centros = ast.literal_eval(linha.split("calibração concluída:", 1)[1].strip())
    return centros


def chegadas(linhas: list[dict], centros: list[float]) -> list[dict]:
    """Uma entrada por abertura de porta: erro de nivelamento no instante do pedido de abertura."""
    eventos, anterior, inicio = [], None, None
    for linha in linhas:
        estado = linha["estado"]
        if estado == "movendo" and anterior != "movendo":
            inicio = float(linha["t"])
        if estado == "abrindo_porta" and anterior == "movendo":
            pos = float(linha["pos_mm"])
            andar = min(range(len(centros)), key=lambda i: abs(pos - centros[i]))
            eventos.append(
                {
                    "t": float(linha["t"]),
                    "andar": andar,
                    "erro_mm": pos - centros[andar],
                    "duracao_s": float(linha["t"]) - inicio if inicio is not None else None,
                }
            )
        anterior = estado
    return eventos


def maior_viagem(linhas: list[dict]) -> list[dict]:
    """Trecho MOVENDO contínuo de maior deslocamento (para o gráfico de trajetória)."""
    trechos, atual = [], []
    for linha in linhas:
        if linha["estado"] == "movendo":
            atual.append(linha)
        elif atual:
            trechos.append(atual)
            atual = []
    if atual:
        trechos.append(atual)
    if not trechos:
        return []
    return max(trechos, key=lambda tr: abs(float(tr[-1]["pos_mm"]) - float(tr[0]["pos_mm"])))


def main() -> int:
    origem = Path(sys.argv[1])
    saida = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "docs" / "graficos"
    saida.mkdir(parents=True, exist_ok=True)
    resumo: dict = {}
    fig_t, eixos_t = plt.subplots(3, 1, figsize=(10, 9), sharex=False)
    fig_n, eixo_n = plt.subplots(figsize=(10, 4.5))
    fig_r, eixo_r = plt.subplots(figsize=(10, 4.5))
    fig_c, (eixo_c, eixo_s) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    correntes: dict[int, dict[int, int]] = {}  # cabine -> janela de tempo -> corrente
    t_base = None
    for n in (1, 2, 3):
        csv_n, log_n = origem / f"malha_cab{n}.csv", origem / f"cabine{n}.log"
        if not csv_n.exists() or not log_n.exists():
            continue
        linhas, centros = ler_csv(csv_n), ler_centros(log_n)
        if not centros:
            continue
        t0 = float(linhas[0]["t"])
        t_base = t0 if t_base is None else min(t_base, t0)
        evs = chegadas(linhas, centros)
        erros = [e["erro_mm"] for e in evs]
        duracoes = [e["duracao_s"] for e in evs if e["duracao_s"]]
        periodos = [float(b["t"]) - float(a["t"]) for a, b in zip(linhas, linhas[1:], strict=False)]
        correcoes = []
        if "correcao_mm" in linhas[0]:
            vistos = 0
            for linha in linhas:
                k = int(linha["reancoragens"])
                if k > vistos:
                    vistos = k
                    correcoes.append((float(linha["t"]) - t0, int(linha["correcao_mm"])))
        resumo[f"cabine{n}"] = {
            "centros_mm": centros,
            "chegadas": len(evs),
            "erro_max_abs_mm": round(max((abs(e) for e in erros), default=0.0), 1),
            "erro_medio_abs_mm": round(sum(abs(e) for e in erros) / len(erros), 2) if erros else None,
            "fora_da_tolerancia": sum(abs(e) > TOLERANCIA_MM for e in erros),
            "viagem_media_s": round(sum(duracoes) / len(duracoes), 1) if duracoes else None,
            "periodo_malha_medio_ms": round(1000 * sum(periodos) / len(periodos), 2),
            "periodo_malha_p99_ms": round(1000 * sorted(periodos)[int(0.99 * (len(periodos) - 1))], 2),
            "reancoragens": int(linhas[-1]["reancoragens"]),
            "correcao_max_abs_mm": max((abs(c) for _, c in correcoes), default=None),
        }
        viagem = maior_viagem(linhas)
        if viagem:
            ts = [float(v["t"]) - float(viagem[0]["t"]) for v in viagem]
            ax = eixos_t[n - 1]
            ax.plot(ts, [float(v["pos_mm"]) for v in viagem], label="posição (encoder)")
            ax.plot(ts, [float(v["alvo_mm"]) for v in viagem], "--", label="referência")
            ax.set_ylabel("mm")
            ax2 = ax.twinx()
            ax2.plot(ts, [float(v["duty"]) for v in viagem], color="tab:red", alpha=0.6, label="duty")
            ax2.plot(ts, [100 * float(v["cota_ma"]) / 6000 for v in viagem], ":", color="tab:gray", label="duty_max")
            ax2.set_ylabel("duty %")
            ax.set_title(f"Cabine {n}: maior viagem registrada")
            ax.legend(loc="upper left", fontsize=8)
            ax2.legend(loc="lower right", fontsize=8)
        eixo_n.scatter([e["t"] - t0 for e in evs], erros, label=f"cabine {n}", s=25)
        if "corrente_ma" in linhas[0]:
            amostras = [(float(r["t"]), int(r["corrente_ma"]), int(r["cota_ma"])) for r in linhas if r["corrente_ma"]]
            correntes[n] = {int(t / JANELA_SOMA_S): i for t, i, _ in amostras}
            excesso = [i - c for _, i, c in amostras if i > c]
            resumo[f"cabine{n}"]["corrente_acima_da_cota_pct_amostras"] = round(100 * len(excesso) / len(amostras), 2)
            resumo[f"cabine{n}"]["corrente_acima_da_cota_max_ma"] = max(excesso, default=0)
            eixo_c.plot([t for t, _, _ in amostras], [i for _, i, _ in amostras], lw=0.6, label=f"corrente cab {n}")
            eixo_c.plot([t for t, _, _ in amostras], [c for _, _, c in amostras], ":", lw=0.8, label=f"Cota cab {n}")
        if correcoes:
            eixo_r.scatter([c[0] for c in correcoes], [c[1] for c in correcoes], s=12, label=f"cabine {n}")
    eixos_t[-1].set_xlabel("s desde a partida")
    fig_t.tight_layout()
    fig_t.savefig(saida / "06_real_trajetoria.png", dpi=110)
    for eixo in (eixo_n,):
        eixo.axhline(TOLERANCIA_MM, color="red", ls="--")
        eixo.axhline(-TOLERANCIA_MM, color="red", ls="--")
    eixo_n.set_title("Bancada real: erro de nivelamento (encoder − andar calibrado) ao pedir abertura da porta")
    eixo_n.set_xlabel("s desde a partida")
    eixo_n.set_ylabel("erro (mm)")
    eixo_n.legend()
    fig_n.tight_layout()
    fig_n.savefig(saida / "07_real_nivelamento.png", dpi=110)
    eixo_r.set_title("Bancada real: correção aplicada em cada reancoragem por bandeirola")
    eixo_r.set_xlabel("s desde a partida")
    eixo_r.set_ylabel("correção (mm)")
    eixo_r.legend()
    fig_r.tight_layout()
    fig_r.savefig(saida / "08_real_reancoragem.png", dpi=110)
    if len(correntes) == 3 and t_base is not None:
        janelas = sorted(set.intersection(*(set(c) for c in correntes.values())))
        soma = [sum(correntes[n][j] for n in correntes) for j in janelas]
        eixo_s.plot([j * JANELA_SOMA_S for j in janelas], soma, lw=0.7, label="soma das correntes medidas")
        resumo["soma_correntes_max_ma"] = max(soma)
        resumo["janelas_soma_acima_de_3000_pct"] = round(100 * sum(x > 3000 for x in soma) / len(soma), 2)
        eixo_s.axhline(3000, color="red", ls="--", label="barramento (piso do watchdog: 3000 mA)")
        eixo_s.set_ylabel("mA")
        eixo_s.set_xlabel("tempo monotônico (s)")
        eixo_s.legend(fontsize=8)
        eixo_c.set_ylabel("mA")
        eixo_c.set_title("Bancada real: corrente medida (MODBUS) x Cota concedida, e soma x barramento")
        eixo_c.legend(fontsize=7, ncol=3)
        fig_c.tight_layout()
        fig_c.savefig(saida / "09_real_corrente.png", dpi=110)
    (saida / "resumo_bancada_real.json").write_text(json.dumps(resumo, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(resumo, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
