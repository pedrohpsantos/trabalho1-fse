"""Interface de terminal: painel consolidado e leitor de comandos (threads próprias)."""

import logging
import os
import shutil
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


ESC = "\x1b"
ALTURA_PAINEL = 15  # linhas reservadas no topo (13 do painel + separador + folga)


class Painel:
    """Painel fixo no topo do terminal e linha de comandos embaixo, sem bloquear o controle.

    A faixa de cima é redesenhada a cada segundo salvando/restaurando o cursor (ESC 7 / ESC 8),
    sem limpar a tela: o que o operador digita na área de baixo (região de rolagem própria,
    DECSTBM) continua visível. Fora de um terminal (stdout redirecionado) o painel vai para o log.
    """

    def __init__(
        self,
        visao: Callable[[], VisaoPainel],
        comando: Callable[[str], str],
        parar: threading.Event,
        interativo: bool | None = None,
        saida=None,
        tamanho: Callable[[], os.terminal_size] | None = None,
    ) -> None:
        self._visao, self._comando, self._parar = visao, comando, parar
        self._saida = saida or sys.stdout
        self.interativo = self._saida.isatty() if interativo is None else interativo
        self._tamanho = tamanho or (lambda: shutil.get_terminal_size((110, 40)))
        self._trava = threading.Lock()
        self._threads = [threading.Thread(target=self._desenhar, name="painel", daemon=True)]
        if self.interativo and sys.stdin and sys.stdin.isatty():
            self._threads.append(threading.Thread(target=self._ler_comandos, name="comandos", daemon=True))

    def iniciar(self) -> None:
        if self.interativo:
            self._preparar_tela()
        for t in self._threads:
            t.start()

    def encerrar(self) -> None:
        """Devolve o terminal ao normal (rolagem inteira, cursor no fim)."""
        if self.interativo:
            altura = self._tamanho().lines
            self._escrever(f"{ESC}[r{ESC}[{altura};1H\n")

    # ------------------------------------------------------------------ tela
    def _escrever(self, texto: str) -> None:
        with self._trava:
            self._saida.write(texto)
            self._saida.flush()

    def _preparar_tela(self) -> None:
        altura = max(self._tamanho().lines, ALTURA_PAINEL + 4)
        self._escrever(
            f"{ESC}[2J{ESC}[{ALTURA_PAINEL + 1};{altura}r{ESC}[{altura};1H"
            "Digite um comando e Enter (ajuda para a lista).\n> "
        )

    def _quadro(self, texto: str) -> str:
        largura = max(20, self._tamanho().columns - 1)
        linhas = texto.split("\n")[: ALTURA_PAINEL - 1]
        linhas += [""] * (ALTURA_PAINEL - 1 - len(linhas))
        linhas.append("─" * largura)
        corpo = "".join(f"{ESC}[{i};1H{linha[:largura]}{ESC}[K" for i, linha in enumerate(linhas, start=1))
        return f"{ESC}7{corpo}{ESC}8"

    def _desenhar(self) -> None:
        while not self._parar.wait(1.0):
            try:
                texto = renderizar(self._visao())
            except Exception:  # noqa: BLE001 - o painel nunca deve afetar o controle
                log.exception("falha ao montar o painel")
                continue
            if self.interativo:
                self._escrever(self._quadro(texto))
            else:
                log.info("painel:\n%s", texto)

    # ------------------------------------------------------------------ comandos
    def executar(self, linha: str) -> None:
        """Executa um comando digitado e mostra a resposta na área de comandos."""
        resposta = self._comando(linha.strip())
        self._escrever((f"{resposta}\n" if resposta else "") + "> ")

    def _ler_comandos(self) -> None:
        while not self._parar.is_set():
            try:
                linha = sys.stdin.readline()
            except (OSError, ValueError):
                return
            if not linha:
                time.sleep(0.5)
                continue
            self.executar(linha)
