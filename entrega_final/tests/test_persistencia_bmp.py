"""Persistência de estado/log e BMP280 (compensação de Bosch, simulador)."""

import json

import pytest

from elevador.central.bmp280 import Bmp280Simulado, Calibracao, compensar
from elevador.central.despacho import Chamada, Despachante
from elevador.central.persistencia import Persistencia

# Exemplo numérico do datasheet do BMP280 (seção 3.12): T = 25,08 °C, P = 100653,27 Pa.
CAL = Calibracao(27504, 26435, -1000, 36477, -10685, 3024, 2855, 140, -7, 15500, -14600, 6000)


def test_compensacao_bate_com_o_exemplo_do_datasheet():
    leitura = compensar(CAL, adc_t=519888, adc_p=415148)
    assert leitura.temperatura_c == pytest.approx(25.08, abs=0.01)
    assert leitura.pressao_hpa == pytest.approx(1006.5327, abs=0.01)


def test_calibracao_a_partir_dos_24_bytes():
    import struct

    bruto = struct.pack("<HhhHhhhhhhhh", *CAL.__dict__.values())
    assert Calibracao.de_bytes(bruto) == CAL


def test_sensor_simulado_oscila_pouco_e_pode_falhar():
    s = Bmp280Simulado(35.0)
    leituras = [s.ler().temperatura_c for _ in range(50)]
    assert all(34.9 <= t <= 35.1 for t in leituras)
    s.falhar = True
    with pytest.raises(OSError):
        s.ler()


def test_estado_roundtrip_atomico(tmp_path):
    p = Persistencia(tmp_path)
    assert p.carregar_estado() is None
    p.salvar_estado({"chamadas": [], "x": 1})
    assert p.carregar_estado()["x"] == 1
    assert not list(tmp_path.glob("*.tmp"))  # arquivo temporário substituído atomicamente


def test_estado_corrompido_ou_de_versao_diferente_e_ignorado(tmp_path):
    p = Persistencia(tmp_path)
    p.arquivo_estado.write_text("{truncado", encoding="utf-8")
    assert p.carregar_estado() is None
    p.arquivo_estado.write_text(json.dumps({"versao": 99}), encoding="utf-8")
    assert p.carregar_estado() is None


def test_log_de_viagens_tem_todos_os_campos(tmp_path):
    p = Persistencia(tmp_path)
    p.registrar_viagem(Chamada(7, 1, 4, 100.0, 2, "concluida", 110.0, 140.0))
    p.registrar_viagem(Chamada(8, 0, 5, 101.0, 3, "concluida", 120.0, 170.0))
    linhas = p.ler_viagens()
    assert linhas[0] == {
        "id_chamada": 7,
        "origem": 1,
        "destino": 4,
        "cabine_atribuida": 2,
        "timestamp_registro": 100.0,
        "timestamp_embarque": 110.0,
        "timestamp_desembarque": 140.0,
    }
    assert [v["id_chamada"] for v in linhas] == [7, 8]


def test_snapshot_e_restauracao_do_despachante(tmp_path):
    d = Despachante()
    d.cabines[2].andares_mm = [0.0, 3000.0]
    d.atribuir(5, 1, 4, 10.0, 2)
    d.atribuir(6, 0, 2, 11.0, 3)
    d.evento(3, 6, "embarque", 15.0)
    d.evento(3, 6, "desembarque", 30.0)  # concluída: não é persistida como em andamento
    p = Persistencia(tmp_path)
    p.salvar_estado(d.snapshot())

    novo = Despachante()
    novo.restaurar(p.carregar_estado())
    assert set(novo.chamadas) == {5}
    assert novo.chamadas[5].cabine == 2 and novo.cabines[2].agenda.tarefas[5].destino == 4
    assert novo.cabines[2].andares_mm == [0.0, 3000.0]
