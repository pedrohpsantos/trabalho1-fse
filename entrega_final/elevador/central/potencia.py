"""Repartição do Barramento de Potência em Cotas por cabine (Seção 7.2).

Modelo de corrente do firmware (medido na rasp32): cada cabine consome CORRENTE_REPOUSO_MA
mesmo parada, desconectada ou freada. Por isso:

* a Cota mínima de qualquer cabine é o repouso (300 mA): parada, ela não ganha duty;
* a repartição usa `MARGEM_BARRAMENTO` do barramento (ruído de ±10 % da corrente medida);
* quem precisa partir recebe até 1000 mA (a "velocidade mínima" da Seção 7.3) e o restante
  vai para quem está em movimento (que consome mais), sempre <= 6000 mA por cabine;
* `reserva`: cabine que caiu (TCP) em movimento pousa sozinha em Modo Degradado com até
  1000 mA; o Central desconta essa corrente para que a soma REAL caiba no barramento;
* admissão de partidas: uma cabine só recebe Cota para PARTIR se couber com >= 1200 mA
  (COTA_MIN_PARTIDA_MA, ~16 % de duty); senão espera com a Cota de repouso. Com o barramento
  no piso (3000 mA) andam 2 cabines a ~17 % em vez de 3 a ~11 %: a vazão total é a mesma
  (velocidade linear no duty, medido), mas a corrente fica longe da cauda do jitter do PWM.
  Quem JÁ está em movimento nunca perde o piso (não para no meio do poço).

Invariante: soma(cotas) <= barramento_max_ma (e, pelo modelo, soma das correntes reais também).
"""

from ..constantes import (
    CORRENTE_MAX_CABINE_MA,
    CORRENTE_REPOUSO_MA,
    COTA_DEGRADADA_MA,
    COTA_MIN_PARTIDA_MA,
    MARGEM_BARRAMENTO,
)

PESO = {"movendo": 3, "partida": 1}
ATIVAS = ("movendo", "partida", "reserva")


def _admitir_partidas(livre: int, necessidades: dict[int, str], ordem: list[int]) -> dict[int, str]:
    """Rebaixa a 'nenhuma' as partidas que não caberiam com COTA_MIN_PARTIDA_MA cada."""
    extra_min = COTA_MIN_PARTIDA_MA - CORRENTE_REPOUSO_MA
    em_curso = [c for c, nec in necessidades.items() if nec in ("movendo", "reserva")]
    resto = livre - len(em_curso) * (COTA_DEGRADADA_MA - CORRENTE_REPOUSO_MA)
    ajustadas = dict(necessidades)
    for c in [c for c in ordem if necessidades.get(c) == "partida"]:
        if resto >= extra_min:
            resto -= extra_min
        else:
            ajustadas[c] = "nenhuma"  # espera a próxima renegociação
    return ajustadas


def repartir(barramento_ma: int, necessidades: dict[int, str], ordem: list[int] | None = None) -> dict[int, int]:
    """`necessidades`: cabine -> 'movendo' | 'partida' | 'reserva' | 'nenhuma' (todas as cabines).

    `ordem`: prioridade de admissão das partidas (ex.: quem espera há mais tempo primeiro).
    """
    n = len(necessidades)
    if n == 0:
        return {}
    orcamento = int(max(0, barramento_ma) * MARGEM_BARRAMENTO)
    repouso = min(CORRENTE_REPOUSO_MA, orcamento // n)
    cotas = dict.fromkeys(necessidades, repouso)
    livre = orcamento - repouso * n
    necessidades = _admitir_partidas(livre, necessidades, ordem or sorted(necessidades))
    ativas = [c for c, nec in necessidades.items() if nec in ATIVAS]
    if not ativas or livre <= 0:
        return cotas
    piso = min(COTA_DEGRADADA_MA - repouso, livre // len(ativas))
    for c in ativas:
        cotas[c] += piso
    livre -= piso * len(ativas)
    for c in ativas:  # partida admitida: garante a Cota mínima de partida antes dos extras
        if necessidades[c] == "partida":
            reforco = min(max(0, COTA_MIN_PARTIDA_MA - cotas[c]), livre)
            cotas[c] += reforco
            livre -= reforco
    for classe in ("movendo", "partida"):
        grupo = [c for c in ativas if necessidades[c] == classe]
        while livre > 0 and grupo:
            fatia = livre // len(grupo)
            if fatia == 0:
                break
            ainda = []
            for c in grupo:
                extra = min(fatia, CORRENTE_MAX_CABINE_MA - cotas[c])
                cotas[c] += extra
                livre -= extra
                if cotas[c] < CORRENTE_MAX_CABINE_MA:
                    ainda.append(c)
            grupo = ainda
    return cotas
