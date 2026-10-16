"""Interface de terminal: painel consolidado e leitor de comandos (threads próprias)."""

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

PORTAS = {0: "fechada", 1: "abrindo", 2: "aberta", 3: "fechando", 4: "obstruída"}
CENARIOS = {0: "nenhum", 1: "up-peak", 2: "down-peak", 3: "interfloor"}
AJUDA = "Comandos: ir <cabine 1-3> <andar 0-5> | recalibrar <cabine> | cotas off|on | diag | ajuda | sair\n"


@dataclass
class VisaoPainel:
    cabines: list[dict]
    barramento_max_ma: int
    watchdog: int
    temperatura_c: float | None
    pressao_hpa: float | None
    cenario: int
    geradas: int
    atendidas: int
    espera_s: float
    viagem_s: float
    pendentes: list[str]
    em_atendimento: list[str]
    erros_modbus: int
    cotas_suspensas: bool = False


def renderizar(v: VisaoPainel) -> str:
    linhas = [
        "=== CONTROLE DE GRUPO DE ELEVADORES — Servidor Central ===",
        f"Barramento: {v.barramento_max_ma} mA | watchdog_ambiente: {v.watchdog} | "
        f"T={'--' if v.temperatura_c is None else f'{v.temperatura_c:.1f}'} °C "
        f"P={'--' if v.pressao_hpa is None else f'{v.pressao_hpa:.1f}'} hPa | erros MODBUS: {v.erros_modbus}",
        f"Cenário: {CENARIOS.get(v.cenario, v.cenario)} | geradas {v.geradas} | atendidas {v.atendidas} | "
        f"espera média {v.espera_s:.1f} s | viagem média {v.viagem_s:.1f} s",
        "*** COTAS SUSPENSAS (teste do Modo Degradado): `cotas on` retoma ***" if v.cotas_suspensas else "",
        f"{'Cab':<4}{'Estado':<19}{'Andar':<6}{'Pos(mm)':>9} {'Sentido':<9}{'Porta':<10}"
        f"{'Corr(mA)':>9}{'Cota(mA)':>9}  Modo",
    ]
    for c in v.cabines:
        if not c["conectada"]:
            linhas.append(f"{c['numero']:<4}{'DESCONECTADA':<19}")
            continue
        modo = "DEGRADADO" if c["degradado"] else ("calibrando" if c["estado"] == "calibrando" else "normal")
        linhas.append(
            f"{c['numero']:<4}{c['estado']:<19}{c['andar']:<6}{c['pos_mm']:>9.0f} {c['sentido']:<9}"
            f"{PORTAS.get(c['porta'], c['porta']):<10}{c['corrente_ma']:>9}{c['cota_ma']:>9}  {modo}"
        )
    linhas += [
        "",
        f"Chamadas pendentes na fila do prédio/atribuídas: {', '.join(v.pendentes) or '-'}",
        f"Em atendimento: {', '.join(v.em_atendimento) or '-'}",
        "",
        AJUDA.strip(),
    ]
    return "\n".join(linhas)


class Painel:
    """Redesenha o painel a cada segundo e executa comandos digitados, sem bloquear o controle."""

    def __init__(
        self,
        visao: Callable[[], VisaoPainel],
        comando: Callable[[str], str],
        parar: threading.Event,
        interativo: bool | None = None,
    ) -> None:
        self._visao, self._comando, self._parar = visao, comando, parar
        self.interativo = sys.stdout.isatty() if interativo is None else interativo
        self._mensagem = ""
        self._threads = [threading.Thread(target=self._desenhar, name="painel", daemon=True)]
        if sys.stdin and sys.stdin.isatty():
            self._threads.append(threading.Thread(target=self._ler_comandos, name="comandos", daemon=True))

    def iniciar(self) -> None:
        for t in self._threads:
            t.start()

    def _desenhar(self) -> None:
        while not self._parar.wait(1.0):
            try:
                texto = renderizar(self._visao())
            except Exception:  # noqa: BLE001 - o painel nunca deve afetar o controle
                log.exception("falha ao montar o painel")
                continue
            if self.interativo:
                sys.stdout.write("\x1b[2J\x1b[H" + texto + (f"\n> {self._mensagem}" if self._mensagem else "") + "\n")
                sys.stdout.flush()
            else:
                log.info("painel:\n%s", texto)

    def _ler_comandos(self) -> None:
        while not self._parar.is_set():
            try:
                linha = sys.stdin.readline()
            except (OSError, ValueError):
                return
            if not linha:
                time.sleep(0.5)
                continue
            self._mensagem = self._comando(linha.strip())
