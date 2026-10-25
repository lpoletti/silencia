"""Gera o ícone e dist/Silencia.exe (arquivo único, sem precisar de Python): python build.py"""
import importlib.util
import os

import PyInstaller.__main__
from PIL import Image, ImageDraw, ImageFont

os.chdir(os.path.dirname(os.path.abspath(__file__)))

img = Image.new("RGBA", (256, 256))
draw = ImageDraw.Draw(img)
draw.ellipse((8, 8, 248, 248), fill="#22C55E")
draw.text((128, 128), "\uE7E8", font=ImageFont.truetype(r"C:\Windows\Fonts\SegoeIcons.ttf", 130), fill="white", anchor="mm")
img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

dll = os.path.join(importlib.util.find_spec("pyrnnoise").submodule_search_locations[0], "rnnoise.dll")
PyInstaller.__main__.run([
    "silencia.py", "--name", "Silencia", "--onefile", "--windowed", "--noconfirm", "--icon", "icon.ico",
    "--add-binary", f"{dll};.", "--add-data", "icon.ico;.", "--collect-data", "customtkinter",
])
