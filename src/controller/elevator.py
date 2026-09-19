"""Módulo de controle de alto nível da Cabine 1."""

import threading
from typing import Optional
from ..config import Fisica, MotorDirection
from ..hal import ElevatorHardware, HardwareEvent, CortinaEvent, BandeirolaEvent
from ..sensors.bandeirola import BandeirolaTracker
from .motion import (
    MotionPlanner,
    ContinuarUpdate,
    ChegouAoAndarUpdate,
    LimiteCursoAtingidoUpdate,
)


class ElevatorController:
    """Controlador central da Cabine 1 (thread-safe com RLock)."""

    def __init__(self, hardware: ElevatorHardware) -> None:
        self.hardware = hardware
        self.motion = MotionPlanner()
        self.bandeirola_tracker = BandeirolaTracker()
        self.duty_atual: float = 0.0
        self.direcao_atual: MotorDirection = MotorDirection.FREIO
        self._lock = threading.RLock()

    def tick(self) -> None:
        """Executa um ciclo da malha de controle (chamado periodicamente, ex: a cada 50 ms)."""
        with self._lock:
            pos = self.hardware.get_position()
            update = self.motion.update(pos)

            if isinstance(update, ContinuarUpdate):
                self.direcao_atual = update.direcao
                self.duty_atual = update.duty_percent
                self.hardware.set_motor(update.direcao, update.duty_percent)

            elif isinstance(update, ChegouAoAndarUpdate):
                self.direcao_atual = MotorDirection.FREIO
                self.duty_atual = 0.0
                self.hardware.set_motor(MotorDirection.FREIO, 0.0)
                print(
                    f"\n🎉 [CHEGADA] Cabine 1 chegou e nivelou no Andar {update.andar}! "
                    f"Posição: {update.posicao} mm (Erro: {update.erro_nivelamento:+d} mm)\n"
                )

            elif isinstance(update, LimiteCursoAtingidoUpdate):
                self.direcao_atual = MotorDirection.FREIO
                self.duty_atual = 0.0
                self.hardware.set_motor(MotorDirection.FREIO, 0.0)
                print(
                    f"\n⚠️ [SEGURANÇA] Fim de curso atingido na posição {update.posicao} mm! "
                    f"Movimento bloqueado.\n"
                )

    def handle_event(self, event: HardwareEvent) -> None:
        """Processa eventos assíncronos de hardware (cortina e sensor de andar)."""
        with self._lock:
            if isinstance(event, CortinaEvent):
                if event.obstruida:
                    print("\n🚨 [CORTINA] >>> PORTA OBSTRUÍDA! Objeto detectado na passagem. <<<")
                else:
                    print("\n🟢 [CORTINA] >>> PORTA LIBERADA! Passagem desobstruída. <<<")

            elif isinstance(event, BandeirolaEvent):
                report = self.bandeirola_tracker.on_edge(event.entrada, event.pos_encoder)
                if report is not None:
                    print(report.format_display())

    def ir_para_andar(self, andar: int) -> None:
        """Comanda a cabine para se deslocar até o andar especificado (0, 1 ou 2)."""
        pos_alvo = None
        for a, p in Fisica.ANDARES_NOMINAIS:
            if a == andar:
                pos_alvo = p
                break

        if pos_alvo is None:
            raise ValueError(f"Andar inválido: {andar}. Andares válidos: 0, 1, 2.")

        with self._lock:
            print(f"▶️ Comandando Cabine 1 para Andar {andar} (alvo: {pos_alvo} mm / pulsos)...")
            self.motion.comandar_andar(andar, pos_alvo)

    def comando_motor(self, direcao: MotorDirection, duty: float) -> None:
        """Comanda acionamento direto do motor."""
        with self._lock:
            print(f"⚙️ Comando manual do motor: Direção = {direcao.value}, Duty = {duty:.1f}%")
            self.motion.comandar_manual(direcao, duty)

    def parar(self) -> None:
        """Para a cabine imediatamente acionando o freio."""
        with self._lock:
            print("⏹️ Parando motor e acionando freio...")
            self.motion.parar()
            self.direcao_atual = MotorDirection.FREIO
            self.duty_atual = 0.0
            self.hardware.set_motor(MotorDirection.FREIO, 0.0)

    def print_status(self) -> None:
        """Imprime o status consolidado no terminal."""
        with self._lock:
            pos = self.hardware.get_position()
            cortina = self.hardware.is_curtain_obstructed()
            sensor_andar = self.hardware.is_floor_sensor_active()

            # Estima o andar mais próximo e verifica nivelamento
            menor_dist = float("inf")
            andar_estimado = 0
            pos_andar = 0

            for andar, p in Fisica.ANDARES_NOMINAIS:
                d = abs(pos - p)
                if d < menor_dist:
                    menor_dist = d
                    andar_estimado = andar
                    pos_andar = p

            nivelado = menor_dist <= Fisica.TOLERANCIA_NIVELAMENTO_MM
            nivelado_str = "SIM (NIVELADO)" if nivelado else "NÃO"
            cortina_str = "OBSTRUÍDA (1)" if cortina else "Livre (0)"
            sensor_str = "NA BANDEIROLA (1)" if sensor_andar else "Entre andares (0)"
            erro_str = f"{pos - pos_andar:+d} mm"

            print(
                "\n┌──────────────────────── STATUS CABINE 1 ────────────────────────┐\n"
                f"│ Posição Atual:        {pos:>6} pulsos ({pos:>6} mm)                 │\n"
                f"│ Andar Estimado:       Andar {andar_estimado} (nominal: {pos_andar:>5} mm)                 │\n"
                f"│ Nivelamento:          {nivelado_str:<16} (erro: {erro_str:>7})       │\n"
                f"│ Direção Motor:        {self.direcao_atual.value:<16}                             │\n"
                f"│ Duty Cycle PWM:       {self.duty_atual:>5.1f}%                                  │\n"
                f"│ Cortina de Luz:       {cortina_str:<16}                             │\n"
                f"│ Sensor de Andar:      {sensor_str:<16}                             │\n"
                "└─────────────────────────────────────────────────────────────────┘\n"
            )

    def cleanup(self) -> None:
        """Libera recursos de hardware."""
        with self._lock:
            self.hardware.cleanup()
