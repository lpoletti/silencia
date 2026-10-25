# Silencia

Remove o ruído do microfone em tempo real no Windows: ventilador, teclado, cachorro, rua.
É o mesmo princípio do [NoiseTorch](https://github.com/noisetorch/NoiseTorch) (Linux): usa a rede neural **RNNoise**
e entrega o som limpo num microfone virtual, que você escolhe no Discord, Teams, Meet, Zoom ou OBS.

## Como usar

1. Baixe o `Silencia.exe` em [Releases](https://github.com/lpoletti/silencia/releases/latest) e abra (não precisa instalar).
   Como o arquivo não tem assinatura digital, o Windows pode avisar na primeira vez: clique em **Mais informações → Executar assim mesmo**.
2. Na primeira vez, clique em **Instalar** no aviso amarelo. Isso instala o VB-CABLE (gratuito), o microfone virtual.
   No instalador, clique em **Install Driver**. O Silencia percebe sozinho quando terminar.
   Se o instalador deixar o PC sem som (ele costuma mandar o som do Windows para o cabo), o app avisa: clique em **Corrigir**.
3. Escolha seu **Microfone** e clique no botão redondo (ou aperte **Espaço**). Verde = filtrando.
4. No app da chamada, escolha **CABLE Output** como microfone.

O app lembra o microfone, a saída e o limiar da última vez.

| Controle | O que faz |
|---|---|
| Botão redondo / Espaço | Liga e desliga o filtro |
| Microfone | O microfone de verdade (com ruído) |
| Saída | Para onde vai o som limpo: o **CABLE Input**. Para se ouvir e testar, escolha seus fones ou alto-falantes |
| Limiar de voz | Abaixo deste nível de voz, o microfone fica mudo (como no NoiseTorch). Em 0% ele só tira o ruído de fundo |
| ⟳ | Procura de novo os aparelhos (depois de conectar um headset) |

Para já abrir filtrando (por exemplo, num atalho na pasta Inicializar): `Silencia.exe --ativar`.

## Para desenvolvedores

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python silencia.py      # roda o app
.venv\Scripts\python test_engine.py   # autoteste do motor sem aparelho de áudio (inclui voz real gerada pelo Windows)
.venv\Scripts\python build.py         # gera dist\Silencia.exe
```

- `engine.py`: captura (WASAPI), RNNoise + portão de voz, fila entre os aparelhos e instalação do VB-CABLE.
- `silencia.py`: interface (customtkinter, segue o tema claro/escuro do Windows).

## Créditos

- [RNNoise](https://github.com/xiph/rnnoise) (BSD-3-Clause), a rede neural de supressão de ruído, via a `rnnoise.dll` do [pyrnnoise](https://github.com/pengzhendong/pyrnnoise) (Apache-2.0)
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) (MIT), [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) e PortAudio (MIT), NumPy (BSD)
- [VB-CABLE](https://vb-audio.com/Cable/) (VB-Audio, donationware), baixado do site oficial na hora da instalação, não vem junto
- Ideia e comportamento inspirados no [NoiseTorch](https://github.com/noisetorch/NoiseTorch); nenhum código dele foi usado
