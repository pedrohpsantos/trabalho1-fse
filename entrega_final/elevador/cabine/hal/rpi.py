"""GPIO real (Raspberry Pi) via RPi.GPIO (ou `rpi-lgpio`, compatível, no Pi 5)."""

import logging

from ...constantes import PWM_FREQ_HZ, Direcao
from ..config import Pinos
from ..encoder import DecodificadorQuadratura
from .base import AoCortina, AoSensor, DebounceCortina, GpioCabine

try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError):
    GPIO = None

log = logging.getLogger(__name__)


class GpioRaspberry(GpioCabine):
    def __init__(self, pinos: Pinos, debounce_s: float = 0.04) -> None:
        if GPIO is None:
            raise RuntimeError("RPi.GPIO indisponível neste sistema: use --mock")
        self.pinos = pinos
        self._decodificador = DecodificadorQuadratura()
        self._ao_sensor: AoSensor | None = None
        self._debounce: DebounceCortina | None = None
        self._debounce_s = debounce_s
        self._pwm = None
        self._ativo = False

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        # Saídas começam em FREIO com PWM 0: nunca energizadas por acidente.
        GPIO.setup(pinos.dir1, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(pinos.dir2, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(pinos.pwm, GPIO.OUT, initial=GPIO.LOW)
        self._pwm = GPIO.PWM(pinos.pwm, PWM_FREQ_HZ)
        self._pwm.start(0.0)
        for pino in (pinos.enc_a, pinos.enc_b, pinos.cortina, pinos.sensor_andar):
            GPIO.setup(pino, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        self._decodificador = DecodificadorQuadratura(GPIO.input(pinos.enc_a), GPIO.input(pinos.enc_b))
        self._ativo = True

    def registrar_callbacks(self, ao_sensor: AoSensor, ao_cortina: AoCortina) -> None:
        p = self.pinos
        self._ao_sensor = ao_sensor
        self._debounce = DebounceCortina(lambda: bool(GPIO.input(p.cortina)), ao_cortina, self._debounce_s)
        # Interrupções nas duas bordas; sem bouncetime nos encoders para não perder pulsos.
        GPIO.add_event_detect(p.enc_a, GPIO.BOTH, callback=self._isr_encoder)
        GPIO.add_event_detect(p.enc_b, GPIO.BOTH, callback=self._isr_encoder)
        GPIO.add_event_detect(p.sensor_andar, GPIO.BOTH, callback=self._isr_sensor)
        GPIO.add_event_detect(p.cortina, GPIO.BOTH, callback=lambda _c: self._debounce.ao_borda())

    def _isr_encoder(self, _canal: int) -> None:
        self._decodificador.atualizar(GPIO.input(self.pinos.enc_a), GPIO.input(self.pinos.enc_b))

    def _isr_sensor(self, _canal: int) -> None:
        nivel = bool(GPIO.input(self.pinos.sensor_andar))
        if self._ao_sensor:
            self._ao_sensor(nivel, self._decodificador.contagem)

    def definir_motor(self, direcao: Direcao, duty: float) -> None:
        dir1, dir2 = direcao.niveis_gpio()
        GPIO.output(self.pinos.dir1, dir1)
        GPIO.output(self.pinos.dir2, dir2)
        self._pwm.ChangeDutyCycle(max(0.0, min(100.0, duty)))

    def posicao(self) -> int:
        return self._decodificador.contagem

    def deslocar_posicao(self, delta: int) -> None:
        self._decodificador.deslocar(delta)

    def cortina_obstruida(self) -> bool:
        return bool(GPIO.input(self.pinos.cortina))

    def sensor_andar_ativo(self) -> bool:
        return bool(GPIO.input(self.pinos.sensor_andar))

    def liberar(self) -> None:
        if not self._ativo:
            return
        self._ativo = False
        if self._debounce:
            self._debounce.cancel()
        try:
            self.definir_motor(Direcao.FREIO, 0.0)
            self._pwm.stop()
        finally:
            # DIR1/DIR2 permanecem em FREIO: só os demais pinos são liberados.
            p = self.pinos
            GPIO.cleanup([p.pwm, p.enc_a, p.enc_b, p.cortina, p.sensor_andar])
