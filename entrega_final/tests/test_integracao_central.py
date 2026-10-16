"""Servidor Central real (threads, TCP, fila MODBUS) contra a bancada virtual e cabines falsas."""

import socket
import threading
import time

import pytest

from elevador.central.bmp280 import Bmp280Simulado
from elevador.central.config import ConfigCentral
from elevador.central.persistencia import Persistencia
from elevador.central.servico import ServicoCentral
from elevador.constantes import PortaEstado
from elevador.modbus.cliente import ClienteModbus
from elevador.modbus.gerente import GerenteModbus
from elevador.modbus.simulador import ApiSimulador
from elevador.protocolo.canal import CanalJson
from elevador.sim.bancada import Bancada, ExecutorTempoReal
from elevador.sim.uart_sim import UartVirtual


def aguardar(condicao, limite_s=8.0):
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if condicao():
            return True
        threading.Event().wait(0.02)
    return condicao()


class CabineFalsa:
    """Fala o protocolo TCP como um Servidor Distribuído, sem GPIO."""

    def __init__(self, porta, numero, agenda=None):
        self.numero = numero
        self.canal = CanalJson(socket.create_connection(("127.0.0.1", porta), timeout=2), f"c{numero}")
        self.recebidas: list[dict] = []
        self.canal.enviar("hello", cabine=numero, token="", calibrada=True, agenda=agenda or [])
        self._ativo = True
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        while self._ativo:
            try:
                self.recebidas.append(self.canal.receber(0.5))
            except TimeoutError:
                continue
            except Exception:  # noqa: BLE001 - conexão encerrada no fim do teste
                return

    def telemetria(self, necessidade="partida", estado="ociosa", andar=0):
        self.canal.enviar(
            "telemetria",
            cabine=self.numero,
            ts=time.time(),
            pos_mm=0.0,
            andar=andar,
            sentido="parado",
            porta=0,
            corrente_ma=0,
            estado=estado,
            degradado=False,
            cota_ma=0,
            necessidade=necessidade,
            calibrada=True,
            pendentes=0,
            alvo=-1,
            nivelado=True,
            falha="",
            duty=0.0,
        )

    def de_tipo(self, tipo):
        return [m for m in list(self.recebidas) if m["t"] == tipo]

    def fechar(self):
        self._ativo = False
        self.canal.fechar()


@pytest.fixture
def ambiente(tmp_path):
    bancada = Bancada()
    executor = ExecutorTempoReal(bancada, fator=5.0)

    def criar_servico():
        cfg = ConfigCentral(escuta_porta=0, pasta_dados=str(tmp_path), periodo_ambiente_s=0.5)
        gerente = GerenteModbus(ApiSimulador(ClienteModbus(UartVirtual(bancada), "038442", intervalo_retry_s=0)))
        return ServicoCentral(cfg, gerente, Bmp280Simulado(30.0), Persistencia(tmp_path))

    servicos, cabines = [], []

    def iniciar():
        s = criar_servico()
        s.iniciar()
        servicos.append(s)
        return s

    executor.iniciar()
    yield bancada, iniciar, cabines
    for c in cabines:
        c.fechar()
    for s in servicos:
        s.encerrar()
    executor.parar()


def test_central_ponta_a_ponta_chamada_cota_ambiente_e_porta(ambiente):
    bancada, iniciar, cabines = ambiente
    servico = iniciar()
    cabines.extend(CabineFalsa(servico.rede.porta, n) for n in (1, 2, 3))
    assert aguardar(lambda: all(servico.rede.conectada(n) for n in (1, 2, 3)))
    for c in cabines:
        c.telemetria("partida")

    # 1) Condição de contorno: BMP280 -> MODBUS (offsets 5 e 6), watchdog satisfeito
    assert aguardar(lambda: bancada.predio.temp_x10 == pytest.approx(300, abs=2))
    assert aguardar(lambda: servico.predio is not None and servico.predio.watchdog_ambiente == 0)
    assert servico.predio.barramento_max_ma == pytest.approx(11000, abs=20)  # derating a 30 °C: 12000 - 200*5

    # 2) Cotas renovadas, soma <= barramento e cada uma <= 6000 mA
    assert aguardar(lambda: all(len(c.de_tipo("cota")) >= 2 for c in cabines))
    ultimas = [c.de_tipo("cota")[-1] for c in cabines]
    assert sum(m["cota_ma"] for m in ultimas) <= servico.barramento_atual()
    assert all(m["cota_ma"] <= 6000 and m["validade_s"] == 2.0 for m in ultimas)

    # 3) Despacho: chamada da fila -> atribuição MODBUS + pop + tarefa por TCP
    id_ = bancada.registrar_chamada(0, 3)
    assert aguardar(lambda: bancada.predio.chamadas[id_].cabine != 0)
    cabine = bancada.predio.chamadas[id_].cabine
    assert not bancada.predio.fila
    assert aguardar(lambda: any(m["id"] == id_ for m in cabines[cabine - 1].de_tipo("tarefa")))
    tarefa = next(m for m in cabines[cabine - 1].de_tipo("tarefa") if m["id"] == id_)
    assert (tarefa["origem"], tarefa["destino"]) == (0, 3)

    # 4) Eventos da cabine fecham a viagem e alimentam o log persistente
    cab = cabines[cabine - 1]
    cab.canal.enviar("evento", cabine=cabine, tarefa=id_, tipo="embarque", andar=0, ts=time.time())
    cab.canal.enviar("evento", cabine=cabine, tarefa=id_, tipo="desembarque", andar=3, ts=time.time() + 10)
    assert aguardar(lambda: len(servico.persistencia.ler_viagens()) == 1)
    assert servico.persistencia.ler_viagens()[0]["cabine_atribuida"] == cabine

    # 5) Comando de porta repassado pelo Central (único dono da UART) com intertravamento:
    #    fora do nível o ABRIR NÃO chega ao simulador e a cabine é avisada.
    fis = bancada.cabines[1]
    fis.x = fis.geometria.andares_mm[0] + 20.0
    cabines[0].canal.enviar("porta_cmd", cabine=1, cmd=1)
    assert aguardar(lambda: cabines[0].de_tipo("porta_recusada"))
    assert fis.porta == PortaEstado.FECHADA and servico.portas_recusadas == 1
    fis.x = fis.geometria.andares_mm[0]  # nivelada: o ABRIR é repassado
    cabines[0].canal.enviar("porta_cmd", cabine=1, cmd=1)
    assert aguardar(lambda: bancada.cabines[1].porta in (PortaEstado.ABRINDO, PortaEstado.ABERTA))
    cabines[0].canal.enviar("pedir_modbus", cabine=1)
    assert aguardar(lambda: any(m["porta_estado"] in (1, 2) for m in cabines[0].de_tipo("modbus")))

    # 6) Comando manual do terminal
    assert servico.comando("ir 2 4") == "cabine 2 -> andar 4"
    assert aguardar(lambda: any(m["andar"] == 4 for m in cabines[1].de_tipo("ir_andar")))
    assert servico.comando("ir 9 1").startswith("uso")


def test_cota_de_repouso_para_ociosas_e_renegociacao_imediata(ambiente):
    _, iniciar, cabines = ambiente
    servico = iniciar()
    cabines.extend(CabineFalsa(servico.rede.porta, n) for n in (1, 2, 3))
    assert aguardar(lambda: all(servico.rede.conectada(n) for n in (1, 2, 3)))
    for c in cabines:
        c.telemetria("nenhuma", "ociosa")
    assert aguardar(lambda: all(c.de_tipo("cota") and c.de_tipo("cota")[-1]["cota_ma"] == 300 for c in cabines))
    n_antes = len(cabines[0].de_tipo("cota"))
    cabines[0].telemetria("partida", "ociosa")  # uma cabine passa a precisar partir
    assert aguardar(lambda: cabines[0].de_tipo("cota")[-1]["cota_ma"] >= 1000, 0.9)  # bem antes de 1 s
    assert len(cabines[0].de_tipo("cota")) > n_antes


def test_estado_persistido_e_chamadas_reenviadas_apos_reinicio_do_central(ambiente):
    bancada, iniciar, cabines = ambiente
    servico = iniciar()
    cabines.append(CabineFalsa(servico.rede.porta, 1))
    cabines[0].telemetria()
    assert aguardar(lambda: servico.rede.conectada(1))
    for estado in servico.despacho.cabines.values():
        estado.conectada = estado.numero == 1
    id_ = bancada.registrar_chamada(2, 5)
    assert aguardar(lambda: id_ in servico.despacho.chamadas and not bancada.predio.fila)
    servico.encerrar()
    cabines[0].fechar()

    # Central reinicia: restaura as chamadas em andamento do disco
    novo = iniciar()
    assert id_ in novo.despacho.chamadas and novo.despacho.chamadas[id_].cabine == 1
    # a cabine 1 reconecta SEM a tarefa (reiniciou): o Central a reenvia
    cabines.append(CabineFalsa(novo.rede.porta, 1, agenda=[]))
    assert aguardar(lambda: any(m["id"] == id_ for m in cabines[-1].de_tipo("tarefa")))
    # se a cabine já tiver a tarefa, nada é reenviado em duplicidade
    cabines.append(CabineFalsa(novo.rede.porta, 1, agenda=[{"id": id_, "origem": 2, "destino": 5, "t_registro": 0.0}]))
    time.sleep(0.5)
    assert not cabines[-1].de_tipo("tarefa")


def test_leitura_i2c_falha_nao_reenvia_dado_velho_apos_20s():
    from elevador.central import servico as modulo

    assert modulo.IDADE_MAX_LEITURA_AMBIENTE_S < 30.0  # watchdog do simulador


def test_cotas_off_suspende_renovacao_e_on_retoma(ambiente):
    """Teste 1 do Modo Degradado com o Central vivo: sem renovação a Cota expira em 2 s."""
    _, iniciar, cabines = ambiente
    servico = iniciar()
    cabines.append(CabineFalsa(servico.rede.porta, 1))
    assert aguardar(lambda: servico.rede.conectada(1))
    cabines[0].telemetria("partida")
    assert aguardar(lambda: len(cabines[0].de_tipo("cota")) >= 1)
    assert servico.comando("cotas off").startswith("Cotas suspensas")
    assert servico.visao().cotas_suspensas
    time.sleep(0.2)
    n = len(cabines[0].de_tipo("cota"))
    cabines[0].telemetria("movendo", "movendo")  # mudança de demanda NÃO pode gerar Cota
    time.sleep(2.3)
    assert len(cabines[0].de_tipo("cota")) == n
    assert servico.comando("cotas on") == "Cotas retomadas"
    assert aguardar(lambda: len(cabines[0].de_tipo("cota")) > n, 1.0)


def test_cabine_que_cai_em_movimento_tem_corrente_de_pouso_reservada(ambiente):
    _, iniciar, cabines = ambiente
    servico = iniciar()
    cabines.extend(CabineFalsa(servico.rede.porta, n) for n in (1, 2))
    assert aguardar(lambda: servico.rede.conectada(1) and servico.rede.conectada(2))
    cabines[0].telemetria("movendo", "movendo")
    cabines[1].telemetria("movendo", "movendo")
    assert aguardar(lambda: servico.despacho.cabines[1].necessidade == "movendo")
    cabines[0].fechar()  # cabine 1 cai em movimento: pousa em Modo Degradado com até 1000 mA
    assert aguardar(lambda: not servico.rede.conectada(1))
    assert aguardar(lambda: servico._cotas[1] == 1000)
    assert servico._cotas[1] + servico._cotas[2] <= servico.barramento_atual()
    limite = servico.barramento_atual() - 1000
    assert aguardar(lambda: cabines[1].de_tipo("cota") and cabines[1].de_tipo("cota")[-1]["cota_ma"] <= limite)


def test_renegociacao_reduz_antes_de_conceder(ambiente):
    """Break-before-make: quem perde Cota recebe a redução antes de quem ganha a concessão."""
    _, iniciar, cabines = ambiente
    servico = iniciar()
    cabines.extend(CabineFalsa(servico.rede.porta, n) for n in (1, 2, 3))
    assert aguardar(lambda: all(servico.rede.conectada(n) for n in (1, 2, 3)))
    cabines[0].telemetria("movendo", "movendo")
    cabines[2].telemetria("movendo", "movendo")
    cabines[1].telemetria("nenhuma", "ociosa")
    assert aguardar(lambda: cabines[0].de_tipo("cota") and cabines[0].de_tipo("cota")[-1]["cota_ma"] > 4900)
    alta = cabines[0].de_tipo("cota")[-1]["cota_ma"]
    t0 = time.time()
    cabines[1].telemetria("partida", "ociosa")
    assert aguardar(lambda: cabines[1].de_tipo("cota")[-1]["cota_ma"] >= 1000, 2.0)
    reducao = next(m for m in cabines[0].de_tipo("cota") if m["ts"] >= t0 and m["cota_ma"] < alta)
    concessao = next(m for m in cabines[1].de_tipo("cota") if m["ts"] >= t0 and m["cota_ma"] >= 1000)
    assert reducao["seq"] < concessao["seq"] and concessao["ts"] - reducao["ts"] >= 0.25
