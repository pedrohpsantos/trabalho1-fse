"""Repartição de Cotas, derating térmico, agenda e Despacho por Destino."""

import itertools
import random

import pytest

from elevador.central.despacho import Despachante
from elevador.central.potencia import repartir
from elevador.constantes import (
    CORRENTE_MAX_CABINE_MA,
    CORRENTE_REPOUSO_MA,
    COTA_MIN_PARTIDA_MA,
    DUTY_ATRITO_ESTATICO,
    DUTY_MAX_DEGRADADO,
    barramento_com_derating,
    corrente_estimada_ma,
    duty_max_da_cota,
)
from elevador.dominio.agenda import Agenda, Sentido, Tarefa
from elevador.dominio.estimativa import estimar

NECESSIDADES = ("movendo", "partida", "nenhuma")


def test_conversao_cota_em_duty():
    assert duty_max_da_cota(6000) == 100.0
    assert duty_max_da_cota(0) == 0.0 and duty_max_da_cota(9000) == 100.0
    assert duty_max_da_cota(CORRENTE_REPOUSO_MA) == 0.0  # Cota de repouso: parada
    # Teto do enunciado (cota/60) restringido pelo modelo medido: (cota - 300)/57.
    assert duty_max_da_cota(1000) == pytest.approx(700 / 57, abs=1e-6) == DUTY_MAX_DEGRADADO
    assert DUTY_ATRITO_ESTATICO < DUTY_MAX_DEGRADADO < 1000 / 60  # velocidade mínima: ainda vence o atrito
    for cota in range(0, 6001, 50):
        assert duty_max_da_cota(cota) <= cota / 60 + 1e-9
        assert corrente_estimada_ma(duty_max_da_cota(cota)) <= max(cota, CORRENTE_REPOUSO_MA)


def test_derating_termico_e_watchdog():
    assert barramento_com_derating(25.0) == 12000
    assert barramento_com_derating(35.0) == 10000
    assert barramento_com_derating(10.0) == 12000
    assert barramento_com_derating(25.0, watchdog_expirado=True) == 3000
    assert barramento_com_derating(90.0) == 3000  # nunca abaixo do piso


@pytest.mark.parametrize("barramento", [3000, 5000, 10000, 12000])
def test_invariantes_da_reparticao_todas_as_combinacoes(barramento):
    for combo in itertools.product((*NECESSIDADES, "reserva"), repeat=3):
        cotas = repartir(barramento, dict(zip((1, 2, 3), combo, strict=True)))
        assert sum(cotas.values()) <= barramento
        assert all(CORRENTE_REPOUSO_MA <= c <= CORRENTE_MAX_CABINE_MA for c in cotas.values())
        # Invariante físico: a corrente real (modelo medido) de todas no duty máximo cabe no barramento.
        assert sum(corrente_estimada_ma(duty_max_da_cota(c)) for c in cotas.values()) <= barramento
        for cabine, necessidade in zip((1, 2, 3), combo, strict=True):
            if necessidade == "nenhuma":
                assert cotas[cabine] == CORRENTE_REPOUSO_MA  # parada: só o repouso, duty 0
            elif necessidade == "partida":  # admitida com folga contra o jitter, ou espera parada
                assert cotas[cabine] in (CORRENTE_REPOUSO_MA,) or cotas[cabine] >= COTA_MIN_PARTIDA_MA
            else:  # em movimento nunca perde o piso: sempre vence o atrito estático (11 % de arranque)
                assert duty_max_da_cota(cotas[cabine]) >= DUTY_ATRITO_ESTATICO + 1.0


def test_invariantes_com_barramentos_aleatorios():
    rng = random.Random(5)
    for _ in range(500):
        barramento = rng.randint(0, 14000)
        nec = {c: rng.choice(NECESSIDADES) for c in (1, 2, 3)}
        cotas = repartir(barramento, nec)
        assert sum(cotas.values()) <= barramento and max(cotas.values()) <= 6000
        assert sum(corrente_estimada_ma(duty_max_da_cota(c)) for c in cotas.values()) <= max(barramento, 900)


def test_tres_cabines_acelerando_nao_cabem_e_dividem_o_barramento():
    # 95 % do barramento menos o repouso das 3 cabines, dividido igualmente
    assert repartir(12000, dict.fromkeys((1, 2, 3), "movendo")) == {1: 3800, 2: 3800, 3: 3800}
    # Piso do watchdog (3000 mA): sem o repouso no cálculo a corrente real seria 3 x 1212 mA
    assert repartir(3000, dict.fromkeys((1, 2, 3), "movendo")) == {1: 950, 2: 950, 3: 950}


def test_cabine_em_movimento_tem_prioridade_sobre_a_que_vai_partir():
    cotas = repartir(12000, {1: "movendo", 2: "partida", 3: "nenhuma"})
    assert cotas == {1: 6000, 2: 5100, 3: CORRENTE_REPOUSO_MA}
    cotas = repartir(7000, {1: "movendo", 2: "partida", 3: "nenhuma"})
    assert cotas[1] > cotas[2] >= 1000 and sum(cotas.values()) == int(7000 * 0.95)


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


def test_reserva_de_pouso_degradado_desconta_do_barramento():
    cotas = repartir(12000, {1: "reserva", 2: "movendo", 3: "movendo"})
    assert cotas[1] == 1000 and sum(cotas.values()) <= 12000
    assert cotas[2] == cotas[3] == 5200
    # piso de 3000 mA: as duas em pouso degradado garantidas; a partida espera (não caberia com 1200 mA)
    assert repartir(3000, {1: "reserva", 2: "reserva", 3: "partida"}) == {1: 1000, 2: 1000, 3: 300}
    for barramento in (0, 1500, 3000, 12000):
        for combo in itertools.product((*NECESSIDADES, "reserva"), repeat=3):
            assert sum(repartir(barramento, dict(zip((1, 2, 3), combo, strict=True))).values()) <= barramento


def test_admissao_de_partidas_no_piso_do_barramento():
    """3000 mA (watchdog): andam 2 cabines a ~17 % em vez de 3 a ~11 % (cauda do jitter do PWM)."""
    cotas = repartir(3000, dict.fromkeys((1, 2, 3), "partida"))
    admitidas = [c for c, v in cotas.items() if v >= COTA_MIN_PARTIDA_MA]
    assert len(admitidas) == 2 and sorted(cotas.values()) == [300, 1275, 1275]
    assert duty_max_da_cota(1275) == pytest.approx(17.1, abs=0.05)
    # a ordem decide quem parte primeiro (quem espera há mais tempo)
    assert repartir(3000, dict.fromkeys((1, 2, 3), "partida"), ordem=[3, 1, 2])[2] == 300
    # quem já está em movimento nunca é rebaixado, mesmo sem folga para 1200 mA
    assert repartir(3000, dict.fromkeys((1, 2, 3), "movendo")) == {1: 950, 2: 950, 3: 950}
    # com o barramento nominal todas partem
    assert all(v >= COTA_MIN_PARTIDA_MA for v in repartir(12000, dict.fromkeys((1, 2, 3), "partida")).values())


def test_agenda_nao_embarca_alem_da_lotacao():
    from elevador.constantes import CAPACIDADE_PASSAGEIROS

    agenda = Agenda()
    for i in range(CAPACIDADE_PASSAGEIROS + 2):
        agenda.adicionar(Tarefa(i + 1, 0, 5, float(i)))
    eventos = agenda.atender(0, 100.0)
    assert len(eventos) == CAPACIDADE_PASSAGEIROS and agenda.a_bordo() == CAPACIDADE_PASSAGEIROS
    assert {e.tarefa.id for e in eventos} == set(range(1, CAPACIDADE_PASSAGEIROS + 1))  # FIFO por registro


def test_despacho_respeita_lotacao_de_8_passageiros():
    """Regressão da rasp32: 10 embarques na mesma cabine ligaram a falha 0x10 'sobrecarga'."""
    d = Despachante()
    for c in d.cabines.values():
        c.conectada, c.calibrada, c.estado, c.andar = True, True, "ociosa", 0
    atribuicoes = []
    for i in range(1, 25):
        cab = d.escolher_cabine(0, 5, 0.0, i)
        atribuicoes.append(cab)
        if cab is not None:
            d.atribuir(i, 0, 5, 0.0, cab)
    por_cabine = {n: sum(1 for c in atribuicoes if c == n) for n in (1, 2, 3)}
    assert max(por_cabine.values()) <= 8 and sum(por_cabine.values()) == 24
    assert d.escolher_cabine(0, 5, 0.0, 99) is None  # sem lugar: a chamada espera na fila do prédio


def test_cabine_lotada_nao_para_na_origem_de_quem_nao_cabe():
    """Regressão do livelock da rasp32: lotada no térreo com embarques antigos pendentes no térreo,
    a cabine abria e fechava a porta sem fim. Agora segue para entregar quem está a bordo."""
    from elevador.constantes import CAPACIDADE_PASSAGEIROS

    agenda = Agenda()
    for i in range(CAPACIDADE_PASSAGEIROS):
        agenda.adicionar(Tarefa(i + 1, 0, 3 + i % 3, 0.0, a_bordo=True))
    agenda.adicionar(Tarefa(50, 0, 1, 0.0))  # esperando no térreo há 100 s (prioridade por idade)
    assert agenda.proxima_parada(0, Sentido.PARADO, 100.0) == 3
    assert 0 not in agenda.paradas()
    agenda.atender(3, 110.0)  # desembarques no 3: agora há lugar e o térreo volta a ser alvo
    assert agenda.proxima_parada(3, Sentido.SUBINDO, 120.0) == 0
