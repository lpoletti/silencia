"""Motor de áudio: captura o microfone, remove o ruído com RNNoise e envia para a saída escolhida."""
import ctypes
import importlib.util
import io
import os
import sys
import tempfile
import urllib.request
import uuid
import winreg
import zipfile
from collections import deque

import numpy as np
import sounddevice as sd

RATE = 48000
FRAME = 480  # RNNoise processa blocos de 10 ms a 48 kHz
MAX_QUEUE = 10  # até 100 ms de atraso; acima disso descarta (relógios de aparelhos diferentes divergem)
PRIME = 3  # blocos acumulados antes de tocar, evita picotar
VBCABLE_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
VBCABLE_PAGE = "https://vb-audio.com/Cable/"


def _load_rnnoise():
    # Só a DLL do pyrnnoise: importar o pacote puxaria audiolab/av sem necessidade.
    base = getattr(sys, "_MEIPASS", None) or importlib.util.find_spec("pyrnnoise").submodule_search_locations[0]
    lib = ctypes.CDLL(os.path.join(base, "rnnoise.dll"))
    lib.rnnoise_create.argtypes = [ctypes.c_void_p]
    lib.rnnoise_create.restype = ctypes.c_void_p
    lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
    lib.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
    lib.rnnoise_process_frame.restype = ctypes.c_float
    return lib


_lib = _load_rnnoise()
_FLOATP = ctypes.POINTER(ctypes.c_float)


class Denoiser:
    """RNNoise + portão de voz: abaixo do limiar o microfone fica mudo (igual ao NoiseTorch)."""

    def __init__(self, threshold=0.6, hold_ms=200):
        self.state = _lib.rnnoise_create(None)
        self.threshold = threshold
        self.hold = hold_ms // 10  # em blocos de 10 ms, para não cortar o fim das palavras
        self._left = 0
        self._gain = 0.0
        self._out = np.zeros(FRAME, np.float32)

    def process(self, frame):
        x = np.ascontiguousarray(frame * 32768, np.float32)  # RNNoise espera escala de int16
        vad = _lib.rnnoise_process_frame(self.state, self._out.ctypes.data_as(_FLOATP), x.ctypes.data_as(_FLOATP))
        if vad >= self.threshold:
            self._left = self.hold
        elif self._left:
            self._left -= 1
        target = 1.0 if self._left else 0.0
        out = self._out / 32768 * np.linspace(self._gain, target, FRAME, dtype=np.float32)  # rampa evita estalos
        self._gain = target
        return out

    def close(self):
        if self.state:
            _lib.rnnoise_destroy(self.state)
            self.state = None


class Engine:
    def __init__(self):
        self.threshold = 0.6
        self.streams = []
        self.denoiser = None
        self.in_level = 0.0
        self.out_level = 0.0
        self._fifo = deque()
        self._primed = False

    @property
    def running(self):
        return bool(self.streams) and all(s.active for s in self.streams)

    def set_threshold(self, value):
        self.threshold = value
        if self.denoiser:
            self.denoiser.threshold = value

    def start(self, in_dev, out_dev):
        self.stop()
        self.denoiser = Denoiser(self.threshold)
        self._fifo.clear()
        self._primed = False
        common = dict(samplerate=RATE, blocksize=FRAME, dtype="float32", latency="low",
                      extra_settings=sd.WasapiSettings(auto_convert=True))
        try:
            self.streams = [
                sd.InputStream(device=in_dev, channels=min(2, sd.query_devices(in_dev)["max_input_channels"]),
                               callback=self._on_input, **common),
                sd.OutputStream(device=out_dev, channels=min(2, sd.query_devices(out_dev)["max_output_channels"]),
                                callback=self._on_output, **common),
            ]
            for s in self.streams:
                s.start()
        except Exception:
            self.stop()
            raise

    def stop(self):
        for s in self.streams:
            s.close()
        self.streams = []
        if self.denoiser:
            self.denoiser.close()
            self.denoiser = None
        self.in_level = self.out_level = 0.0

    def _on_input(self, indata, frames, time, status):
        mono = indata.mean(axis=1)
        out = self.denoiser.process(mono)
        self.in_level = float(np.abs(mono).max())
        self._fifo.append(out)
        while len(self._fifo) > MAX_QUEUE:
            self._fifo.popleft()

    def _on_output(self, outdata, frames, time, status):
        if not self._primed and len(self._fifo) >= PRIME:
            self._primed = True
        if self._primed and self._fifo:
            block = self._fifo.popleft()
            outdata[:] = block[:, None]
            self.out_level = float(np.abs(block).max())
        else:
            self._primed = False  # faltou áudio: acumula de novo antes de voltar a tocar
            outdata.fill(0)
            self.out_level = 0.0


def list_devices():
    """Microfones e saídas WASAPI (nomes completos, sem duplicatas de MME/DirectSound)."""
    sd._terminate()  # força o PortAudio a enxergar aparelhos conectados depois de aberto
    sd._initialize()
    api_idx, api = next((i, a) for i, a in enumerate(sd.query_hostapis()) if "WASAPI" in a["name"])
    devs = sd.query_devices()

    def named(kind):
        seen, result = {}, {}
        for i in api["devices"]:
            # o "CABLE Output" como microfone faria o app ler a própria saída (loop)
            if devs[i][f"max_{kind}_channels"] > 0 and not (kind == "input" and _is_virtual(devs[i]["name"])):
                name = devs[i]["name"]
                seen[name] = seen.get(name, 0) + 1
                result[name if seen[name] == 1 else f"{name} ({seen[name]})"] = i
        return result

    ins, outs = named("input"), named("output")
    default_in = next((n for n, i in ins.items() if i == api["default_input_device"]), None)
    default_out = next((n for n, i in outs.items() if i == api["default_output_device"]), None)
    return ins, outs, default_in, default_out


def _is_virtual(name):
    return "VB-Audio" in name  # VB-CABLE, CABLE-A/B e Voicemeeter: "CABLE Input (VB-Audio Virtual Cable)"


def find_virtual_cable(outputs):
    virtual = [n for n in outputs if _is_virtual(n)]
    return next((n for n in virtual if "Input" in n), virtual[0] if virtual else None)  # "CABLE Input" antes do "CABLE In 16ch"


def set_default_output(name):
    """Volta a saída de som padrão do Windows (o instalador do VB-CABLE a troca para o cabo, e o PC fica mudo)."""
    root = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
    endpoint = None
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as render:
        for i in range(winreg.QueryInfoKey(render)[0]):
            guid = winreg.EnumKey(render, i)
            try:
                with winreg.OpenKey(render, guid) as dev, winreg.OpenKey(render, guid + r"\Properties") as props:
                    active = winreg.QueryValueEx(dev, "DeviceState")[0] == 1
                    label = (f"{winreg.QueryValueEx(props, '{a45c254e-df1c-4efd-8020-67d146a850e0},2')[0]} "
                             f"({winreg.QueryValueEx(props, '{b3f8fa53-0004-438e-9003-51a46e139bfc},6')[0]})")
            except OSError:
                continue
            if active and label == name:  # mesmo formato de nome que o WASAPI mostra
                endpoint = "{0.0.0.00000000}." + guid
    if not endpoint:
        raise LookupError(name)
    # IPolicyConfig: interface (não documentada) que o próprio painel de Som do Windows usa
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    guid = lambda s: (ctypes.c_byte * 16)(*uuid.UUID(s).bytes_le)
    obj = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9"), None, 1,
                                guid("F8679F50-850A-41CF-9C72-430F290290C8"), ctypes.byref(obj))
    if hr:
        raise OSError(f"IPolicyConfig indisponível ({hr:#x})")
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    set_default = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)(vtable[13])
    try:
        for role in range(3):  # console, multimídia e comunicação
            if set_default(obj, endpoint, role):
                raise OSError("o Windows recusou a troca da saída padrão")
    finally:
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)  # Release


def install_vbcable():
    """Baixa o VB-CABLE oficial e abre o instalador (pede permissão de administrador)."""
    dest = os.path.join(tempfile.gettempdir(), "vbcable")
    data = urllib.request.urlopen(VBCABLE_URL, timeout=60).read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", os.path.join(dest, "VBCABLE_Setup_x64.exe"), None, dest, 1)
    if rc <= 32:
        raise OSError(f"instalador não abriu (código {rc})")
