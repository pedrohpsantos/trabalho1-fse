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


try:
    import fcntl
except ImportError:
    fcntl = None


def _acquire_instance_lock() -> object:
    """Garante que apenas uma instância do processo controle os pinos GPIO."""
    if fcntl is None:
        return None
    lock_path = "/tmp/entrega1_fse_cabine1.lock"
    try:
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.write(str(os.getpid()) + "\n")
        lock_file.flush()
        return lock_file
    except (IOError, BlockingIOError):
        print("\n[ERRO CRÍTICO] Já existe outra instância do controle do elevador em execução!")
        print("Para evitar conflitos de GPIO e leituras incorretas de encoder:")
        print("  1. Encerre o processo anterior no outro terminal aberto (Ctrl+C).")
        print("  2. Ou execute no terminal:")
        print("     pkill -9 -f 'python.*main.py'\n")
        sys.exit(1)
    except Exception:
        return None


def main() -> None:
    _lock_handle = _acquire_instance_lock()

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
    if args.mock or (not args.rpi and not RPI_GPIO_AVAILABLE):
        if not args.mock and not RPI_GPIO_AVAILABLE:
            print("[HAL] RPi.GPIO não detectado. Ativando simulação local.")
        else:
            print("[HAL] Modo simulado ativo.")
        hardware = MockHardware()
    else:
        print("[HAL] Modo Raspberry Pi ativo (RPi.GPIO).")
        try:
            hardware = RpiHardware(pin_config)
        except Exception as e:
            print(f"\n[ERRO CRÍTICO] Falha ao inicializar periféricos GPIO: {e}")
            print("Possível processo anterior travado ou pinos ocupados.")
            print("Execute 'pkill -9 -f main.py' para liberar os recursos.\n")
            sys.exit(1)

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
