"""Estimativa de tempos de atendimento por simulação da agenda (usada pelo Despacho)."""

from dataclasses import dataclass

from .agenda import Agenda, Sentido


@dataclass(frozen=True)
class ParametrosTempo:
    """Tempos médios medidos na bancada (ajustáveis por arquivo de configuração)."""

    seg_por_andar: float = 5.0
    seg_arranque: float = 3.0
    seg_porta_abrir: float = 1.5
    seg_parada: float = 8.0  # ciclo completo de porta + embarque


@dataclass(frozen=True)
class TempoEstimado:
    embarque: float | None  # segundos até a porta abrir na origem (None se já embarcou)
    desembarque: float


def _simular(
    agenda: Agenda,
    piso: int,
    sentido: Sentido,
    agora: float,
    parametros: ParametrosTempo,
    alvo_atual: int | None,
    parado_com_porta_aberta: bool,
):
    """Executa a agenda parada a parada; gera (instante da porta, eventos, ocupação pretendida)."""
    sim = agenda.copiar()
    t = 0.0
    primeiro = True
    for _ in range(4 * len(sim) + 8):
        if not sim.tarefas:
            return
        if primeiro and alvo_atual is not None and alvo_atual != piso:
            destino = alvo_atual  # a cabine já está a caminho: mantém o trajeto
            t += abs(destino - piso) * parametros.seg_por_andar
        else:
            destino = sim.proxima_parada(piso, sentido, agora + t)
            if destino is None:
                return
            if destino != piso:
                t += parametros.seg_arranque + abs(destino - piso) * parametros.seg_por_andar
            elif primeiro and parado_com_porta_aberta:
                t += parametros.seg_parada * 0.5  # restante da porta atual
        sentido = Sentido.SUBINDO if destino > piso else Sentido.DESCENDO if destino < piso else sentido
        piso = destino
        primeiro = False
        eventos = sim.atender(piso, agora + t)
        yield t + parametros.seg_porta_abrir, eventos, sim.a_bordo() + _sem_vaga(sim, piso)
        t += parametros.seg_parada


def _sem_vaga(sim: Agenda, piso: int) -> int:
    """Embarques que ficariam para trás por lotação (contam como excesso)."""
    return sum(1 for t in sim.tarefas.values() if not t.a_bordo and not t.avulsa and t.origem == piso)


def estimar(
    agenda: Agenda,
    piso: int,
    sentido: Sentido,
    agora: float,
    parametros: ParametrosTempo | None = None,
    alvo_atual: int | None = None,
    parado_com_porta_aberta: bool = False,
) -> dict[int, TempoEstimado]:
    """Simula a execução completa da agenda e devolve os instantes relativos a `agora`."""
    parametros = parametros or ParametrosTempo()
    resultado: dict[int, TempoEstimado] = {}
    embarques: dict[int, float | None] = {i: None for i, t in agenda.tarefas.items() if not t.a_bordo}
    t_final = 0.0
    for instante, eventos, _ in _simular(agenda, piso, sentido, agora, parametros, alvo_atual, parado_com_porta_aberta):
        t_final = instante
        for evento in eventos:
            if evento.tipo == "embarque":
                embarques[evento.tarefa.id] = instante
            else:
                resultado[evento.tarefa.id] = TempoEstimado(embarques.get(evento.tarefa.id), instante)
    # Tarefas que não terminaram na simulação (laço limitado): penaliza com o tempo final.
    for id_ in agenda.tarefas:
        resultado.setdefault(id_, TempoEstimado(embarques.get(id_), t_final + 60.0))
    return resultado


def ocupacao_maxima(
    agenda: Agenda,
    piso: int,
    sentido: Sentido,
    agora: float,
    parametros: ParametrosTempo | None = None,
    alvo_atual: int | None = None,
    parado_com_porta_aberta: bool = False,
) -> int:
    """Maior ocupação (a bordo + embarques pretendidos) ao longo da execução da agenda."""
    parametros = parametros or ParametrosTempo()
    maximo = agenda.a_bordo()
    for _, _, ocupacao in _simular(agenda, piso, sentido, agora, parametros, alvo_atual, parado_com_porta_aberta):
        maximo = max(maximo, ocupacao)
    return maximo
