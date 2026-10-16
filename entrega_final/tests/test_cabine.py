"""Calibração, reancoragem, nivelamento, cortina, Modo Degradado e Cota (runtime em tempo virtual)."""

import ast
import inspect

import pytest
from conftest import PID_TESTE, criar_harness_calibrado, ir_para

import elevador.cabine.calibracao as modulo_calibracao
from elevador.cabine.controlador import EstadoCabine
from elevador.constantes import DUTY_MAX_DEGRADADO, NUM_ANDARES, PortaEstado
from elevador.sim.bancada import Bancada
from elevador.sim.harness import HarnessCabine


def test_calibracao_mede_andares_reais_pela_media_das_bordas(harness):
    rt, geo = harness.runtime, harness.fisica.geometria
    assert rt.calibrada and len(rt.centros) == NUM_ANDARES
    reais = [a - geo.andares_mm[0] for a in geo.andares_mm]
    # centros no referencial do andar 0 (poço negativo): erro bem menor que a tolerância
    for medido, real in zip(rt.centros, reais, strict=True):
        assert abs(medido - real) <= 5
    # larguras de bandeirola diferentes em cada andar são medidas (não assumidas)
    assert max(rt.larguras) - min(rt.larguras) > 20
    assert all(abs(larg - 2 * s) <= 6 for larg, s in zip(rt.larguras, geo.semilargura_mm, strict=True))


def test_vao_real_difere_do_nominal_de_3000(harness):
    vaos = [b - a for a, b in zip(harness.runtime.centros, harness.runtime.centros[1:], strict=False)]
    assert any(abs(v - 3000) > 15 for v in vaos)


def test_calibracao_nao_consulta_registrador_nivelado():
    """A calibração só usa bordas por interrupção + `andar_atual`; o registrador 1 não é lido."""
    arvore = ast.parse(inspect.getsource(modulo_calibracao))
    corpo = arvore.body[1:] if ast.get_docstring(arvore) else arvore.body  # ignora o docstring do módulo
    textos = set()
    for no in (n for item in corpo for n in ast.walk(item)):
        if isinstance(no, ast.FunctionDef):
            textos.add(no.name)
        elif isinstance(no, ast.Name | ast.Attribute | ast.arg):
            textos.add(getattr(no, "id", None) or getattr(no, "attr", None) or no.arg)
        elif isinstance(no, ast.Constant) and isinstance(no.value, str):
            textos.add(no.value)
    assert not any("nivelado" in t for t in textos if t)
    assert "ao_borda" in {t for t in textos if t} and "ao_modbus" in {t for t in textos if t}


def test_so_calibra_com_central_e_espelho_modbus(harness_sem_calibrar):
    h = harness_sem_calibrar
    h.central.conectado = False
    h.rodar(5)
    assert h.runtime.estado == EstadoCabine.AGUARDANDO_CENTRAL
    assert h.fisica.duty == 0.0


def test_calibracao_apos_poco_inferior_comecando_abaixo_do_andar_0():
    bancada = Bancada()
    geo = bancada.cabines[1].geometria
    bancada.cabines[1].x = bancada.cabines[1].x0 = geo.andares_mm[0] - 300  # dentro do poço
    h = criar_harness_calibrado(bancada=bancada)
    assert h.runtime.calibrada and abs(h.runtime.centros[0]) < 1


def test_nivela_em_todos_os_andares_e_porta_nunca_abre_fora_da_faixa(harness):
    for andar in (3, 5, 1, 0, 4, 2):
        assert ir_para(harness, andar)
        erro = harness.fisica.x - harness.fisica.geometria.andares_mm[andar]
        assert abs(erro) <= 10, f"andar {andar}: erro real {erro:.1f} mm"
        harness.rodar(20, lambda: harness.runtime.estado == EstadoCabine.OCIOSA)
    assert harness.bancada.violacoes()["porta_fora_nivel"] == 0
    assert harness.bancada.violacoes()["movimento_porta_aberta"] == 0


def test_so_parte_com_porta_confirmada_fechada(harness):
    ir_para(harness, 2)
    harness.runtime.receber({"t": "ir_andar", "andar": 4})
    visto_movendo_com_porta_aberta = False
    for _ in range(1200):
        harness.passo()
        if harness.fisica.v != 0.0 and harness.fisica.porta != PortaEstado.FECHADA:
            visto_movendo_com_porta_aberta = True
    assert not visto_movendo_com_porta_aberta
    assert harness.bancada.cabines[1].andar_proximo == 4


def test_reancoragem_corrige_deriva_injetada_ao_passar_pela_bandeirola(harness):
    ir_para(harness, 1)
    harness.rodar(10, lambda: harness.runtime.estado == EstadoCabine.OCIOSA)
    harness.hal.deslocar_posicao(22)  # simula 22 mm de escorregamento do contador (> tolerância)
    antes = harness.runtime.reancoragens
    assert ir_para(harness, 3)
    assert harness.runtime.reancoragens > antes
    erro_real = harness.fisica.x - harness.fisica.geometria.andares_mm[3]
    assert abs(erro_real) <= 10


def test_deriva_acumulada_em_muitas_viagens_fica_dentro_da_tolerancia(harness):
    sequencia = [5, 0, 4, 1, 3, 0, 5, 2, 0, 5, 1, 4, 0, 3, 5, 0]
    for andar in sequencia:
        assert ir_para(harness, andar)
        assert abs(harness.fisica.x - harness.fisica.geometria.andares_mm[andar]) <= 10
        harness.rodar(15, lambda: harness.runtime.estado == EstadoCabine.OCIOSA)
    assert abs(harness.fisica.deriva) > 0  # havia deriva real a corrigir
    assert harness.runtime.reancoragens >= len(sequencia)


def test_corrente_respeita_a_cota_concedida():
    h = criar_harness_calibrado(cota_ma=2000)
    h.fisica.max_corrente_ma = 0
    assert ir_para(h, 2)
    assert h.fisica.max_corrente_ma <= 2000


def test_cortina_inibe_fechamento_e_reinicia_quando_libera(harness):
    ir_para(harness, 1)
    rt, fis = harness.runtime, harness.fisica
    fis.definir_cortina(True)  # passageiro na porta
    assert harness.rodar(30, lambda: rt.estado == EstadoCabine.EMBARQUE)
    harness.rodar(20)  # além do tempo de porta aberta: segue aberta enquanto obstruída
    assert fis.porta == PortaEstado.ABERTA
    fis.definir_cortina(False)
    assert harness.rodar(30, lambda: fis.porta == PortaEstado.FECHADA)


def test_cortina_durante_fechamento_manda_reabrir(harness):
    ir_para(harness, 1)
    rt, fis = harness.runtime, harness.fisica
    assert harness.rodar(30, lambda: rt.estado == EstadoCabine.FECHANDO_PORTA)
    fis.definir_cortina(True)
    assert harness.rodar(10, lambda: fis.porta in (PortaEstado.ABRINDO, PortaEstado.ABERTA))
    harness.rodar(5)
    assert fis.porta == PortaEstado.ABERTA
    fis.definir_cortina(False)
    assert harness.rodar(30, lambda: fis.porta == PortaEstado.FECHADA)


def test_modo_degradado_em_voo_pousa_no_proximo_andar_e_abre_a_porta():
    h = criar_harness_calibrado(cota_ma=4000)
    rt, fis = h.runtime, h.fisica
    h.runtime.receber({"t": "ir_andar", "andar": 5})
    assert h.rodar(60, lambda: fis.v > 100 and rt.hal.posicao() > rt.centros[1] + 600)  # já acima do 1º andar
    h.central.cota_ma = None  # Central deixa de renovar a Cota
    assert h.rodar(5, lambda: rt.degradado)  # expira em 2 s
    vmax = 0.0
    for _ in range(int(60 / 0.05)):
        h.passo()
        if rt.degradado and fis.v:
            vmax = max(vmax, abs(fis.v))
        if fis.porta == PortaEstado.ABERTA:
            break
    assert fis.porta == PortaEstado.ABERTA
    assert fis.andar_proximo == 2  # próximo andar no sentido do movimento (subindo)
    assert abs(fis.x - fis.geometria.andares_mm[2]) <= 10
    assert h.bancada.violacoes()["porta_fora_nivel"] == 0
    assert vmax <= 8.0 * DUTY_MAX_DEGRADADO * 1.1  # velocidade mínima (1000 mA)
    h.rodar(30)  # permanece parada de porta aberta enquanto não houver Cota válida
    assert fis.porta == PortaEstado.ABERTA and fis.v == 0.0
    h.central.cota_ma = 4000  # Cota volta: retoma a viagem até o andar 5
    assert h.rodar(120, lambda: fis.andar_proximo == 5 and fis.porta == PortaEstado.ABERTA)


def test_modo_degradado_parada_nivelada_abre_porta_e_aguarda():
    h = criar_harness_calibrado()
    h.runtime.receber({"t": "ir_andar", "andar": 1})
    h.rodar(60, lambda: h.runtime.estado == EstadoCabine.OCIOSA)
    h.central.cota_ma = None
    assert h.rodar(30, lambda: h.fisica.porta == PortaEstado.ABERTA)


def test_queda_do_tcp_tambem_leva_ao_modo_degradado():
    h = criar_harness_calibrado()
    h.rodar(2)
    assert not h.runtime.degradado
    h.central.desconectar()
    h.rodar(0.2)
    assert h.runtime.degradado


def test_telemetria_a_cada_500ms_com_campos_do_enunciado(harness):
    harness.central.telemetrias.clear()
    harness.rodar(10)
    n = len(harness.central.telemetrias)
    assert 18 <= n <= 22
    t = harness.central.telemetrias[-1]
    for campo in ("pos_mm", "andar", "sentido", "porta", "corrente_ma"):
        assert campo in t


def test_pid_padrao_do_enunciado_e_configuravel():
    from elevador.cabine.config import ParametrosPid

    padrao = ParametrosPid()
    assert (padrao.kp, padrao.ki, padrao.kd) == (0.5, 0.05, 40.0)
    h = HarnessCabine(pid=PID_TESTE)
    assert h.runtime.pid.kd == pytest.approx(5.0)


def test_espelho_modbus_por_tcp_nao_carrega_nivelado_nem_posicao_mm():
    """A cabine não recebe `nivelado` (reg. 1) nem `posicao_mm` (reg. 7): não pode usá-los."""
    from elevador.protocolo.mensagens import ESQUEMAS

    campos = {c.lstrip("?") for c in ESQUEMAS["modbus"]}
    assert "nivelado" not in campos and "posicao_mm" not in campos


def test_registro_csv_da_malha_grava_um_registro_por_tick(harness, tmp_path):
    import csv

    from elevador.cabine.registro import CAMPOS, RegistroCsv

    registro = RegistroCsv(str(tmp_path / "malha.csv"))
    harness.runtime.receber({"t": "ir_andar", "andar": 2})
    for _ in range(40):
        harness.passo()
        registro.gravar(harness.runtime.relogio(), harness.runtime)
    registro.fechar()
    registro.gravar(0.0, harness.runtime)  # depois de fechado: ignora em silêncio
    linhas = list(csv.DictReader(open(tmp_path / "malha.csv", encoding="utf-8")))
    assert len(linhas) == 40 and tuple(linhas[0]) == CAMPOS
    assert any(linha["estado"] == "movendo" and linha["alvo_mm"] for linha in linhas)


def test_porta_ignorada_pelo_simulador_dispara_recentralizacao_estrita(harness):
    """Regressão da rasp32: parada a 10 mm (borda da faixa) e o simulador não abria a porta."""
    from elevador.cabine.controlador import ACEITE_RECENTRALIZACAO_MM, PORTA_SEM_RESPOSTA_S

    rt = harness.runtime
    assert ir_para(harness, 1)
    rt.estado = EstadoCabine.OCIOSA
    harness.rodar(10, lambda: rt.estado == EstadoCabine.OCIOSA and harness.bancada.cabines[1].porta == 0)
    rt.hal.deslocar_posicao(-4)  # encoder 4 mm aquém do centro: "nivelada" para a cabine
    agora = rt.relogio()
    rt._abrir_porta(agora, 1)
    rt._modbus = {"andar_atual": 1, "porta_estado": PortaEstado.FECHADA, "corrente_ma": 0, "falha": 0, "ts": agora}
    rt._passo_abrindo(agora + 1.0, rt.hal.posicao())
    assert rt.estado == EstadoCabine.ABRINDO_PORTA  # ainda dentro do prazo: só reenvia o pedido
    rt._modbus["ts"] = agora + PORTA_SEM_RESPOSTA_S + 0.1
    rt._passo_abrindo(agora + PORTA_SEM_RESPOSTA_S + 0.1, rt.hal.posicao())
    assert rt.estado == EstadoCabine.MOVENDO and rt.recentralizacoes == 1
    assert rt.ctrl.aceite_mm == ACEITE_RECENTRALIZACAO_MM


def test_modo_degradado_reabre_a_porta_que_o_firmware_fecha_sozinho(harness):
    """Na rasp32 o firmware fecha a porta 3 s após abrir; degradada, a cabine a mantém aberta."""
    rt = harness.runtime
    assert ir_para(harness, 1)
    enviados = []
    enviar_original = rt.enlace.enviar
    rt.enlace.enviar = lambda tipo, **c: enviados.append((tipo, c)) or enviar_original(tipo, **c)
    agora = rt.relogio()
    rt.cota.invalidar()
    rt.degradado = True
    rt.estado = EstadoCabine.EMBARQUE
    rt._t_cmd_porta = float("-inf")
    rt._modbus = {"andar_atual": 1, "porta_estado": PortaEstado.FECHANDO, "corrente_ma": 300, "falha": 0, "ts": agora}
    rt._passo_embarque(agora, rt.hal.posicao())
    assert ("porta_cmd", {"cabine": 1, "cmd": 1}) in enviados
    enviados.clear()
    rt._passo_embarque(agora + 0.2, rt.hal.posicao())  # limitado a 1 pedido a cada 0,5 s
    assert not enviados
    rt._modbus["porta_estado"] = PortaEstado.ABERTA
    rt._passo_embarque(agora + 1.0, rt.hal.posicao())  # aberta: nada a fazer
    assert not enviados and rt.estado == EstadoCabine.EMBARQUE


def test_so_pede_cota_de_partida_quem_esta_pronta_para_sair(harness):
    """Ciclos de porta não seguram a admissão de partida (a outra cabine ficava sem Cota)."""
    from elevador.dominio.agenda import Tarefa

    rt = harness.runtime
    rt.agenda.adicionar(Tarefa(7, 3, 5, rt.relogio()))
    rt.degradado = False
    for estado in (EstadoCabine.ABRINDO_PORTA, EstadoCabine.EMBARQUE, EstadoCabine.FECHANDO_PORTA):
        rt.estado = estado
        assert rt.necessidade() == "nenhuma"
    rt.estado = EstadoCabine.OCIOSA
    assert rt.necessidade() == "partida"
    rt.estado = EstadoCabine.MOVENDO
    assert rt.necessidade() == "movendo"
