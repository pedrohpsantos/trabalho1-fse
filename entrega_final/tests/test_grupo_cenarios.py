"""Cenários de tráfego com o grupo completo em tempo virtual: invariantes da Seção 7.5."""

import pytest

from elevador.constantes import LIMITE_ESPERA_S, Cenario
from elevador.sim.grupo import GrupoVirtual


def executar_cenario(cenario: Cenario, semente: int, intervalo_s: float = 8.0, temperatura_c: float = 25.0):
    g = GrupoVirtual(semente=semente, temperatura_c=temperatura_c)
    assert g.rodar(400, lambda: all(r.calibrada for r in g.runtimes.values())), "calibração do grupo"
    g.rodar(20)
    g.bancada.iniciar_cenario(cenario, semente, intervalo_s)
    predio = g.bancada.predio
    g.rodar(300)
    g.rodar(300, lambda: predio.cenario == Cenario.NENHUM)
    g.rodar(3)  # deixa os últimos eventos de desembarque chegarem ao Central
    return g, predio


@pytest.mark.parametrize("cenario", [Cenario.UP_PEAK, Cenario.DOWN_PEAK, Cenario.INTERFLOOR])
@pytest.mark.parametrize("semente", [1, 2])
def test_invariantes_do_despacho_por_cenario(cenario, semente):
    g, predio = executar_cenario(cenario, semente)
    assert predio.geradas > 20 and predio.atendidas == predio.geradas  # toda chamada atendida
    esperas = [c.t_embarque - c.t_registro for c in predio.chamadas.values()]
    assert max(esperas) < LIMITE_ESPERA_S  # nenhuma espera de embarque acima de 90 s
    assert g.cotas_violadas == 0  # soma(cotas) <= barramento_max_ma
    assert g.bancada.metricas.tempo_sobrecarga_s == 0.0  # corrente real nunca passou do barramento
    v = g.bancada.violacoes()
    assert v["porta_fora_nivel"] == 0 and v["movimento_porta_aberta"] == 0
    assert v["chamadas_perdidas"] == 0 and v["fim_de_curso"] == 0 and v["sobrecarga_lotacao"] == 0
    # métricas consolidadas pelo simulador batem com as observadas
    assert predio.registradores(g.bancada.t)[13] == predio.atendidas


def test_viagens_registradas_no_log_persistente():
    g, predio = executar_cenario(Cenario.INTERFLOOR, 3)
    viagens = g.servico.persistencia.ler_viagens()
    assert len(viagens) == predio.atendidas
    for v in viagens:
        assert v["timestamp_registro"] <= v["timestamp_embarque"] < v["timestamp_desembarque"]
        assert {"id_chamada", "origem", "destino", "cabine_atribuida"} <= set(v)


def test_derating_termico_reduz_cotas_mas_sistema_atende_as_chamadas():
    g, predio = executar_cenario(Cenario.INTERFLOOR, 1, temperatura_c=40.0)  # 12000 - 200*15 = 9000
    assert g.servico.predio.barramento_max_ma == 9000
    assert g.max_soma_cotas <= 9000 and g.cotas_violadas == 0
    assert predio.atendidas == predio.geradas


def test_condicao_de_contorno_mantem_o_watchdog_satisfeito():
    g = GrupoVirtual()
    g.rodar(120)
    assert g.bancada.predio.watchdog_expirado(g.bancada.t) is False
    assert g.servico.predio.watchdog_ambiente == 0
    assert g.bancada.predio.temp_x10 == pytest.approx(250, abs=2)


def test_sem_leitura_i2c_o_watchdog_expira_e_barramento_cai_ao_piso():
    g = GrupoVirtual()
    g.sensor.falhar = True  # I2C morto: o Central não reescreve dado velho
    g.rodar(70)
    assert g.servico.predio.watchdog_ambiente == 1
    assert g.servico.predio.barramento_max_ma == 3000
    assert g.max_soma_cotas <= 12000 and g.servico._cotas and sum(g.servico._cotas.values()) <= 3000
