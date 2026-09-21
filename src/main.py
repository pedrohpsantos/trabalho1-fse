"""Entrega 1: Módulo da GPIO e Controle da Cabine 1 (Python).

Disciplina: Fundamentos de Sistemas Embarcados (2026/2)
Faculdade do Gama - Universidade de Brasília (FGA-UnB)
"""

import argparse
import os
import queue
import signal
import sys
import threading
import time

# Garante suporte a UTF-8 em terminais e redirecionamentos no Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Adiciona o diretório raiz do entrega1_python ao sys.path
_pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

if __package__ is None or __package__ == "":
    from src.cli.cli import Cli
    from src.config import PinConfig
    from src.controller.elevator import ElevatorController
    from src.hal.base import ElevatorHardware
    from src.hal.mock import MockHardware
    from src.hal.rpi import RpiHardware, RPI_GPIO_AVAILABLE
else:
    from .cli.cli import Cli
    from .config import PinConfig
    from .controller.elevator import ElevatorController
    from .hal.base import ElevatorHardware
    from .hal.mock import MockHardware
    from .hal.rpi import RpiHardware, RPI_GPIO_AVAILABLE


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sistema de Controle da Cabine 1 - FSE 2026/2"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Execução em modo simulado (cinemática local)",
    )
    parser.add_argument(
        "--rpi",
        action="store_true",
        help="Execução na Raspberry Pi com driver RPi.GPIO",
    )
    parser.add_argument(
        "--widget",
        "--bancada",
        action="store_true",
        dest="widget",
        help="Preset alternativo (DIR1=17, DIR2=27, SENSOR=11)",
    )

    args = parser.parse_args()

    print("[INIT] Inicializando controle da Cabine 1...")

    # Configuração dos pinos
    if args.widget:
        print("[CONFIG] Preset: Widget alternativo (DIR1=17, DIR2=27, SENSOR=11)")
        pin_config = PinConfig.widget_bancada()
    else:
        print("[CONFIG] Preset: Bancada (PWM=13, DIR1=22, DIR2=23, SENSOR=0)")
        pin_config = PinConfig.tabela_oficial()

    # Seleção da camada HAL
    hardware: ElevatorHardware
    if args.rpi:
        print("[HAL] Modo Raspberry Pi configurado.")
        try:
            hardware = RpiHardware(pin_config)
        except Exception as e:
            print(f"[ERRO] Falha ao inicializar periféricos: {e}")
            sys.exit(1)
    elif args.mock or not RPI_GPIO_AVAILABLE:
        if not args.mock and not RPI_GPIO_AVAILABLE:
            print("[HAL] RPi.GPIO não detectado. Ativando simulação local.")
        else:
            print("[HAL] Modo simulado ativo.")
        hardware = MockHardware()
    else:
        print("[HAL] Modo Raspberry Pi detectado.")
        try:
            hardware = RpiHardware(pin_config)
        except Exception as e:
            print(f"[ERRO] Falha ao inicializar RPi.GPIO: {e}. Alternando para simulador.")
            hardware = MockHardware()

    event_queue = hardware.get_event_queue()
    controller = ElevatorController(hardware)
    running_event = threading.Event()
    running_event.set()

    # Interrupção de emergência (Ctrl+C)
    def sigint_handler(_sig, _frame) -> None:
        print("\n[SIGINT] Interrupção capturada. Acionando freio e liberando GPIO...")
        running_event.clear()
        try:
            controller.parar()
            controller.cleanup()
        except Exception:
            pass
        print("[INFO] Recursos liberados. Encerrando processo.")
        sys.exit(0)

    signal.signal(signal.SIGINT, sigint_handler)

    # Thread da Malha de Controle periódica (20 ms / 50 Hz, sem busy-wait)
    def control_loop() -> None:
        tick_interval = 0.02  # 20 ms
        while running_event.is_set():
            try:
                controller.tick()
            except Exception as e:
                print(f"Erro na malha de controle: {e}")
            time.sleep(tick_interval)

    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()

    # Thread Receptora de Eventos Assíncronos (Cortina e Sensor de Andar)
    def event_loop() -> None:
        while running_event.is_set():
            try:
                event = event_queue.get(timeout=0.1)
                controller.handle_event(event)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Erro no processamento de eventos: {e}")

    event_thread = threading.Thread(target=event_loop, daemon=True)
    event_thread.start()

    # Executa a interface de linha de comando no terminal (Thread Principal)
    try:
        Cli.run_loop(controller, running_event)
    except KeyboardInterrupt:
        sigint_handler(signal.SIGINT, None)

    print("[INFO] Sistema finalizado.")


if __name__ == "__main__":
    main()
