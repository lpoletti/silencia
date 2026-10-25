"""Silencia: remove o ruído do microfone em tempo real (inspirado no NoiseTorch)."""
import ctypes
import json
import math
import os
import queue
import sys
import tempfile
import threading
import tkinter.font
import webbrowser
from ctypes import wintypes

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw, ImageFont

import engine

APP = "Silencia"
CONFIG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP, "config.json")

# (tema claro, tema escuro): segue o tema do Windows
BG = ("#F3F4F6", "#0F1115")
CARD = ("#FFFFFF", "#181B21")
BORDER = ("#E5E7EB", "#252932")
TEXT = ("#111827", "#F3F4F6")
MUTED = ("#6B7280", "#9CA3AF")
TRACK = ("#E5E7EB", "#2A2F38")
OFF = ("#E5E7EB", "#252932")
OFF_HOVER = ("#D1D5DB", "#303643")
ACCENT = "#22C55E"
ACCENT_HOVER = "#16A34A"
ICON_OFF = "#6B7280"  # cinza do ícone desligado (bandeja e barra de tarefas)
ERROR = "#EF4444"
WARN_BG = ("#FEF3C7", "#2A2414")
WARN_TEXT = ("#92400E", "#FCD34D")
ICON_POWER, ICON_REFRESH = "", ""  # glifos da Segoe Fluent Icons / MDL2 Assets


def make_icon(color, size=256):
    """Círculo colorido com o símbolo de liga/desliga: ícone da janela, da bandeja e do .exe."""
    img = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(img)
    draw.ellipse((size * 0.03, size * 0.03, size * 0.97, size * 0.97), fill=color)
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):  # Windows 11 / Windows 10
        try:
            glyph = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name), round(size * 0.5))
        except OSError:
            continue
        draw.text((size / 2, size / 2), ICON_POWER, font=glyph, fill="white", anchor="mm")
        break
    return img


def font(size, weight="normal", display=False):
    return ctk.CTkFont(family="Segoe UI Variable Display" if display else "Segoe UI Variable Text", size=size, weight=weight)


def level_to_bar(peak):
    return max(0.0, 1 + 20 * math.log10(peak + 1e-9) / 60)  # -60 dBFS..0 dBFS -> 0..1


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.title(APP)
        self.resizable(False, False)
        self.icon_files, self.tray_images = {}, {}
        for on, color in ((True, ACCENT), (False, ICON_OFF)):
            self.icon_files[on] = os.path.join(tempfile.gettempdir(), f"silencia_{'on' if on else 'off'}.ico")
            make_icon(color).save(self.icon_files[on], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)])
            self.tray_images[on] = make_icon(color, 64)
        self._icon_on = None
        self.closed = False
        self._calls = queue.SimpleQueue()  # pedidos da bandeja (outra thread), executados no _tick, na thread da janela
        self.tray = pystray.Icon(APP, self.tray_images[False], APP, menu=pystray.Menu(
            pystray.MenuItem("Abrir Silencia", lambda: self._calls.put(self.show_window), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda: self._calls.put(self.on_close))))
        self.tray.run_detached()
        self.bind("<Unmap>", self._on_unmap)
        self.icons = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in tkinter.font.families()), None)
        try:
            with open(CONFIG, encoding="utf-8") as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            self.cfg = {}
        self.engine = engine.Engine()
        self.engine.set_threshold(min(max(float(self.cfg.get("threshold", 0.6)), 0.0), 0.95))
        self.ins, self.outs = {}, {}
        self.bars = [0.0, 0.0]
        self._build()
        self.refresh_devices()
        area = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA: tela sem a barra de tarefas
        x = area.left + (area.right - area.left - self.winfo_reqwidth()) // 2
        y = area.top + max(0, (area.bottom - area.top - self.winfo_reqheight() - 40) // 2)  # 40: barra de título
        self.geometry(f"+{x}+{y}")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<space>", lambda e: self.toggle())
        if "--ativar" in sys.argv:  # como o "-i" do NoiseTorch: já abre filtrando (útil em atalhos)
            self.start()
        self._tick()

    def _build(self):
        pad = dict(padx=24)
        ctk.CTkLabel(self, text=APP, font=font(24, "bold", True), text_color=TEXT).pack(pady=(14, 0), **pad)
        ctk.CTkLabel(self, text="Remove ruídos do microfone em tempo real", font=font(12), text_color=MUTED).pack(**pad)

        size = 116
        self.power = ctk.CTkButton(self, width=size, height=size, corner_radius=size // 2, border_width=0, text="", command=self.toggle)
        self.power.pack(pady=(16, 10))
        # Com texto o CTkButton deixa de ser redondo; o ícone vai direto no canvas dele.
        mid = self.power._apply_widget_scaling(size / 2)
        self.glyph = self.power._canvas.create_text(
            mid, mid, text=ICON_POWER if self.icons else "ON",
            font=(self.icons or "Segoe UI", -round(self.power._apply_widget_scaling(42 if self.icons else 28)), "bold"))
        self.status = ctk.CTkLabel(self, font=font(15, "bold"), wraplength=380)
        self.status.pack(**pad)
        self.hint = ctk.CTkLabel(self, font=font(12), text_color=MUTED, wraplength=380, justify="center")
        self.hint.pack(pady=(0, 12), **pad)

        devices = self._card()
        head = ctk.CTkFrame(devices, fg_color="transparent")
        head.pack(fill="x")
        self._label(head, "Microfone").pack(side="left")
        if self.icons:
            ctk.CTkButton(head, text=ICON_REFRESH, font=ctk.CTkFont(self.icons, 14), width=28, height=28, corner_radius=8,
                          fg_color="transparent", hover_color=OFF_HOVER, text_color=MUTED,
                          command=self.refresh_devices).pack(side="right")
        self.in_menu = self._menu(devices)
        self.in_meter = self._meter(devices)
        self._label(devices, "Saída (microfone virtual)").pack(anchor="w", pady=(10, 0))
        self.out_menu = self._menu(devices)
        self.out_meter = self._meter(devices)

        gate = self._card()
        head = ctk.CTkFrame(gate, fg_color="transparent")
        head.pack(fill="x")
        self._label(head, "Limiar de voz").pack(side="left")
        self.th_value = ctk.CTkLabel(head, font=font(13, "bold"), text_color=TEXT)
        self.th_value.pack(side="right")
        self.slider = ctk.CTkSlider(gate, from_=0, to=0.95, number_of_steps=19, height=18, command=self.on_threshold,
                                    progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER, fg_color=TRACK)
        self.slider.set(self.engine.threshold)
        self.slider.pack(fill="x", pady=(8, 4))
        ctk.CTkLabel(gate, text="Abaixo deste nível de voz o microfone fica mudo. Em 0% só remove o ruído de fundo.",
                     font=font(12), text_color=MUTED, wraplength=360, justify="left").pack(anchor="w")
        self.on_threshold(self.engine.threshold)

        self.banner = ctk.CTkFrame(self, fg_color=WARN_BG, corner_radius=14)
        head = ctk.CTkFrame(self.banner, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(10, 0))
        self.banner_title = ctk.CTkLabel(head, font=font(13, "bold"), text_color=WARN_TEXT)
        self.banner_title.pack(side="left")
        self.banner_btn = ctk.CTkButton(head, font=font(13, "bold"), width=90, height=30, corner_radius=10,
                                        fg_color=WARN_TEXT, hover_color=MUTED, text_color=WARN_BG)
        self.banner_btn.pack(side="right")
        self.banner_text = ctk.CTkLabel(self.banner, font=font(12), text_color=WARN_TEXT, wraplength=360, justify="left")
        self.banner_text.pack(anchor="w", padx=16, pady=(2, 10))
        self.bottom = ctk.CTkFrame(self, fg_color="transparent", height=8)  # margem inferior
        self.bottom.pack()

    def _card(self):
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        card.pack(fill="x", padx=20, pady=(0, 10))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)
        return inner

    def _label(self, master, text):
        return ctk.CTkLabel(master, text=text, font=font(13, "bold"), text_color=TEXT)

    def _menu(self, master):
        menu = ctk.CTkOptionMenu(master, values=[""], width=360, height=34, corner_radius=10, dynamic_resizing=False,
                                 font=font(13), dropdown_font=font(13), fg_color=TRACK, button_color=TRACK,
                                 button_hover_color=OFF_HOVER, text_color=TEXT, command=self.on_device_change)
        menu.pack(fill="x", pady=(5, 7))
        return menu

    def _meter(self, master):
        bar = ctk.CTkProgressBar(master, height=6, corner_radius=3, progress_color=ACCENT, fg_color=TRACK)
        bar.set(0)
        bar.pack(fill="x")
        return bar

    def refresh_devices(self):
        was_on = bool(self.engine.streams)
        self.engine.stop()  # o PortAudio é reiniciado para enxergar aparelhos novos
        self.ins, self.outs, default_in, default_out = engine.list_devices()
        cable = engine.find_virtual_cable(self.outs)
        pick = lambda saved, options, fallback: saved if saved in options else fallback or next(iter(options), "")
        for menu, options, choice in ((self.in_menu, self.ins, pick(self.cfg.get("input"), self.ins, default_in)),
                                      (self.out_menu, self.outs, pick(self.cfg.get("output"), self.outs, cable))):
            menu.configure(values=list(options) or ["Nenhum aparelho encontrado"])
            menu.set(choice or "Nenhum aparelho encontrado")
        speakers = next((n for n in self.outs if not engine.find_virtual_cable([n])), None)
        if not cable:
            self._banner("Microfone virtual não encontrado", "O VB-CABLE (grátis) leva o som filtrado ao Discord, Teams, Meet etc.",
                         "Instalar", self.install_cable)
        elif speakers and engine.find_virtual_cable([default_out or ""]):  # o instalador do VB-CABLE faz isso
            self._banner("Seu PC ficou sem som", f"A saída de som do Windows foi para o CABLE. Corrigir volta para {speakers}.",
                         "Corrigir", lambda: self.fix_speakers(speakers))
        else:
            self._banner(None)
        if was_on:
            self.start()
        self._update_status()
        self.update_idletasks()  # janela do tamanho do conteúdo (o CTk começa com 600x500 fixo)
        self.geometry(f"{self._reverse_window_scaling(self.winfo_reqwidth())}x{self._reverse_window_scaling(self.winfo_reqheight())}")

    def toggle(self):
        if self.engine.streams:
            self.engine.stop()
            self._update_status()
        else:
            self.start()

    def start(self):
        in_name, out_name = self.in_menu.get(), self.out_menu.get()
        if in_name not in self.ins or out_name not in self.outs:
            return self._update_status("Escolha um microfone e uma saída.")
        try:
            self.engine.start(self.ins[in_name], self.outs[out_name])
        except Exception as e:
            self.engine.stop()
            return self._update_status(f"Não foi possível iniciar: {e}")
        self._update_status()

    def _update_status(self, error=None):
        on = bool(self.engine.streams)
        self.power.configure(fg_color=ACCENT if on else OFF, hover_color=ACCENT_HOVER if on else OFF_HOVER)
        self.power._canvas.itemconfig(self.glyph, fill="#FFFFFF" if on else "#8B93A1")
        if on != self._icon_on:  # verde ligado, cinza desligado: janela, barra de tarefas e bandeja
            self._icon_on = on
            self.iconbitmap(self.icon_files[on])
            self.tray.icon = self.tray_images[on]
        self.tray.title = f"{APP}: {'ligado' if on else 'desligado'}"
        if error:
            if self.state() == "withdrawn":  # escondido na bandeja: mostra a janela para o erro não passar batido
                self.show_window()
            self.status.configure(text=error, text_color=ERROR)
        elif on:
            self.status.configure(text="Ativo: ruído sendo removido", text_color=ACCENT)
        else:
            self.status.configure(text="Desligado: clique para ativar", text_color=MUTED)
        out = self.out_menu.get()
        if engine.find_virtual_cable([out]):
            mic = "CABLE Output" if "Virtual Cable" in out else out.split(" (")[0].replace("Input", "Output")
            self.hint.configure(text=f"Nos apps (Discord, Teams, Meet, OBS), escolha “{mic}” como microfone.")
        elif out in self.outs:
            self.hint.configure(text="Modo teste: você vai ouvir seu microfone filtrado nesta saída.")
        else:
            self.hint.configure(text="")

    def on_device_change(self, _):
        self._save()
        if self.engine.streams:
            self.start()
        else:
            self._update_status()

    def on_threshold(self, value):
        self.engine.set_threshold(value)
        self.th_value.configure(text=f"{round(value * 100)}%")

    def _banner(self, title, text="", button="", command=None):
        if not title:
            return self.banner.pack_forget()
        self.banner_title.configure(text=title)
        self.banner_text.configure(text=text)
        self.banner_btn.configure(text=button, command=command, state="normal")
        self.banner.pack(fill="x", padx=20, pady=(0, 10), before=self.bottom)

    def install_cable(self):
        self.banner_btn.configure(state="disabled", text="Baixando…")

        def work():
            try:
                engine.install_vbcable()
                msg, wait = "No instalador, clique em “Install Driver”. O Silencia percebe sozinho quando terminar.", True
            except Exception:
                webbrowser.open(engine.VBCABLE_PAGE)  # sem internet/bloqueado: baixa manualmente pelo site
                msg, wait = "Abrimos o site do VB-CABLE: baixe, instale e clique em ⟳.", False
            self.after(0, lambda: done(msg, wait))

        def done(msg, wait):
            self.banner_btn.configure(state="normal", text="Instalar")
            self.banner_text.configure(text=msg)
            if wait:
                self._wait_cable()

        threading.Thread(target=work, daemon=True).start()

    def _wait_cable(self, tries=200):
        # Recarregar aparelhos com o filtro ligado daria um corte no som; nesse caso só espera.
        cable = None if self.engine.streams else engine.find_virtual_cable(engine.list_devices()[1])
        if cable:
            self.cfg["output"] = cable  # cabo recém-instalado vira a saída
            self.refresh_devices()
            self._save()
        elif tries:
            self.after(3000, self._wait_cable, tries - 1)

    def fix_speakers(self, speakers):
        try:
            engine.set_default_output(speakers)
        except Exception:
            os.startfile("ms-settings:sound")  # não deu automático: abre a tela de Som do Windows
        self.refresh_devices()

    def _on_unmap(self, event):
        if event.widget is self and self.state() == "iconic":
            self.withdraw()  # sai da barra de tarefas; fica só o ícone nos itens ocultos da bandeja

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tick(self):
        while not self._calls.empty():
            self._calls.get()()
        if self.closed:  # "Sair" da bandeja acabou de fechar a janela
            return
        if self.engine.streams and not self.engine.running:  # aparelho desconectado no meio do uso
            self.engine.stop()
            self._update_status("O aparelho de áudio foi desconectado.")
        for i, (level, meter) in enumerate(((self.engine.in_level, self.in_meter), (self.engine.out_level, self.out_meter))):
            self.bars[i] = max(level_to_bar(level), self.bars[i] * 0.85)  # sobe rápido, desce suave
            meter.set(self.bars[i])
        self.after(33, self._tick)

    def _save(self):
        self.cfg.update(input=self.in_menu.get(), output=self.out_menu.get(), threshold=round(self.engine.threshold, 2))
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    def on_close(self):
        self.engine.stop()
        self._save()
        self.tray.stop()  # remove o ícone da bandeja (e encerra a thread dele)
        self.closed = True
        self.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("system")
    App().mainloop()
