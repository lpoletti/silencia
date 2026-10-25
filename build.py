"""Gera o ícone e dist/Silencia.exe (arquivo único, sem precisar de Python): python build.py"""
import importlib.util
import os

import PyInstaller.__main__

from silencia import ACCENT, make_icon

os.chdir(os.path.dirname(os.path.abspath(__file__)))
make_icon(ACCENT).save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

dll = os.path.join(importlib.util.find_spec("pyrnnoise").submodule_search_locations[0], "rnnoise.dll")
PyInstaller.__main__.run([
    "silencia.py", "--name", "Silencia", "--onefile", "--windowed", "--noconfirm", "--icon", "icon.ico",
    "--add-binary", f"{dll};.", "--collect-data", "customtkinter",
    "--hidden-import", "pystray._win32",  # o pystray escolhe o backend em tempo de execução; o PyInstaller não enxerga
])
