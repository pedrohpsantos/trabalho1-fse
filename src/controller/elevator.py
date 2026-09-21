"""Módulo de controle de alto nível da Cabine 1."""

import threading
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
                    f"\n[CHEGADA] Cabine nivelada no Andar {update.andar}. "
                    f"Posição: {update.posicao} mm (erro: {update.erro_nivelamento:+d} mm)\n"
                )

            elif isinstance(update, LimiteCursoAtingidoUpdate):
                self.direcao_atual = MotorDirection.FREIO
                self.duty_atual = 0.0
                self.hardware.set_motor(MotorDirection.FREIO, 0.0)
                print(
                    f"\n[ALERTA] Limite de curso atingido: {update.posicao} mm. "
                    "Movimento bloqueado.\n"
                )

    def handle_event(self, event: HardwareEvent) -> None:
        """Processa eventos assíncronos de sensores."""
        with self._lock:
            if isinstance(event, CortinaEvent):
                if event.obstruida:
                    print("\n[CORTINA] Obstrução detectada.")
                else:
                    print("\n[CORTINA] Passagem desobstruída.")

            elif isinstance(event, BandeirolaEvent):
                report = self.bandeirola_tracker.on_edge(event.entrada, event.pos_encoder)
                if report is not None:
                    print(report.format_display())

    def ir_para_andar(self, andar: int) -> None:
        """Inicia trajetória até o andar especificado."""
        pos_alvo = None
        for a, p in Fisica.ANDARES_NOMINAIS:
            if a == andar:
                pos_alvo = p
                break

        if pos_alvo is None:
            raise ValueError(f"Andar inválido: {andar}.")

        with self._lock:
            print(f"[COMANDO] Deslocamento para o Andar {andar} (alvo: {pos_alvo} mm)")
            self.motion.comandar_andar(andar, pos_alvo)

    def comando_motor(self, direcao: MotorDirection, duty: float) -> None:
        """Aciona diretamente o motor de tração."""
        with self._lock:
            print(f"[COMANDO] Manual: {direcao.value}, duty={duty:.1f}%")
            self.motion.comandar_manual(direcao, duty)

    def parar(self) -> None:
        """Interrompe movimento e aciona freio elétrico."""
        with self._lock:
            print("[COMANDO] Parada de motor e acionamento de freio.")
            self.motion.parar()
            self.direcao_atual = MotorDirection.FREIO
            self.duty_atual = 0.0
            self.hardware.set_motor(MotorDirection.FREIO, 0.0)

    def recalibrar_posicao(self, nova_posicao: int = 0) -> None:
        """Redefine a cota de posição de referência acumulada."""
        with self._lock:
            self.hardware.set_position(nova_posicao)
            print(f"[CALIBRAÇÃO] Posição redefinida para {nova_posicao} mm.")

    def print_status(self) -> None:
        """Exibe telemetria atual da cabine."""
        with self._lock:
            pos = self.hardware.get_position()
            cortina = self.hardware.is_curtain_obstructed()
            sensor_andar = self.hardware.is_floor_sensor_active()

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
            erro = pos - pos_andar

            print(
                "\n--- TELEMETRIA CABINE 1 ---\n"
                f"Posição:         {pos:>5} mm ({pos:>5} pulsos)\n"
                f"Andar estimado:  Andar {andar_estimado} (nominal: {pos_andar} mm)\n"
                f"Nivelamento:     {'NIVELADO' if nivelado else 'NAO NIVELADO'} (erro: {erro:+d} mm)\n"
                f"Motor:           {self.direcao_atual.value} | PWM: {self.duty_atual:.1f}%\n"
                f"Cortina de luz:  {'OBSTRUIDA' if cortina else 'DESOBSTRUIDA'}\n"
                f"Sensor de andar: {'ATIVO (bandeirola)' if sensor_andar else 'INATIVO'}\n"
                "---------------------------"
            )

    def cleanup(self) -> None:
        """Libera recursos de hardware."""
        with self._lock:
            self.hardware.cleanup()
