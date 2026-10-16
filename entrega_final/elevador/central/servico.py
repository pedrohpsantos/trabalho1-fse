"""Orquestração do Servidor Central: MODBUS, TCP, potência, despacho, ambiente e persistência."""

import logging
import threading
from collections.abc import Callable

from ..constantes import (
    COTA_VALIDADE_S,
    NUM_ANDARES,
    PISO_BARRAMENTO_MA,
    PortaComando,
)
from ..dominio.agenda import Tarefa
from ..modbus.cliente import ErroModbus
from ..modbus.gerente import PRIO_AMBIENTE, PRIO_CABINES, PRIO_CONTROLE, PRIO_URGENTE, GerenteModbus
from ..modbus.simulador import EstadoPredio
from ..util.relogio import relogio_real
from .bmp280 import Leitura, SensorAmbiente
from .config import ConfigCentral
from .despacho import Despachante
from .painel import VisaoPainel
from .persistencia import Persistencia
from .potencia import repartir
from .rede import ServidorCabines

log = logging.getLogger(__name__)

MAX_CHAMADAS_POR_CICLO = 8
IDADE_MAX_LEITURA_AMBIENTE_S = 20.0  # abaixo dos 30 s do watchdog do simulador
IDADE_MAX_BARRAMENTO_S = 5.0
INTERVALO_MIN_COTA_S = 0.1
ESPERA_REDUCAO_COTA_S = 0.3  # reduções aplicadas antes das concessões (break-before-make)
RESERVA_POUSO_S = 60.0  # tempo máximo de um pouso degradado (16,7 % de duty, até 1 andar)


class ServicoCentral:
    def __init__(
        self,
        cfg: ConfigCentral,
        gerente: GerenteModbus,
        sensor: SensorAmbiente,
        persistencia: Persistencia,
        relogio: Callable[[], float] = relogio_real,
    ) -> None:
        self.cfg = cfg
        self.gerente = gerente
        self.sensor = sensor
        self.persistencia = persistencia
        self.relogio = relogio
        self.despacho = Despachante(cfg.tempos)
        self.rede = ServidorCabines(
            cfg.escuta_host, cfg.escuta_porta, cfg.token, self._ao_hello, self._ao_mensagem, self._ao_desconexao
        )
        self.parar = threading.Event()
        self._trava = threading.RLock()
        self._trava_cotas = threading.Lock()  # uma renegociação de cada vez (laço de cotas x laço de chamadas)
        self._threads: list[threading.Thread] = []
        self._ev_cota = threading.Event()
        self._ev_poll = threading.Event()
        self.predio: EstadoPredio | None = None
        self._t_predio = float("-inf")
        self.leitura_ambiente: Leitura | None = None
        self._t_leitura_ambiente = float("-inf")
        self.erros_modbus = 0
        self.portas_recusadas = 0
        self._seq_cota = 0
        self._cotas: dict[int, int] = {1: 0, 2: 0, 3: 0}
        self._necessidades: dict[int, str] = {1: "nenhuma", 2: "nenhuma", 3: "nenhuma"}
        self._ultimo_cenario = 0
        self.cotas_suspensas = False  # teste do Modo Degradado (comando `cotas off`)
        self._auditoria: dict[int, tuple[int, int, int]] = {}  # cabine -> (porta, falha, passageiros) registrados

    # ------------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        estado = self.persistencia.carregar_estado()
        if estado:
            self.despacho.restaurar(estado)
            log.info("estado restaurado: %d chamadas em andamento", len(self.despacho.chamadas))
        self.gerente.iniciar()
        self.rede.iniciar()
        for nome, alvo in (
            ("chamadas", self._laco_chamadas),
            ("cotas", self._laco_cotas),
            ("poll-cabines", self._laco_cabines),
            ("ambiente", self._laco_ambiente),
            ("estado", self._laco_estado),
        ):
            t = threading.Thread(target=alvo, name=nome, daemon=True)
            t.start()
            self._threads.append(t)

    def encerrar(self) -> None:
        self.parar.set()
        self._ev_cota.set()
        self._ev_poll.set()
        self.rede.parar()
        for t in self._threads:
            t.join(timeout=2.0)
        self._salvar()
        self.gerente.parar()
        self.sensor.fechar()
        log.info("central encerrado; estado salvo em %s", self.persistencia.arquivo_estado)

    def _salvar(self) -> None:
        with self._trava:
            dados = self.despacho.snapshot()
        try:
            self.persistencia.salvar_estado(dados)
        except OSError:
            log.exception("falha ao persistir o estado")

    # ------------------------------------------------------------------ eventos da rede
    def _ao_hello(self, cabine: int, msg: dict) -> None:
        with self._trava:
            est = self.despacho.cabines[cabine]
            est.conectada = True
            est.calibrada = bool(msg["calibrada"])
            if msg.get("andares_mm"):
                est.andares_mm = list(msg["andares_mm"])
            reenviar = self.despacho.reconciliar(cabine, msg.get("agenda", []))
        for tarefa in reenviar:
            self._enviar_tarefa(cabine, tarefa)
        log.info("cabine %d conectada (calibrada=%s, reenviadas=%d)", cabine, est.calibrada, len(reenviar))
        self._ev_cota.set()
        self._ev_poll.set()

    def _ao_desconexao(self, cabine: int) -> None:
        with self._trava:
            est = self.despacho.cabines[cabine]
            if est.necessidade == "movendo":
                est.reserva_ate = self.relogio() + RESERVA_POUSO_S
            est.conectada = False
            est.estado = "desconectada"
            est.necessidade = "nenhuma"
        self._ev_cota.set()

    def _ao_mensagem(self, cabine: int, msg: dict) -> None:
        tipo = msg["t"]
        if tipo == "telemetria":
            self._telemetria(cabine, msg)
        elif tipo == "evento":
            with self._trava:
                concluida = self.despacho.evento(cabine, msg["tarefa"], msg["tipo"], msg["ts"])
            if concluida:
                self.persistencia.registrar_viagem(concluida)
                log.info("viagem %d concluída na cabine %d", concluida.id, cabine)
            if msg["tipo"] in ("embarque", "desembarque"):
                self._salvar()
        elif tipo == "porta_cmd":
            if msg["cmd"] == PortaComando.ABRIR:
                self._abrir_porta_intertravada(cabine)
            else:
                self.gerente.submeter(
                    lambda api: api.comandar_porta(cabine, PortaComando(msg["cmd"])), PRIO_URGENTE
                ).add_done_callback(lambda f: self._falha_modbus(f, f"porta cabine {cabine}"))
        elif tipo == "pedir_modbus":
            self._ev_poll.set()
            self._ler_e_empurrar(cabine, PRIO_URGENTE)

    def _abrir_porta_intertravada(self, cabine: int) -> None:
        """Defesa em profundidade: a cabine decide pelo encoder, mas o Central (dono da UART) só
        repassa ABRIR se o simulador confirma a cabine nivelada. Nunca entra na calibração nem
        na malha; serve para que nenhuma porta seja comandada fora do nível (Seção 7.5)."""

        def concluir(futuro) -> None:
            if futuro.exception():
                self._falha_modbus(futuro, f"porta cabine {cabine}")
                return
            recusada = futuro.result()
            if recusada is not None:
                self.portas_recusadas += 1
                log.warning(
                    "intertravamento: ABRIR da cabine %d recusado (fora de nível; andar_atual=%d)",
                    cabine,
                    recusada.andar_atual,
                )
                self.rede.enviar(cabine, "porta_recusada", motivo="fora_de_nivel")

        self.gerente.submeter(lambda api: api.abrir_porta_se_nivelada(cabine), PRIO_URGENTE).add_done_callback(concluir)

    def _telemetria(self, cabine: int, msg: dict) -> None:
        with self._trava:
            est = self.despacho.cabines[cabine]
            mudou = est.necessidade != msg["necessidade"] or est.degradado != msg["degradado"]
            est.conectada = True
            est.calibrada = msg["calibrada"]
            est.andar, est.sentido, est.estado = msg["andar"], msg["sentido"], msg["estado"]
            if msg["necessidade"] == "partida" and est.necessidade != "partida":
                est.partida_desde = self.relogio()
            elif msg["necessidade"] != "partida":
                est.partida_desde = float("inf")
            est.degradado, est.necessidade, est.alvo = msg["degradado"], msg["necessidade"], msg["alvo"]
            est.porta, est.corrente_ma, est.cota_ma = msg["porta"], msg["corrente_ma"], msg["cota_ma"]
            est.pos_mm, est.nivelado, est.falha = msg["pos_mm"], msg["nivelado"], msg.get("falha", "")
            est.ts_telemetria = self.relogio()
            est.reserva_ate = float("-inf")
            self._necessidades[cabine] = est.necessidade
        if mudou:
            self._ev_cota.set()  # renegociação imediata: uma cabine quer partir

    def _falha_modbus(self, futuro, contexto: str) -> None:
        erro = futuro.exception()
        if erro:
            self.erros_modbus += 1
            log.warning("MODBUS (%s): %s", contexto, erro)

    # ------------------------------------------------------------------ despacho
    def _laco_chamadas(self) -> None:
        while not self.parar.wait(self.cfg.periodo_chamadas_s):
            try:
                estado = self.gerente.executar(lambda api: api.ler_predio(), PRIO_CONTROLE)
            except (ErroModbus, TimeoutError) as erro:
                self.erros_modbus += 1
                log.warning("leitura do prédio falhou: %s", erro)
                continue
            teto_anterior = self.barramento_atual()
            self.predio, self._t_predio = estado, self.relogio()
            self._ultimo_cenario = estado.cenario_ativo
            if self.barramento_atual() != teto_anterior:
                self._renovar_cotas()
            try:
                self._processar_fila(estado)
            except (ErroModbus, TimeoutError) as erro:
                self.erros_modbus += 1
                log.warning("atribuição de chamada falhou: %s", erro)

    def _processar_fila(self, estado: EstadoPredio) -> None:
        for _ in range(MAX_CHAMADAS_POR_CICLO):
            if estado.chamadas_na_fila <= 0:
                return
            id_, origem, destino = estado.chamada_id, estado.chamada_origem, estado.chamada_destino
            agora = self.relogio()
            if origem == destino or not (0 <= origem < NUM_ANDARES and 0 <= destino < NUM_ANDARES):
                log.warning("chamada %d inválida (%d->%d) descartada", id_, origem, destino)
                self.gerente.executar(lambda api: api.remover_chamada_da_fila(), PRIO_CONTROLE)
            else:
                with self._trava:
                    existente = self.despacho.chamadas.get(id_)
                    cabine = (
                        existente.cabine if existente else self.despacho.escolher_cabine(origem, destino, agora, id_)
                    )
                    if cabine is None:
                        return  # nenhuma cabine apta agora: a chamada espera na fila
                    chamada = self.despacho.atribuir(id_, origem, destino, agora, cabine)
                self._salvar()  # persiste a atribuição ANTES de retirar a chamada da fila
                self.gerente.executar(lambda api, i=id_, c=chamada.cabine: api.atribuir_chamada(i, c), PRIO_CONTROLE)
                self.gerente.executar(lambda api: api.remover_chamada_da_fila(), PRIO_CONTROLE)
                self._enviar_tarefa(chamada.cabine, Tarefa(id_, origem, destino, chamada.t_registro))
                log.info("chamada %d (%d->%d) atribuída à cabine %d", id_, origem, destino, chamada.cabine)
            estado = self.gerente.executar(lambda api: api.ler_predio(), PRIO_CONTROLE)
            self.predio = estado

    def _enviar_tarefa(self, cabine: int, tarefa: Tarefa, suave: bool = False) -> None:
        self.rede.enviar(
            cabine,
            "tarefa",
            id=tarefa.id,
            origem=tarefa.origem,
            destino=tarefa.destino,
            t_registro=tarefa.t_registro,
            suave=suave,
        )

    # ------------------------------------------------------------------ potência
    def barramento_atual(self) -> int:
        if self.predio is None or self.relogio() - self._t_predio > IDADE_MAX_BARRAMENTO_S:
            return PISO_BARRAMENTO_MA  # sem leitura recente: assume o piso conservador
        return self.predio.barramento_max_ma

    def _laco_cotas(self) -> None:
        proxima_forcada = 0.0
        while not self.parar.is_set():
            self._ev_cota.wait(timeout=1.0)  # renova a cada 1 s ou ao mudar a demanda
            if self.parar.is_set():
                return
            self._ev_cota.clear()
            self._renovar_cotas()
            proxima_forcada = INTERVALO_MIN_COTA_S
            self.parar.wait(proxima_forcada)  # limita a taxa de renegociações

    def _necessidade_para_cota(self, cab, agora: float) -> str:
        if cab.conectada:
            return cab.necessidade
        return "reserva" if agora < cab.reserva_ate else "nenhuma"

    def _renovar_cotas(self) -> None:
        """Reparte e envia as Cotas. Break-before-make: quando uma renegociação reduz a Cota de
        umas cabines para conceder a outras, as reduções saem primeiro e as concessões só depois
        de `ESPERA_REDUCAO_COTA_S`; assim a soma das correntes reais não passa do barramento no
        transitório (na rasp32 um envio simultâneo levou uma cabine a 1668 mA com Cota de 950)."""
        if self.cotas_suspensas:
            return  # sem renovação as Cotas expiram em 2 s e as cabines entram em Modo Degradado
        with self._trava_cotas:
            agora = self.relogio()
            with self._trava:
                necessidades = {n: self._necessidade_para_cota(c, agora) for n, c in self.despacho.cabines.items()}
                ordem = sorted(self.despacho.cabines, key=lambda n: (self.despacho.cabines[n].partida_desde, n))
                cotas = repartir(self.barramento_atual(), necessidades, ordem)
                anteriores = dict(self._cotas)
            reducoes = {c: v for c, v in cotas.items() if v < anteriores.get(c, 0)}
            if reducoes and any(v > anteriores.get(c, 0) for c, v in cotas.items()):
                transicao = {c: min(v, anteriores.get(c, 0)) for c, v in cotas.items()}
                self._enviar_cotas(transicao)
                self.parar.wait(ESPERA_REDUCAO_COTA_S)
            self._enviar_cotas(cotas)

    def _enviar_cotas(self, cotas: dict[int, int]) -> None:
        with self._trava:
            self._cotas = dict(cotas)
            self._seq_cota += 1
            seq = self._seq_cota
        for cabine, cota in cotas.items():
            self.rede.enviar(cabine, "cota", cota_ma=cota, ts=self.relogio(), validade_s=COTA_VALIDADE_S, seq=seq)

    # ------------------------------------------------------------------ cabines (espelho MODBUS)
    def _laco_cabines(self) -> None:
        while not self.parar.is_set():
            for cabine in (1, 2, 3):
                if self.rede.conectada(cabine):
                    self._ler_e_empurrar(cabine, PRIO_CABINES)
            self._ev_poll.wait(timeout=self.cfg.periodo_poll_cabine_s)
            self._ev_poll.clear()

    def _ler_e_empurrar(self, cabine: int, prioridade: int) -> None:
        def concluir(futuro) -> None:
            erro = futuro.exception()
            if erro:
                self.erros_modbus += 1
                log.debug("leitura da cabine %d falhou: %s", cabine, erro)
                return
            e = futuro.result()
            self._auditar_cabine(cabine, e)
            self.rede.enviar(
                cabine,
                "modbus",
                andar_atual=e.andar_atual,
                porta_estado=e.porta_estado,
                corrente_ma=e.corrente_ma,
                falha=e.falha,
                ts=self.relogio(),
            )

        self.gerente.submeter(lambda api: api.ler_cabine(cabine), prioridade).add_done_callback(concluir)

    def _auditar_cabine(self, cabine: int, e) -> None:
        """Log de auditoria: cada mudança de porta/falha com o `nivelado` e a `posicao_mm` do simulador.

        Só observabilidade (evidência de que a porta abre nivelada); esses registradores nunca
        são enviados às cabines nem entram no controle.
        """
        atual = (e.porta_estado, e.falha, e.passageiros)
        anterior = self._auditoria.get(cabine)
        if anterior == atual:
            return
        self._auditoria[cabine] = atual
        nivel = log.warning if e.falha and (anterior is None or e.falha != anterior[1]) else log.info
        nivel(
            "auditoria cabine %d: porta=%d falha=0x%02X nivelado=%d andar_atual=%d posicao_mm=%d corrente=%d mA "
            "passageiros=%d carga=%d kg",
            cabine,
            e.porta_estado,
            e.falha,
            e.nivelado,
            e.andar_atual,
            e.posicao_mm,
            e.corrente_ma,
            e.passageiros,
            e.carga_kg,
        )

    # ------------------------------------------------------------------ ambiente (I2C -> MODBUS)
    def _laco_ambiente(self) -> None:
        while True:
            self._ciclo_ambiente()
            if self.parar.wait(self.cfg.periodo_ambiente_s):
                return

    def _ciclo_ambiente(self) -> None:
        agora = self.relogio()
        try:
            self.leitura_ambiente = self.sensor.ler()
            self._t_leitura_ambiente = agora
        except Exception as erro:  # noqa: BLE001 - qualquer falha de I2C é tratada igual
            log.warning("leitura do BMP280 falhou: %s", erro)
        leitura = self.leitura_ambiente
        # Dado velho não é reenviado: deixa o watchdog do simulador agir (piso de 3000 mA).
        if leitura is None or agora - self._t_leitura_ambiente > IDADE_MAX_LEITURA_AMBIENTE_S:
            return
        self.gerente.submeter(
            lambda api: api.escrever_condicao_contorno(leitura.temperatura_c, leitura.pressao_hpa), PRIO_AMBIENTE
        ).add_done_callback(lambda f: self._falha_modbus(f, "condição de contorno"))

    # ------------------------------------------------------------------ estado e estacionamento
    def _laco_estado(self) -> None:
        while not self.parar.wait(2.0):
            self._salvar()
            self._estacionar()

    def _estacionar(self) -> None:
        with self._trava:
            plano = self.despacho.plano_estacionamento(self._ultimo_cenario)
            ids = [(c, a, self.despacho.nova_tarefa_avulsa()) for c, a in plano]
        for cabine, andar, id_ in ids:
            self._enviar_tarefa(cabine, Tarefa(id_, andar, andar, self.relogio(), suave=True), suave=True)

    # ------------------------------------------------------------------ interface
    def comando(self, linha: str) -> str:
        partes = linha.split()
        if not partes:
            return ""
        try:
            if partes[0] == "ir" and len(partes) == 3:
                cabine, andar = int(partes[1]), int(partes[2])
                if cabine not in (1, 2, 3) or not 0 <= andar < NUM_ANDARES:
                    return "uso: ir <cabine 1-3> <andar 0-5>"
                ok = self.rede.enviar(cabine, "ir_andar", andar=andar)
                return f"cabine {cabine} -> andar {andar}" if ok else f"cabine {cabine} desconectada"
            if partes[0] == "recalibrar" and len(partes) == 2:
                ok = self.rede.enviar(int(partes[1]), "recalibrar")
                return "recalibração solicitada" if ok else "cabine desconectada"
            if partes[0] == "cotas" and len(partes) == 2 and partes[1] in ("on", "off"):
                self.cotas_suspensas = partes[1] == "off"
                if self.cotas_suspensas:
                    log.warning("renovação de Cotas SUSPENSA pelo operador (teste do Modo Degradado)")
                    return "Cotas suspensas: as cabines entram em Modo Degradado em até 2 s"
                self._ev_cota.set()
                log.warning("renovação de Cotas retomada pelo operador")
                return "Cotas retomadas"
            if partes[0] == "diag":
                return (
                    f"erros MODBUS={self.erros_modbus} portas recusadas={self.portas_recusadas} "
                    f"cotas={self._cotas} barramento={self.barramento_atual()} "
                    f"viagens registradas={len(self.persistencia.ler_viagens())}"
                )
            if partes[0] in ("ajuda", "help"):
                return "ir <cab> <andar> | recalibrar <cab> | cotas off|on | diag | sair"
            if partes[0] in ("sair", "exit", "quit"):
                self.parar.set()
                return "encerrando..."
        except ValueError:
            return "argumentos inválidos"
        return f"comando desconhecido: {partes[0]}"

    def visao(self) -> VisaoPainel:
        with self._trava:
            cabines = [
                {
                    "numero": c.numero,
                    "conectada": c.conectada,
                    "estado": c.estado,
                    "andar": c.andar,
                    "pos_mm": c.pos_mm,
                    "sentido": c.sentido,
                    "porta": c.porta,
                    "corrente_ma": c.corrente_ma,
                    "cota_ma": c.cota_ma,
                    "degradado": c.degradado,
                }
                for c in self.despacho.cabines.values()
            ]
            ativas = [c for c in self.despacho.chamadas.values() if c.estado != "concluida"]
            pendentes = [f"#{c.id}({c.origem}->{c.destino})→C{c.cabine}" for c in ativas if c.estado == "atribuida"]
            a_bordo = [f"#{c.id}({c.origem}->{c.destino})→C{c.cabine}" for c in ativas if c.estado == "a_bordo"]
        p = self.predio
        amb = self.leitura_ambiente
        return VisaoPainel(
            cabines=cabines,
            barramento_max_ma=p.barramento_max_ma if p else 0,
            watchdog=p.watchdog_ambiente if p else 0,
            temperatura_c=amb.temperatura_c if amb else None,
            pressao_hpa=amb.pressao_hpa if amb else None,
            cenario=p.cenario_ativo if p else 0,
            geradas=p.cenario_geradas if p else 0,
            atendidas=p.cenario_atendidas if p else 0,
            espera_s=(p.espera_media_s_x10 / 10.0) if p else 0.0,
            viagem_s=(p.viagem_media_s_x10 / 10.0) if p else 0.0,
            pendentes=pendentes + ([f"fila:{p.chamadas_na_fila}"] if p and p.chamadas_na_fila else []),
            em_atendimento=a_bordo,
            erros_modbus=self.erros_modbus,
            cotas_suspensas=self.cotas_suspensas,
        )
