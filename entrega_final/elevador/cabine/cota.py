"""Cota de Potência vigente e Modo Degradado (Seções 7.2 e 7.3)."""

from ..constantes import COTA_DEGRADADA_MA, COTA_VALIDADE_S, DUTY_MAX_DEGRADADO, duty_max_da_cota


class GerenciadorCota:
    """Guarda a última Cota; vale `COTA_VALIDADE_S` a partir do recebimento local.

    Sem Cota válida (nunca recebida, expirada ou conexão TCP caída) a cabine está em Modo
    Degradado e não pode arbitrar potência para si: fica limitada a 1000 mA (~16,67% de duty).
    """

    def __init__(self, validade_s: float = COTA_VALIDADE_S) -> None:
        self.validade_s = validade_s
        self._cota_ma = 0
        self._expira_em = float("-inf")

    def receber(self, cota_ma: int, agora: float, validade_s: float | None = None) -> None:
        self._cota_ma = max(0, int(cota_ma))
        self._expira_em = agora + min(validade_s or self.validade_s, self.validade_s)

    def invalidar(self) -> None:
        """Conexão TCP caída: a Cota deixa de valer imediatamente."""
        self._expira_em = float("-inf")

    def valida(self, agora: float) -> bool:
        return agora < self._expira_em

    def degradado(self, agora: float) -> bool:
        return not self.valida(agora)

    def cota_ma(self, agora: float) -> int:
        return self._cota_ma if self.valida(agora) else COTA_DEGRADADA_MA

    def duty_max(self, agora: float) -> float:
        return duty_max_da_cota(self._cota_ma) if self.valida(agora) else DUTY_MAX_DEGRADADO
