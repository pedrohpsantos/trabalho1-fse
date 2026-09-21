"""Driver simulado para execução e testes em ambiente de desenvolvimento."""

import queue
import threading
import time

from .base import ElevatorHardware, HardwareEvent, CortinaEvent, BandeirolaEvent
from ..config import MotorDirection, Fisica


class MockHardware(ElevatorHardware):
    """Hardware simulado em memória com aproximação cinemática."""

    def __init__(self, initial_position: int = 0) -> None:
        self._lock = threading.Lock()
        self._posicao: int = initial_position
        self._direction: MotorDirection = MotorDirection.FREIO
        self._duty_cycle: float = 0.0
        self._cortina_obstruida: bool = False
        self._event_queue: queue.Queue = queue.Queue()
        self._running: bool = True

        self._sensor_andar_ativo: bool = any(
            abs(initial_position - pos_nominal) <= 100
            for _andar, pos_nominal in Fisica.ANDARES_NOMINAIS
        )

        self._thread = threading.Thread(target=self._physics_loop, daemon=True)
        self._thread.start()

    def _physics_loop(self) -> None:
        dt = 0.02  # 20 ms
        with self._lock:
            dentro_bandeirola_anterior = self._sensor_andar_ativo

        while self._running:
            time.sleep(dt)

            with self._lock:
                dir_atual = self._direction
                duty = self._duty_cycle
                p = self._posicao

                # Limiar de atrito estático
                if duty >= 10.0:
                    vel_mm_por_tick = int(round((duty / 100.0) * 16.0))

                    if dir_atual == MotorDirection.SUBIR:
                        p = min(p + vel_mm_por_tick, Fisica.POS_MAXIMA_MM + 200)
                    elif dir_atual == MotorDirection.DESCER:
                        p = max(p - vel_mm_por_tick, Fisica.POS_MINIMA_MM - 200)

                    self._posicao = p

                # Intervalo da bandeirola (+-100 mm ao redor da cota nominal)
                dentro_alguma = False
                for _andar, pos_nominal in Fisica.ANDARES_NOMINAIS:
                    largura_semi = 100
                    if abs(p - pos_nominal) <= largura_semi:
                        dentro_alguma = True
                        break

                self._sensor_andar_ativo = dentro_alguma

                # Detecção de transições do sensor de andar
                if dentro_alguma != dentro_bandeirola_anterior:
                    dentro_bandeirola_anterior = dentro_alguma
                    event = BandeirolaEvent(
                        entrada=dentro_alguma,
                        pos_encoder=p,
                    )
                    self._event_queue.put(event)

    def set_motor(self, direction: MotorDirection, duty_percent: float) -> None:
        duty = max(0.0, min(100.0, float(duty_percent)))
        with self._lock:
            self._direction = direction
            self._duty_cycle = duty

    def get_position(self) -> int:
        with self._lock:
            return self._posicao

    def is_curtain_obstructed(self) -> bool:
        with self._lock:
            return self._cortina_obstruida

    def is_floor_sensor_active(self) -> bool:
        with self._lock:
            return self._sensor_andar_ativo

    def get_event_queue(self) -> queue.Queue:
        return self._event_queue

    def trigger_curtain(self, obstructed: bool) -> None:
        """Simula manualmente a obstrução ou liberação da cortina de luz."""
        with self._lock:
            self._cortina_obstruida = obstructed
        self._event_queue.put(CortinaEvent(obstruida=obstructed))

    def cleanup(self) -> None:
        self._running = False
        self.emergency_brake()
        if self._thread.is_alive():
            self._thread.join(timeout=0.1)
