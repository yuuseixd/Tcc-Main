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
| GET  | `/risco-moedas` | Volatilidade e queda máxima de cada moeda em 365 dias (aberta) |
| GET  | `/comparacao-fontes` | Compara X e Reddit (concordância, correlação, série horária) |
| POST | `/simulacao/investimento` | Backtest de compra e venda (FOMO ou Flat; fonte X ou Reddit) |
| POST | `/auth/cadastro` | Cria a conta e entra |
| POST | `/auth/login` | Entra (limite de tentativas por e-mail e IP) |
| POST | `/auth/logout` | Encerra a sessão |
| GET  | `/auth/eu` | Dados da conta logada |
| GET  | `/perfil/questionario` | Perguntas do questionário de perfil (aberta) |
| POST | `/perfil/questionario` | Calcula o perfil sugerido |
| POST | `/perfil/confirmar` | Grava o perfil escolhido |

**Quem precisa de conta.** Só a Visão geral é aberta (`/`, `/status/*`,
`/sentimento`, `/risco-moedas`, `/historico-*`, `/correlacao`). Todo o resto (simulação,
coleta, feed, comparação, relatórios em PDF, análise de texto, sincronização)
exige o cabeçalho `Authorization: Bearer <token>` e responde 401 sem ele.

---

## Contas e perfil de investimento

- **Cadastro** com nome, e-mail e senha (mínimo de 8 caracteres). A senha nunca
  é guardada: só o hash `scrypt` com sal. A sessão usa um token aleatório; o
  banco guarda só o SHA-256 dele, e ele expira em 7 dias ou no logout.
- **Login** devolve a mesma mensagem para e-mail inexistente e senha errada, e
  bloqueia por 10 minutos após 5 falhas (contagem em memória: reiniciar o
  servidor a zera).
- **Perfil**: o questionário tem 11 perguntas (`backend/services/perfil.py`),
  cada uma ligada a um item do art. 3º da **Resolução CVM 30/2021**: objetivos
  (§ 1º: período, preferência de risco, finalidade), situação financeira (§ 2º:
  receitas, patrimônio, necessidade futura de recursos) e conhecimento (§ 3º:
  familiaridade com produtos, frequência de operações, formação e experiência).
  Um teste garante que os 9 itens continuam cobertos. A soma vai de 11 a 44
  pontos, dividida em terços (Conservador até 22, Moderado até 33, Arrojado
  acima). O sistema **sugere** e a pessoa **confirma ou ajusta**.
- **Travas**: não ter reserva de emergência, precisar do dinheiro em breve e não
  ter experiência alguma são fragilidades que uma boa pontuação não esconde. Uma
  limita o perfil a Moderado; duas ou mais, a Conservador. A ANBIMA passou a
  considerar o baixo conhecimento no perfil conservador em 05/09/2023.
- **Risco das moedas** (`/risco-moedas`): medido nos preços diários da Binance
  dos últimos 365 dias, não escrito à mão. A volatilidade anualizada (desvio
  padrão dos retornos logarítmicos diários × √365) define a classe: até 50% ao
  ano é baixo, até 70% é médio, acima é alto. Os cortes são uma escolha do
  projeto, e a classificação é relativa entre criptomoedas (todas oscilam muito
  mais que a renda fixa). Medição de 20/10/2025 a 19/10/2026:

  | Moeda | Volatilidade ao ano | Queda máxima | Risco |
  |-------|--------------------:|-------------:|-------|
  | BTC | 44,0% | -48,8% | baixo |
  | ETH | 61,0% | -62,3% | médio |
  | SOL | 63,9% | -68,9% | médio |
  | XRP | 64,2% | -62,5% | médio |
  | DOGE | 66,3% | -66,4% | médio |
  | LINK | 66,6% | -61,4% | médio |
  | AVAX | 72,5% | -71,7% | alto |
  | ADA | 75,3% | -78,9% | alto |

  Os números mudam com o mercado (cache de 6 horas). A primeira versão do
  projeto classificava o DOGE como o de maior risco por palpite; a medição
  mostrou que ADA e AVAX oscilaram mais.
- **O que o perfil muda**: o Simulador abre com meta, queda e stop-loss do
  perfil; aparece um aviso, com os números medidos, ao escolher moedas de risco
  acima do que o perfil tolera (Conservador até baixo, Moderado até médio,
  Arrojado todas); e a Visão geral mostra uma leitura adaptada.
- **Fundamentação**: Resolução CVM 30, art. 3º e 4º
  (<https://conteudo.cvm.gov.br/legislacao/resolucoes/resol030.html>); o art. 4º
  manda classificar o cliente em categorias previamente estabelecidas, sem
  dizer quais. ANBIMA, regras de suitability de 05/09/2023, que citam os perfis
  conservador, moderado e arrojado e criam regras para produtos com criptoativos
  (<https://www.anbima.com.br/pt_br/noticias/suitability-novas-regras-da-anbima-para-definir-perfis-de-investidor-e-de-produtos-ja-estao-valendo.htm>).
  A pontuação de 1 a 4, os cortes em terços e as travas são **escolhas do
  projeto**; as normas não as definem.
- É um instrumento **educacional**, não recomendação de investimento. Criptomoedas
  nem sempre são "valores mobiliários" no sentido da Resolução 30.
- Limites assumidos: o token fica no `localStorage` do navegador (exposto a XSS)
  e o tráfego local não usa HTTPS. Para expor fora de `localhost`, use HTTPS.

---

## Configuração

Tudo é ajustável pelo `backend/.env` (veja `.env.example` para a lista completa):

| Variável | Padrão | Efeito |
|----------|--------|--------|
| `BERT_MODEL_NAME` | `cardiffnlp/twitter-roberta-base-sentiment-latest` | Modelo de sentimento |
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

### Troca de modelo (acurácia contra o sentimento correto)

O benchmark acima mede o acerto sobre o *preço*, um alvo ruidoso. Para escolher
o modelo, `tools/avaliar_modelos.py` compara cada um com o sentimento *correto*
de posts rotulados à mão. Resultado em `rotulagem/amostra_sanidade.csv`
(45 frases escritas pelos autores, 15 por classe — uma checagem inicial, não
substitui a base rotulada de posts reais):

| Modelo | Acurácia | IC 95% | F1 macro |
|--------|---------:|--------|---------:|
| `cardiffnlp/twitter-roberta-base-sentiment-latest` (atual) | 88,9% | [77% – 95%] | 0,892 |
| `ProsusAI/finbert` | 73,3% | [59% – 84%] | 0,735 |
| `ElKulako/cryptobert` | 73,3% | [59% – 84%] | 0,733 |
| `nlptown/...-sentiment` (anterior) | 57,8% | [43% – 71%] | 0,510 |

Nos 581 posts reais, o modelo anterior marcava 73% como negativos; o atual
marca 14% (63% neutros), o que combina com uma timeline de posts factuais.
Após reclassificar o banco (`tools/reclassificar.py --aplicar`), a taxa de
acerto sobre o preço passou a ser 54,5% (X, 11 horas comparáveis) e 50,0%
(Reddit, 6 horas). Esses números **não indicam poder preditivo**: o novo modelo
produz mais horas neutras, que não contam como previsão, e a amostra
comparável encolheu. O limite continua sendo a quantidade de dados.

Para a avaliação definitiva, use `tools/exportar_para_rotular.py --n 80` (gera
`rotulagem/posts_para_rotular.xlsx`, com lista suspensa para rotular), rotule em
duas pessoas, cada uma na sua coluna, e rode
`tools/avaliar_modelos.py --csv rotulagem/posts_para_rotular.xlsx`.

---

## Limitações conhecidas

1. **O modelo é de tweets em inglês, não de finanças.**
   `cardiffnlp/twitter-roberta-base-sentiment-latest` foi treinado em tweets
   (a maioria em inglês); o modelo anterior (avaliações de produtos) enviesava
   a base para negativo. A escolha foi validada só com a amostra de sanidade;
   a validação com posts reais rotulados ainda está pendente.

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
