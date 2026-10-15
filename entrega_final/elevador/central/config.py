"""Configuração do Servidor Central (arquivo JSON)."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..dominio.estimativa import ParametrosTempo
from ..modbus.cliente import MATRICULA_PADRAO, matricula_em_bytes


@dataclass(frozen=True)
class ConfigCentral:
    escuta_host: str = "127.0.0.1"
    escuta_porta: int = 5000
    token: str = ""
    uart_porta: str = "/dev/serial0"
    uart_timeout_s: float = 0.3
    matricula: str = MATRICULA_PADRAO
    i2c_barramento: int = 1
    i2c_endereco: int = 0x76
    pasta_dados: str = "dados"
    periodo_ambiente_s: float = 2.5
    periodo_poll_cabine_s: float = 0.2
    periodo_chamadas_s: float = 0.25
    tempos: ParametrosTempo = field(default_factory=ParametrosTempo)


def carregar_config_central(caminho: str | Path) -> ConfigCentral:
    with open(caminho, encoding="utf-8") as arq:
        bruto = json.load(arq)
    try:
        escuta = bruto.get("escuta", {})
        uart = bruto.get("uart", {})
        i2c = bruto.get("i2c", {})
        periodos = bruto.get("periodos", {})
        cfg = ConfigCentral(
            escuta_host=str(escuta.get("host", "127.0.0.1")),
            escuta_porta=int(escuta.get("porta", 5000)),
            token=str(escuta.get("token", "")),
            uart_porta=str(uart.get("porta", "/dev/serial0")),
            uart_timeout_s=float(uart.get("timeout_s", 0.3)),
            matricula=str(bruto.get("matricula", MATRICULA_PADRAO)),
            i2c_barramento=int(i2c.get("barramento", 1)),
            i2c_endereco=int(str(i2c.get("endereco", "0x76")), 0),
            pasta_dados=str(bruto.get("pasta_dados", "dados")),
            periodo_ambiente_s=float(periodos.get("ambiente_s", 2.5)),
            periodo_poll_cabine_s=float(periodos.get("poll_cabine_s", 0.2)),
            periodo_chamadas_s=float(periodos.get("chamadas_s", 0.25)),
            tempos=ParametrosTempo(**bruto.get("tempos", {})),
        )
    except (TypeError, ValueError) as erro:
        raise ValueError(f"configuração do Central inválida: {erro}") from erro
    if not 0.2 <= cfg.uart_timeout_s <= 0.5:
        raise ValueError("uart.timeout_s deve estar entre 0,2 e 0,5 s")
    if not 0 < cfg.periodo_ambiente_s <= 5.0:
        raise ValueError("periodos.ambiente_s deve ser > 0 e <= 5 s (watchdog do simulador)")
    matricula_em_bytes(cfg.matricula)
    return cfg
