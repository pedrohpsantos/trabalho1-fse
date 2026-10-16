"""Registro opcional da malha de controle em CSV (um registro por tick de 50 ms).

Alimenta os gráficos de sintonia do README com dados da bancada real. Nunca interfere no
controle: uma falha de escrita desliga o registro e é avisada uma única vez.
"""

import csv
import logging

from .controlador import CabineRuntime, EstadoCabine

log = logging.getLogger(__name__)

CAMPOS = (
    "t",
    "estado",
    "pos_mm",
    "alvo_mm",
    "erro_mm",
    "duty",
    "cota_ma",
    "degradado",
    "reancoragens",
    "correcao_mm",
    "corrente_ma",
)


class RegistroCsv:
    def __init__(self, caminho: str) -> None:
        self.caminho = caminho
        # Buffer de linha: em NFS o buffer padrão (st_blksize) chega a 1 MB e um processo morto
        # perderia minutos de registro. 20 linhas/s não pesam na malha. Fechado em fechar().
        self._arquivo = open(caminho, "w", newline="", encoding="utf-8", buffering=1)
        self._csv = csv.writer(self._arquivo)
        self._csv.writerow(CAMPOS)

    def gravar(self, t: float, rt: CabineRuntime) -> None:
        if self._arquivo is None:
            return
        movendo = rt.estado == EstadoCabine.MOVENDO
        modbus = rt._modbus or {}
        try:
            self._csv.writerow(
                (
                    f"{t:.3f}",
                    rt.estado.value,
                    rt.hal.posicao(),
                    f"{rt.ctrl.alvo:.1f}" if movendo else "",
                    f"{rt.ctrl.erro:.1f}" if movendo else "",
                    f"{rt.duty_calibracao if rt.estado == EstadoCabine.CALIBRANDO else rt.ctrl.duty_saida:.2f}",
                    rt.cota.cota_ma(rt.relogio()),
                    int(rt.degradado),
                    rt.reancoragens,
                    rt.ultima_correcao_mm,
                    modbus.get("corrente_ma", ""),
                )
            )
        except OSError:
            log.exception("registro CSV desativado: falha de escrita em %s", self.caminho)
            self.fechar()

    def fechar(self) -> None:
        if self._arquivo is not None:
            try:
                self._arquivo.close()
            finally:
                self._arquivo = None
