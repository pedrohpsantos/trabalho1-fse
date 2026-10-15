"""Sobe bancada virtual + Servidor Central + 3 Servidores Distribuídos (processos independentes).

Uso: python scripts/run_all_mock.py [--cenario 1|2|3] [--atraso 120] [--temp 25]
Ctrl+C encerra todos com parada segura (SIGINT repassado a cada processo).
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cenario", type=int, default=0)
    ap.add_argument("--atraso", type=float, default=120.0, help="segundos até iniciar o cenário")
    ap.add_argument("--temp", type=float, default=25.0, help="temperatura do BMP280 virtual (°C)")
    ap.add_argument("--porta-bancada", default="5600")
    ap.add_argument("--semente", default="1")
    ap.add_argument("--dados", default=str(RAIZ / "dados"))
    args = ap.parse_args()

    env = {**os.environ, "PYTHONPATH": str(RAIZ), "PYTHONUNBUFFERED": "1"}
    py = sys.executable
    bancada = f"127.0.0.1:{args.porta_bancada}"
    cmds = [
        (
            "bancada",
            [
                py,
                "-m",
                "elevador.sim",
                "--porta",
                args.porta_bancada,
                "--semente",
                args.semente,
                "--cenario",
                str(args.cenario),
                "--atraso",
                str(args.atraso),
            ],
        ),
        (
            "central",
            [
                py,
                "-m",
                "elevador.central",
                "--config",
                "config/central.json",
                "--mock",
                "--bancada",
                bancada,
                "--temp-mock",
                str(args.temp),
                "--sem-painel",
                "--log-arquivo",
                str(Path(args.dados) / "central.log"),
            ],
        ),
    ] + [
        (
            f"cabine{n}",
            [py, "-m", "elevador.cabine", "--config", f"config/cabine{n}.json", "--mock", "--bancada", bancada],
        )
        for n in (1, 2, 3)
    ]
    Path(args.dados).mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    procs = []
    try:
        for nome, cmd in cmds:
            print(f"[run_all_mock] iniciando {nome}", flush=True)
            procs.append((nome, subprocess.Popen(cmd, cwd=RAIZ, env=env, creationflags=flags)))
            time.sleep(0.5)
        print("[run_all_mock] sistema no ar; Ctrl+C encerra tudo", flush=True)
        while all(p.poll() is None for _, p in procs):
            time.sleep(1.0)
        mortos = [n for n, p in procs if p.poll() is not None]
        print(f"[run_all_mock] processo(s) encerrado(s): {mortos}", flush=True)
    except KeyboardInterrupt:
        print("\n[run_all_mock] encerrando...", flush=True)
    finally:
        for _, p in reversed(procs):  # cabines primeiro (freio), depois central e bancada
            if p.poll() is None:
                if os.name == "nt":
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    p.send_signal(signal.SIGINT)
        for _, p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
