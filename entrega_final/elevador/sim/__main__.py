"""Executa a bancada virtual: `python -m elevador.sim --porta 5600 [--cenario 1]`."""

import argparse
import logging
import sys
import threading

from ..constantes import Cenario
from ..util.logs import configurar_logs
from ..util.sinais import EncerramentoSeguro
from .bancada import Bancada, ExecutorTempoReal
from .servidor import ServidorBancada

log = logging.getLogger(__name__)


def _comandos(bancada: Bancada, parar: threading.Event) -> None:
    ajuda = "chamada <o> <d> | cenario <1-3> | cortina <cab> <0|1> | status | sair"
    print(ajuda)
    while not parar.is_set():
        linha = sys.stdin.readline()
        if not linha:
            parar.wait(0.5)
            continue
        p = linha.split()
        try:
            if p[:1] == ["chamada"]:
                print("id", bancada.registrar_chamada(int(p[1]), int(p[2])))
            elif p[:1] == ["cenario"]:
                bancada.iniciar_cenario(Cenario(int(p[1])))
            elif p[:1] == ["cortina"]:
                with bancada.lock:
                    bancada.cabines[int(p[1])].definir_cortina(bool(int(p[2])))
            elif p[:1] == ["status"]:
                print(bancada.violacoes(), bancada.predio.registradores(bancada.t))
            elif p[:1] == ["sair"]:
                parar.set()
        except (ValueError, KeyError, IndexError) as erro:
            print(f"comando inválido ({erro}); {ajuda}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bancada virtual (ESP32 + GPIO simulados)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--porta", type=int, default=5600)
    ap.add_argument("--semente", type=int, default=1)
    ap.add_argument("--cenario", type=int, default=0, help="1=up-peak 2=down-peak 3=interfloor")
    ap.add_argument("--atraso", type=float, default=120.0, help="segundos até iniciar o cenário")
    ap.add_argument("--intervalo-chamadas", type=float, default=8.0)
    ap.add_argument("--fator", type=float, default=1.0, help="fator de tempo da física (1 = tempo real)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    configurar_logs("bancada", args.log_level)
    enc = EncerramentoSeguro()
    enc.instalar()
    bancada = Bancada(semente=args.semente)
    servidor = ServidorBancada(bancada, args.host, args.porta)
    executor = ExecutorTempoReal(bancada, fator=args.fator)
    servidor.iniciar()
    executor.iniciar()
    if args.cenario:
        temporizador = threading.Timer(
            args.atraso, lambda: bancada.iniciar_cenario(Cenario(args.cenario), args.semente, args.intervalo_chamadas)
        )
        temporizador.daemon = True
        temporizador.start()
    if sys.stdin and sys.stdin.isatty():
        threading.Thread(target=_comandos, args=(bancada, enc.evento), daemon=True).start()
    try:
        enc.aguardar()
    finally:
        executor.parar()
        servidor.parar()
    return 0


sys.exit(main())
