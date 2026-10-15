"""PID de posição na forma discreta "por amostra", normalizada ao período nominal de 50 ms.

    u = Kp*e + Ki*soma(e) + Kd*(e - e_anterior)

Os termos integral e derivativo são escalados por (dt / T_nominal), de modo que o jitter
do escalonador do Linux não altera o ganho efetivo (sem reduzir o período da malha).
Anti-windup: integração condicional (congelada quando a saída está saturada no sentido do
erro) e contribuição integral limitada em `limite_integral_pct` pontos de duty.
"""

from ..constantes import PERIODO_PID_S


class Pid:
    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        limite_integral_pct: float = 15.0,
        periodo_nominal_s: float = PERIODO_PID_S,
    ) -> None:
        self.kp, self.ki, self.kd = kp, ki, kd
        self.limite_integral_pct = limite_integral_pct
        self.periodo_nominal_s = periodo_nominal_s
        self.integral = 0.0
        self._erro_anterior: float | None = None

    def reiniciar(self) -> None:
        self.integral = 0.0
        self._erro_anterior = None

    def deslocar_erro(self, delta: float) -> None:
        """Compensa um salto de referência/posição sem gerar pico derivativo."""
        if self._erro_anterior is not None:
            self._erro_anterior += delta

    def calcular(self, erro: float, dt: float, saturado: int = 0) -> float:
        """`saturado`: +1/-1 se a saída anterior estava limitada nesse sentido, 0 caso contrário."""
        escala = dt / self.periodo_nominal_s
        if self._erro_anterior is None:
            self._erro_anterior = erro
        derivada = (erro - self._erro_anterior) / escala if escala > 0 else 0.0
        self._erro_anterior = erro
        congelar = saturado != 0 and (erro > 0) == (saturado > 0)
        if not congelar:
            self.integral += erro * escala
            if self.ki > 0:
                limite = self.limite_integral_pct / self.ki
                self.integral = max(-limite, min(limite, self.integral))
        return self.kp * erro + self.ki * self.integral + self.kd * derivada
