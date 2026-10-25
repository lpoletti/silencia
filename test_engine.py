"""Autoteste do motor sem aparelho de áudio: python test_engine.py"""
import os
import subprocess
import tempfile
import wave

import numpy as np

from engine import FRAME, MAX_QUEUE, PRIME, RATE, Denoiser, Engine, find_virtual_cable

rng = np.random.default_rng(0)
noise = [rng.normal(0, 0.05, FRAME).astype(np.float32) for _ in range(300)]  # 3 s de chiado

# Sem portão (limiar 0): RNNoise sozinho precisa derrubar o chiado
d = Denoiser(threshold=0.0)
out = np.concatenate([d.process(f) for f in noise][100:])  # ignora 1 s de adaptação
rms_in, rms_out = np.sqrt(np.mean(np.concatenate(noise) ** 2)), np.sqrt(np.mean(out ** 2))
assert rms_out < rms_in * 0.1, (rms_in, rms_out)
d.close()

# Limiar impossível: portão nunca abre, saída muda
d = Denoiser(threshold=1.01)
assert all(not d.process(f).any() for f in noise[:50])
d.close()

# Fila entre entrada e saída
e = Engine()
e.denoiser = Denoiser(threshold=0.0)
outbuf = np.zeros((FRAME, 2), np.float32)
e._on_output(outbuf, FRAME, None, None)
assert not outbuf.any(), "sem áudio na fila deve sair silêncio"
for f in noise[:MAX_QUEUE + 5]:
    e._on_input(np.stack([f, f], axis=1), FRAME, None, None)
assert len(e._fifo) == MAX_QUEUE, "a fila não pode crescer além do limite (atraso)"
for _ in range(MAX_QUEUE):
    e._on_output(outbuf, FRAME, None, None)
assert len(e._fifo) == 0 and e._primed
e._on_output(outbuf, FRAME, None, None)
assert not e._primed and not outbuf.any(), "faltou áudio: volta a acumular PRIME blocos"
e.denoiser.close()

assert find_virtual_cable(["Alto-falantes", "CABLE Input (VB-Audio Virtual Cable)"]) == "CABLE Input (VB-Audio Virtual Cable)"
assert find_virtual_cable(["Alto-falantes", "Voicemeeter Input (VB-Audio Voicemeeter VAIO)"]) == "Voicemeeter Input (VB-Audio Voicemeeter VAIO)"
assert find_virtual_cable(["CABLE In 16ch (VB-Audio Virtual Cable)", "CABLE Input (VB-Audio Virtual Cable)"]) == "CABLE Input (VB-Audio Virtual Cable)"
assert find_virtual_cable(["Alto-falantes"]) is None
print(f"ok: chiado {20 * np.log10(rms_in / rms_out):.0f} dB mais baixo, PRIME={PRIME}")

# Voz de verdade (voz do Windows) + ventilador + teclado a 5 dB de SNR: o ruído some e a voz fica
tmp = tempfile.gettempdir()
wav = os.path.join(tmp, "silencia_voz.wav")
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(48000, 'Sixteen', 'Mono'); "
                f"$s.SetOutputToWaveFile('{wav}', $f); $s.Speak('Olá, este é um teste de remoção de ruído. "
                "A voz precisa continuar clara, mesmo com o ventilador ligado e alguém digitando.'); $s.Dispose()"], check=True)
with wave.open(wav) as w:
    clean = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
silence = np.zeros(RATE, np.float32)
clean = np.concatenate([silence, clean / np.abs(clean).max() * 0.5, silence])
clean = clean[:len(clean) // FRAME * FRAME]
n = len(clean)
fan = np.convolve(rng.normal(size=n), np.ones(12) / 12, "same")
keys = np.zeros(n)
for t in rng.choice(n - 2000, size=n * 6 // RATE, replace=False):  # ~6 teclas por segundo
    keys[t:t + 2000] += rng.normal(size=2000) * np.exp(-np.arange(2000) / 200)
energy = lambda x: np.mean(x.reshape(-1, FRAME) ** 2, axis=1)
act = energy(clean)
speech, pauses = np.repeat(act > 0.0025 * act.max(), FRAME), act < 1e-10
noise = fan / fan.std() + 0.5 * keys / keys.std()
noisy = (clean + noise * np.sqrt(np.mean(clean[speech] ** 2) / 10 ** 0.5 / np.mean(noise ** 2))).astype(np.float32)

for threshold in (0.0, 0.6):
    d = Denoiser(threshold)
    out = np.concatenate([d.process(noisy[i:i + FRAME]) for i in range(0, n, FRAME)])
    d.close()
    lag = 2 * FRAME  # atraso do RNNoise
    o, c, z, s = out[lag:], clean[:-lag], noisy[:-lag], speech[:-lag]
    corr_in, corr_out = np.corrcoef(z[s], c[s])[0, 1], np.corrcoef(o[s], c[s])[0, 1]
    kept = 10 * np.log10(np.mean(o[s] ** 2) / np.mean(c[s] ** 2))
    assert corr_out > max(corr_in, 0.9) and kept > -3, (threshold, corr_in, corr_out, kept)
    pause_out = energy(out)[pauses]
    if threshold == 0:
        cut = 10 * np.log10(np.median(energy(noisy)[pauses]) / (np.median(pause_out) + 1e-20))
        assert cut > 40, cut
        print(f"ok: voz real fiel à original {corr_in:.2f} -> {corr_out:.2f}, voz {kept:+.1f} dB, ruído nas pausas -{cut:.0f} dB")
    else:
        muted = np.mean(pause_out == 0)
        assert muted > 0.7, muted
        print(f"ok: limiar {threshold:.0%} deixa {muted:.0%} das pausas em silêncio total, voz {kept:+.1f} dB")
        for name, sig in (("antes", noisy), ("depois", out)):
            with wave.open(os.path.join(tmp, f"silencia_{name}.wav"), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
                w.writeframes((np.clip(sig, -1, 1) * 32767).astype(np.int16).tobytes())
        print(f"   ouça: {os.path.join(tmp, 'silencia_antes.wav')} e silencia_depois.wav")
