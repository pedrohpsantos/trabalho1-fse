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
        description="Sistema de Controle da Cabine 1 - FSE 2026/2 (Python)"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Força a execução em modo simulado (Mock com física integrada)",
    )
    parser.add_argument(
        "--rpi",
        action="store_true",
        help="Força a execução com hardware real na Raspberry Pi (RPi.GPIO)",
    )
    parser.add_argument(
        "--widget",
        "--bancada",
        action="store_true",
        dest="widget",
        help="Utiliza preset alternativo do widget ThingsBoard (DIR1=17, DIR2=27, SENSOR=11)",
    )

    args = parser.parse_args()

    print("Inicializando Sistema de Controle da Cabine 1 (FSE 2026/2 - Python)...")

    # Configuração dos pinos
    if args.widget:
        print("⚙️ Preset ativo: Widget alternativo (DIR1=17, DIR2=27, SENSOR=11)")
        pin_config = PinConfig.widget_bancada()
    else:
        print("⚙️ Preset ativo: Bancada 36 (PWM=13, DIR1=22, DIR2=23, SENSOR=0)")
        pin_config = PinConfig.tabela_oficial()

    # Seleção da camada HAL (Real ou Mock)
    hardware: ElevatorHardware
    if args.rpi:
        print("🔧 Camada HAL: Modo Raspberry Pi Forçado (RPi.GPIO / interrupções)")
        try:
            hardware = RpiHardware(pin_config)
        except Exception as e:
            print(f"❌ Falha ao inicializar periféricos da Raspberry Pi: {e}")
            sys.exit(1)
    elif args.mock or not RPI_GPIO_AVAILABLE:
        if not args.mock and not RPI_GPIO_AVAILABLE:
            print("💡 Camada HAL: RPi.GPIO não detectado neste sistema. Ativando Modo Simulado (Mock).")
        else:
            print("💡 Camada HAL: Modo Simulado Ativo (Mock local com física integrada).")
        hardware = MockHardware()
    else:
        # RPI_GPIO disponível e mock não solicitado
        print("🔧 Camada HAL: Modo Raspberry Pi Detectado e Ativo.")
        try:
            hardware = RpiHardware(pin_config)
        except Exception as e:
            print(f"❌ Falha ao inicializar RPi.GPIO: {e}. Alternando para Mock.")
            hardware = MockHardware()

    event_queue = hardware.get_event_queue()
    controller = ElevatorController(hardware)
    running_event = threading.Event()
    running_event.set()

    # Tratamento de interrupção SIGINT (Ctrl+C)
    def sigint_handler(_sig, _frame) -> None:
        print("\n\n🛑 [SIGINT] Sinal Ctrl+C capturado pelo sistema!")
        print("🛑 Acionando freio elétrico de emergência (DIR1=1, DIR2=1), zerando PWM...")
        running_event.clear()
        try:
            controller.parar()
            controller.cleanup()
        except Exception:
            pass
        print("✅ Recursos de GPIO desativados e liberados com segurança. Encerrando.")
        sys.exit(0)

    signal.signal(signal.SIGINT, sigint_handler)

    # Thread da Malha de Controle periódica (50 ms / 20 Hz, sem busy-wait)
    def control_loop() -> None:
        tick_interval = 0.05  # 50 ms
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

    print("Sistema de Controle da Cabine 1 encerrado com sucesso.")


if __name__ == "__main__":
    main()
