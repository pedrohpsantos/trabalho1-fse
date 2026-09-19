"""Interface de Linha de Comando (CLI) para controle interativo da Cabine 1."""

import sys
import threading
import time
from typing import List

from ..config import MotorDirection
from ..controller.elevator import ElevatorController


class Cli:
    """Interpretador de comandos interativos do terminal."""

    @staticmethod
    def print_banner() -> None:
        print(
            "\n╔══════════════════════════════════════════════════════════════╗\n"
            "║        SISTEMA DE CONTROLE DE ELEVADORES — FSE 2026/2        ║\n"
            "║                   ENTREGA 1 — CABINE 1 (PYTHON)             ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            "║ Comandos disponíveis:                                        ║\n"
            "║   andar <0|1|2>           - Move para o andar desejado       ║\n"
            "║   motor <dir> <duty>      - dir: livre | subir | descer | freio║\n"
            "║                             duty: 0 a 100                    ║\n"
            "║   status                  - Exibe estado dos sensores e motor║\n"
            "║   parar                   - Para o motor e aciona o freio    ║\n"
            "║   ajuda                   - Exibe esta mensagem de ajuda     ║\n"
            "║   sair                    - Encerra o programa graciosamente ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n"
        )

    @classmethod
    def run_loop(cls, controller: ElevatorController, running_event: threading.Event) -> None:
        """Executa o loop interativo da CLI."""
        cls.print_banner()

        while running_event.is_set():
            try:
                # Leitura do comando no terminal
                sys.stdout.write("elevador-cabine1> ")
                sys.stdout.flush()

                line = sys.stdin.readline()
                if not line:
                    # EOF (Ctrl+D / fim de stream)
                    break

                line = line.strip()
                if not line:
                    continue

                tokens: List[str] = line.split()
                cmd = tokens[0].lower()

                if cmd == "andar":
                    if len(tokens) < 2:
                        print("❌ Uso: andar <0|1|2>")
                        continue

                    try:
                        andar = int(tokens[1])
                        if andar in (0, 1, 2):
                            controller.ir_para_andar(andar)
                        else:
                            print(f"❌ Andar inválido: '{tokens[1]}'. Escolha entre 0, 1 ou 2.")
                    except ValueError:
                        print(f"❌ Andar inválido: '{tokens[1]}'. Escolha entre 0, 1 ou 2.")

                elif cmd == "motor":
                    if len(tokens) < 3:
                        print("❌ Uso: motor <livre|subir|descer|freio> <duty_0_a_100>")
                        continue

                    dir_str = tokens[1].lower()
                    direcao_map = {
                        "livre": MotorDirection.LIVRE,
                        "subir": MotorDirection.SUBIR,
                        "descer": MotorDirection.DESCER,
                        "freio": MotorDirection.FREIO,
                    }

                    if dir_str not in direcao_map:
                        print(f"❌ Direção desconhecida: '{tokens[1]}'. Use livre, subir, descer ou freio.")
                        continue

                    try:
                        duty = float(tokens[2])
                        if 0.0 <= duty <= 100.0:
                            controller.comando_motor(direcao_map[dir_str], duty)
                        else:
                            print(f"❌ Duty cycle inválido: '{tokens[2]}'. Use um valor entre 0 e 100.")
                    except ValueError:
                        print(f"❌ Duty cycle inválido: '{tokens[2]}'. Use um valor numérico entre 0 e 100.")

                elif cmd == "status":
                    controller.print_status()

                elif cmd == "parar":
                    controller.parar()

                elif cmd in ("ajuda", "help", "?"):
                    cls.print_banner()

                elif cmd in ("sair", "exit", "quit"):
                    print("⏹️ Finalizando sistema por solicitação do usuário...")
                    running_event.clear()
                    break

                else:
                    print(f"❌ Comando desconhecido: '{cmd}'. Digite 'ajuda' para ver os comandos.")

            except (KeyboardInterrupt, EOFError):
                break
            except Exception as e:
                print(f"❌ Erro ao processar comando: {e}")

            time.sleep(0.01)

        # Garante a parada imediata e liberação dos recursos
        try:
            controller.parar()
            controller.cleanup()
        except Exception:
            pass
