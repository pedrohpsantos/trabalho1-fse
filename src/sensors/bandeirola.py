"""Monitoramento e cálculo do centro da bandeirola (Sensor de Andar)."""

from dataclasses import dataclass
from typing import Optional

from ..config import Fisica


@dataclass(frozen=True)
class BandeirolaReport:
    """Relatório detalhado da medição da bandeirola ao término de sua travessia."""
    andar_nominal: int
    pos_nominal: int
    pos_entrada: int
    pos_saida: int
    largura: int
    centro_estimado: int
    erro_mm: int

    def format_display(self) -> str:
        return (
            "\n╔══════════════════════════════════════════════════════════════╗\n"
            "║                   MEDIÇÃO DE BANDEIROLA                      ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            f"║ Borda de Entrada: {self.pos_entrada:>6} pulsos / mm                       ║\n"
            f"║ Borda de Saída:   {self.pos_saida:>6} pulsos / mm                       ║\n"
            f"║ Largura Medida:   {self.largura:>6} mm                                ║\n"
            f"║ Centro Estimado:  {self.centro_estimado:>6} mm                                ║\n"
            f"║ Andar Nominal:    Andar {self.andar_nominal} ({self.pos_nominal:>5} mm)                       ║\n"
            f"║ Erro de Centro:   {self.erro_mm:>+6} mm                                ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n"
        )


class BandeirolaTracker:
    """Rastreador de bordas e estimador de centro da bandeirola."""

    def __init__(self) -> None:
        self.pos_entrada: Optional[int] = None

    def on_edge(self, entrada: bool, pos_encoder: int) -> Optional[BandeirolaReport]:
        """Processa uma borda do sensor de andar.

        Retorna BandeirolaReport apenas na borda de saída (após ter registrado uma entrada).
        """
        if entrada:
            # Borda de subida (entrou na bandeirola)
            self.pos_entrada = pos_encoder
            print(f"[SENSOR_ANDAR] Borda de ENTRADA detectada em: {pos_encoder} pulsos/mm")
            return None
        else:
            # Borda de descida (saiu da bandeirola)
            print(f"[SENSOR_ANDAR] Borda de SAÍDA detectada em: {pos_encoder} pulsos/mm")
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
