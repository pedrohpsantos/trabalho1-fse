"""Repartição de Cotas, derating térmico, agenda e Despacho por Destino."""

import itertools
import random

import pytest

from elevador.central.despacho import Despachante
from elevador.central.potencia import repartir
from elevador.constantes import (
    CORRENTE_MAX_CABINE_MA,
    DUTY_MAX_DEGRADADO,
    barramento_com_derating,
    duty_max_da_cota,
)
from elevador.dominio.agenda import Agenda, Sentido, Tarefa
from elevador.dominio.estimativa import estimar

NECESSIDADES = ("movendo", "partida", "nenhuma")


def test_conversao_cota_em_duty():
    assert duty_max_da_cota(6000) == 100.0
    assert duty_max_da_cota(1000) == pytest.approx(16.6667, abs=1e-3) == DUTY_MAX_DEGRADADO
    assert duty_max_da_cota(0) == 0.0 and duty_max_da_cota(9000) == 100.0


def test_derating_termico_e_watchdog():
    assert barramento_com_derating(25.0) == 12000
    assert barramento_com_derating(35.0) == 10000
    assert barramento_com_derating(10.0) == 12000
    assert barramento_com_derating(25.0, watchdog_expirado=True) == 3000
    assert barramento_com_derating(90.0) == 3000  # nunca abaixo do piso


@pytest.mark.parametrize("barramento", [3000, 5000, 10000, 12000])
def test_invariantes_da_reparticao_todas_as_combinacoes(barramento):
    for combo in itertools.product(NECESSIDADES, repeat=3):
        cotas = repartir(barramento, dict(zip((1, 2, 3), combo, strict=True)))
        assert sum(cotas.values()) <= barramento
        assert all(0 <= c <= CORRENTE_MAX_CABINE_MA for c in cotas.values())
        for cabine, necessidade in zip((1, 2, 3), combo, strict=True):
            if necessidade == "nenhuma":
                assert cotas[cabine] == 0
            else:
                assert cotas[cabine] >= min(1000, barramento // 3)  # nunca zero para quem precisa mover


def test_invariantes_com_barramentos_aleatorios():
    rng = random.Random(5)
    for _ in range(500):
        barramento = rng.randint(0, 14000)
        nec = {c: rng.choice(NECESSIDADES) for c in (1, 2, 3)}
        cotas = repartir(barramento, nec)
        assert sum(cotas.values()) <= barramento and max(cotas.values()) <= 6000


def test_tres_cabines_acelerando_nao_cabem_e_dividem_o_barramento():
    assert repartir(12000, dict.fromkeys((1, 2, 3), "movendo")) == {1: 4000, 2: 4000, 3: 4000}
    assert repartir(3000, dict.fromkeys((1, 2, 3), "movendo")) == {1: 1000, 2: 1000, 3: 1000}


def test_cabine_em_movimento_tem_prioridade_sobre_a_que_vai_partir():
    cotas = repartir(12000, {1: "movendo", 2: "partida", 3: "nenhuma"})
    assert cotas == {1: 6000, 2: 6000, 3: 0}
    cotas = repartir(7000, {1: "movendo", 2: "partida", 3: "nenhuma"})
    assert cotas[1] > cotas[2] >= 1000 and sum(cotas.values()) == 7000


# --- agenda -------------------------------------------------------------------------------------
def test_agenda_varredura_direcional_e_idempotencia():
    ag = Agenda()
    assert ag.adicionar(Tarefa(1, 0, 4, 0.0))
    assert not ag.adicionar(Tarefa(1, 0, 4, 0.0))
    ag.adicionar(Tarefa(2, 2, 5, 0.0))
    assert ag.proxima_parada(0, Sentido.SUBINDO, 0.0) == 0  # embarque no próprio andar
    eventos = ag.atender(0, 1.0)
    assert [(e.tarefa.id, e.tipo) for e in eventos] == [(1, "embarque")]
    assert ag.proxima_parada(0, Sentido.SUBINDO, 1.0) == 2
    ag.atender(2, 2.0)
    assert ag.proxima_parada(2, Sentido.SUBINDO, 2.0) == 4
    assert [e.tipo for e in ag.atender(4, 3.0)] == ["desembarque"]
    assert ag.proxima_parada(4, Sentido.SUBINDO, 3.0) == 5


def test_agenda_embarque_envelhecido_tem_prioridade():
    ag = Agenda()
    ag.adicionar(Tarefa(1, 0, 5, 0.0, a_bordo=True))  # entrega no 5
    ag.adicionar(Tarefa(2, 1, 2, 0.0))
    assert ag.proxima_parada(3, Sentido.SUBINDO, 5.0) == 5
    assert ag.proxima_parada(3, Sentido.SUBINDO, 100.0) == 1  # esperando há muito tempo


def test_agenda_estacionamento_cede_a_viagem_real():
    ag = Agenda()
    ag.adicionar(Tarefa(2**30 + 1, 3, 3, 0.0, suave=True, a_bordo=True))
    assert ag.proxima_parada(0, Sentido.PARADO, 0.0) == 3
    ag.adicionar(Tarefa(7, 1, 2, 0.0))
    assert ag.paradas() == {1}


def test_estimativa_agrupa_paradas_coincidentes():
    sozinha = Agenda()
    sozinha.adicionar(Tarefa(1, 0, 3, 0.0))
    dupla = sozinha.copiar()
    dupla.adicionar(Tarefa(2, 0, 3, 0.0))
    t1 = estimar(sozinha, 0, Sentido.PARADO, 0.0)[1].desembarque
    t2 = estimar(dupla, 0, Sentido.PARADO, 0.0)[2].desembarque
    assert t2 == pytest.approx(t1)  # mesmo destino: nenhuma parada extra


# --- despacho -----------------------------------------------------------------------------------
def despachante_pronto() -> Despachante:
    d = Despachante()
    for n, andar in ((1, 0), (2, 3), (3, 5)):
        c = d.cabines[n]
        c.conectada = c.calibrada = True
        c.estado, c.andar, c.degradado, c.nivelado = "ociosa", andar, False, True
    return d


def test_despacho_escolhe_a_cabine_mais_proxima_quando_todas_ociosas():
    d = despachante_pronto()
    assert d.escolher_cabine(0, 4, 0.0) == 1
    assert d.escolher_cabine(3, 0, 0.0) == 2
    assert d.escolher_cabine(5, 2, 0.0) == 3


def test_despacho_nao_usa_cabine_desconectada_ou_nao_calibrada():
    d = despachante_pronto()
    d.cabines[1].conectada = False
    d.cabines[2].calibrada = False
    assert d.escolher_cabine(0, 4, 0.0) == 3
    d.cabines[3].conectada = False
    assert d.escolher_cabine(0, 4, 0.0) is None


def test_despacho_agrupa_destinos_coincidentes_na_mesma_cabine():
    d = despachante_pronto()
    d.cabines[1].andar, d.cabines[2].andar, d.cabines[3].andar = 0, 4, 5
    primeira = d.escolher_cabine(0, 3, 0.0, id_=1)
    d.atribuir(1, 0, 3, 0.0, primeira)
    segunda = d.escolher_cabine(0, 3, 1.0, id_=2)
    assert segunda == primeira  # mesma origem/destino já agendados: sem parada extra


def test_despacho_balanceia_quando_uma_cabine_esta_sobrecarregada():
    d = despachante_pronto()
    d.cabines[1].andar = d.cabines[2].andar = d.cabines[3].andar = 0
    escolhidas = []
    for i in range(1, 7):
        cab = d.escolher_cabine(0, 1 + i % 5, float(i), id_=i)
        d.atribuir(i, 0, 1 + i % 5, float(i), cab)
        escolhidas.append(cab)
    assert len(set(escolhidas)) == 3


def test_atribuicao_definitiva_e_idempotente():
    d = despachante_pronto()
    a = d.atribuir(10, 0, 3, 5.0, 1)
    b = d.atribuir(10, 0, 3, 9.0, 2)  # reatribuição não é permitida
    assert a is b and b.cabine == 1


def test_eventos_completam_a_viagem_e_alimentam_o_log():
    d = despachante_pronto()
    d.atribuir(10, 0, 3, 5.0, 1)
    assert d.evento(1, 10, "embarque", 8.0) is None
    chamada = d.evento(1, 10, "desembarque", 30.0)
    assert (chamada.t_registro, chamada.t_embarque, chamada.t_desembarque) == (5.0, 8.0, 30.0)
    assert d.cabines[1].agenda.tarefas == {}


def test_reconciliacao_reenvia_tarefas_perdidas_apos_reinicio_da_cabine():
    d = despachante_pronto()
    d.atribuir(10, 0, 3, 5.0, 1)
    d.atribuir(11, 2, 4, 6.0, 1)
    reenviar = d.reconciliar(1, [{"id": 11, "origem": 2, "destino": 4, "t_registro": 6.0}])
    assert [t.id for t in reenviar] == [10]


def test_plano_de_estacionamento_por_cenario():
    d = despachante_pronto()
    plano = dict(d.plano_estacionamento(2))  # down-peak: cabines sobem para os andares altos
    assert 3 not in plano  # a cabine 3 já está no andar 5
    assert plano == {2: 4, 1: 2}
    assert d.plano_estacionamento(0) == []
