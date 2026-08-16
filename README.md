# SentCrypto

**Análise de sentimento do mercado de criptomoedas com IA (BERT) aplicada a redes sociais.**

Projeto de Trabalho de Conclusão de Curso. O sistema coleta publicações do X
(Twitter) e do Reddit, classifica o sentimento de cada texto com um modelo BERT,
cruza esse sentimento com a variação de preço obtida na Binance e mede se o
humor das redes sociais antecede o movimento do mercado.

---

## Sumário

- [Como funciona](#como-funciona)
- [Arquitetura](#arquitetura)
- [Instalação](#instalação)
- [Executando](#executando)
- [Métricas](#métricas)
- [Endpoints da API](#endpoints-da-api)
- [Configuração](#configuração)
- [Segurança](#segurança)
- [Limitações conhecidas](#limitações-conhecidas)

---

## Como funciona

```
┌──────────────┐   ┌─────────────┐   ┌──────────────┐   ┌─────────────┐
│  Coleta      │   │  Filtro de  │   │  Classifica- │   │ Persistência│
│  X / Reddit  │──▶│ relevância  │──▶│  ção (BERT)  │──▶│   SQLite    │
└──────────────┘   │   cripto    │   └──────────────┘   └──────┬──────┘
                   └─────────────┘                             │
┌──────────────┐                                               ▼
│   Binance    │────────────────────────────────────▶ ┌─────────────────┐
│ preço (1h)   │                                      │   Correlação    │
└──────────────┘                                      │ sentimento×preço│
                                                      └────────┬────────┘
                                                               ▼
                                              ┌────────────────────────────┐
                                              │ Dashboard React + PDF      │
                                              └────────────────────────────┘
```

1. **Coleta** — posts do X (via cookies de sessão, syndication ou API v2) e do
   Reddit (endpoints JSON públicos).
2. **Filtro de relevância** — descarta textos que não falam de cripto. Eles são
   salvos com `sentimento = "nulo"` para auditoria, mas ficam fora das métricas.
3. **Classificação** — o BERT atribui de 1 a 5 estrelas, traduzidas em
   `negativo` / `neutro` / `positivo` e num índice numérico de 0 a 1.
4. **Agregação** — os posts são agrupados por hora cheia (UTC).
5. **Correlação** — cada hora é comparada com o candle correspondente da
   Binance para verificar se o sentimento previu a direção do preço.
6. **Saída** — dashboard interativo e relatórios PDF.

---

## Arquitetura

```
backend/
  app.py                  Rotas HTTP (camada fina: valida, chama serviço, responde)
  config.py               Configuração central lida do .env
  db.py                   Engine e sessão do SQLAlchemy
  models.py               Tabelas market_points e social_posts
  schemas.py              Validação das entradas (Pydantic)
  setup_db.py             Criação e migração do banco
  services/
    sentimento.py         BERT, mapeamento de labels, filtro de relevância
    mercado.py            Binance, candles, sentimento derivado do preço
    correlacao.py         Agregação por hora e métricas de correlação
    posts.py              Persistência com deduplicação
    relatorios.py         Geração dos PDFs
  collectors/
    x_collector.py        Coleta do X (3 estratégias em cascata)
    reddit_collector.py   Coleta do Reddit
    cookie_auth.py        Extração de cookies do navegador
  utils/
    tempo.py              Normalização de datas (tudo em UTC)
    moedas.py             Reconhecimento de menções a cada moeda

frontend/
  src/
    App.js                Dashboard
    api.js                Cliente HTTP da API
    format.js             Formatação de datas e números
    App.css               Estilos
```

**Convenção de tempo:** todo `datetime` é armazenado em **UTC sem tzinfo**. O
SQLite descarta o fuso ao gravar, então normalizar na entrada e remarcar na
saída (`utils/tempo.py`) é o que mantém banco, gráficos e PDF concordando entre si.

---

## Instalação

**Pré-requisitos:** Python 3.11+, Node.js 18+.

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/macOS

pip install -r requirements.txt

copy .env.example .env         # Windows  (cp no Linux/macOS)
python setup_db.py
```

> O primeiro `uvicorn` baixa o modelo BERT (~700 MB) do Hugging Face. Isso
> acontece só uma vez; depois ele fica em cache local.

### Frontend

```bash
cd frontend
npm install
copy .env.example .env         # opcional; o padrão já aponta para localhost
```

---

## Executando

Dois terminais:

```bash
# Terminal 1 — API em http://127.0.0.1:8000
cd backend
venv\Scripts\activate
uvicorn app:app --reload
```

```bash
# Terminal 2 — dashboard em http://localhost:3000
cd frontend
npm start
```

A documentação interativa da API fica em <http://127.0.0.1:8000/docs>.

### Fluxo de uso

1. Abra o dashboard e confirme que **API Online** está verde.
2. Se for coletar do X, clique em **Configurar** e informe os cookies
   `auth_token` e `ct0` (instruções no próprio modal).
3. Escolha a moeda e a fonte (**Reddit** ou **X / Twitter**).
4. Clique em **Coletar Reddit** ou **Analisar e salvar**.
5. O gráfico de sentimento aparece; clique numa barra para gerar o PDF daquela
   hora, ou use **Gerar PDF** na seção de correlação.

---

## Credenciais do Reddit

Sem credenciais, a coleta usa os endpoints públicos — que o Reddit limita por
IP e hoje recusa com **HTTP 403** na maioria das tentativas. Com um app
registrado, a coleta passa a usar OAuth (~100 requisições por minuto).

Leva dois minutos:

1. Acesse <https://www.reddit.com/prefs/apps> logado
2. Clique em **"are you a developer? create an app..."**
3. Preencha — o tipo **precisa** ser `script`:

   | Campo | Valor |
   |-------|-------|
   | name | `sentcrypto-tcc` |
   | tipo | **script** |
   | redirect uri | `http://localhost:8000` |

4. Clique em **create app**
5. No `backend/.env`:

   ```bash
   REDDIT_CLIENT_ID=<sequência sob o nome do app, à esquerda>
   REDDIT_CLIENT_SECRET=<campo "secret">
   REDDIT_USER_AGENT=python:sentcrypto-tcc:1.0 (by /u/seu_usuario)
   ```

Confira em <http://127.0.0.1:8000/status/reddit> — deve responder
`"modo": "oauth"`. Se as credenciais estiverem erradas, o sistema registra o
erro e volta ao modo anônimo em vez de falhar.

---

## Coleta contínua

O botão de coleta automática do dashboard depende do navegador: ele só roda
enquanto a aba estiver aberta na fonte "X". Para acumular dados por dias ou
semanas — que é o que a análise exige — use o coletor dedicado:

```bash
cd backend

# Coleta a cada 20 minutos; Ctrl+C encerra com segurança
python tools/coletor_continuo.py --intervalo 20

# Uma rodada só (para o Agendador de Tarefas do Windows ou cron)
python tools/coletor_continuo.py --once

# Escolhendo moedas e perfis
python tools/coletor_continuo.py --moedas BTC ETH --perfis whale_alert CoinDesk
```

Ele grava em `backend/logs/coleta.log` (rotativo) e mostra a cada rodada
quantas **horas distintas** a base já cobre — esse é o número que limita a
amostra da correlação, não a quantidade de posts. Rodadas frequentes são
seguras: posts já salvos são descartados antes de chegar ao modelo.

> Para maximizar a cobertura, prefira **mais perfis** a intervalos mais
> curtos. O que amplia a amostra é ter publicações em horas diferentes do dia,
> e um único perfil deixa muitas horas vazias.

---

## Testes

```bash
cd backend
python -m pytest tests/ -v      # 43 testes
```

```bash
cd frontend
npm test                        # CI=true npm test para rodar sem watch
```

A suíte cobre justamente o que sustenta os números do trabalho: deduplicação
de posts e candles, normalização de fuso horário, filtro de relevância cripto,
mapeamento dos labels do modelo e o cálculo do Sentiment Score.

---

## Métricas

### Sentiment Score

Resume o humor de cada hora num único número:

```
SentimentScore = (Positivos − Negativos) / Total       →  [−1, +1]
```

`+1` = extremamente positivo · `0` = neutro · `−1` = extremamente negativo.

### Return After Sentiment

Mede se o sentimento tem relação com o que o preço fez **depois**:

```
Return = ((Preço_t+n − Preço_t) / Preço_t) × 100
```

Calculado nas janelas de **1h**, **4h** e **24h**.

### Taxa de acerto

Uma hora é *comparável* quando o sentimento não é neutro **e** o preço não ficou
estável. Nessas horas, conta-se acerto quando a direção do sentimento coincide
com a direção do preço.

> ⚠️ Com menos de 30 horas comparáveis a taxa não sustenta conclusão
> estatística. O sistema sinaliza isso no dashboard e no PDF (`amostra_suficiente`).

---

## Endpoints da API

| Método | Rota | Descrição |
|--------|------|-----------|
| GET  | `/` | Estado da API, do BERT e dos cookies |
| GET  | `/status/twitter` | Situação dos cookies do X |
| POST | `/login/x` | Salva os cookies de sessão do X |
| GET  | `/sentimento` | Sentimento do último candle |
| GET  | `/historico-sentimento` | Preço + sentimento ao vivo (Binance) |
| GET  | `/historico-db` | Histórico salvo no banco |
| POST | `/sync-binance` | Baixa N dias de candles para o banco |
| GET  | `/historico-social` | Sentimento social agregado por hora (com preço) |
| GET  | `/posts-por-hora` | Posts individuais de uma hora |
| GET  | `/correlacao` | Métricas de correlação sentimento × preço |
| POST | `/gerar-relatorio` | PDF dos posts de uma hora |
| POST | `/gerar-relatorio-correlacao` | PDF da análise de correlação |
| POST | `/coletar/reddit` | Coleta e classifica posts do Reddit |
| POST | `/coletar/x` | Coleta e classifica tweets |
| POST | `/feed/x` | Timeline do X com sentimento (não grava) |
| POST | `/analisar-texto` | Classifica um texto avulso |

---

## Configuração

Tudo é ajustável pelo `backend/.env` (veja `.env.example` para a lista completa):

| Variável | Padrão | Efeito |
|----------|--------|--------|
| `BERT_MODEL_NAME` | `nlptown/bert-base-...` | Modelo de sentimento |
| `LIMIAR_VARIACAO_CANDLE` | `0.005` | Variação para o candle ser alta/baixa |
| `LIMIAR_SENTIMENT_SCORE` | `0.1` | Score mínimo para a hora não ser neutra |
| `CORS_ORIGINS` | `localhost:3000` | Origens autorizadas |
| `ADMIN_TOKEN` | *(vazio)* | Protege `/login/x`; **defina se expuser a API** |
| `MOEDAS_SUPORTADAS` | 8 moedas | Moedas aceitas |

No frontend, `REACT_APP_API_URL` define o endereço do backend.

---

## Segurança

- **Nunca versione** `backend/.env` nem `backend/x_cookies.json`. O
  `.gitignore` já cobre os dois, mas confira antes de qualquer commit.
- `x_cookies.json` guarda uma **sessão ativa** do X: quem tiver esse arquivo
  age como você na plataforma, sem precisar de senha ou 2FA. Se ele vazar,
  revogue a sessão em *X → Configurações → Segurança → Sessões ativas*.
- Ao expor a API fora de `localhost`, defina `ADMIN_TOKEN` no `.env` — sem ele
  a rota `/login/x` fica aberta e qualquer um pode sobrescrever seus cookies.

---

## Comparação de modelos

O projeto inclui um benchmark que roda vários modelos sobre **a mesma base de
posts e os mesmos preços**, de forma que qualquer diferença venha só do modelo:

```bash
cd backend
python tools/benchmark_modelos.py --moeda BTC --fonte X
```

Resultado com a base atual (109 posts relevantes, BTC/X):

| Modelo | Taxa de acerto | n | IC 95% | p |
|--------|---------------:|--:|--------|--:|
| `ProsusAI/finbert` | 50,0% | 24 | [31,4% – 68,6%] | 1,000 |
| `lucas-leme/FinBERT-PT-BR` | 40,0% | 10 | [16,8% – 68,7%] | 0,754 |
| `nlptown/...-sentiment` (atual) | 36,7% | 49 | [24,7% – 50,7%] | 0,085 |
| `cardiffnlp/twitter-xlm-roberta` | 25,0% | 16 | [10,2% – 49,5%] | 0,077 |

**Nenhum modelo difere estatisticamente do acaso** (todos com p > 0,05, e todos
os intervalos de confiança contendo ou encostando em 50%). Com no máximo 49
horas comparáveis, a amostra não permite nem distinguir os modelos entre si nem
afirmar que algum antecipa o preço.

Um ponto que os dados mostram com clareza: o modelo atual rotula **77 dos 109
posts como negativos (71%)**, enquanto os modelos financeiros classificam a
maioria como neutro (81 a 87). Para uma timeline dominada por tweets factuais
(`whale_alert`), 71% de negatividade indica viés sistemático do modelo de
avaliações, não pessimismo real do mercado.

---

## Limitações conhecidas

1. **O modelo BERT não é especializado em finanças.**
   `nlptown/bert-base-multilingual-uncased-sentiment` foi treinado em
   avaliações de produtos (1 a 5 estrelas). Em textos financeiros ele erra com
   frequência — classifica "bitcoin subindo forte" como negativo, com confiança
   baixa (~0,38), e rotula 71% da base como negativa. Trocar o modelo é
   questão de mudar `BERT_MODEL_NAME` no `.env`, mas o benchmark acima mostra
   que, com a amostra atual, a troca não é sustentada por evidência.

2. **Amostra insuficiente para conclusão estatística.** Este é o limite
   principal do trabalho hoje. Com ~50 horas comparáveis, o intervalo de
   confiança da taxa de acerto tem cerca de 26 pontos percentuais de largura.
   Para reduzi-lo a ±5 pontos seriam necessárias cerca de 400 horas
   comparáveis — o que significa coletar de forma contínua por várias semanas.

3. **Coleta do X é frágil por natureza.** Depende de cookies de sessão que
   expiram e de endpoints não oficiais que podem mudar sem aviso. As três
   estratégias em cascata mitigam, mas não eliminam o problema.

4. **Coleta do Reddit exige credenciais.** O acesso anônimo é limitado por IP
   e hoje retorna HTTP 403 na maioria das tentativas — na prática, o modo
   anônimo não sustenta coleta contínua. Registre um app e preencha
   `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` (veja
   [Credenciais do Reddit](#credenciais-do-reddit)).

5. **Correlação não é causalidade.** A taxa de acerto mede coincidência de
   direção, não relação causal. Preço de cripto responde a muitos fatores fora
   do escopo deste trabalho.

6. **Janela de preço.** A consulta à Binance cobre no máximo 180 dias para trás;
   posts mais antigos que isso ficam sem preço correspondente.
