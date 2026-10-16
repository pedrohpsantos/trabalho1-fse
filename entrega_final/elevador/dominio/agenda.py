"""Agenda de paradas de uma cabine (domínio puro, sem I/O).

A mesma lógica é usada pela cabine (para decidir a próxima parada) e pelo Despacho do
Central (para estimar o efeito de uma nova atribuição). Política: varredura direcional
(SCAN) com prioridade para embarques que esperam há muito tempo.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from ..constantes import CAPACIDADE_PASSAGEIROS, NUM_ANDARES

ID_MANUAL_BASE = 2**30  # ids acima disto são paradas sem passageiro (manual/estacionamento)
IDADE_PRIORITARIA_S = 30.0


class Sentido(StrEnum):
    SUBINDO = "subindo"
    DESCENDO = "descendo"
    PARADO = "parado"


@dataclass
class Tarefa:
    """Uma viagem atribuída (origem -> destino) ou uma parada avulsa."""

    id: int
    origem: int
    destino: int
    t_registro: float
    suave: bool = False  # parada de estacionamento: cede lugar a viagens reais
    a_bordo: bool = False
    t_embarque: float | None = None
    t_chegada: float = 0.0  # quando a cabine recebeu a tarefa (só embarca quem chegou antes da abertura)

    @property
    def avulsa(self) -> bool:
        return self.id >= ID_MANUAL_BASE

    @property
    def parada(self) -> int:
        """Andar onde esta tarefa exige uma parada agora."""
        return self.destino if self.a_bordo else self.origem

    def para_dict(self) -> dict:
        return {
            "id": self.id,
            "origem": self.origem,
            "destino": self.destino,
            "t_registro": self.t_registro,
            "suave": self.suave,
            "a_bordo": self.a_bordo,
            "t_embarque": self.t_embarque,
            "t_chegada": self.t_chegada,
        }

    @classmethod
    def de_dict(cls, d: dict) -> "Tarefa":
        tarefa = cls(
            id=int(d["id"]),
            origem=int(d["origem"]),
            destino=int(d["destino"]),
            t_registro=float(d["t_registro"]),
            suave=bool(d.get("suave", False)),
            a_bordo=bool(d.get("a_bordo", False)),
            t_embarque=d.get("t_embarque"),
            t_chegada=float(d.get("t_chegada", 0.0)),
        )
        if not (0 <= tarefa.origem < NUM_ANDARES and 0 <= tarefa.destino < NUM_ANDARES):
            raise ValueError("andar fora da faixa")
        return tarefa


@dataclass(frozen=True)
class EventoParada:
    """Resultado de atender um andar."""

    tarefa: Tarefa
    tipo: str  # "embarque" | "desembarque" | "manual"


@dataclass
class Agenda:
    idade_prioritaria_s: float = IDADE_PRIORITARIA_S
    tarefas: dict[int, Tarefa] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.tarefas)

    def adicionar(self, tarefa: Tarefa) -> bool:
        """Insere a tarefa; devolve False se o id já existia (idempotência)."""
        if tarefa.id in self.tarefas:
            return False
        if not tarefa.suave:  # viagem real anula estacionamento pendente
            for id_ in [i for i, t in self.tarefas.items() if t.suave]:
                del self.tarefas[id_]
        self.tarefas[tarefa.id] = tarefa
        return True

    def copiar(self) -> "Agenda":
        copia = Agenda(self.idade_prioritaria_s)
        copia.tarefas = {i: Tarefa(**t.para_dict()) for i, t in self.tarefas.items()}
        return copia

    def _ativas(self) -> list[Tarefa]:
        reais = [t for t in self.tarefas.values() if not t.suave]
        return reais if reais else list(self.tarefas.values())

    def _alcancaveis(self) -> list[Tarefa]:
        """Tarefas que justificam uma parada agora. Lotada, a cabine não para para embarcar
        ninguém (a porta abriria sem embarque nem desembarque, em ciclo — livelock visto na
        rasp32): primeiro entrega quem está a bordo, depois volta."""
        ativas = self._ativas()
        if self.a_bordo() < CAPACIDADE_PASSAGEIROS:
            return ativas
        return [t for t in ativas if t.a_bordo or t.avulsa]

    def paradas(self) -> set[int]:
        return {t.parada for t in self._alcancaveis()}

    def a_bordo(self) -> int:
        """Passageiros a bordo (paradas avulsas não carregam ninguém)."""
        return sum(1 for t in self.tarefas.values() if t.a_bordo and not t.avulsa)

    def tem_viagem_real(self) -> bool:
        return any(not t.suave for t in self.tarefas.values())

    def proxima_parada(self, piso: int, sentido: Sentido, agora: float) -> int | None:
        """Andar para onde a cabine deve ir (pode ser o próprio `piso`)."""
        ativas = self._alcancaveis()
        if not ativas:
            return None
        envelhecidas = [
            t for t in ativas if not t.a_bordo and not t.suave and agora - t.t_registro > self.idade_prioritaria_s
        ]
        if envelhecidas:
            return min(envelhecidas, key=lambda t: t.t_registro).origem
        alvos = {t.parada for t in ativas}
        if piso in alvos:
            return piso
        acima = [f for f in alvos if f > piso]
        abaixo = [f for f in alvos if f < piso]
        if sentido == Sentido.SUBINDO:
            return min(acima) if acima else max(abaixo)
        if sentido == Sentido.DESCENDO:
            return max(abaixo) if abaixo else min(acima)
        todos = acima + abaixo
        return min(todos, key=lambda f: (abs(f - piso), f))

    def atender(self, piso: int, agora: float, chegadas_ate: float | None = None) -> list[EventoParada]:
        """Registra desembarques e embarques que ocorrem ao abrir a porta em `piso`.

        `chegadas_ate`: só embarcam tarefas recebidas até este instante (quem foi atribuído com
        a porta já abrindo espera a próxima abertura).
        """
        eventos: list[EventoParada] = []
        for tarefa in list(self.tarefas.values()):
            if tarefa.a_bordo and tarefa.destino == piso:
                del self.tarefas[tarefa.id]
                eventos.append(EventoParada(tarefa, "manual" if tarefa.avulsa else "desembarque"))
        vagas = CAPACIDADE_PASSAGEIROS - self.a_bordo()
        for tarefa in sorted(self.tarefas.values(), key=lambda t: t.t_registro):  # FIFO, como o firmware
            if not tarefa.a_bordo and tarefa.origem == piso:
                if chegadas_ate is not None and tarefa.t_chegada > chegadas_ate:
                    continue
                if not tarefa.avulsa:
                    if vagas <= 0:
                        continue  # lotada: o passageiro espera a próxima visita
                    vagas -= 1
                tarefa.a_bordo = True
                tarefa.t_embarque = agora
                eventos.append(EventoParada(tarefa, "embarque"))
        return eventos

    def serializar(self) -> list[dict]:
        return [t.para_dict() for t in self.tarefas.values()]

    @classmethod
    def desserializar(cls, itens: list[dict]) -> "Agenda":
        agenda = cls()
        for item in itens:
            tarefa = Tarefa.de_dict(item)
            agenda.tarefas[tarefa.id] = tarefa
        return agenda
