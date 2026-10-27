# Calibração do motor local de separação de falantes (sherpa-onnx)

Registro da escolha do modelo de embedding e dos parâmetros padrão do motor local, feita com as reuniões
já gravadas. Complementa o [design](2026-10-27-diarizacao-local-sherpa-design.md). Os dados são anônimos:
as pessoas aparecem como "voz 1…6" e as reuniões como "reunião 1…10".

## Como foi medido

- **Amostra:** 10 reuniões, 6 vozes e 18 falantes ligados a essas vozes (voz 1 tem 9 falantes, voz 2 tem 2,
  voz 3 tem 3, voz 4 tem 1, voz 5 tem 2 e voz 6 tem 1).
- **Áudio:** só a faixa `pc` (a que passa pela separação de falantes), nos primeiros 20 min de cada
  reunião. Seis reuniões têm menos que isso (de 0,8 a 15,7 min); quatro chegam aos 20 min.
- **Referência:** os segmentos `pc` gravados, ou seja, os falantes que o pyannote encontrou. Não é
  gabarito humano.
- **Ferramenta:** `scripts/eval_diarization.py`, que lê o banco só em leitura. A parte numérica fica em
  `src/whisper_ditado/meeting/calibration.py`.

Duas medidas, uma para cada decisão:

1. `voices`: para cada modelo de embedding, o cosseno de cada falante ligado a uma voz contra a média
   (normalizada) das outras amostras da mesma voz (positivos) e contra a média de cada outra voz
   (negativos), em leave-one-out. Dela sai o limiar de reconhecimento de voz
   (`DEFAULT_LOCAL_VOICE_THRESHOLD`).
2. `clusters`: para cada reunião, roda o motor com `(modelo, limiar de cluster, fala mínima por falante)` e
   mede o `agreement` com a referência: fração do tempo de referência coberta por um falante da hipótese
   com o mesmo rótulo, depois de mapear rótulos um a um, de forma gulosa, pela maior sobreposição. Mostra a
   média simples entre reuniões, a média ponderada pela duração de fala e o erro médio no número de
   falantes (em módulo).

## Rodada `voices`: seis modelos e o pyannote como referência

16 positivos e 90 negativos por modelo. "Separação" é `min(pos) − max(neg)`; "limiar" é o de
`pick_threshold` (maximiza TPR − FPR, já que nenhum modelo separa por completo). "Casamento" são as
vozes reconhecidas certas, erradas e sem voz no limiar escolhido. O tempo é o de extrair os 18 embeddings.

| modelo | separação | min pos | max neg | AUC | limiar | TPR | FPR | casamento certos/errados/sem voz | tempo (s) |
|---|---|---|---|---|---|---|---|---|---|
| wespeaker_en_voxceleb_resnet34_LM | -0,146 | 0,814 | 0,961 | 0,908 | 0,89 | 0,94 | 0,22 | 11/2/5 | 53,0 |
| wespeaker_en_voxceleb_CAM++ | -0,375 | 0,568 | 0,943 | 0,651 | 0,61 | 0,88 | 0,62 | 4/10/4 | 24,1 |
| 3dspeaker_speech_campplus_sv_en_voxceleb_16k | -0,297 | 0,532 | 0,829 | 0,929 | 0,53 | 1,00 | 0,24 | 12/2/4 | 25,4 |
| 3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k | -0,322 | 0,357 | 0,679 | 0,962 | 0,69 | 0,81 | 0,00 | 12/0/6 | 89,5 |
| 3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common | -0,160 | 0,538 | 0,698 | 0,981 | 0,72 | 0,88 | 0,00 | 12/0/6 | 200,7 |
| nemo_en_titanet_small | -0,378 | 0,256 | 0,634 | 0,922 | 0,62 | 0,81 | 0,02 | 11/0/7 | 28,2 |
| pyannote (gravado, só referência) | -0,231 | 0,291 | 0,522 | 0,956 | 0,46 | 0,88 | 0,10 | 13/0/5 | n/d |

Nenhum modelo tem separação positiva: sempre existe algum par de vozes diferentes mais parecido que o
par de falas da mesma voz mais distante. Por isso o critério do plano ("maior separação") não discrimina
entre eles, e a escolha usou o `agreement` do clustering (ver abaixo).

## Rodada `clusters`

Grade: limiar de cluster 0,6 a 1,2 (passo 0,1) e fala mínima por falante de 0, 4, 8 e 12 s. A fala mínima
funde clusters pequenos no falante maior. As tabelas abaixo mostram as combinações mais relevantes de cada
modelo; a grade completa tem 28 linhas por modelo.

### wespeaker resnet34

| limiar | fala mín. (s) | agreement médio | agreement ponderado | erro nº falantes |
|---|---|---|---|---|
| 0,8 | 12 | 0,717 | 0,785 | 0,70 |
| 0,9 | 12 | 0,718 | 0,783 | 1,00 |
| 1,0 | 0 a 12 | 0,722 | 0,787 a 0,788 | 1,00 |
| 1,1 | qualquer | **0,725** | **0,796** | 1,10 |
| 1,2 | qualquer | 0,725 | 0,796 | 1,10 |

Melhor: limiar 1,1, sem fusão (agreement 0,725; erro 1,10). A fala mínima quase não faz diferença, porque
o resnet34 já produz poucos clusters.

### nemo titanet_small

| limiar | fala mín. (s) | agreement médio | agreement ponderado | erro nº falantes |
|---|---|---|---|---|
| 0,6 | 0 | 0,517 | 0,566 | 21,40 |
| 0,6 | 12 | 0,597 | 0,618 | 1,20 |
| 0,8 | 12 | 0,686 | 0,743 | 0,70 |
| 0,9 | 12 | 0,714 | 0,781 | 0,40 |
| 1,0 | 0 | 0,745 | 0,834 | 3,80 |
| 1,0 | 4 | 0,750 | 0,836 | 0,70 |
| 1,0 | 8 | 0,753 | 0,838 | 0,60 |
| **1,0** | **12** | **0,754** | **0,839** | 0,70 |
| 1,1 | 12 | 0,752 | 0,837 | 0,80 |
| 1,2 | 12 | 0,731 | 0,809 | 1,10 |

Melhor: limiar 1,0, fala mínima 12 s (agreement 0,754; erro 0,70). Sem fusão, o titanet gera muitos
clusters pequenos (erro de 21 falantes em 0,6 e de 3,8 em 1,0), então a fala mínima é necessária.

### Extensão: fala mínima acima de 12 s

Para ver se a melhora continuava, rodou-se o titanet a 1,0 com 12, 16 e 20 s.

| limiar | fala mín. (s) | agreement médio | agreement ponderado | erro nº falantes |
|---|---|---|---|---|
| 1,0 | 12 | 0,754 | 0,839 | 0,70 |
| 1,0 | 16 | 0,754 | 0,839 | 0,70 |
| 1,0 | 20 | 0,754 | 0,839 | 0,70 |

Os três resultados são idênticos: a partir de 12 s há um platô.

### Por reunião (limiar 1,0, fala mínima 12 s)

| reunião | duração analisada | falantes na referência | titanet: falantes | titanet: agreement | resnet34: falantes | resnet34: agreement |
|---|---|---|---|---|---|---|
| reunião 1 | 0,8 min | 1 | 0 | 0,000 | 0 | 0,000 |
| reunião 2 | 20,0 min | 3 | 2 | 0,812 | 1 | 0,733 |
| reunião 3 | 7,7 min | 3 | 3 | 0,783 | 1 | 0,684 |
| reunião 4 | 20,0 min | 1 | 1 | 0,874 | 1 | 0,872 |
| reunião 5 | 6,9 min | 1 | 1 | 0,789 | 1 | 0,763 |
| reunião 6 | 3,7 min | 1 | 1 | 0,843 | 1 | 0,843 |
| reunião 7 | 7,8 min | 1 | 1 | 0,871 | 1 | 0,871 |
| reunião 8 | 20,0 min | 6 | 2 | 0,837 | 2 | 0,723 |
| reunião 9 | 20,0 min | 2 | 1 | 0,867 | 1 | 0,867 |
| reunião 10 | 15,7 min | 1 | 1 | 0,862 | 1 | 0,864 |

## Escolha

| parâmetro | valor | onde |
|---|---|---|
| modelo de embedding | `nemo_en_titanet_small` | `DEFAULT_EMBEDDING` em `local_diarize.py` |
| limiar de cluster | 1,0 | `DEFAULT_CLUSTER_THRESHOLD` em `local_diarize.py` |
| fala mínima por falante | 12 s | `MIN_SPEAKER_S` em `local_diarize.py` |
| limiar de voz conhecida | 0,62 | `DEFAULT_LOCAL_VOICE_THRESHOLD` em `settings.py` |

Valores gravados pelo usuário continuam valendo: os padrões só se aplicam sem linha no banco.

**Por que o titanet_small, e um desvio do plano.** O plano mandava escolher o modelo pela maior separação
da rodada `voices`. Como todas as separações são negativas, esse critério não decide nada de útil, e a
escolha foi feita pelo `agreement` do clustering, que é o que o usuário sente ao ler a transcrição:

- Melhor `agreement`: 0,754 de média e 0,839 ponderado, contra 0,725 e 0,796 do resnet34, o segundo melhor.
  A diferença aparece sobretudo nas reuniões com mais de um falante (reuniões 2, 3 e 8).
- FPR baixo no reconhecimento de voz: 0,02 no limiar 0,62, com nenhuma voz errada (11/0/7). Para
  nomear uma pessoa automaticamente, errar é pior que deixar sem voz. O resnet34 erra 2 de 18 e tem FPR 0,22.
- Entre os mais rápidos: 28 s para extrair os embeddings, contra 53 s do resnet34 e 200 s do eres2netv2.
  Como o motor roda na CPU, isso pesa.

**Limiar de cluster e fala mínima.** 1,0/12 s e 1,0/8 s diferem em 0,001 de `agreement`
(0,754 contra 0,753). Ficou 12 s por ter o maior `agreement` e porque a extensão mostrou platô acima
disso, então o valor não é uma borda frágil. O limiar 1,0 também é o centro do melhor trecho da grade
(1,1 dá 0,752 e 1,2 cai para 0,731).

**Limiar de voz 0,62.** É o valor de `pick_threshold` para o titanet_small (TPR 0,81, FPR 0,02). Como os
positivos e negativos se sobrepõem, o limiar maximiza TPR − FPR em vez de ser um ponto médio. Ele deixa 7
dos 18 falantes sem voz reconhecida, o que é intencional: o usuário vê "falante N" e liga manualmente.

## Limitações

- **Referência não é gabarito.** O `agreement` mede concordância com o pyannote, não com a verdade. Onde
  o pyannote errou o número de falantes, a nota pune o motor local por discordar dele. Os resultados dizem
  qual modelo se parece mais com o pyannote, não qual acerta mais.
- **Amostra pequena.** São 10 reuniões, 6 vozes e 18 falantes ligados, com 16 positivos e 90 negativos. Seis
  reuniões têm menos de 20 min e a voz 1 concentra metade dos falantes. As diferenças de 0,01 a 0,03 de
  `agreement` estão dentro do que outra amostra poderia inverter.
- **Reunião 1 sem falantes.** Tem 0,8 min de áudio e a segmentação do sherpa não achou fala suficiente
  ("No speakers found"), então o `agreement` é 0 com qualquer parâmetro e qualquer modelo. Isso puxa a
  média simples para baixo em cerca de 0,08 (a ponderada quase não sente). Reuniões muito curtas podem ficar sem
  falantes no motor local.
- **Subestima o número de falantes.** Nas reuniões 2 e 8 (3 e 6 falantes na referência) o motor achou 2.
  Na reunião 9 também (2 na referência, 1 no motor). Em seis reuniões o número bate; a exceção restante é a
  reunião 1. Falantes com pouca fala são fundidos no maior pela fala
  mínima de 12 s.
- **Só os primeiros 20 min.** A calibração não cobre reuniões longas, onde o clustering pode se comportar
  diferente. Uma reunião de 31 min levou ~150 s com 4 threads na sondagem do planejamento.
- **Só faixa `pc`, e só áudio gravado por este usuário.** Outro microfone, idioma ou sala pode mudar os
  limiares. Os modelos `en` (titanet, wespeaker) foram treinados em inglês; o efeito disso sobre áudio em outro idioma
  não foi medido.
- **Reproduzir:** `uv run --frozen python scripts/eval_diarization.py voices` e
  `... clusters --embedding <nome>`. Ambos exigem o banco e as gravações locais e levam de dezenas de
  segundos (`voices`) a dezenas de minutos (`clusters`).
- **Reuniões reprocessadas:** o script só usa como referência do pyannote os embeddings com
  `embedding_model` vazio ou igual a `pyannote-community-1`, e `clusters` pula (e informa quantas) as
  reuniões que já foram reprocessadas com o motor local, porque os segmentos `pc` delas deixaram de ser do
  pyannote. Os números acima vieram de um banco sem a coluna `embedding_model` (ainda sem reprocessamentos
  locais), então nenhuma reunião foi pulada.
