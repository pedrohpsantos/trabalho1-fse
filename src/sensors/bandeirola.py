"""Monitoramento e cálculo do centro da bandeirola (Sensor de Andar)."""

from dataclasses import dataclass
from typing import Optional

from ..config import Fisica


@dataclass(frozen=True)
class BandeirolaReport:
    """Registro de transição e cálculo de centro da bandeirola."""
    andar_nominal: int
    pos_nominal: int
    pos_entrada: int
    pos_saida: int
    largura: int
    centro_estimado: int
    erro_mm: int

    def format_display(self) -> str:
        return (
            f"[BANDEIROLA] Andar nominal: {self.andar_nominal} | "
            f"Centro: {self.centro_estimado} mm | "
            f"Largura: {self.largura} mm | "
            f"Erro: {self.erro_mm:+d} mm "
            f"(Entrada: {self.pos_entrada} mm, Saída: {self.pos_saida} mm)"
        )


class BandeirolaTracker:
    """Processamento de bordas e cálculo de centro do sensor de andar."""

    def __init__(self) -> None:
        self.pos_entrada: Optional[int] = None

    def on_edge(self, entrada: bool, pos_encoder: int) -> Optional[BandeirolaReport]:
        """Processa transição de borda no sensor de andar."""
        if entrada:
            self.pos_entrada = pos_encoder
            print(f"[SENSOR_ANDAR] Transição de entrada: {pos_encoder} mm")
            return None
        else:
            print(f"[SENSOR_ANDAR] Transição de saída: {pos_encoder} mm")
            if self.pos_entrada is not None:
                pos_in = self.pos_entrada
                self.pos_entrada = None

                centro = (pos_in + pos_encoder) // 2
                largura = abs(pos_encoder - pos_in)

                # Determina o andar nominal mais próximo do centro medido
                menor_dist = float("inf")
                melhor_andar = 0
                pos_nominal_andar = 0

                for andar, pos_nom in Fisica.ANDARES_NOMINAIS:
                    dist = abs(centro - pos_nom)
                    if dist < menor_dist:
                        menor_dist = dist
                        melhor_andar = andar
                        pos_nominal_andar = pos_nom

                erro = centro - pos_nominal_andar

                return BandeirolaReport(
                    andar_nominal=melhor_andar,
                    pos_nominal=pos_nominal_andar,
                    pos_entrada=pos_in,
                    pos_saida=pos_encoder,
                    largura=largura,
                    centro_estimado=centro,
                    erro_mm=erro,
                )
            return None
