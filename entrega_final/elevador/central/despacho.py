"""Despacho por Destino (Seções 5.3 e 7.5).

Cada chamada é atribuída definitivamente a UMA cabine. A escolha minimiza um custo obtido
por simulação da agenda de cada candidata com e sem a nova viagem:

    custo = W + 0,3*V + 0,7*D + 2*N        (+ penalidades de estado)

  W  tempo estimado até a porta abrir na origem (espera da nova chamada)
  V  tempo estimado de viagem da nova chamada
  D  atraso imposto aos passageiros já atribuídos
  N  número de tarefas já na agenda da cabine (equilíbrio de carga)

Paradas coincidentes (mesma origem/destino já na agenda) custam menos porque a simulação
conta cada parada uma só vez; assim o custo favorece agrupar destinos coincidentes.
Anti-inanição: a agenda de cada cabine promove embarques com mais de 45 s de espera
(`Agenda.proxima_parada`), mantendo o limite de 90 s da Seção 7.5.
"""

import logging
from dataclasses import dataclass, field

from ..constantes import CAPACIDADE_PASSAGEIROS, NUM_ANDARES, Cenario
from ..dominio.agenda import ID_MANUAL_BASE, Agenda, Sentido, Tarefa
from ..dominio.estimativa import ParametrosTempo, estimar, ocupacao_maxima

log = logging.getLogger(__name__)

PENALIDADE_DEGRADADA = 120.0
HOMES = {
    Cenario.UP_PEAK: [0, 0, 2],
    Cenario.DOWN_PEAK: [5, 4, 2],
    Cenario.INTERFLOOR: [0, 2, 4],
}


@dataclass
class EstadoCabineCentral:
    numero: int
    conectada: bool = False
    calibrada: bool = False
    andar: int = 0
    sentido: str = "parado"
    estado: str = "desconectada"
    degradado: bool = True
    necessidade: str = "nenhuma"
    alvo: int = -1
    porta: int = 0
    corrente_ma: int = 0
    cota_ma: int = 0
    pos_mm: float = 0.0
    nivelado: bool = False
    falha: str = ""
    ts_telemetria: float = 0.0
    reserva_ate: float = float("-inf")  # caiu em movimento: reserva a corrente do pouso degradado
    partida_desde: float = float("inf")  # desde quando pede Cota para partir (ordem de admissão)
    andares_mm: list[float] = field(default_factory=list)
    agenda: Agenda = field(default_factory=Agenda)  # espelho das tarefas reais atribuídas

    def disponivel(self) -> bool:
        return self.conectada and self.calibrada and self.estado != "falha" and not self.falha


@dataclass
class Chamada:
    id: int
    origem: int
    destino: int
    t_registro: float
    cabine: int
    estado: str = "atribuida"  # atribuida | a_bordo | concluida
    t_embarque: float | None = None
    t_desembarque: float | None = None

    def para_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def de_dict(cls, d: dict) -> "Chamada":
        return cls(**d)


class Despachante:
    def __init__(self, params: ParametrosTempo | None = None) -> None:
        self.params = params or ParametrosTempo()
        self.cabines = {n: EstadoCabineCentral(n) for n in (1, 2, 3)}
        self.chamadas: dict[int, Chamada] = {}
        self._seq_avulsa = 0

    # --- escolha ----------------------------------------------------------------------
    def custo(self, cab: EstadoCabineCentral, nova: Tarefa, agora: float) -> float:
        piso = cab.andar
        sentido = Sentido(cab.sentido) if cab.sentido in ("subindo", "descendo") else Sentido.PARADO
        alvo = cab.alvo if cab.alvo >= 0 else None
        porta_aberta = cab.estado in ("embarque", "abrindo_porta")
        antes = estimar(cab.agenda, piso, sentido, agora, self.params, alvo, porta_aberta)
        agenda = cab.agenda.copiar()
        agenda.adicionar(nova)
        depois = estimar(agenda, piso, sentido, agora, self.params, alvo, porta_aberta)
        espera = depois[nova.id].embarque or 0.0
        viagem = depois[nova.id].desembarque - espera
        atraso = 0.0
        for id_, antigo in antes.items():
            novo = depois[id_]
            if antigo.embarque is not None and novo.embarque is not None:
                atraso += max(0.0, novo.embarque - antigo.embarque)
            atraso += 0.5 * max(0.0, novo.desembarque - antigo.desembarque)
        custo = espera + 0.3 * viagem + 0.7 * atraso + 2.0 * len(cab.agenda)
        custo += 4.0 * max(0.0, espera - 35.0) + 6.0 * max(0.0, atraso - 40.0)  # evita esperas longas (limite de 90 s)
        if cab.degradado:
            custo += PENALIDADE_DEGRADADA
        return custo

    def cabe(self, cab: EstadoCabineCentral, nova: Tarefa, agora: float) -> bool:
        """A nova viagem não leva a ocupação prevista acima da lotação (8 passageiros)."""
        sentido = Sentido(cab.sentido) if cab.sentido in ("subindo", "descendo") else Sentido.PARADO
        agenda = cab.agenda.copiar()
        agenda.adicionar(nova)
        alvo = cab.alvo if cab.alvo >= 0 else None
        porta_aberta = cab.estado in ("embarque", "abrindo_porta")
        ocupacao = ocupacao_maxima(agenda, cab.andar, sentido, agora, self.params, alvo, porta_aberta)
        return ocupacao <= CAPACIDADE_PASSAGEIROS

    def escolher_cabine(self, origem: int, destino: int, agora: float, id_: int = 0) -> int | None:
        """Cabine de menor custo entre as aptas e com lugar; None => a chamada espera na fila."""
        nova = Tarefa(id_, origem, destino, agora)
        candidatas = [c for c in self.cabines.values() if c.disponivel() and self.cabe(c, nova, agora)]
        if not candidatas:
            return None
        return min(candidatas, key=lambda c: (self.custo(c, nova, agora), c.numero)).numero

    def atribuir(self, id_: int, origem: int, destino: int, t_registro: float, cabine: int) -> Chamada:
        """Registra a atribuição definitiva (idempotente por id)."""
        existente = self.chamadas.get(id_)
        if existente:
            return existente
        chamada = Chamada(id_, origem, destino, t_registro, cabine)
        self.chamadas[id_] = chamada
        self.cabines[cabine].agenda.adicionar(Tarefa(id_, origem, destino, t_registro))
        return chamada

    # --- eventos vindos das cabines -------------------------------------------------------
    def evento(self, cabine: int, tarefa: int, tipo: str, ts: float) -> Chamada | None:
        """Atualiza a viagem; devolve a chamada quando ela é concluída (para o log)."""
        espelho = self.cabines[cabine].agenda
        chamada = self.chamadas.get(tarefa)
        if chamada is None:
            return None
        if tipo == "embarque" and chamada.t_embarque is None:
            chamada.t_embarque, chamada.estado = ts, "a_bordo"
            if tarefa in espelho.tarefas:
                espelho.tarefas[tarefa].a_bordo = True
        elif tipo == "desembarque" and chamada.estado != "concluida":
            chamada.t_desembarque, chamada.estado = ts, "concluida"
            if chamada.t_embarque is None:
                chamada.t_embarque = ts
            espelho.tarefas.pop(tarefa, None)
            return chamada
        return None

    def reconciliar(self, cabine: int, agenda_reportada: list[dict]) -> list[Tarefa]:
        """Após (re)conexão: devolve as tarefas que a cabine perdeu e precisa receber de novo."""
        reportadas = {int(t["id"]) for t in agenda_reportada}
        espelho = self.cabines[cabine].agenda
        reenviar = []
        for chamada in self.chamadas.values():
            if chamada.cabine != cabine or chamada.estado == "concluida":
                continue
            if chamada.id not in reportadas:
                tarefa = Tarefa(chamada.id, chamada.origem, chamada.destino, chamada.t_registro)
                tarefa.a_bordo = chamada.estado == "a_bordo"
                if not tarefa.a_bordo:
                    reenviar.append(tarefa)
                # a bordo e ausente da agenda: a cabine reiniciou; passageiro simulado já embarcou,
                # então a tarefa volta como entrega pendente no destino.
                else:
                    reenviar.append(
                        Tarefa(chamada.id, chamada.destino, chamada.destino, chamada.t_registro, a_bordo=True)
                    )
        # espelho = chamadas ativas da cabine
        espelho.tarefas = {
            c.id: Tarefa(c.id, c.origem, c.destino, c.t_registro, a_bordo=c.estado == "a_bordo")
            for c in self.chamadas.values()
            if c.cabine == cabine and c.estado != "concluida"
        }
        return reenviar

    # --- estacionamento ------------------------------------------------------------------------
    def plano_estacionamento(self, cenario: int) -> list[tuple[int, int]]:
        """Pares (cabine, andar) para cabines ociosas se posicionarem conforme o cenário."""
        try:
            homes = list(HOMES[Cenario(cenario)])
        except (KeyError, ValueError):
            return []
        ociosas = [
            c
            for c in self.cabines.values()
            if c.disponivel() and c.estado == "ociosa" and not c.agenda.tarefas and not c.degradado
        ]
        plano = []
        for andar in homes:
            if not ociosas:
                break
            if any(c.nivelado and c.andar == andar for c in ociosas):
                ociosas = [c for c in ociosas if not (c.nivelado and c.andar == andar)]
                continue
            escolhida = min(ociosas, key=lambda c: abs(c.andar - andar))
            ociosas.remove(escolhida)
            plano.append((escolhida.numero, andar))
        return plano

    def nova_tarefa_avulsa(self) -> int:
        self._seq_avulsa += 1
        return ID_MANUAL_BASE + self._seq_avulsa

    # --- persistência ---------------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "chamadas": [c.para_dict() for c in self.chamadas.values() if c.estado != "concluida"],
            "seq_avulsa": self._seq_avulsa,
            "calibracao": {
                str(n): {"calibrada": c.calibrada, "andares_mm": c.andares_mm} for n, c in self.cabines.items()
            },
        }

    def restaurar(self, dados: dict) -> None:
        self._seq_avulsa = int(dados.get("seq_avulsa", 0))
        for item in dados.get("chamadas", []):
            chamada = Chamada.de_dict(item)
            if not (0 <= chamada.origem < NUM_ANDARES and 0 <= chamada.destino < NUM_ANDARES):
                continue
            self.chamadas[chamada.id] = chamada
            self.cabines[chamada.cabine].agenda.tarefas[chamada.id] = Tarefa(
                chamada.id, chamada.origem, chamada.destino, chamada.t_registro, a_bordo=chamada.estado == "a_bordo"
            )
        for n, info in dados.get("calibracao", {}).items():
            self.cabines[int(n)].andares_mm = list(info.get("andares_mm") or [])
