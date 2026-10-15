"""Protocolo TCP (validação), servidor do Central e reconexão automática das cabines."""

import json
import socket
import threading
import time

import pytest

from elevador.cabine.config import ConfigCabine, Pinos, carregar_config
from elevador.cabine.rede import RedeCentral
from elevador.central.rede import ServidorCabines
from elevador.protocolo.canal import CanalFechado, CanalJson
from elevador.protocolo.mensagens import MensagemInvalida, codificar, decodificar, validar


def aguardar(condicao, limite_s=5.0):
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if condicao():
            return True
        threading.Event().wait(0.02)
    return condicao()


# --- esquema --------------------------------------------------------------------------------
def test_roundtrip_de_mensagem_valida():
    dados = codificar("cota", cota_ma=4000, ts=1.5, validade_s=2.0, seq=3)
    assert dados.endswith(b"\n")
    assert decodificar(dados.rstrip(b"\n"))["cota_ma"] == 4000


@pytest.mark.parametrize(
    "msg",
    [
        {"t": "desconhecida"},
        {"t": "cota", "cota_ma": -1, "ts": 0, "validade_s": 2, "seq": 1},  # fora da faixa
        {"t": "cota", "cota_ma": 99999, "ts": 0, "validade_s": 2, "seq": 1},
        {"t": "cota", "cota_ma": True, "ts": 0, "validade_s": 2, "seq": 1},  # bool não é inteiro
        {"t": "cota", "ts": 0, "validade_s": 2, "seq": 1},  # campo ausente
        {"t": "cota", "cota_ma": 1, "ts": 0, "validade_s": 2, "seq": 1, "extra": 1},  # campo extra
        {"t": "porta_cmd", "cabine": 4, "cmd": 1},
        {"t": "porta_cmd", "cabine": 1, "cmd": 3},
        {"t": "tarefa", "id": 1, "origem": 6, "destino": 2, "t_registro": 0},
        {"t": "evento", "cabine": 1, "tarefa": 1, "tipo": "teleporte", "andar": 1, "ts": 0},
        [1, 2, 3],
    ],
)
def test_mensagens_invalidas_sao_rejeitadas(msg):
    with pytest.raises(MensagemInvalida):
        validar(msg)


def test_json_malformado_e_linha_gigante_sao_rejeitados():
    with pytest.raises(MensagemInvalida):
        decodificar(b"{nao e json")
    with pytest.raises(MensagemInvalida):
        decodificar(b"x" * (70 * 1024))


def test_campo_tipo_do_evento_nao_colide_com_o_tipo_da_mensagem():
    dados = codificar("evento", cabine=1, tarefa=5, tipo="embarque", andar=2, ts=1.0)
    assert json.loads(dados)["t"] == "evento" and json.loads(dados)["tipo"] == "embarque"


# --- servidor do Central ----------------------------------------------------------------------
class Coletor:
    def __init__(self):
        self.hellos, self.mensagens, self.quedas = [], [], []

    def ao_hello(self, cabine, msg):
        self.hellos.append(cabine)

    def ao_mensagem(self, cabine, msg):
        self.mensagens.append((cabine, msg))

    def ao_desconexao(self, cabine):
        self.quedas.append(cabine)


@pytest.fixture
def servidor():
    c = Coletor()
    s = ServidorCabines("127.0.0.1", 0, "segredo", c.ao_hello, c.ao_mensagem, c.ao_desconexao)
    s.iniciar()
    s.coletor = c
    yield s
    s.parar()


def conectar(servidor, cabine=1, token="segredo"):
    sock = socket.create_connection(("127.0.0.1", servidor.porta), timeout=2)
    canal = CanalJson(sock, "teste")
    canal.enviar("hello", cabine=cabine, token=token, calibrada=False)
    return canal


def test_servidor_aceita_cabines_em_qualquer_ordem(servidor):
    canais = [conectar(servidor, n) for n in (3, 1, 2)]
    assert aguardar(lambda: sorted(servidor.coletor.hellos) == [1, 2, 3])
    assert all(servidor.conectada(n) for n in (1, 2, 3))
    assert canais[0].receber(2.0)["t"] == "boas_vindas"
    assert servidor.enviar(2, "cota", cota_ma=1000, ts=0.0, validade_s=2.0, seq=1)
    assert canais[2].receber(2.0)["t"] == "boas_vindas"
    assert canais[2].receber(2.0)["cota_ma"] == 1000


def test_token_invalido_e_rejeitado(servidor):
    canal = conectar(servidor, 1, token="errado")
    with pytest.raises(CanalFechado):
        canal.receber(2.0)
    assert servidor.coletor.hellos == []


def test_mensagem_malformada_nao_derruba_a_sessao(servidor):
    canal = conectar(servidor, 1)
    canal._sock.sendall(b"lixo\n")
    canal.enviar("pedir_modbus", cabine=1)
    assert aguardar(lambda: any(m["t"] == "pedir_modbus" for _, m in servidor.coletor.mensagens))
    assert servidor.conectada(1)


def test_desconexao_notificada_e_reconexao_substitui_a_sessao(servidor):
    canal = conectar(servidor, 1)
    assert aguardar(lambda: servidor.conectada(1))
    canal.fechar()
    assert aguardar(lambda: servidor.coletor.quedas == [1])
    nova = conectar(servidor, 1)
    assert aguardar(lambda: servidor.conectada(1))
    nova2 = conectar(servidor, 1)  # reinício da cabine: a sessão antiga é substituída
    assert aguardar(lambda: servidor.coletor.hellos.count(1) == 3)
    assert servidor.conectada(1)
    nova.fechar(), nova2.fechar()


# --- reconexão da cabine ---------------------------------------------------------------------
class RuntimeFalso:
    def __init__(self):
        self.recebidas, self.quedas = [], 0

    def mensagem_hello(self):
        return {"cabine": 2, "token": "", "calibrada": False, "agenda": []}

    def receber(self, msg):
        self.recebidas.append(msg)

    def conexao_perdida(self):
        self.quedas += 1


def test_cabine_reconecta_sozinha_apos_queda_e_inicia_antes_do_central():
    porta_livre = socket.socket()
    porta_livre.bind(("127.0.0.1", 0))
    porta = porta_livre.getsockname()[1]
    porta_livre.close()

    rede = RedeCentral("127.0.0.1", porta)
    rede.runtime = RuntimeFalso()
    rede.iniciar()  # a cabine sobe ANTES do Central
    try:
        assert not rede.conectado
        c = Coletor()
        s = ServidorCabines("127.0.0.1", porta, "", c.ao_hello, c.ao_mensagem, c.ao_desconexao)
        s.iniciar()
        assert aguardar(lambda: rede.conectado, 8)
        assert aguardar(lambda: 2 in c.hellos)
        assert rede.enviar("pedir_modbus", cabine=2)
        assert aguardar(lambda: any(m["t"] == "pedir_modbus" for _, m in c.mensagens))
        s.parar()  # Central cai
        assert aguardar(lambda: not rede.conectado and rede.runtime.quedas >= 1)
        assert not rede.enviar("pedir_modbus", cabine=2)
        s2 = ServidorCabines("127.0.0.1", porta, "", c.ao_hello, c.ao_mensagem, c.ao_desconexao)
        s2.iniciar()  # Central volta
        assert aguardar(lambda: rede.conectado, 8)
        assert aguardar(lambda: c.hellos.count(2) >= 2)
        s2.parar()
    finally:
        rede.parar()


# --- configuração -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("cabine", "pinos", "addr"),
    [
        (1, Pinos(13, 22, 23, 20, 21, 26, 0), 0x11),
        (2, Pinos(11, 17, 27, 5, 6, 16, 12), 0x12),
        (3, Pinos(18, 24, 25, 7, 8, 19, 1), 0x13),
    ],
)
def test_arquivos_de_configuracao_seguem_a_tabela_1(cabine, pinos, addr):
    cfg = carregar_config(f"config/cabine{cabine}.json")
    assert isinstance(cfg, ConfigCabine)
    assert (cfg.cabine, cfg.pinos, cfg.endereco_modbus) == (cabine, pinos, addr)
    assert cfg.central_porta == 5000


def test_configuracao_inconsistente_e_rejeitada(tmp_path):
    ruim = tmp_path / "c.json"
    ruim.write_text(json.dumps({"cabine": 2, "endereco_modbus": "0x11", "central": {"host": "h", "porta": 1}}))
    with pytest.raises(ValueError):
        carregar_config(ruim)
    ruim.write_text(json.dumps({"cabine": 9, "central": {"host": "h", "porta": 1}}))
    with pytest.raises(ValueError):
        carregar_config(ruim)
    ruim.write_text(json.dumps({"cabine": 1}))
    with pytest.raises(ValueError):
        carregar_config(ruim)
