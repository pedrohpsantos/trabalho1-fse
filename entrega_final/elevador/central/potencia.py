"""Repartição do Barramento de Potência em Cotas por cabine (Seção 7.2).

Invariantes garantidos: soma(cotas) <= barramento_max_ma e cota <= 6000 mA por cabine.
Política: cabines paradas recebem 0; as que precisam partir recebem o piso de 1000 mA
(o mínimo para vencer o atrito estático com folga); o restante vai, em proporção ao peso,
para as que já estão em movimento (que consomem mais na aceleração).
"""

from ..constantes import CORRENTE_MAX_CABINE_MA, COTA_DEGRADADA_MA

PESO = {"movendo": 3, "partida": 1}


def repartir(barramento_ma: int, necessidades: dict[int, str]) -> dict[int, int]:
    """`necessidades`: cabine -> 'movendo' | 'partida' | 'nenhuma' (cabines desconectadas = 'nenhuma')."""
    cotas = dict.fromkeys(necessidades, 0)
    ativas = [c for c, n in necessidades.items() if n in PESO]
    if not ativas or barramento_ma <= 0:
        return cotas
    piso = min(COTA_DEGRADADA_MA, barramento_ma // len(ativas))
    for c in ativas:
        cotas[c] = piso
    sobra = barramento_ma - piso * len(ativas)
    for classe in ("movendo", "partida"):
        grupo = [c for c in ativas if necessidades[c] == classe]
        while sobra > 0 and grupo:
            peso_total = len(grupo)  # dentro da classe todos têm o mesmo peso
            fatia = sobra // peso_total
            if fatia == 0:
                break
            ainda = []
            for c in grupo:
                extra = min(fatia, CORRENTE_MAX_CABINE_MA - cotas[c])
                cotas[c] += extra
                sobra -= extra
                if cotas[c] < CORRENTE_MAX_CABINE_MA:
                    ainda.append(c)
            grupo = ainda
    return cotas
