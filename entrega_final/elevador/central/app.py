"""Ponto de entrada do Servidor Central: `python -m elevador.central --config ...`."""

import argparse
import logging
import sys
from pathlib import Path

from ..modbus.cliente import ClienteModbus
from ..modbus.gerente import GerenteModbus
from ..modbus.simulador import ApiSimulador
from ..modbus.uart import UartBase, UartSerial
from ..util.logs import configurar_logs
from ..util.sinais import EncerramentoSeguro
from .bmp280 import Bmp280, Bmp280Simulado, SensorAmbiente
from .config import ConfigCentral, carregar_config_central
from .painel import Painel
from .persistencia import Persistencia
from .servico import ServicoCentral

log = logging.getLogger(__name__)


def criar_perifericos(
    cfg: ConfigCentral, mock: bool, bancada: str | None, temp_mock: float
) -> tuple[UartBase, SensorAmbiente]:
    """Só o Central abre a UART e o I2C."""
    if mock:
        from ..sim.uart_sim import UartViaTcp

        host, _, porta = (bancada or "127.0.0.1:5600").partition(":")
        return UartViaTcp(host, int(porta or 5600), cfg.uart_timeout_s), Bmp280Simulado(temp_mock)
    return UartSerial(cfg.uart_porta, cfg.uart_timeout_s), Bmp280(cfg.i2c_barramento, cfg.i2c_endereco)


def montar_servico(cfg: ConfigCentral, uart: UartBase, sensor: SensorAmbiente) -> ServicoCentral:
    cliente = ClienteModbus(uart, cfg.matricula)
    gerente = GerenteModbus(ApiSimulador(cliente))
    return ServicoCentral(cfg, gerente, sensor, Persistencia(cfg.pasta_dados))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Servidor Central do grupo de elevadores")
    ap.add_argument("--config", required=True)
    ap.add_argument("--mock", action="store_true", help="UART/I2C virtuais (bancada)")
    ap.add_argument("--bancada", default=None, help="host:porta da bancada virtual")
    ap.add_argument("--temp-mock", type=float, default=25.0, help="temperatura do BMP280 simulado (°C)")
    ap.add_argument("--sem-painel", action="store_true", help="não redesenha o painel de terminal")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--log-arquivo", default=None)
    args = ap.parse_args(argv)

    cfg = carregar_config_central(args.config)
    com_painel = not args.sem_painel and sys.stdout.isatty()
    arquivo_log = args.log_arquivo
    if com_painel and arquivo_log is None:
        # Com o painel na tela o log vai para arquivo; senão ele embaralharia a área de comandos.
        arquivo_log = str(Path(cfg.pasta_dados) / "central.log")
    if arquivo_log:
        Path(arquivo_log).parent.mkdir(parents=True, exist_ok=True)
    configurar_logs("central", args.log_level, arquivo_log)
    encerramento = EncerramentoSeguro()
    encerramento.instalar()
    uart, sensor = criar_perifericos(cfg, args.mock, args.bancada, args.temp_mock)
    servico = montar_servico(cfg, uart, sensor)
    encerramento.ao_encerrar(servico.parar.set)
    servico.iniciar()
    painel = None
    if not args.sem_painel:
        painel = Painel(servico.visao, servico.comando, servico.parar)
        painel.iniciar()
    try:
        # Espera com timeout: no Windows um wait() sem prazo não deixa o handler de sinal rodar.
        while not servico.parar.wait(0.5):  # sai por SIGINT/SIGTERM ou pelo comando "sair"
            pass
    finally:
        if painel:
            painel.encerrar()
        servico.encerrar()
        uart.fechar()
    return 0
