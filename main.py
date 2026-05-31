"""
CLI entry point for RaspiPi 02 AI Hub.

Runs an infinite-loop menu that dispatches to each feature module.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from chatbot import chatbot
from weather_forecaster import forecast
from study_tracker import study_log
from finance_tracker import finance_log
from writing_corrector import corrector
from system_monitor import monitor as sysmon
from utils import setup_logging


def _clear() -> None:
    os.system("clear" if os.name == "posix" else "cls")


def _pause() -> None:
    input("\nPress Enter to return to menu…")


def _run_monitor() -> None:
    print(sysmon.get_report())


MENU = """
╔══════════════════════════════════╗
║     🍓 RaspiPi 02 — AI Hub       ║
╠══════════════════════════════════╣
║  1. 🌤   Weather Forecast         ║
║  2. 🤖  Chatbot                  ║
║  3. 📚  Study Tracker            ║
║  4. 💰  Finance Tracker          ║
║  5. ✍️   Writing Corrector       ║
║  6. 🖥  System Monitor           ║
║  0. 🚪  Exit                     ║
╚══════════════════════════════════╝
"""

ACTIONS: dict[str, tuple[Callable, bool]] = {
    "1": (forecast.main, True),
    "2": (chatbot.main, False),     # chatbot has its own inner loop
    "3": (study_log.main, True),
    "4": (finance_log.main, True),
    "5": (corrector.main, True),
    "6": (_run_monitor, True),
}


def main() -> None:
    setup_logging()
    while True:
        _clear()
        print(MENU)
        choice = input("Wassis gona be: ").strip()

        if choice == "0":
            print("Cheerio!")
            sys.exit(0)

        entry = ACTIONS.get(choice)
        if entry is not None:
            _clear()
            fn, needs_pause = entry
            fn()
            if needs_pause:
                _pause()
        else:
            print("Invalid option.\n")
            _pause()


if __name__ == "__main__":
    main()
