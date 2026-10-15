"""Interface da camada de GPIO de uma cabine e utilitário de debounce da cortina."""

import abc
import threading
from collections.abc import Callable

from ...constantes import Direcao

AoSensor = Callable[[bool, int], None]  # (entrou_na_bandeirola, contagem_do_encoder_na_borda)
AoCortina = Callable[[bool], None]  # (obstruida)


class GpioCabine(abc.ABC):
    """Única porta de acesso aos 7 pinos da cabine."""

    @abc.abstractmethod
    def registrar_callbacks(self, ao_sensor: AoSensor, ao_cortina: AoCortina) -> None: ...

    @abc.abstractmethod
    def definir_motor(self, direcao: Direcao, duty: float) -> None: ...

    @abc.abstractmethod
    def posicao(self) -> int:
        """Contador do encoder (int32 com sinal, em pulsos == mm)."""

    @abc.abstractmethod
    def deslocar_posicao(self, delta: int) -> None: ...

    @abc.abstractmethod
    def cortina_obstruida(self) -> bool: ...

    @abc.abstractmethod
    def sensor_andar_ativo(self) -> bool: ...

    @abc.abstractmethod
    def liberar(self) -> None:
        """PWM em 0, freio (DIR1=DIR2=1) e liberação dos recursos."""

    def parada_segura(self) -> None:
        self.definir_motor(Direcao.FREIO, 0.0)


class DebounceCortina:
    """Confirma uma mudança de nível somente após `janela_s` estável (sem busy-wait).

    Cada borda reinicia um temporizador; ao vencer, o nível é relido e, se mudou em
    relação ao último nível notificado, `ao_mudar` é chamado.
    """

    def __init__(self, ler_nivel: Callable[[], bool], ao_mudar: AoCortina, janela_s: float = 0.04) -> None:
        self._ler = ler_nivel
        self._ao_mudar = ao_mudar
        self.janela_s = janela_s
        self._trava = threading.Lock()
        self._timer: threading.Timer | None = None
        self.nivel_estavel = bool(ler_nivel())

    def ao_borda(self) -> None:
        with self._trava:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.janela_s, self._confirmar)
            self._timer.daemon = True
            self._timer.start()

    def _confirmar(self) -> None:
        nivel = bool(self._ler())
        with self._trava:
            if nivel == self.nivel_estavel:
                return
            self.nivel_estavel = nivel
        self._ao_mudar(nivel)

    def cancelar(self) -> None:
        with self._trava:
            if self._timer is not None:
                self._timer.cancel()
