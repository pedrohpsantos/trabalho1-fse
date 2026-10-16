"""Decodificador de quadratura, PID (anti-windup, saturação) e controlador de posição."""

import threading
import time

import pytest

from elevador.cabine.encoder import DecodificadorQuadratura, envolver_int32
from elevador.cabine.hal.base import DebounceCortina
from elevador.cabine.movimento import ControladorPosicao, FaseMovimento
from elevador.cabine.pid import Pid
from elevador.constantes import DUTY_ATRITO_ESTATICO, Direcao

SUBINDO = [(0, 1), (1, 1), (1, 0), (0, 0)]  # sequência de estados A,B com contagem crescente


def test_quadratura_4x_conta_quatro_por_ciclo_nos_dois_sentidos():
    dec = DecodificadorQuadratura(0, 0)
    for _ in range(3):
        for a, b in SUBINDO:
            dec.atualizar(a, b)
    assert dec.contagem == 12
    for _ in range(5):
        for a, b in reversed([(0, 0), *SUBINDO[:-1]]):
            dec.atualizar(a, b)
    assert dec.contagem == 12 - 20  # negativa no poço: inteiro com sinal


def test_quadratura_ignora_transicao_invalida_e_repeticao():
    dec = DecodificadorQuadratura(0, 0)
    dec.atualizar(0, 0)  # sem mudança
    dec.atualizar(1, 1)  # dois bits mudaram: transição inválida
    assert dec.contagem == 0


def test_contador_int32_com_sinal_e_deslocamento_atomico():
    assert envolver_int32(2**31) == -(2**31)
    assert envolver_int32(-(2**31) - 1) == 2**31 - 1
    dec = DecodificadorQuadratura()
    dec.deslocar(-400)
    assert dec.contagem == -400


def test_decodificador_thread_safe():
    dec = DecodificadorQuadratura(0, 0)

    def girar():
        for _ in range(2000):
            for a, b in SUBINDO:
                dec.atualizar(a, b)

    # Uma única "ISR" por vez gira o encoder; a outra thread só desloca (reancoragem).
    t = threading.Thread(target=girar)
    t.start()
    for _ in range(500):
        dec.deslocar(1)
        dec.deslocar(-1)
    t.join()
    assert dec.contagem == 8000


def test_debounce_cortina_filtra_ruido_e_confirma_nivel_estavel():
    nivel = {"v": False}
    mudancas = []
    deb = DebounceCortina(lambda: nivel["v"], mudancas.append, janela_s=0.05)
    for valor in (True, False, True, False, True):  # ruído: nenhuma confirmação enquanto oscila
        nivel["v"] = valor
        deb.ao_borda()
        time.sleep(0.01)
    time.sleep(0.2)
    assert mudancas == [True]
    nivel["v"] = False
    deb.ao_borda()
    time.sleep(0.2)
    assert mudancas == [True, False]
    deb.cancelar()


def test_pid_proporcional_integral_derivativo_por_amostra():
    pid = Pid(2.0, 0.5, 3.0, limite_integral_pct=1000)
    assert pid.calcular(10.0, 0.05) == pytest.approx(2 * 10 + 0.5 * 10 + 0)  # 1ª amostra: derivada 0
    # erro cai 4: P=12, I=0,5*(10+6)=8, D=3*(-4)
    assert pid.calcular(6.0, 0.05) == pytest.approx(2 * 6 + 0.5 * 16 + 3 * -4)


def test_pid_derivada_normalizada_pelo_periodo():
    a, b = Pid(0, 0, 1.0), Pid(0, 0, 1.0)
    a.calcular(10.0, 0.05)
    b.calcular(10.0, 0.10)
    assert a.calcular(8.0, 0.05) == pytest.approx(-2.0)
    assert b.calcular(8.0, 0.10) == pytest.approx(-1.0)  # amostra 2x mais longa: metade do ganho


def test_pid_antiwindup_congela_integral_saturado_e_limita_contribuicao():
    pid = Pid(0.5, 0.05, 0.0, limite_integral_pct=15.0)
    for _ in range(1000):
        pid.calcular(5.0, 0.05, saturado=+1)  # saturado no sentido do erro: não integra
    assert pid.integral == 0.0
    for _ in range(1000):
        pid.calcular(5.0, 0.05)  # integra, mas limitado a 15 pontos de duty
    assert 0.05 * pid.integral == pytest.approx(15.0)


def rodar_controlador(ctrl, plant_pos, ate=400, duty_max=100.0, vel_por_pct=8.0):
    """Planta mínima de integrador com atrito estático, 20 passos de 50 ms por segundo."""
    pos, v, historico = plant_pos, 0.0, []
    for _ in range(ate):
        cmd = ctrl.passo(round(pos), duty_max, 0.05)
        alvo_v = 0.0
        if cmd.direcao in (Direcao.SUBIR, Direcao.DESCER):
            if abs(v) > 0.5 or cmd.duty >= DUTY_ATRITO_ESTATICO:
                alvo_v = vel_por_pct * cmd.duty * (1 if cmd.direcao == Direcao.SUBIR else -1)
        v += (alvo_v - v) * (0.05 / 0.15 if alvo_v else 0.05 / 0.03)
        pos += v * 0.05
        historico.append((pos, cmd))
        if ctrl.chegou:
            break
    return pos, historico


def test_controlador_chega_dentro_de_10mm_e_freia():
    ctrl = ControladorPosicao(Pid(0.5, 0.02, 5.0))
    ctrl.ir_para(3000)
    pos, hist = rodar_controlador(ctrl, 0.0)
    assert ctrl.fase == FaseMovimento.CHEGOU and abs(pos - 3000) <= 10
    assert hist[-1][1].direcao == Direcao.FREIO and hist[-1][1].duty == 0.0


def test_rampa_parte_acima_do_atrito_estatico_e_cresce_gradualmente():
    ctrl = ControladorPosicao(Pid(0.5, 0.02, 5.0), rampa_pct_por_s=25.0)
    ctrl.ir_para(3000)
    _, hist = rodar_controlador(ctrl, 0.0, ate=40)
    duties = [c.duty for _, c in hist]
    assert duties[0] >= DUTY_ATRITO_ESTATICO  # 1º duty já vence o atrito
    assert duties[0] < 20 and all(b - a <= 25 * 0.05 + 1e-9 for a, b in zip(duties, duties[1:], strict=False))


def test_saturacao_pela_cota_nunca_excede_duty_max():
    ctrl = ControladorPosicao(Pid(0.5, 0.02, 5.0))
    ctrl.ir_para(3000)
    _, hist = rodar_controlador(ctrl, 0.0, duty_max=1000 / 6000 * 100)
    assert max(c.duty for _, c in hist) <= 1000 / 60 + 1e-9


def test_sem_cota_suficiente_para_vencer_atrito_mantem_freio():
    ctrl = ControladorPosicao(Pid(0.5, 0.02, 5.0))
    ctrl.ir_para(500)
    pos, hist = rodar_controlador(ctrl, 0.0, ate=60, duty_max=5.0)
    assert pos == 0.0 and all(c.direcao == Direcao.FREIO for _, c in hist)


def test_erro_pequeno_nao_deixa_cabine_parada_com_windup():
    ctrl = ControladorPosicao(Pid(0.5, 0.02, 5.0))
    ctrl.ir_para(25)  # P = 12,5%; Ki limitado: sai do atrito e nivela sem overshoot grande
    pos, _ = rodar_controlador(ctrl, 0.0)
    assert abs(pos - 25) <= 10 and ctrl.chegou


def test_reancorar_nao_gera_pico_derivativo():
    pid = Pid(0.0, 0.0, 10.0)
    pid.calcular(100.0, 0.05)
    pid.deslocar_erro(-7.0)  # posição deslocada +7 mm
    assert pid.calcular(93.0, 0.05) == pytest.approx(0.0)


def rodar_planta_bancada(ctrl, pos, duty_max, ate=4000):
    """Planta calibrada pelos CSVs da rasp32 (Pi 3 + ESP32): ~9,6 mm/s por 1 % de duty,
    aceleração com constante de ~0,15 s, atrito estático de 10 % (abaixo disso a cabine
    "gruda" logo que perde velocidade), freio com ~1 tick de inércia e encoder lido com
    1 tick de atraso. Reproduz o stick-slip de ~8 mm por pulso visto perto do alvo."""
    v, medida = 0.0, pos
    for _ in range(ate):
        cmd = ctrl.passo(round(medida), duty_max, 0.05)
        medida = pos  # o controle enxerga a posição do tick anterior
        sinal = 1 if cmd.direcao == Direcao.SUBIR else -1
        if cmd.direcao in (Direcao.SUBIR, Direcao.DESCER) and (abs(v) > 20 or cmd.duty >= DUTY_ATRITO_ESTATICO):
            v += (9.6 * cmd.duty * sinal - v) * (0.05 / 0.15)
        else:
            v *= 0.35  # freio/livre: perde ~65 % da velocidade por tick
            if abs(v) < 20:
                v = 0.0
        pos += v * 0.05
        if ctrl.chegou:
            return pos
    return None


@pytest.mark.parametrize("duty_max", [1000 / 60, 3000 / 60, 100.0])
@pytest.mark.parametrize("distancia", [-14870, -3000, -60, -26, -12, 9, 18, 40, 2865, 14870])
def test_chegada_com_margem_para_a_tolerancia_na_planta_da_bancada(duty_max, distancia):
    """Regressão da rasp32: a cabine 3 parou a 10 mm do alvo e o simulador recusou a porta.

    A chegada tem de ficar a <= 5 mm (metade da tolerância), sobrando margem para o erro
    residual de calibração/deriva entre o encoder e o andar real.
    """
    ctrl = ControladorPosicao(Pid(0.5, 0.02, 5.0))
    ctrl.ir_para(20000.0 + distancia)
    final = rodar_planta_bancada(ctrl, 20000.0, duty_max)
    assert final is not None, "não chegou"
    assert abs(final - ctrl.alvo) <= ControladorPosicao.ACEITE_CHEGADA_MM
