"""Implementação de hardware real para Raspberry Pi utilizando RPi.GPIO."""

import queue
import threading

from .base import ElevatorHardware, CortinaEvent, BandeirolaEvent, QUADRATURE_TABLE
from ..config import PinConfig, MotorDirection, Fisica

try:
    import RPi.GPIO as GPIO
    RPI_GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO = None
    RPI_GPIO_AVAILABLE = False


class RpiHardware(ElevatorHardware):
    """Driver real de controle da Cabine 1 utilizando a biblioteca RPi.GPIO."""

    def __init__(self, pins: PinConfig) -> None:
        if not RPI_GPIO_AVAILABLE or GPIO is None:
            raise RuntimeError(
                "A biblioteca RPi.GPIO não está disponível neste ambiente. "
                "Para testar em computadores comuns (Windows/Linux PC), utilize o modo simulado com '--mock'."
            )

        self.pins = pins
        self._lock = threading.Lock()
        self._posicao: int = 0
        self._event_queue: queue.Queue = queue.Queue()

        print(
            f"[HAL] Pinos BCM: PWM={pins.pwm}, DIR1={pins.dir1}, DIR2={pins.dir2}, "
            f"ENC_A={pins.enc_a}, ENC_B={pins.enc_b}, CORTINA={pins.cortina}, SENSOR={pins.sensor_andar}"
        )

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)

        # Configuração de saídas digitais e PWM
        GPIO.setup(pins.dir1, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(pins.dir2, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(pins.pwm, GPIO.OUT, initial=GPIO.LOW)

        self._pwm = GPIO.PWM(pins.pwm, Fisica.PWM_FREQ_HZ)
        self._pwm.start(0.0)

        # 2. Configuração dos pinos do encoder em quadratura
        GPIO.setup(pins.enc_a, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
        GPIO.setup(pins.enc_b, GPIO.IN, pull_up_down=GPIO.PUD_OFF)

        self._state_a: bool = bool(GPIO.input(pins.enc_a))
        self._state_b: bool = bool(GPIO.input(pins.enc_b))
        curr_a = 1 if self._state_a else 0
        curr_b = 1 if self._state_b else 0
        self._last_state: int = (curr_a << 1) | curr_b

        # Interrupções para ambos os canais do encoder (sem bouncetime para não perder pulsos rápidos)
        GPIO.add_event_detect(pins.enc_a, GPIO.BOTH, callback=self._on_enc_a)
        GPIO.add_event_detect(pins.enc_b, GPIO.BOTH, callback=self._on_enc_b)

        # 3. Configuração da cortina de luz (com debounce nativo de 40 ms)
        GPIO.setup(pins.cortina, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
        GPIO.add_event_detect(
            pins.cortina,
            GPIO.BOTH,
            callback=self._on_cortina_edge,
            bouncetime=Fisica.DEBOUNCE_CORTINA_MS,
        )

        # 4. Configuração do sensor de andar (bandeirola)
        GPIO.setup(pins.sensor_andar, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
        GPIO.add_event_detect(
            pins.sensor_andar,
            GPIO.BOTH,
            callback=self._on_sensor_andar_edge,
        )

    def _on_enc_a(self, _channel: int) -> None:
        """Callback de interrupção executado a cada transição de ENC_A."""
        with self._lock:
            self._state_a = bool(GPIO.input(self.pins.enc_a))
            curr_a = 1 if self._state_a else 0
            curr_b = 1 if self._state_b else 0
            curr = (curr_a << 1) | curr_b

            prev = self._last_state
            self._last_state = curr
            delta = QUADRATURE_TABLE[((prev << 2) | curr) & 0x0F]
            if delta != 0:
                new_pos = self._posicao + delta
                # Inteiro de 32 bits com sinal (-2^31 a 2^31 - 1)
                if new_pos > 2147483647:
                    new_pos = -2147483648
                elif new_pos < -2147483648:
                    new_pos = 2147483647
                self._posicao = new_pos

    def _on_enc_b(self, _channel: int) -> None:
        """Callback de interrupção executado a cada transição de ENC_B."""
        with self._lock:
            self._state_b = bool(GPIO.input(self.pins.enc_b))
            curr_a = 1 if self._state_a else 0
            curr_b = 1 if self._state_b else 0
            curr = (curr_a << 1) | curr_b

            prev = self._last_state
            self._last_state = curr
            delta = QUADRATURE_TABLE[((prev << 2) | curr) & 0x0F]
            if delta != 0:
                new_pos = self._posicao + delta
                if new_pos > 2147483647:
                    new_pos = -2147483648
                elif new_pos < -2147483648:
                    new_pos = 2147483647
                self._posicao = new_pos

    def _on_cortina_edge(self, _channel: int) -> None:
        """Callback acionado na mudança de estado da cortina de luz."""
        obstructed = bool(GPIO.input(self.pins.cortina))
        self._event_queue.put(CortinaEvent(obstruida=obstructed))

    def _on_sensor_andar_edge(self, _channel: int) -> None:
        """Callback acionado ao cruzar bordas de entrada ou saída da bandeirola."""
        is_high = bool(GPIO.input(self.pins.sensor_andar))
        pos_atual = self.get_position()
        self._event_queue.put(BandeirolaEvent(entrada=is_high, pos_encoder=pos_atual))

    def set_motor(self, direction: MotorDirection, duty_percent: float) -> None:
        duty = max(0.0, min(100.0, float(duty_percent)))
        dir1, dir2 = direction.to_gpio_levels()

        GPIO.output(self.pins.dir1, GPIO.HIGH if dir1 else GPIO.LOW)
        GPIO.output(self.pins.dir2, GPIO.HIGH if dir2 else GPIO.LOW)
        self._pwm.ChangeDutyCycle(duty)

    def get_position(self) -> int:
        with self._lock:
            return self._posicao

    def is_curtain_obstructed(self) -> bool:
        return bool(GPIO.input(self.pins.cortina))

    def is_floor_sensor_active(self) -> bool:
        return bool(GPIO.input(self.pins.sensor_andar))

    def get_event_queue(self) -> queue.Queue:
        return self._event_queue

    def cleanup(self) -> None:
        self.emergency_brake()
        if hasattr(self, "_pwm") and self._pwm is not None:
            try:
                self._pwm.stop()
            except Exception:
                pass
            self._pwm = None
        try:
            GPIO.cleanup()
        except Exception:
            pass
