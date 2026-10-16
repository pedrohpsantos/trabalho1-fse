"""Runtime do Servidor Distribuído: máquina de estados da cabine, acionada por `tick()`.

Toda a lógica roda numa única thread (laço de 50 ms). Interrupções de GPIO e mensagens da
rede apenas enfileiram entradas; o tempo e o enlace são injetados, o que permite executar
exatamente o mesmo código em tempo virtual nos testes e nos experimentos de PID.
"""

import logging
import queue
import threading
from collections import deque
from collections.abc import Callable
from enum import Enum
from typing import Protocol

from ..constantes import (
    NUM_ANDARES,
    PERIODO_PID_S,
    PERIODO_TELEMETRIA_S,
    POCO_MM,
    SOBRECURSO_MM,
    TOLERANCIA_NIVELAMENTO_MM,
    Direcao,
    PortaComando,
    PortaEstado,
)
from ..dominio.agenda import ID_MANUAL_BASE, Agenda, Sentido, Tarefa
from ..util.relogio import relogio_real
from .calibracao import Calibrador, FaseCalibracao
from .config import ConfigCabine
from .cota import GerenciadorCota
from .hal.base import GpioCabine
from .movimento import ControladorPosicao
from .pid import Pid

log = logging.getLogger(__name__)

MODBUS_VALIDADE_S = 1.5  # idade máxima do espelho MODBUS para decisões de porta
MARGEM_FREIO_EM_VOO_MM = 700.0
TOLERANCIA_REANCORAGEM_MM = 150.0
REENVIO_PORTA_S = 2.0
REENVIO_PORTA_DEGRADADO_S = 0.5  # o firmware fecha a porta sozinho 3 s após abrir (medido)
FOLGA_CORTINA_S = 0.5
RETENTATIVA_CALIBRACAO_S = 3.0
PORTA_SEM_RESPOSTA_S = 6.0  # ~3 pedidos de abertura ignorados => recentraliza no andar
ACEITE_RECENTRALIZACAO_MM = 2.0


class EstadoCabine(Enum):
    AGUARDANDO_CENTRAL = "aguardando_central"
    CALIBRANDO = "calibrando"
    OCIOSA = "ociosa"
    MOVENDO = "movendo"
    ABRINDO_PORTA = "abrindo_porta"
    EMBARQUE = "embarque"
    FECHANDO_PORTA = "fechando_porta"
    FALHA = "falha"


class Enlace(Protocol):
    """O que o runtime precisa da rede (implementado por `RedeCentral` e por dublês de teste)."""

    @property
    def conectado(self) -> bool: ...

    def enviar(self, tipo: str, /, **campos) -> bool: ...


class CabineRuntime:
    def __init__(
        self,
        cfg: ConfigCabine,
        hal: GpioCabine,
        enlace: Enlace,
        relogio: Callable[[], float] = relogio_real,
    ) -> None:
        self.cfg = cfg
        self.hal = hal
        self.enlace = enlace
        self.relogio = relogio
        self.numero = cfg.cabine
        self.cota = GerenciadorCota()
        self.pid = Pid(cfg.pid.kp, cfg.pid.ki, cfg.pid.kd, cfg.pid.limite_integral_pct)
        self.ctrl = ControladorPosicao(self.pid, cfg.pid.rampa_pct_por_s)
        self.agenda = Agenda()
        self.estado = EstadoCabine.AGUARDANDO_CENTRAL
        self.acordar = threading.Event()  # ISRs acordam o laço para reagir sem esperar o tick
        self._entradas: queue.SimpleQueue = queue.SimpleQueue()
        self._saida: deque[tuple[str, dict]] = deque(maxlen=512)
        self._modbus: dict | None = None
        self._calibrador: Calibrador | None = None
        self.centros: list[float] | None = None
        self.larguras: list[float] | None = None
        self.alvo_andar: int | None = None
        self.sentido_pref = Sentido.PARADO
        self.degradado = True
        self._degradado_alvo_definido = False
        self._entrada_bandeirola: tuple[int, float] | None = None
        self._t_anterior: float | None = None
        self._t_telemetria = 0.0
        self._t_cmd_porta = 0.0
        self._t_aberta = 0.0
        self._t_cortina_livre = 0.0
        self._reabertura = False
        self._t_pedido_abertura = 0.0
        self._t_falha_calibracao = 0.0
        self._manual_seq = 0
        self._cortina = False
        self.motivo_falha = ""
        self.reancoragens = 0
        self.ultima_correcao_mm = 0
        self.max_erro_nivelamento_mm = 0.0
        self.aberturas = 0
        self.recentralizacoes = 0
        self.duty_calibracao = 0.0
        self._porta_recusada = False

    # ------------------------------------------------------------------ entradas assíncronas
    def ao_sensor(self, entrou: bool, pos: int) -> None:
        """Interrupção do Sensor de Andar (as duas bordas)."""
        self._entradas.put(("sensor", entrou, pos, self.relogio()))
        self.acordar.set()

    def ao_cortina(self, obstruida: bool) -> None:
        self._entradas.put(("cortina", obstruida, self.relogio()))
        self.acordar.set()

    def receber(self, msg: dict) -> None:
        """Mensagem vinda do Central (thread de rede)."""
        self._entradas.put(("msg", msg, self.relogio()))
        self.acordar.set()

    def conexao_perdida(self) -> None:
        self._entradas.put(("desconexao", self.relogio()))
        self.acordar.set()

    # ------------------------------------------------------------------ consultas
    @property
    def calibrada(self) -> bool:
        return self.centros is not None

    def posicao_andar(self, andar: int) -> float:
        return self.centros[andar]  # type: ignore[index]

    def andar_proximo(self, pos: float) -> int:
        return min(range(NUM_ANDARES), key=lambda i: abs(pos - self.centros[i]))  # type: ignore[index]

    def nivelada_em(self, pos: float, andar: int) -> bool:
        return abs(pos - self.posicao_andar(andar)) <= TOLERANCIA_NIVELAMENTO_MM

    def _modbus_fresco(self, agora: float, desde: float = 0.0) -> dict | None:
        m = self._modbus
        if m and agora - m["ts"] <= MODBUS_VALIDADE_S and m["ts"] >= desde:
            return m
        return None

    def mensagem_hello(self) -> dict:
        msg: dict = {
            "cabine": self.numero,
            "token": self.cfg.token,
            "calibrada": self.calibrada,
            "agenda": self.agenda.serializar(),
        }
        if self.centros:
            msg["andares_mm"] = [round(c, 1) for c in self.centros]
        return msg

    # ------------------------------------------------------------------ laço principal
    def atender_interrupcoes(self) -> None:
        """Reage de imediato a bordas/mensagens (ex.: cortina) sem avançar a malha de controle."""
        self._processar_entradas()

    def tick(self) -> None:
        agora = self.relogio()
        dt = PERIODO_PID_S if self._t_anterior is None else min(max(agora - self._t_anterior, 0.005), 0.25)
        self._t_anterior = agora
        self._processar_entradas()
        self.degradado = self.cota.degradado(agora)
        pos = self.hal.posicao()
        try:
            self._executar_estado(agora, dt, pos)
        except Exception:  # noqa: BLE001 - qualquer erro inesperado leva a cabine ao estado seguro
            log.exception("erro inesperado no controle; freando")
            self._falhar("erro interno", agora)
        self._enviar_telemetria(agora, pos)
        self._drenar_saida()

    def _executar_estado(self, agora: float, dt: float, pos: int) -> None:
        e = self.estado
        if e == EstadoCabine.AGUARDANDO_CENTRAL:
            self.hal.definir_motor(Direcao.FREIO, 0.0)
            if self.enlace.conectado and self._modbus_fresco(agora):
                self._iniciar_calibracao(agora)
        elif e == EstadoCabine.CALIBRANDO:
            self._passo_calibracao(agora, pos)
        elif e == EstadoCabine.OCIOSA:
            self._passo_ociosa(agora, pos)
        elif e == EstadoCabine.MOVENDO:
            self._passo_movendo(agora, dt, pos)
        elif e == EstadoCabine.ABRINDO_PORTA:
            self._passo_abrindo(agora, pos)
        elif e == EstadoCabine.EMBARQUE:
            self._passo_embarque(agora, pos)
        elif e == EstadoCabine.FECHANDO_PORTA:
            self._passo_fechando(agora)
        else:
            self.hal.definir_motor(Direcao.FREIO, 0.0)

    # ------------------------------------------------------------------ entradas
    def _processar_entradas(self) -> None:
        while True:
            try:
                item = self._entradas.get_nowait()
            except queue.Empty:
                break
            tipo = item[0]
            if tipo == "sensor":
                self._tratar_sensor(item[1], item[2], item[3])
            elif tipo == "cortina":
                self._tratar_cortina(item[1], item[2])
            elif tipo == "desconexao":
                self.cota.invalidar()
                self._modbus = None
            else:
                self._tratar_mensagem(item[1], item[2])
        self.acordar.clear()

    def _tratar_sensor(self, entrou: bool, pos: int, ts: float) -> None:
        if self.estado == EstadoCabine.CALIBRANDO and self._calibrador:
            self._calibrador.ao_borda(entrou, pos, ts)
            self.enlace.enviar("pedir_modbus", cabine=self.numero)
            return
        if not self.calibrada:
            return
        if entrou:
            pos += self._reancorar_borda(pos)
            self._entrada_bandeirola = (pos, ts)
            return
        if self._entrada_bandeirola is None:
            return
        entrada, _ = self._entrada_bandeirola
        self._entrada_bandeirola = None
        self._reancorar(entrada, pos)

    def _reancorar_borda(self, pos: int) -> int:
        """Corrige a deriva já na borda de entrada: ela deve estar a meia largura do centro."""
        andar = self.andar_proximo(pos)
        meia = self.larguras[andar] / 2.0  # type: ignore[index]
        bordas = (self.centros[andar] - meia, self.centros[andar] + meia)  # type: ignore[index]
        proxima = min(bordas, key=lambda b: abs(pos - b))
        if self.larguras[andar] < 100 or abs(pos - proxima) > 40:  # type: ignore[index]
            return 0
        delta = int(round(proxima - pos))
        if delta:
            self.hal.deslocar_posicao(delta)
            self.ctrl.reancorar(delta)
            self.reancoragens += 1
            self.ultima_correcao_mm = delta
        return delta

    def _reancorar(self, entrada: int, saida: int) -> None:
        """Reancora a contagem no centro calibrado a cada passagem completa por uma bandeirola."""
        centro = (entrada + saida) / 2.0
        largura = abs(saida - entrada)
        andar = self.andar_proximo(centro)
        calibrada = self.larguras[andar]  # type: ignore[index]
        if abs(centro - self.centros[andar]) > TOLERANCIA_REANCORAGEM_MM:  # type: ignore[index]
            return
        if not 0.6 * calibrada <= largura <= 1.5 * calibrada:
            return  # passagem incompleta (inversão dentro da bandeirola)
        delta = int(round(self.centros[andar] - centro))  # type: ignore[index]
        if delta:
            self.hal.deslocar_posicao(delta)
            self.ctrl.reancorar(delta)
        self.reancoragens += 1
        self.ultima_correcao_mm = delta
        log.debug("reancoragem andar %d: %+d mm", andar, delta)

    def _tratar_cortina(self, obstruida: bool, ts: float) -> None:
        self._cortina = obstruida
        if obstruida:
            if self.estado == EstadoCabine.FECHANDO_PORTA:
                self._reabrir_por_obstrucao(ts)
        else:
            self._t_cortina_livre = ts

    def _tratar_mensagem(self, msg: dict, agora: float) -> None:
        tipo = msg["t"]
        if tipo == "cota":
            self.cota.receber(msg["cota_ma"], agora, msg["validade_s"])
        elif tipo == "modbus":
            self._modbus = dict(msg)
            if self._calibrador:
                self._calibrador.ao_modbus(msg["andar_atual"], msg["ts"])
        elif tipo == "tarefa":
            self.agenda.adicionar(
                Tarefa(
                    msg["id"],
                    msg["origem"],
                    msg["destino"],
                    msg["t_registro"],
                    bool(msg.get("suave")),
                    a_bordo=msg["id"] >= ID_MANUAL_BASE,  # paradas avulsas não têm passageiro a buscar
                    t_chegada=agora,
                )
            )
        elif tipo == "ir_andar":
            self._manual_seq += 1
            self.agenda.adicionar(
                Tarefa(ID_MANUAL_BASE + self._manual_seq, msg["andar"], msg["andar"], agora, a_bordo=True)
            )
        elif tipo == "porta_recusada":
            self._porta_recusada = True
        elif tipo == "recalibrar":
            self.hal.definir_motor(Direcao.FREIO, 0.0)
            self.centros = self.larguras = None
            self._calibrador = None
            self.ctrl.cancelar()
            self.estado = EstadoCabine.AGUARDANDO_CENTRAL

    # ------------------------------------------------------------------ calibração
    def _iniciar_calibracao(self, agora: float) -> None:
        log.info("iniciando calibração")
        self.centros = self.larguras = None
        self._calibrador = Calibrador(self.cfg.params.duty_calibracao, self.cfg.params.margem_extremo_mm)
        if self._modbus is not None:
            self._calibrador.ao_modbus(self._modbus["andar_atual"], self._modbus["ts"])
        if self.hal.sensor_andar_ativo():
            ts_ref = self._modbus["ts"] if self._modbus is not None else agora
            self._calibrador.ao_borda(True, self.hal.posicao(), ts_ref)
        self.estado = EstadoCabine.CALIBRANDO

    def _passo_calibracao(self, agora: float, pos: int) -> None:
        cal = self._calibrador
        if cal is None:
            self.estado = EstadoCabine.AGUARDANDO_CENTRAL
            return
        if cal.fase == FaseCalibracao.FALHA:
            self.hal.definir_motor(Direcao.FREIO, 0.0)
            if self._t_falha_calibracao == 0.0:
                self._t_falha_calibracao = agora
            elif agora - self._t_falha_calibracao > RETENTATIVA_CALIBRACAO_S:
                self._t_falha_calibracao = 0.0
                self._iniciar_calibracao(agora)
            return
        comando = cal.passo(pos, self.cota.duty_max(agora), agora)
        self.hal.definir_motor(comando.direcao, comando.duty)
        self.duty_calibracao = comando.duty
        if cal.fase == FaseCalibracao.CONCLUIDA and cal.resultado:
            self._concluir_calibracao(cal, pos, agora)

    def _concluir_calibracao(self, cal: Calibrador, pos: int, agora: float) -> None:
        c0 = cal.resultado.centros_mm[0]  # type: ignore[union-attr]
        deslocamento = int(round(-c0))
        self.hal.deslocar_posicao(deslocamento)  # andar 0 passa a ser a origem (poço negativo)
        self.centros = [c + deslocamento for c in cal.resultado.centros_mm]  # type: ignore[union-attr]
        self.larguras = list(cal.resultado.larguras_mm)  # type: ignore[union-attr]
        self._calibrador = None
        log.info("calibração concluída: %s", [round(c) for c in self.centros])
        self.ctrl.pid.reiniciar()
        self._partir_para(0, self.hal.posicao())

    # ------------------------------------------------------------------ estados de operação
    def _limite_seguro(self, pos: float) -> bool:
        return self.centros[0] - POCO_MM + 15 <= pos <= self.centros[-1] + SOBRECURSO_MM - 15  # type: ignore[index]

    def _partir_para(self, andar: int, pos: float, aceite_mm: float | None = None) -> None:
        self.alvo_andar = andar
        self.ctrl.ir_para(self.posicao_andar(andar), aceite_mm)
        self.sentido_pref = Sentido.SUBINDO if self.posicao_andar(andar) > pos else Sentido.DESCENDO
        self.estado = EstadoCabine.MOVENDO

    def _passo_ociosa(self, agora: float, pos: int) -> None:
        self.hal.definir_motor(Direcao.FREIO, 0.0)
        andar = self.andar_proximo(pos)
        nivelada = self.nivelada_em(pos, andar)
        modbus = self._modbus_fresco(agora)
        if self.degradado:
            if nivelada and modbus and modbus["porta_estado"] != PortaEstado.ABERTA:
                self._abrir_porta(agora, andar)
            return
        parada = self.agenda.proxima_parada(andar, self.sentido_pref, agora)
        if parada is None:
            return
        if parada == andar and nivelada:
            self._abrir_porta(agora, andar)
            return
        if modbus is None:
            return
        if modbus["porta_estado"] != PortaEstado.FECHADA:
            if agora - self._t_cmd_porta >= REENVIO_PORTA_S:
                self._comandar_porta(PortaComando.FECHAR, agora)  # nunca parte com a porta aberta
            return
        if self.cota.duty_max(agora) >= ControladorPosicao.DUTY_ARRANQUE:
            self._partir_para(parada, pos)

    def _passo_movendo(self, agora: float, dt: float, pos: int) -> None:
        if not self._limite_seguro(pos):
            self._falhar("limite de curso", agora)
            return
        duty_max = self.cota.duty_max(agora)
        if self.degradado:
            self._alvo_degradado(pos)
        else:
            self._degradado_alvo_definido = False
            self._retargetar_em_voo(pos)
        comando = self.ctrl.passo(pos, duty_max, dt)
        self.hal.definir_motor(comando.direcao, comando.duty)
        if self.ctrl.chegou:
            self._chegada(agora, pos)

    def _alvo_degradado(self, pos: int) -> None:
        """Modo Degradado: segue para o próximo andar no sentido atual de movimento."""
        if self._degradado_alvo_definido:
            return
        self._degradado_alvo_definido = True
        sobe = self.ctrl.alvo > pos
        a_frente = [
            i
            for i in range(NUM_ANDARES)
            if (self.centros[i] > pos + 30 if sobe else self.centros[i] < pos - 30)  # type: ignore[index]
        ]
        if not a_frente:
            return
        andar = min(a_frente, key=lambda i: abs(self.centros[i] - pos))  # type: ignore[index]
        self.alvo_andar = andar
        self.ctrl.retargetar(self.centros[andar])  # type: ignore[index]
        log.warning("modo degradado: pouso no andar %d", andar)

    def _retargetar_em_voo(self, pos: int) -> None:
        if self.alvo_andar is None:
            return
        sobe = self.ctrl.alvo > pos
        candidatos = [
            f
            for f in self.agenda.paradas()
            if (
                self.centros[f] > pos + MARGEM_FREIO_EM_VOO_MM
                if sobe
                else self.centros[f] < pos - MARGEM_FREIO_EM_VOO_MM
            )  # type: ignore[index]
            and (self.centros[f] < self.ctrl.alvo if sobe else self.centros[f] > self.ctrl.alvo)  # type: ignore[index]
        ]
        if candidatos:
            andar = min(candidatos, key=lambda f: abs(self.centros[f] - pos))  # type: ignore[index]
            self.alvo_andar = andar
            self.ctrl.retargetar(self.centros[andar])  # type: ignore[index]

    def _chegada(self, agora: float, pos: int) -> None:
        andar = self.alvo_andar if self.alvo_andar is not None else self.andar_proximo(pos)
        erro = abs(pos - self.posicao_andar(andar))
        self.max_erro_nivelamento_mm = max(self.max_erro_nivelamento_mm, erro)
        self.hal.definir_motor(Direcao.FREIO, 0.0)
        if erro > TOLERANCIA_NIVELAMENTO_MM:  # nunca abre fora da faixa: nova aproximação
            self._partir_para(andar, pos)
            return
        self.ctrl.cancelar()
        if self.degradado or andar in self.agenda.paradas():
            self._abrir_porta(agora, andar)
        else:
            self.estado = EstadoCabine.OCIOSA

    # ------------------------------------------------------------------ porta
    def _comandar_porta(self, comando: PortaComando, agora: float) -> None:
        if self.enlace.enviar("porta_cmd", cabine=self.numero, cmd=int(comando)):
            self._t_cmd_porta = agora

    def _abrir_porta(self, agora: float, andar: int) -> None:
        self._porta_recusada = False
        self.alvo_andar = andar
        self._t_cmd_porta = float("-inf")
        self._t_pedido_abertura = agora
        self.estado = EstadoCabine.ABRINDO_PORTA

    def _passo_abrindo(self, agora: float, pos: int) -> None:
        self.hal.definir_motor(Direcao.FREIO, 0.0)
        andar = self.alvo_andar if self.alvo_andar is not None else self.andar_proximo(pos)
        if not self.nivelada_em(pos, andar):  # intertravamento: porta só abre nivelada (±10 mm)
            self._partir_para(andar, pos)
            return
        modbus = self._modbus_fresco(agora)
        if modbus and modbus["porta_estado"] == PortaEstado.ABERTA and agora - self._t_cmd_porta >= 0.0:
            self._porta_aberta(agora, andar)
            return
        if self._porta_ignorada(agora, modbus, pos, andar):
            return
        if agora - self._t_cmd_porta >= REENVIO_PORTA_S:
            self._comandar_porta(PortaComando.ABRIR, agora)

    def _porta_ignorada(self, agora: float, modbus: dict | None, pos: int, andar: int) -> bool:
        """O simulador não abriu apesar de vários pedidos: o andar real está na borda da faixa.

        Recentraliza com margem estrita (±2 mm pelo encoder) e pede de novo.
        """
        recusada, self._porta_recusada = self._porta_recusada, False
        if not recusada:
            if modbus is None or modbus["porta_estado"] != PortaEstado.FECHADA:
                return False
            if agora - self._t_pedido_abertura < PORTA_SEM_RESPOSTA_S or modbus["ts"] < self._t_pedido_abertura:
                return False
        if abs(pos - self.posicao_andar(andar)) <= ACEITE_RECENTRALIZACAO_MM:
            return False  # já centrada pelo encoder: só resta continuar pedindo
        self.recentralizacoes += 1
        log.warning(
            "porta não abriu no andar %d (erro %+d mm); recentralizando", andar, pos - self.posicao_andar(andar)
        )
        self._partir_para(andar, pos, aceite_mm=ACEITE_RECENTRALIZACAO_MM)
        return True

    def _porta_aberta(self, agora: float, andar: int) -> None:
        self.aberturas += 1
        if self._reabertura:
            self._reabertura = False
            self._t_aberta = agora - (self.cfg.params.tempo_porta_aberta_s - FOLGA_CORTINA_S)
        else:
            self._t_aberta = agora
            self._registrar_eventos(agora, andar, self._t_pedido_abertura)
        self.estado = EstadoCabine.EMBARQUE

    def _registrar_eventos(self, agora: float, andar: int, chegadas_ate: float | None = None) -> None:
        for ev in self.agenda.atender(andar, agora, chegadas_ate):
            self._saida.append(
                (
                    "evento",
                    {"cabine": self.numero, "tarefa": ev.tarefa.id, "tipo": ev.tipo, "andar": andar, "ts": agora},
                )
            )

    def _passo_embarque(self, agora: float, pos: int) -> None:
        self.hal.definir_motor(Direcao.FREIO, 0.0)
        if self.degradado:
            self._manter_porta_aberta(agora)
            return
        if self._cortina or agora - self._t_cortina_livre < FOLGA_CORTINA_S:
            return
        if agora - self._t_aberta >= self.cfg.params.tempo_porta_aberta_s:
            self._t_cmd_porta = float("-inf")
            self.estado = EstadoCabine.FECHANDO_PORTA

    def _manter_porta_aberta(self, agora: float) -> None:
        """Modo Degradado (Seção 7.3): parada de porta aberta até voltar uma Cota válida.

        O firmware fecha a porta sozinho 3 s depois de aberta; ao ver `fechando`/`fechada` no
        espelho MODBUS a cabine re-comanda ABRIR (o Central só repassa se estiver nivelada).
        """
        modbus = self._modbus_fresco(agora)
        if modbus is None or modbus["porta_estado"] not in (PortaEstado.FECHANDO, PortaEstado.FECHADA):
            return
        if agora - self._t_cmd_porta >= REENVIO_PORTA_DEGRADADO_S:
            self._comandar_porta(PortaComando.ABRIR, agora)

    def _passo_fechando(self, agora: float) -> None:
        self.hal.definir_motor(Direcao.FREIO, 0.0)
        if self.degradado:
            self._abrir_porta(agora, self.alvo_andar if self.alvo_andar is not None else 0)
            return
        modbus = self._modbus_fresco(agora, desde=self._t_cmd_porta)
        if self._cortina or (modbus and modbus["porta_estado"] == PortaEstado.OBSTRUIDA):
            self._reabrir_por_obstrucao(agora)
            return
        if modbus and modbus["porta_estado"] == PortaEstado.FECHADA and self._t_cmd_porta > float("-inf"):
            self.estado = EstadoCabine.OCIOSA  # só parte após confirmar porta_estado == 0
            return
        if agora - self._t_cmd_porta >= REENVIO_PORTA_S:
            self._comandar_porta(PortaComando.FECHAR, agora)

    def _reabrir_por_obstrucao(self, agora: float) -> None:
        """Cortina obstruída: inibe o fechamento (manda abrir) e retoma quando liberar."""
        self._reabertura = True
        self._abrir_porta(agora, self.alvo_andar if self.alvo_andar is not None else 0)
        self._comandar_porta(PortaComando.ABRIR, agora)

    # ------------------------------------------------------------------ segurança e telemetria
    def _falhar(self, motivo: str, agora: float) -> None:
        self.hal.definir_motor(Direcao.FREIO, 0.0)
        self.ctrl.cancelar()
        self.estado = EstadoCabine.FALHA
        self.motivo_falha = motivo
        log.error("FALHA: %s", motivo)

    def parada_segura(self) -> None:
        self.hal.definir_motor(Direcao.FREIO, 0.0)

    def necessidade(self) -> str:
        if self.estado in (EstadoCabine.CALIBRANDO, EstadoCabine.MOVENDO):
            return "movendo"
        if self.estado in (EstadoCabine.FALHA, EstadoCabine.AGUARDANDO_CENTRAL):
            return "nenhuma"
        # Só pede Cota de partida quem está pronta para sair: ciclos de porta não seguram
        # a admissão (na rasp32 duas cabines em ciclo de porta deixaram a terceira sem Cota).
        pronta = self.estado == EstadoCabine.OCIOSA and len(self.agenda) and not self.degradado
        return "partida" if pronta else "nenhuma"

    def sentido(self) -> str:
        if self.estado == EstadoCabine.MOVENDO:
            return self.sentido_pref.value
        return Sentido.PARADO.value

    def _enviar_telemetria(self, agora: float, pos: int) -> None:
        if agora - self._t_telemetria < PERIODO_TELEMETRIA_S:
            return
        self._t_telemetria = agora
        modbus = self._modbus or {}
        andar = self.andar_proximo(pos) if self.calibrada else int(modbus.get("andar_atual", 0))
        self._saida.append(
            (
                "telemetria",
                {
                    "cabine": self.numero,
                    "ts": agora,
                    "pos_mm": float(pos),
                    "andar": andar,
                    "sentido": self.sentido(),
                    "porta": int(modbus.get("porta_estado", 0)),
                    "corrente_ma": int(modbus.get("corrente_ma", 0)),
                    "estado": self.estado.value,
                    "degradado": self.degradado,
                    "cota_ma": self.cota.cota_ma(agora),
                    "necessidade": self.necessidade(),
                    "calibrada": self.calibrada,
                    "pendentes": len(self.agenda),
                    "alvo": -1 if self.alvo_andar is None or self.estado != EstadoCabine.MOVENDO else self.alvo_andar,
                    "nivelado": bool(self.calibrada and self.nivelada_em(pos, andar)),
                    "falha": self.motivo_falha,
                    "duty": round(self.ctrl.duty_saida, 1),
                },
            )
        )

    def _drenar_saida(self) -> None:
        """Envia a fila de saída em ordem; mantém o que não pôde ser enviado (enlace caído)."""
        while self._saida:
            tipo, campos = self._saida[0]
            if tipo == "telemetria" and len(self._saida) > 1:
                self._saida.popleft()  # telemetria velha não vale reenviar
                continue
            if not self.enlace.enviar(tipo, **campos):
                if tipo == "telemetria":
                    self._saida.popleft()
                return
            self._saida.popleft()
