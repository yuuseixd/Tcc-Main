import { useCallback, useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import "./App.css";
import { api, API_URL, ApiError } from "./api";
import {
  dataCurta,
  dataHoraCompleta,
  moeda as fmtMoeda,
  percentual,
  rotuloHora,
} from "./format";

const MOEDAS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "LINK"];

/** Valores padrão de compra/venda do simulador — usados ao marcar uma nova
 * moeda; cada moeda pode ajustá-los depois de forma independente. */
const SIM_DEFAULTS = {
  capital_inicial: 1000,
  valor_por_compra: 100,
  percentual_lucro_venda: 5,
  percentual_queda_compra: 3,
  percentual_perda_aceita: 10,
};

const SIM_CAMPOS = [
  { chave: "capital_inicial", rotulo: "Capital inicial (USD)", min: "1", step: "1" },
  { chave: "valor_por_compra", rotulo: "Valor/compra (USD)", min: "1", step: "1" },
  { chave: "percentual_lucro_venda", rotulo: "% Lucro", min: "0.1", step: "0.1" },
  { chave: "percentual_queda_compra", rotulo: "% Queda", min: "0.1", step: "0.1" },
  { chave: "percentual_perda_aceita", rotulo: "% Stop-loss", min: "0.1", step: "0.1" },
];

const FONTES = {
  api: { label: "Binance (ao vivo)", icon: "\u{1F4CA}" },
  db: { label: "Histórico (SQLite)", icon: "\u{1F4BE}" },
  reddit: { label: "Reddit", icon: "\u{1F534}" },
  x: { label: "X / Twitter", icon: "\u{1F426}" },
};

/** Fontes que representam redes sociais (têm sentimento e correlação). */
const FONTES_SOCIAIS = ["reddit", "x"];
const ehSocial = (f) => FONTES_SOCIAIS.includes(f);
const nomeFonteApi = (f) => (f === "x" ? "X" : "Reddit");

const SUBREDDITS_DEFAULT = {
  BTC: ["Bitcoin", "CryptoCurrency", "BitcoinMarkets"],
  ETH: ["ethereum", "ethtrader", "CryptoCurrency"],
  SOL: ["solana", "CryptoCurrency"],
  DOGE: ["dogecoin", "CryptoCurrency"],
  XRP: ["XRP", "Ripple", "CryptoCurrency"],
  ADA: ["cardano", "CryptoCurrency"],
  AVAX: ["Avax", "CryptoCurrency"],
  LINK: ["Chainlink", "CryptoCurrency"],
};

const INTERVALOS_COLETA = [
  { valor: 0, rotulo: "Desativada" },
  { valor: 3, rotulo: "A cada 3 min" },
  { valor: 5, rotulo: "A cada 5 min" },
  { valor: 10, rotulo: "A cada 10 min" },
  { valor: 15, rotulo: "A cada 15 min" },
  { valor: 30, rotulo: "A cada 30 min" },
];

const listaDePerfis = (texto) =>
  texto
    .split(",")
    .map((p) => p.trim().replace(/^@/, ""))
    .filter(Boolean);

/** Ignora o erro disparado quando cancelamos uma requisição de propósito. */
const foiCancelada = (e) => e?.name === "AbortError";

/** Cabeçalho clicável para recolher/expandir uma seção (tabelas longas). */
function CollapseToggle({ aberto, onToggle, rotulo, contagem }) {
  return (
    <button
      type="button"
      className="collapse-toggle"
      onClick={onToggle}
      aria-expanded={aberto}
    >
      <span>
        {rotulo}
        {contagem != null && ` (${contagem})`}
      </span>
      <span className="collapse-toggle__icone">{aberto ? "▲" : "▼"}</span>
    </button>
  );
}

function App() {
  const [moeda, setMoeda] = useState("BTC");
  const [fonte, setFonte] = useState("api");
  const [historico, setHistorico] = useState([]);
  const [sentimento, setSentimento] = useState(null);
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState(null);
  const [apiOnline, setApiOnline] = useState(null);
  const [twitterOk, setTwitterOk] = useState(false);

  const [feedTweets, setFeedTweets] = useState([]);
  const [feedLoading, setFeedLoading] = useState(false);
  const [perfisX, setPerfisX] = useState(
    () =>
      localStorage.getItem("sentcrypto_perfisX") ||
      "whale_alert, cabortopcripto",
  );
  const [feedDataInicio, setFeedDataInicio] = useState("");
  const [feedDataFim, setFeedDataFim] = useState("");

  // ── Simulador de investimento ─────────────────────────────────────
  // Moedas simuladas de forma independente da moeda global do dashboard —
  // o usuário pode comparar várias de uma vez.
  // "fomo": compra depende do sentimento dos tweets já coletados;
  // "flat": ignora sentimento, compra/vende só pela variação de preço.
  const [simModo, setSimModo] = useState("fomo");
  const [simMoedas, setSimMoedas] = useState(() => ["BTC"]);
  const [simDataInicio, setSimDataInicio] = useState("");
  const [simDataFim, setSimDataFim] = useState("");
  // Capital/valor/percentuais são configuráveis por moeda — cada uma pode
  // ter sua própria estratégia. { [moeda]: { capital_inicial, ... } }.
  const [simParamsPorMoeda, setSimParamsPorMoeda] = useState(() => ({
    BTC: { ...SIM_DEFAULTS },
  }));
  const [simulando, setSimulando] = useState(false);
  // { [moeda]: resultado } — uma simulação independente por moeda marcada.
  const [simResultados, setSimResultados] = useState(null);

  const paramsDaMoeda = (m) => simParamsPorMoeda[m] || SIM_DEFAULTS;

  const alternarSimMoeda = (m) => {
    setSimMoedas((atual) =>
      atual.includes(m) ? atual.filter((x) => x !== m) : [...atual, m],
    );
    // Ao marcar uma moeda nova, já dá pra editar seus parâmetros; se ela já
    // tinha valores de uma marcação anterior, mantém (evita perder ajustes
    // ao desmarcar/remarcar sem querer).
    setSimParamsPorMoeda((atual) =>
      atual[m] ? atual : { ...atual, [m]: { ...SIM_DEFAULTS } },
    );
  };

  const atualizarParamSimMoeda = (m, campo, valor) =>
    setSimParamsPorMoeda((atual) => ({
      ...atual,
      [m]: { ...(atual[m] || SIM_DEFAULTS), [campo]: valor },
    }));

  const [textoAnalise, setTextoAnalise] = useState("");
  const [resultadoAnalise, setResultadoAnalise] = useState(null);
  const [analisando, setAnalisando] = useState(false);

  const [coletando, setColetando] = useState(false);
  const [coletaMsg, setColetaMsg] = useState(null);
  // Aviso de fallback: quando a coleta autenticada (twikit) falha e o
  // sistema usa um método com cobertura mais limitada (ver x_collector.py).
  const [avisoMsg, setAvisoMsg] = useState(null);

  const [autoRefresh, setAutoRefresh] = useState(true);

  const [showLoginModal, setShowLoginModal] = useState(false);
  const [loginAuthToken, setLoginAuthToken] = useState("");
  const [loginCt0, setLoginCt0] = useState("");
  const [salvandoLogin, setSalvandoLogin] = useState(false);

  const [autoCollectInterval, setAutoCollectInterval] = useState(() =>
    parseInt(localStorage.getItem("sentcrypto_autoCollect") || "0", 10),
  );

  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [correlacao, setCorrelacao] = useState(null);
  const [correlacaoLoading, setCorrelacaoLoading] = useState(false);
  const [gerandoPdf, setGerandoPdf] = useState(false);
  const [gerandoPdfCorr, setGerandoPdfCorr] = useState(false);

  // Seções recolhíveis (tabela de correlação, tabelas de trades por moeda).
  // Guarda só os ids FECHADOS — por padrão tudo começa expandido.
  const [secoesFechadas, setSecoesFechadas] = useState(() => new Set());
  const alternarSecao = (id) =>
    setSecoesFechadas((atual) => {
      const novo = new Set(atual);
      if (novo.has(id)) novo.delete(id);
      else novo.add(id);
      return novo;
    });
  const secaoAberta = (id) => !secoesFechadas.has(id);

  const [dataInicio, setDataInicio] = useState("");
  const [dataFim, setDataFim] = useState("");
  const [sincronizando, setSincronizando] = useState(false);

  // Cancela a requisição anterior quando os filtros mudam. Sem isso, uma
  // resposta lenta de uma moeda antiga podia sobrescrever os dados da moeda
  // recém-selecionada.
  const abortRef = useRef(null);
  const abortCorrRef = useRef(null);

  useEffect(() => {
    localStorage.setItem("sentcrypto_perfisX", perfisX);
  }, [perfisX]);

  useEffect(() => {
    localStorage.setItem("sentcrypto_autoCollect", String(autoCollectInterval));
  }, [autoCollectInterval]);

  // ── Carregamento de dados ──────────────────────────────────────────

  const checkApiHealth = useCallback(async () => {
    try {
      const dados = await api.saude();
      setApiOnline(dados.status === "ok");
      setTwitterOk(Boolean(dados.twitter_cookies));
    } catch {
      setApiOnline(false);
    }
  }, []);

  const carregarDados = useCallback(async (m, f, di, df) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;

    setLoading(true);
    setErro(null);

    try {
      let pontos = [];

      if (f === "api") {
        pontos = (await api.historicoBinance(m, di, df, signal)).pontos || [];
      } else if (f === "db") {
        pontos = (await api.historicoDb(m, di, df, signal)).pontos || [];
      } else {
        // O preço da mesma hora já vem junto na resposta social, casado no
        // backend. Antes eram duas chamadas, e a segunda pedia "as últimas
        // 24h" e cruzava por "HH:MM" — chave que colide entre dias distintos.
        const social = await api.historicoSocial(
          m, nomeFonteApi(f), di, df, signal,
        );
        pontos = social.pontos || [];
      }

      setHistorico(
        pontos.map((ponto) => ({
          ...ponto,
          rotulo: rotuloHora(ponto.timestamp),
          timestamp_raw: ponto.timestamp,
        })),
      );

      try {
        setSentimento(await api.sentimento(m, signal));
      } catch (e) {
        if (foiCancelada(e)) throw e;
        setSentimento(null);
      }
    } catch (e) {
      if (foiCancelada(e)) return; // troca de filtro, não é falha
      setErro(
        e instanceof ApiError
          ? `Erro ao carregar dados: ${e.message}`
          : "Não foi possível falar com o backend. Ele está rodando?",
      );
      setHistorico([]);
    } finally {
      // Só o pedido mais recente pode desligar o indicador de carregamento.
      if (abortRef.current === controller) setLoading(false);
    }
  }, []);

  const carregarCorrelacao = useCallback(async (m, f) => {
    if (!ehSocial(f)) {
      setCorrelacao(null);
      return;
    }

    abortCorrRef.current?.abort();
    const controller = new AbortController();
    abortCorrRef.current = controller;

    setCorrelacaoLoading(true);
    try {
      setCorrelacao(
        await api.correlacao(m, nomeFonteApi(f), controller.signal),
      );
    } catch (e) {
      if (!foiCancelada(e)) setCorrelacao(null);
    } finally {
      if (abortCorrRef.current === controller) setCorrelacaoLoading(false);
    }
  }, []);

  useEffect(() => {
    checkApiHealth();
  }, [checkApiHealth]);

  useEffect(() => {
    carregarDados(moeda, fonte, dataInicio, dataFim);
  }, [moeda, fonte, dataInicio, dataFim, carregarDados]);

  useEffect(() => {
    carregarCorrelacao(moeda, fonte);
  }, [moeda, fonte, carregarCorrelacao]);

  // Cancela requisições pendentes ao desmontar.
  useEffect(
    () => () => {
      abortRef.current?.abort();
      abortCorrRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = setInterval(
      () => carregarDados(moeda, fonte, dataInicio, dataFim),
      60000,
    );
    return () => clearInterval(id);
  }, [autoRefresh, moeda, fonte, dataInicio, dataFim, carregarDados]);

  // ── Coleta ─────────────────────────────────────────────────────────

  const executarColeta = useCallback(
    async (f, { silencioso = false } = {}) => {
      const corpo =
        f === "x"
          ? {
              moeda,
              perfis: listaDePerfis(perfisX),
              limite_por_perfil: 100,
            }
          : {
              moeda,
              subreddits: SUBREDDITS_DEFAULT[moeda] || ["CryptoCurrency"],
              limite_por_sub: 25,
              ordenacao: "new",
            };

      if (f === "x" && corpo.perfis.length === 0) {
        setErro("Informe ao menos um perfil do X.");
        return;
      }

      if (!silencioso) setColetando(true);
      try {
        const dados =
          f === "x" ? await api.coletarX(corpo) : await api.coletarReddit(corpo);
        setColetaMsg(`${silencioso ? "[Auto] " : ""}${dados.mensagem}`);
        setAvisoMsg(
          dados.avisos && dados.avisos.length > 0
            ? dados.avisos.join(" · ")
            : null,
        );
        await carregarDados(moeda, f, dataInicio, dataFim);
        carregarCorrelacao(moeda, f);
      } catch (e) {
        if (!silencioso) {
          setErro(
            e instanceof ApiError
              ? `Falha na coleta: ${e.message}`
              : "Falha ao coletar. Verifique o backend.",
          );
        }
      } finally {
        if (!silencioso) setColetando(false);
      }
    },
    [moeda, perfisX, dataInicio, dataFim, carregarDados, carregarCorrelacao],
  );

  // Coleta automática periódica do X.
  useEffect(() => {
    if (autoCollectInterval <= 0 || fonte !== "x") return undefined;
    const id = setInterval(
      () => executarColeta("x", { silencioso: true }),
      autoCollectInterval * 60 * 1000,
    );
    return () => clearInterval(id);
  }, [autoCollectInterval, fonte, executarColeta]);

  const carregarFeedX = async () => {
    const perfis = listaDePerfis(perfisX);
    if (perfis.length === 0) {
      setErro("Informe ao menos um perfil do X.");
      return;
    }

    setFeedLoading(true);
    setErro(null);
    try {
      const dados = await api.feedX({
        perfis,
        limite_por_perfil: 100,
        data_inicio: feedDataInicio || undefined,
        data_fim: feedDataFim || undefined,
      });
      setFeedTweets(dados.tweets || []);
      if ((dados.tweets || []).length === 0 && !(dados.avisos || []).length) {
        setColetaMsg(
          feedDataInicio || feedDataFim
            ? "Nenhum tweet dentro do período informado (o X só traz os mais recentes)."
            : "Nenhum tweet retornado para esses perfis.",
        );
      }
      setAvisoMsg(
        dados.avisos && dados.avisos.length > 0
          ? dados.avisos.join(" · ")
          : null,
      );
    } catch (e) {
      setErro(`Falha ao carregar feed: ${e.message}`);
    } finally {
      setFeedLoading(false);
    }
  };

  // ── Simulador de investimento ─────────────────────────────────────

  const simularInvestimento = async () => {
    // No modo Flat, a compra depende só do preço — perfis não entram.
    const perfis = simModo === "fomo" ? listaDePerfis(perfisX) : [];
    if (simModo === "fomo" && perfis.length === 0) {
      setErro("Informe ao menos um perfil do X para simular no modo FOMO.");
      return;
    }
    if (simMoedas.length === 0) {
      setErro("Selecione ao menos uma moeda para simular.");
      return;
    }
    if (!simDataInicio || !simDataFim) {
      setErro(
        "Informe o período (de/até) da simulação — os campos ficam dentro " +
          "da seção do Simulador, separados do filtro de data do Feed.",
      );
      return;
    }
    for (const m of simMoedas) {
      const p = paramsDaMoeda(m);
      if (Number(p.valor_por_compra) > Number(p.capital_inicial)) {
        setErro(
          `${m}: o valor por compra não pode ser maior que o capital inicial.`,
        );
        return;
      }
    }

    setSimulando(true);
    setErro(null);
    try {
      // Cada moeda roda como uma simulação independente e isolada, com seus
      // próprios parâmetros — os posts e os candles são filtrados por moeda
      // no backend, então uma compra de ETH nunca entra no resultado do BTC
      // (ver services/simulacao.py). São backtests paralelos para
      // comparação, não um único portfólio compartilhado.
      const entradas = await Promise.all(
        simMoedas.map(async (m) => {
          const p = paramsDaMoeda(m);
          try {
            const resultado = await api.simularInvestimento({
              moeda: m,
              modo: simModo,
              perfis: simModo === "fomo" ? perfis : undefined,
              data_inicio: simDataInicio,
              data_fim: simDataFim,
              capital_inicial: Number(p.capital_inicial),
              valor_por_compra: Number(p.valor_por_compra),
              percentual_lucro_venda: Number(p.percentual_lucro_venda),
              percentual_queda_compra: Number(p.percentual_queda_compra),
              percentual_perda_aceita: Number(p.percentual_perda_aceita),
            });
            return [m, resultado];
          } catch (e) {
            return [
              m,
              {
                vazio: true,
                mensagem:
                  e instanceof ApiError
                    ? `Falha na simulação de ${m}: ${e.message}`
                    : `Falha ao simular ${m}.`,
              },
            ];
          }
        }),
      );

      const resultados = Object.fromEntries(entradas);
      setSimResultados(resultados);

      const vazias = entradas.filter(([, r]) => r.vazio);
      if (vazias.length === entradas.length) {
        setColetaMsg(vazias[0][1].mensagem);
      }
    } catch (e) {
      setErro("Falha ao simular. Verifique o backend.");
    } finally {
      setSimulando(false);
    }
  };

  const analisarTexto = async () => {
    if (!textoAnalise.trim()) return;
    setAnalisando(true);
    setResultadoAnalise(null);
    try {
      setResultadoAnalise(await api.analisarTexto(textoAnalise, moeda));
    } catch (e) {
      setErro(`Falha ao analisar texto: ${e.message}`);
    } finally {
      setAnalisando(false);
    }
  };

  const salvarCookiesTwitter = async () => {
    setSalvandoLogin(true);
    try {
      await api.loginX(loginAuthToken, loginCt0);
      setTwitterOk(true);
      setShowLoginModal(false);
      setLoginAuthToken("");
      setLoginCt0("");
      setColetaMsg("Cookies do X salvos com sucesso!");
    } catch (e) {
      setErro(`Falha ao salvar cookies: ${e.message}`);
    } finally {
      setSalvandoLogin(false);
    }
  };

  const sincronizarBinance = async () => {
    setSincronizando(true);
    try {
      const dados = await api.syncBinance(moeda, 7);
      setColetaMsg(dados.mensagem);
      await carregarDados(moeda, fonte, dataInicio, dataFim);
    } catch (e) {
      setErro(`Erro ao sincronizar: ${e.message}`);
    } finally {
      setSincronizando(false);
    }
  };

  // ── Relatórios ─────────────────────────────────────────────────────

  const abrirPdf = (url) => window.open(`${API_URL}${url}`, "_blank", "noopener");

  const gerarPdfPorHora = async (ponto) => {
    if (!ponto?.timestamp_raw || gerandoPdf) return;
    setGerandoPdf(true);
    try {
      const info = await api.gerarRelatorio(
        moeda,
        nomeFonteApi(fonte),
        ponto.timestamp_raw,
        ponto.indice_sentimento,
      );
      abrirPdf(info.url);
      setColetaMsg(`Relatório salvo: ${info.arquivo}`);
    } catch (e) {
      setErro(`Falha ao gerar PDF: ${e.message}`);
    } finally {
      setGerandoPdf(false);
    }
  };

  const gerarPdfCorrelacao = async () => {
    if (gerandoPdfCorr) return;
    setGerandoPdfCorr(true);
    try {
      const info = await api.gerarRelatorioCorrelacao(moeda, nomeFonteApi(fonte));
      abrirPdf(info.url);
      setColetaMsg(`Relatório de correlação salvo: ${info.arquivo}`);
    } catch (e) {
      setErro(`Falha ao gerar PDF de correlação: ${e.message}`);
    } finally {
      setGerandoPdfCorr(false);
    }
  };

  // ── Helpers visuais ────────────────────────────────────────────────

  const corSent = (s) =>
    s === "positivo"
      ? "#22c55e"
      : s === "negativo"
        ? "#ef4444"
        : s === "nulo"
          ? "#64748b"
          : "#eab308";

  const corIndice = (v) =>
    v == null ? "#64748b" : v > 0.6 ? "#22c55e" : v < 0.4 ? "#ef4444" : "#eab308";

  const variacao = sentimento?.variacao_percentual ?? 0;
  const variacaoCor =
    variacao > 0 ? "#22c55e" : variacao < 0 ? "#ef4444" : "#9ca3af";
  const sentIndex = sentimento?.indice_sentimento ?? 0.5;
  const pieData = [
    { name: "Positivo", value: sentIndex },
    { name: "Negativo", value: 1 - sentIndex },
  ];

  const resumoCorr = correlacao?.resumo;
  const temCorrelacao = ehSocial(fonte) && correlacao?.pontos?.length > 0;

  // Consolida o resultado de todas as moedas simuladas num "view" único de
  // lucro total — cada moeda continua sendo um backtest independente (ver
  // simularInvestimento), isto só soma os resultados pra dar a visão geral.
  const simResultadosValidos = simResultados
    ? Object.entries(simResultados).filter(([, r]) => !r.vazio)
    : [];
  const simResumoTotal =
    simResultadosValidos.length > 0
      ? simResultadosValidos.reduce(
          (acc, [m, r]) => {
            acc.capitalTotal += r.parametros.capital_inicial;
            acc.patrimonioTotal += r.resumo.patrimonio_final;
            acc.totalCompras += r.resumo.total_compras;
            acc.totalVendas += r.resumo.total_vendas;
            if (r.resumo.superou_buy_and_hold) acc.superaramBH += 1;
            acc.porMoeda.push({
              moeda: m,
              lucro: r.resumo.lucro_total,
              lucroPct: r.resumo.lucro_total_pct,
            });
            return acc;
          },
          {
            capitalTotal: 0,
            patrimonioTotal: 0,
            totalCompras: 0,
            totalVendas: 0,
            superaramBH: 0,
            porMoeda: [],
          },
        )
      : null;
  if (simResumoTotal) {
    simResumoTotal.lucroTotal =
      simResumoTotal.patrimonioTotal - simResumoTotal.capitalTotal;
    simResumoTotal.lucroTotalPct = simResumoTotal.capitalTotal
      ? (simResumoTotal.lucroTotal / simResumoTotal.capitalTotal) * 100
      : 0;
  }

  const tooltipStyle = {
    backgroundColor: "#0f172a",
    border: "1px solid #334155",
    borderRadius: 12,
    boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
  };

  return (
    <div className="app">
      <button
        className="hamburger"
        onClick={() => setSidebarOpen((v) => !v)}
        aria-label="Abrir menu"
      >
        {sidebarOpen ? "✕" : "☰"}
      </button>

      {/* SIDEBAR */}
      <aside className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}>
        <div className="sidebar-top">
          <h2 className="logo">
            <span className="logo-icon">◈</span> SentCrypto
          </h2>

          <p className="sidebar-label">Moedas</p>
          <div className="sidebar-list">
            {MOEDAS.map((m) => (
              <button
                key={m}
                className={`sidebar-item ${moeda === m ? "sidebar-item--active" : ""}`}
                onClick={() => {
                  setMoeda(m);
                  setSidebarOpen(false);
                }}
              >
                <span className="coin-name">{m}</span>
                {moeda === m && sentimento && (
                  <span className="coin-price">
                    {fmtMoeda(sentimento.preco)}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="sidebar-status">
            <p className="sidebar-label">Status</p>
            <div className="status-item">
              <span
                className={`status-dot ${apiOnline ? "status-dot--ok" : "status-dot--err"}`}
              />
              API {apiOnline ? "Online" : "Offline"}
            </div>
            <div className="status-item">
              <span
                className={`status-dot ${twitterOk ? "status-dot--ok" : "status-dot--warn"}`}
              />
              Twitter{" "}
              {twitterOk ? (
                "OK"
              ) : (
                <button
                  className="link-btn"
                  onClick={() => setShowLoginModal(true)}
                >
                  Configurar
                </button>
              )}
            </div>
          </div>
        </div>

        <div className="sidebar-footer">
          <p>Projeto TCC</p>
          <span className="sidebar-tag">IA • BERT • Análise de Sentimento</span>
        </div>
      </aside>

      {/* MAIN */}
      <main className="main">
        <header className="header">
          <div className="header-left">
            <h1>
              Dashboard <span className="highlight">{moeda}/USDT</span>
            </h1>
            <p className="subtitle">
              Análise de sentimento em tempo real com inteligência artificial
            </p>
          </div>
          <div className="header-right">
            <label className="auto-refresh">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              Auto-refresh
            </label>
            <button
              className="btn btn-ghost"
              onClick={() => carregarDados(moeda, fonte, dataInicio, dataFim)}
              disabled={loading}
            >
              ↻ Atualizar
            </button>
          </div>
        </header>

        {/* FONTE DE DADOS */}
        <section className="controls">
          <div className="toggle-group">
            {Object.entries(FONTES).map(([chave, { label, icon }]) => (
              <button
                key={chave}
                className={`toggle-button ${fonte === chave ? "toggle-button--active" : ""}`}
                onClick={() => setFonte(chave)}
              >
                {icon} {label}
              </button>
            ))}
          </div>

          <div className="date-filter">
            <label className="date-filter__label">
              De:
              <input
                type="date"
                className="date-filter__input"
                value={dataInicio}
                max={dataFim || undefined}
                onChange={(e) => setDataInicio(e.target.value)}
              />
            </label>
            <label className="date-filter__label">
              Até:
              <input
                type="date"
                className="date-filter__input"
                value={dataFim}
                min={dataInicio || undefined}
                onChange={(e) => setDataFim(e.target.value)}
              />
            </label>
            {(dataInicio || dataFim) && (
              <button
                className="btn btn-ghost btn--sm"
                onClick={() => {
                  setDataInicio("");
                  setDataFim("");
                }}
              >
                Limpar datas
              </button>
            )}
            {fonte === "db" && (
              <button
                className="btn btn-primary btn--sm"
                disabled={sincronizando}
                onClick={sincronizarBinance}
              >
                {sincronizando
                  ? "Sincronizando..."
                  : "\u{1F504} Sincronizar Binance (7d)"}
              </button>
            )}
          </div>
        </section>

        {/* TOASTS */}
        {erro && (
          <div className="toast toast--error">
            <span>⚠ {erro}</span>
            <button className="toast-close" onClick={() => setErro(null)}>
              ✕
            </button>
          </div>
        )}
        {coletaMsg && (
          <div className="toast toast--success">
            <span>✓ {coletaMsg}</span>
            <button className="toast-close" onClick={() => setColetaMsg(null)}>
              ✕
            </button>
          </div>
        )}
        {avisoMsg && (
          <div className="toast toast--warning">
            <span>⚠ {avisoMsg}</span>
            <button className="toast-close" onClick={() => setAvisoMsg(null)}>
              ✕
            </button>
          </div>
        )}

        {/* CARDS */}
        <section className="cards">
          <div className="card card-accent">
            <div className="card-icon">{"\u{1F4B0}"}</div>
            <div>
              <p className="card-label">Preço Atual</p>
              <p className="card-value">{fmtMoeda(sentimento?.preco)}</p>
              <p className="card-extra" style={{ color: variacaoCor }}>
                {variacao > 0 ? "▲" : variacao < 0 ? "▼" : "—"}{" "}
                {Math.abs(variacao).toFixed(2)}%
              </p>
            </div>
          </div>

          <div className="card">
            <div className="card-icon">{"\u{1F9E0}"}</div>
            <div>
              <p className="card-label">Sentimento (Candle)</p>
              <p
                className="card-value"
                style={{
                  color: corSent(sentimento?.sentimento_atual),
                  textTransform: "capitalize",
                }}
              >
                {sentimento?.sentimento_atual || "—"}
              </p>
              <p className="card-extra">
                Índice: {sentimento?.indice_sentimento ?? "—"}
              </p>
            </div>
          </div>

          <div className="card">
            <div className="card-icon">⏰</div>
            <div>
              <p className="card-label">Última Atualização</p>
              <p className="card-value small">
                {dataHoraCompleta(sentimento?.ultimo_update)}
              </p>
            </div>
          </div>

          <div className="card card-gauge">
            <p className="card-label">Gauge de Sentimento</p>
            <div className="gauge-chart">
              <PieChart width={120} height={70}>
                <Pie
                  data={pieData}
                  cx={60}
                  cy={65}
                  startAngle={180}
                  endAngle={0}
                  innerRadius={40}
                  outerRadius={55}
                  paddingAngle={2}
                  dataKey="value"
                >
                  <Cell fill="#22c55e" />
                  <Cell fill="#ef4444" />
                </Pie>
              </PieChart>
              <span className="gauge-label">{(sentIndex * 100).toFixed(0)}%</span>
            </div>
          </div>
        </section>

        {/* GRÁFICO PRINCIPAL */}
        <section className="chart-section">
          <div className="chart-header">
            <div>
              <h2>
                {ehSocial(fonte)
                  ? "Histórico — Preço × Sentimento"
                  : "Histórico de Preço"}
              </h2>
              <span className="chart-pill">
                {FONTES[fonte]?.icon} {FONTES[fonte]?.label} · horários em UTC
              </span>
            </div>
            {loading && <span className="spinner" />}
          </div>

          {historico.length === 0 ? (
            <div className="chart-wrapper">
              <p className="no-data">
                {loading
                  ? "Carregando..."
                  : `Nenhum dado encontrado para ${moeda} + ${FONTES[fonte]?.label}.`}
                {!loading && fonte === "reddit" && " Clique em 'Coletar Reddit'."}
                {!loading && fonte === "x" && " Clique em 'Analisar e salvar'."}
              </p>
            </div>
          ) : !ehSocial(fonte) ? (
            <div className="chart-wrapper">
              <ResponsiveContainer minWidth={0} minHeight={180}>
                <AreaChart data={historico}>
                  <defs>
                    <linearGradient id="gradPreco" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#60a5fa" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#60a5fa" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="rotulo" stroke="#64748b" tick={{ fontSize: 12 }} />
                  <YAxis
                    stroke="#60a5fa"
                    domain={["auto", "auto"]}
                    tickFormatter={(v) =>
                      v == null ? "" : `$${Number(v).toLocaleString()}`
                    }
                    tick={{ fontSize: 12 }}
                  />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(v) => [fmtMoeda(v), "Preço"]}
                  />
                  <Legend wrapperStyle={{ fontSize: 13 }} />
                  <Area
                    type="monotone"
                    dataKey="preco"
                    name="Preço (USD)"
                    stroke="#60a5fa"
                    strokeWidth={2}
                    fill="url(#gradPreco)"
                    dot={false}
                    connectNulls={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="chart-dual">
              <div className="chart-wrapper chart-wrapper--half">
                <p className="chart-sublabel">Preço (USD)</p>
                <ResponsiveContainer minWidth={0} minHeight={180}>
                  <AreaChart data={historico}>
                    <defs>
                      <linearGradient id="gradPreco2" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#60a5fa" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#60a5fa" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="rotulo" stroke="#64748b" tick={{ fontSize: 11 }} />
                    <YAxis
                      stroke="#60a5fa"
                      domain={["auto", "auto"]}
                      tickFormatter={(v) =>
                        v == null ? "" : `$${Number(v).toLocaleString()}`
                      }
                      tick={{ fontSize: 11 }}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v) => [fmtMoeda(v), "Preço"]}
                    />
                    <Area
                      type="monotone"
                      dataKey="preco"
                      name="Preço (USD)"
                      stroke="#60a5fa"
                      strokeWidth={2}
                      fill="url(#gradPreco2)"
                      dot={false}
                      connectNulls
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              <div className="chart-wrapper chart-wrapper--half">
                <p className="chart-sublabel">
                  Sentimento (0% negativo — 50% neutro — 100% positivo)
                  {gerandoPdf && <span className="spinner spinner--inline" />}
                  <span className="chart-sublabel-hint">
                    Clique numa barra para gerar o relatório PDF da hora
                  </span>
                </p>
                <ResponsiveContainer minWidth={0} minHeight={180}>
                  <BarChart data={historico}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="rotulo" stroke="#64748b" tick={{ fontSize: 11 }} />
                    <YAxis
                      stroke="#34d399"
                      domain={[0, 1]}
                      tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                      tick={{ fontSize: 11 }}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v, nome, props) => {
                        const p = props?.payload || {};
                        return [
                          `${(v * 100).toFixed(1)}% — ${p.total_posts || 0} posts ` +
                            `(${p.positivos || 0} pos / ${p.negativos || 0} neg / ${p.neutros || 0} neu)`,
                          "Sentimento",
                        ];
                      }}
                    />
                    <ReferenceLine
                      y={0.5}
                      stroke="#eab308"
                      strokeDasharray="3 3"
                      label={{
                        value: "Neutro",
                        fill: "#eab308",
                        fontSize: 11,
                        position: "right",
                      }}
                    />
                    <Bar
                      dataKey="indice_sentimento"
                      name="Sentimento"
                      radius={[4, 4, 0, 0]}
                      maxBarSize={40}
                      style={{ cursor: "pointer" }}
                      onClick={(data) => gerarPdfPorHora(data?.payload)}
                    >
                      {historico.map((entrada, i) => (
                        <Cell
                          key={`c-${i}`}
                          fill={corIndice(entrada.indice_sentimento)}
                          fillOpacity={0.85}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </section>

        {/* CORRELAÇÃO */}
        {temCorrelacao && (
          <section className="chart-section correlation-section">
            <div className="chart-header">
              <div>
                <h2>{"\u{1F4CA}"} Correlação: Sentimento vs Preço</h2>
                <span className="chart-pill">
                  {resumoCorr?.taxa_acerto_pct != null
                    ? `Taxa de acerto: ${resumoCorr.taxa_acerto_pct}% (${resumoCorr.total_comparavel} horas)`
                    : "Sem horas comparáveis ainda"}
                </span>
              </div>
              <div className="chart-header-actions">
                {correlacaoLoading && <span className="spinner" />}
                <button
                  className="btn btn-primary btn--sm"
                  onClick={gerarPdfCorrelacao}
                  disabled={gerandoPdfCorr}
                >
                  {gerandoPdfCorr ? "Gerando..." : "\u{1F4C4} Gerar PDF"}
                </button>
              </div>
            </div>

            {/* Ressalva estatística: amostra pequena não sustenta conclusão. */}
            {resumoCorr?.total_comparavel > 0 && !resumoCorr?.amostra_suficiente && (
              <p className="corr-aviso">
                ⚠ Amostra pequena ({resumoCorr.total_comparavel} horas
                comparáveis). A taxa de acerto é indicativa e ainda não sustenta
                conclusão estatística — colete mais dados.
              </p>
            )}

            {resumoCorr?.total_comparavel > 0 && (
              <div className="corr-summary">
                <div className="corr-card corr-card--acerto">
                  <span className="corr-card-value">{resumoCorr.acertos}</span>
                  <span className="corr-card-label">Acertos</span>
                  <span className="corr-card-desc">
                    Sentimento previu a direção
                  </span>
                </div>
                <div className="corr-card corr-card--erro">
                  <span className="corr-card-value">{resumoCorr.erros}</span>
                  <span className="corr-card-label">Erros</span>
                  <span className="corr-card-desc">Não correspondeu</span>
                </div>
                <div className="corr-card corr-card--taxa">
                  <span className="corr-card-value">
                    {resumoCorr.taxa_acerto_pct}%
                  </span>
                  <span className="corr-card-label">Taxa de Acerto</span>
                  <span className="corr-card-desc">
                    De {resumoCorr.total_comparavel} horas comparáveis
                  </span>
                </div>
                <div className="corr-card">
                  <span className="corr-card-value">
                    {percentual(resumoCorr.retorno_medio_apos_positivo, 3)}
                  </span>
                  <span className="corr-card-label">Retorno pós-positivo</span>
                  <span className="corr-card-desc">Média em 1h</span>
                </div>
                <div className="corr-card">
                  <span className="corr-card-value">
                    {percentual(resumoCorr.retorno_medio_apos_negativo, 3)}
                  </span>
                  <span className="corr-card-label">Retorno pós-negativo</span>
                  <span className="corr-card-desc">Média em 1h</span>
                </div>
              </div>
            )}

            <div className="chart-wrapper">
              <ResponsiveContainer minWidth={0} minHeight={180}>
                <BarChart data={correlacao.pontos}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="hora" stroke="#64748b" tick={{ fontSize: 11 }} />
                  <YAxis
                    yAxisId="sent"
                    stroke="#a78bfa"
                    domain={[-1, 1]}
                    tickFormatter={(v) => v.toFixed(1)}
                    tick={{ fontSize: 11 }}
                  />
                  <YAxis
                    yAxisId="preco"
                    orientation="right"
                    stroke="#60a5fa"
                    tickFormatter={(v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`}
                    tick={{ fontSize: 11 }}
                  />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(valor, nome) =>
                      nome === "Sentiment Score"
                        ? [Number(valor).toFixed(3), nome]
                        : [percentual(valor, 3), nome]
                    }
                    labelFormatter={(l) => `Hora (UTC): ${l}`}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <ReferenceLine yAxisId="sent" y={0} stroke="#64748b" strokeDasharray="3 3" />
                  <Bar
                    yAxisId="sent"
                    dataKey="sentiment_score"
                    name="Sentiment Score"
                    radius={[4, 4, 0, 0]}
                    maxBarSize={30}
                  >
                    {correlacao.pontos.map((p, i) => (
                      <Cell
                        key={`cs-${i}`}
                        fill={
                          p.sentiment_score > 0
                            ? "#a78bfa"
                            : p.sentiment_score < 0
                              ? "#f472b6"
                              : "#fbbf24"
                        }
                        fillOpacity={0.75}
                      />
                    ))}
                  </Bar>
                  <Bar
                    yAxisId="preco"
                    dataKey="variacao_preco"
                    name="Variação Preço"
                    radius={[4, 4, 0, 0]}
                    maxBarSize={30}
                  >
                    {correlacao.pontos.map((p, i) => (
                      <Cell
                        key={`cp-${i}`}
                        fill={
                          p.variacao_preco > 0
                            ? "#22c55e"
                            : p.variacao_preco < 0
                              ? "#ef4444"
                              : "#64748b"
                        }
                        fillOpacity={0.75}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            <CollapseToggle
              aberto={secaoAberta("corr-tabela")}
              onToggle={() => alternarSecao("corr-tabela")}
              rotulo="Detalhamento por hora"
              contagem={correlacao.pontos.length}
            />

            {secaoAberta("corr-tabela") && (
            <div className="corr-table-wrapper">
              <table className="corr-table">
                <thead>
                  <tr>
                    <th>Hora (UTC)</th>
                    <th>Score</th>
                    <th>Posts</th>
                    <th>Direção Sent.</th>
                    <th>Var. Preço</th>
                    <th>Ret. 1h</th>
                    <th>Ret. 4h</th>
                    <th>Resultado</th>
                  </tr>
                </thead>
                <tbody>
                  {correlacao.pontos.map((p) => (
                    <tr
                      key={p.timestamp}
                      className={
                        p.acertou === true
                          ? "corr-row--ok"
                          : p.acertou === false
                            ? "corr-row--fail"
                            : ""
                      }
                    >
                      <td>{p.hora}</td>
                      <td>{p.sentiment_score.toFixed(2)}</td>
                      <td>
                        {p.total}{" "}
                        <span className="corr-mini">
                          ({p.positivos}+/{p.negativos}−)
                        </span>
                      </td>
                      <td>
                        <span className={`corr-dir corr-dir--${p.sentimento_direcao}`}>
                          {p.sentimento_direcao === "positivo"
                            ? "▲ Positivo"
                            : p.sentimento_direcao === "negativo"
                              ? "▼ Negativo"
                              : "● Neutro"}
                        </span>
                      </td>
                      <td>{percentual(p.variacao_preco, 3)}</td>
                      <td>{percentual(p.retorno_1h, 3)}</td>
                      <td>{percentual(p.retorno_4h, 3)}</td>
                      <td>
                        {p.acertou === true && (
                          <span className="corr-badge corr-badge--ok">✓ Acerto</span>
                        )}
                        {p.acertou === false && (
                          <span className="corr-badge corr-badge--fail">✗ Erro</span>
                        )}
                        {p.acertou == null && (
                          <span className="corr-badge corr-badge--na">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            )}
          </section>
        )}

        {/* AÇÕES DE COLETA */}
        {fonte === "reddit" && (
          <section className="action-bar">
            <button
              className="btn btn-primary"
              onClick={() => executarColeta("reddit")}
              disabled={coletando}
            >
              {coletando ? "Coletando..." : "🔴 Coletar Reddit agora"}
            </button>
            <span className="action-hint">
              Subreddits: {(SUBREDDITS_DEFAULT[moeda] || []).join(", ")}
            </span>
          </section>
        )}

        {fonte === "x" && (
          <section className="action-bar action-bar--col">
            <div className="input-group">
              <label htmlFor="perfis-x">
                Perfis do X (separados por vírgula):
              </label>
              <input
                id="perfis-x"
                className="input"
                type="text"
                value={perfisX}
                onChange={(e) => setPerfisX(e.target.value)}
                placeholder="whale_alert, elonmusk, VitalikButerin"
              />
            </div>
            <div className="date-filter">
              <label className="date-filter__label">
                Feed de:
                <input
                  type="date"
                  className="date-filter__input"
                  value={feedDataInicio}
                  max={feedDataFim || undefined}
                  onChange={(e) => setFeedDataInicio(e.target.value)}
                />
              </label>
              <label className="date-filter__label">
                até:
                <input
                  type="date"
                  className="date-filter__input"
                  value={feedDataFim}
                  min={feedDataInicio || undefined}
                  onChange={(e) => setFeedDataFim(e.target.value)}
                />
              </label>
              {(feedDataInicio || feedDataFim) && (
                <button
                  className="btn btn-ghost btn--sm"
                  onClick={() => {
                    setFeedDataInicio("");
                    setFeedDataFim("");
                  }}
                >
                  Limpar
                </button>
              )}
              <span className="action-hint">
                Filtra os tweets carregados — o X só traz os mais recentes,
                então períodos antigos podem vir vazios.
              </span>
            </div>
            <div className="input-group input-group--inline">
              <label htmlFor="auto-coleta">Coleta automática:</label>
              <select
                id="auto-coleta"
                className="input input--select"
                value={autoCollectInterval}
                onChange={(e) =>
                  setAutoCollectInterval(parseInt(e.target.value, 10))
                }
              >
                {INTERVALOS_COLETA.map(({ valor, rotulo }) => (
                  <option key={valor} value={valor}>
                    {rotulo}
                  </option>
                ))}
              </select>
              {autoCollectInterval > 0 && (
                <span className="auto-collect-badge">
                  {"⏱"} Ativa ({autoCollectInterval} min)
                </span>
              )}
            </div>
            <div className="btn-row">
              <button
                className="btn btn-primary"
                disabled={feedLoading}
                onClick={carregarFeedX}
              >
                {feedLoading ? "Carregando..." : "🐦 Carregar Feed"}
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => executarColeta("x")}
                disabled={coletando}
              >
                {coletando ? "Analisando..." : "🧠 Analisar e salvar"}
              </button>
            </div>
          </section>
        )}

        {/* SIMULADOR DE INVESTIMENTO */}
        {fonte === "x" && (
          <section className="chart-section simulador-section">
            <div className="chart-header">
              <div>
                <h2>{"\u{1F3AF}"} Simulador de Investimento</h2>
                <span className="chart-pill">
                  {simModo === "fomo"
                    ? "FOMO — compra baseada no sentimento dos posts já coletados"
                    : "Flat — compra baseada só na variação percentual do preço"}
                </span>
              </div>
            </div>

            <div className="toggle-group sim-modo-toggle">
              <button
                type="button"
                className={`toggle-button ${simModo === "fomo" ? "toggle-button--active" : ""}`}
                onClick={() => setSimModo("fomo")}
              >
                {"\u{1F525}"} FOMO (sentimento)
              </button>
              <button
                type="button"
                className={`toggle-button ${simModo === "flat" ? "toggle-button--active" : ""}`}
                onClick={() => setSimModo("flat")}
              >
                {"\u{1F4C8}"} Flat (só preço)
              </button>
            </div>
            <p className="sim-modo-desc">
              {simModo === "fomo"
                ? "Só compra quando o sentimento dos perfis do X é positivo " +
                  "e o preço caiu — exige posts já coletados (\"Analisar e " +
                  "salvar\") no período."
                : "Ignora sentimento e perfis por completo: compra só pela " +
                  "queda de preço, funciona em qualquer período com dados " +
                  "da Binance, mesmo sem nenhuma coleta prévia."}
            </p>

            <div className="action-bar action-bar--col">
              <div className="input-group">
                <label>Moedas a simular</label>
                <div className="sim-moedas-grid">
                  {MOEDAS.map((m) => (
                    <label key={m} className="checkbox-pill">
                      <input
                        type="checkbox"
                        checked={simMoedas.includes(m)}
                        onChange={() => alternarSimMoeda(m)}
                      />
                      {m}
                    </label>
                  ))}
                </div>
              </div>

              <div className="date-filter">
                <label className="date-filter__label">
                  Período da simulação de:
                  <input
                    type="date"
                    className="date-filter__input"
                    value={simDataInicio}
                    max={simDataFim || undefined}
                    onChange={(e) => setSimDataInicio(e.target.value)}
                  />
                </label>
                <label className="date-filter__label">
                  até:
                  <input
                    type="date"
                    className="date-filter__input"
                    value={simDataFim}
                    min={simDataInicio || undefined}
                    onChange={(e) => setSimDataFim(e.target.value)}
                  />
                </label>
                {(feedDataInicio || feedDataFim) && (
                  <button
                    type="button"
                    className="btn btn-ghost btn--sm"
                    onClick={() => {
                      setSimDataInicio(feedDataInicio);
                      setSimDataFim(feedDataFim);
                    }}
                  >
                    Usar período do Feed ({feedDataInicio || "…"} a{" "}
                    {feedDataFim || "…"})
                  </button>
                )}
              </div>

              {simMoedas.length > 0 && (
                <div className="sim-params-table-wrapper">
                  <p className="sim-params-hint">
                    Cada moeda marcada tem sua própria estratégia — ajuste o
                    capital, o valor por compra e os percentuais linha a
                    linha.
                  </p>
                  <table className="sim-params-table">
                    <thead>
                      <tr>
                        <th>Moeda</th>
                        {SIM_CAMPOS.map((c) => (
                          <th key={c.chave}>{c.rotulo}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {simMoedas.map((m) => {
                        const p = paramsDaMoeda(m);
                        return (
                          <tr key={m}>
                            <td className="sim-params-table__moeda">{m}</td>
                            {SIM_CAMPOS.map((c) => (
                              <td key={c.chave}>
                                <input
                                  className="input input--table"
                                  type="number"
                                  min={c.min}
                                  step={c.step}
                                  value={p[c.chave]}
                                  onChange={(e) =>
                                    atualizarParamSimMoeda(
                                      m, c.chave, e.target.value,
                                    )
                                  }
                                />
                              </td>
                            ))}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="btn-row">
                <button
                  className="btn btn-primary"
                  disabled={simulando}
                  onClick={simularInvestimento}
                >
                  {simulando ? "Simulando..." : "▶ Simular investimento"}
                </button>
                <span className="action-hint">
                  {simModo === "fomo"
                    ? `Perfis: ${listaDePerfis(perfisX).join(", ") || "nenhum"} · ` +
                      `posts já salvos no banco nesse período (colete antes ` +
                      `com "Analisar e salvar" se estiver vazio)`
                    : "Modo Flat: não depende de perfis nem de posts coletados."}
                </span>
              </div>
            </div>

            {/* VIEW DE LUCRO TOTAL — soma de todas as moedas simuladas */}
            {simResumoTotal && (
              <div className="sim-total-view">
                <h3 className="sim-total-view__titulo">
                  {"\u{1F4B0}"} Lucro Total ({simResumoTotal.porMoeda.length}{" "}
                  {simResumoTotal.porMoeda.length === 1 ? "moeda" : "moedas"})
                </h3>
                <div className="corr-summary">
                  <div className="corr-card">
                    <span className="corr-card-value">
                      {fmtMoeda(simResumoTotal.patrimonioTotal)}
                    </span>
                    <span className="corr-card-label">Patrimônio Total</span>
                    <span className="corr-card-desc">
                      Capital inicial somado: {fmtMoeda(simResumoTotal.capitalTotal)}
                    </span>
                  </div>
                  <div
                    className={`corr-card ${
                      simResumoTotal.lucroTotal >= 0
                        ? "corr-card--acerto"
                        : "corr-card--erro"
                    }`}
                  >
                    <span className="corr-card-value">
                      {fmtMoeda(simResumoTotal.lucroTotal)}
                    </span>
                    <span className="corr-card-label">Ganho/Perda Total</span>
                    <span className="corr-card-desc">
                      {percentual(simResumoTotal.lucroTotalPct)} sobre o
                      capital somado
                    </span>
                  </div>
                  <div className="corr-card corr-card--taxa">
                    <span className="corr-card-value">
                      {simResumoTotal.superaramBH}/{simResumoTotal.porMoeda.length}
                    </span>
                    <span className="corr-card-label">
                      Superaram Buy &amp; Hold
                    </span>
                    <span className="corr-card-desc">moedas simuladas</span>
                  </div>
                  <div className="corr-card">
                    <span className="corr-card-value">
                      {simResumoTotal.totalCompras}/{simResumoTotal.totalVendas}
                    </span>
                    <span className="corr-card-label">
                      Compras/Vendas (todas as moedas)
                    </span>
                    <span className="corr-card-desc">somado de todos os backtests</span>
                  </div>
                </div>

                {simResumoTotal.porMoeda.length > 1 && (
                  <div className="chart-wrapper">
                    <ResponsiveContainer minWidth={0} minHeight={140}>
                      <BarChart data={simResumoTotal.porMoeda}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis
                          dataKey="moeda"
                          stroke="#64748b"
                          tick={{ fontSize: 11 }}
                        />
                        <YAxis
                          stroke="#64748b"
                          tickFormatter={(v) => `${v}%`}
                          tick={{ fontSize: 11 }}
                        />
                        <Tooltip
                          contentStyle={tooltipStyle}
                          formatter={(v, _n, props) => [
                            `${percentual(v)} (${fmtMoeda(props.payload.lucro)})`,
                            "Lucro",
                          ]}
                        />
                        <ReferenceLine y={0} stroke="#64748b" strokeDasharray="3 3" />
                        <Bar dataKey="lucroPct" name="Lucro %" radius={[4, 4, 0, 0]} maxBarSize={40}>
                          {simResumoTotal.porMoeda.map((p, i) => (
                            <Cell
                              key={`sim-total-${i}`}
                              fill={p.lucroPct >= 0 ? "#22c55e" : "#ef4444"}
                              fillOpacity={0.85}
                            />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
            )}

            {/* Cada moeda marcada roda como um backtest independente e
                isolado — posts e candles são filtrados por moeda no
                backend, então uma compra de ETH nunca aparece no
                resultado do BTC (ver services/simulacao.py). */}
            {simResultados &&
              Object.entries(simResultados).map(([m, resultado]) => (
                <div key={m} className="sim-moeda-bloco">
                  <h3 className="sim-moeda-titulo">
                    {"\u{1FA99}"} {m}
                  </h3>

                  {resultado.vazio ? (
                    <p className="corr-aviso">⚠ {resultado.mensagem}</p>
                  ) : (
                    <>
                      <div className="corr-summary">
                        <div className="corr-card">
                          <span className="corr-card-value">
                            {fmtMoeda(resultado.resumo.patrimonio_final)}
                          </span>
                          <span className="corr-card-label">
                            Patrimônio Final
                          </span>
                          <span className="corr-card-desc">
                            Caixa + posições abertas
                          </span>
                        </div>
                        <div
                          className={`corr-card ${
                            resultado.resumo.lucro_total >= 0
                              ? "corr-card--acerto"
                              : "corr-card--erro"
                          }`}
                        >
                          <span className="corr-card-value">
                            {resultado.resumo.taxa_acerto_pct != null
                              ? `${resultado.resumo.taxa_acerto_pct}%`
                              : "—"}
                          </span>
                          <span className="corr-card-label">
                            Taxa de Acerto
                          </span>
                          <span className="corr-card-desc">
                            {fmtMoeda(resultado.resumo.lucro_total)} (
                            {percentual(resultado.resumo.lucro_total_pct)})
                            ganho/perda total
                          </span>
                        </div>
                        <div className="corr-card corr-card--taxa">
                          <span className="corr-card-value">
                            {percentual(resultado.resumo.buy_and_hold.lucro_pct)}
                          </span>
                          <span className="corr-card-label">Buy &amp; Hold</span>
                          <span className="corr-card-desc">
                            {resultado.resumo.superou_buy_and_hold
                              ? "Estratégia superou o benchmark"
                              : "Estratégia ficou abaixo do benchmark"}
                          </span>
                        </div>
                        <div className="corr-card">
                          <span className="corr-card-value">
                            {resultado.resumo.total_compras}/
                            {resultado.resumo.total_vendas}
                          </span>
                          <span className="corr-card-label">
                            Compras/Vendas
                          </span>
                          <span className="corr-card-desc">
                            {resultado.resumo.posicoes_abertas_final}{" "}
                            posição(ões) aberta(s)
                            {resultado.resumo.vendas_por_stop_loss > 0 &&
                              ` · ${resultado.resumo.vendas_por_stop_loss} por stop-loss`}
                          </span>
                        </div>
                      </div>

                      <div className="chart-wrapper">
                        <ResponsiveContainer minWidth={0} minHeight={200}>
                          <AreaChart
                            data={resultado.serie_patrimonio.map((p) => ({
                              ...p,
                              rotulo: rotuloHora(p.timestamp),
                            }))}
                          >
                            <defs>
                              <linearGradient
                                id={`gradPatrimonio-${m}`}
                                x1="0"
                                y1="0"
                                x2="0"
                                y2="1"
                              >
                                <stop
                                  offset="5%"
                                  stopColor="#22c55e"
                                  stopOpacity={0.3}
                                />
                                <stop
                                  offset="95%"
                                  stopColor="#22c55e"
                                  stopOpacity={0}
                                />
                              </linearGradient>
                            </defs>
                            <CartesianGrid
                              strokeDasharray="3 3"
                              stroke="#1e293b"
                            />
                            <XAxis
                              dataKey="rotulo"
                              stroke="#64748b"
                              tick={{ fontSize: 11 }}
                            />
                            <YAxis
                              stroke="#22c55e"
                              domain={["auto", "auto"]}
                              tickFormatter={(v) =>
                                `$${Number(v).toLocaleString()}`
                              }
                              tick={{ fontSize: 11 }}
                            />
                            <Tooltip
                              contentStyle={tooltipStyle}
                              formatter={(v, nome) => [fmtMoeda(v), nome]}
                              labelFormatter={(l) => `Hora (UTC): ${l}`}
                            />
                            <ReferenceLine
                              y={Number(resultado.parametros.capital_inicial)}
                              stroke="#64748b"
                              strokeDasharray="3 3"
                              label={{
                                value: "Capital inicial",
                                fill: "#64748b",
                                fontSize: 11,
                                position: "right",
                              }}
                            />
                            <Area
                              type="monotone"
                              dataKey="patrimonio"
                              name="Patrimônio"
                              stroke="#22c55e"
                              strokeWidth={2}
                              fill={`url(#gradPatrimonio-${m})`}
                              dot={false}
                            />
                          </AreaChart>
                        </ResponsiveContainer>
                      </div>

                      {resultado.trades.length > 0 && (
                        <>
                          <CollapseToggle
                            aberto={secaoAberta(`sim-trades-${m}`)}
                            onToggle={() => alternarSecao(`sim-trades-${m}`)}
                            rotulo="Trades"
                            contagem={resultado.trades.length}
                          />
                          {secaoAberta(`sim-trades-${m}`) && (
                            <div className="corr-table-wrapper">
                              <table className="corr-table">
                                <thead>
                                  <tr>
                                    <th>Tipo</th>
                                    <th>Hora (UTC)</th>
                                    <th>Preço</th>
                                    <th>Qtd.</th>
                                    <th>Valor</th>
                                    <th>Lucro</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {resultado.trades.map((t, i) => (
                                    <tr
                                      key={i}
                                      className={
                                        t.tipo !== "venda"
                                          ? ""
                                          : t.motivo === "perda"
                                            ? "corr-row--fail"
                                            : "corr-row--ok"
                                      }
                                    >
                                      <td>
                                        <span
                                          className={`corr-badge ${
                                            t.tipo === "compra"
                                              ? "corr-badge--na"
                                              : t.motivo === "perda"
                                                ? "corr-badge--fail"
                                                : "corr-badge--ok"
                                          }`}
                                        >
                                          {t.tipo === "compra"
                                            ? "\u{1F7E2} Compra"
                                            : t.motivo === "perda"
                                              ? "\u{1F6D1} Venda (Stop-Loss)"
                                              : "\u{1F534} Venda (Lucro)"}
                                        </span>
                                      </td>
                                      <td>{dataHoraCompleta(t.timestamp)}</td>
                                      <td>{fmtMoeda(t.preco)}</td>
                                      <td>{t.qtd.toFixed(6)}</td>
                                      <td>{fmtMoeda(t.valor)}</td>
                                      <td>
                                        {t.tipo === "venda"
                                          ? `${fmtMoeda(t.lucro)} (${percentual(t.lucro_pct)})`
                                          : "—"}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </>
                      )}
                    </>
                  )}
                </div>
              ))}
          </section>
        )}

        {/* ANÁLISE DE TEXTO LIVRE */}
        <section className="analise-section">
          <h2>{"\u{1F9E0}"} Análise de Texto Livre</h2>
          <p className="analise-desc">
            Cole qualquer texto e o modelo BERT vai analisar o sentimento.
          </p>
          <div className="analise-box">
            <textarea
              className="textarea"
              value={textoAnalise}
              onChange={(e) => setTextoAnalise(e.target.value)}
              placeholder="Cole aqui uma notícia, tweet, comentário do Reddit..."
              rows={4}
              maxLength={10000}
            />
            <button
              className="btn btn-primary"
              onClick={analisarTexto}
              disabled={analisando || !textoAnalise.trim()}
            >
              {analisando ? "Analisando..." : "Analisar com BERT"}
            </button>
            {resultadoAnalise && (
              <div className="analise-result">
                <div
                  className="analise-badge"
                  style={{ backgroundColor: corSent(resultadoAnalise.sentimento) }}
                >
                  {resultadoAnalise.sentimento}
                </div>
                <div className="analise-stats">
                  <span>
                    Índice: <strong>{resultadoAnalise.indice}</strong>
                  </span>
                  <span>
                    Score BERT: <strong>{resultadoAnalise.score_bert}</strong>
                  </span>
                  <span>
                    Label: <strong>{resultadoAnalise.label_bert}</strong>
                  </span>
                  <span>
                    Cripto:{" "}
                    <strong>
                      {resultadoAnalise.crypto_relevante ? "sim" : "não"}
                    </strong>
                  </span>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* FEED DO X */}
        {fonte === "x" && feedTweets.length > 0 && (
          <section className="feed-section">
            <div className="feed-header">
              <h2>{"\u{1F426}"} Timeline do X</h2>
              <span className="chart-pill">{feedTweets.length} tweets</span>
            </div>
            <div className="feed-list">
              {feedTweets.map((tw, i) => (
                <div key={tw.tweet_id || i} className="tweet-card">
                  <div className="tweet-top">
                    <div className="tweet-avatar">
                      {tw.avatar ? (
                        <img src={tw.avatar} alt="" />
                      ) : (
                        <div className="tweet-avatar-placeholder">
                          {(tw.nome_exibicao || tw.perfil || "?")[0].toUpperCase()}
                        </div>
                      )}
                    </div>
                    <div className="tweet-meta">
                      <span className="tweet-name">
                        {tw.nome_exibicao || tw.perfil}
                      </span>
                      <span className="tweet-handle">{tw.perfil}</span>
                      <span className="tweet-dot">·</span>
                      <span className="tweet-time">{dataCurta(tw.timestamp)}</span>
                    </div>
                  </div>
                  <p className="tweet-text">{tw.texto}</p>
                  <div className="tweet-bottom">
                    <div className="tweet-stats">
                      <span title="Respostas">{"\u{1F4AC}"} {tw.replies}</span>
                      <span title="Retweets">{"\u{1F504}"} {tw.retweets}</span>
                      <span title="Curtidas">❤️ {tw.likes}</span>
                    </div>
                    {tw.sentimento && (
                      <span
                        className="tweet-sentiment"
                        style={{ backgroundColor: corSent(tw.sentimento) }}
                      >
                        {tw.sentimento === "nulo"
                          ? "não-cripto"
                          : `${tw.sentimento} (${tw.score_bert})`}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>

      {/* MODAL DE LOGIN DO X */}
      {showLoginModal && (
        <div className="modal-overlay" onClick={() => setShowLoginModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{"\u{1F426}"} Configurar Twitter</h3>
            <p className="modal-desc">
              Para coletar tweets, forneça os cookies de autenticação do X:
            </p>
            <ol className="modal-steps">
              <li>
                Abra <strong>x.com</strong> no Chrome e faça login
              </li>
              <li>
                Pressione <strong>F12</strong> → aba <strong>Application</strong>
              </li>
              <li>
                Menu lateral: <strong>Cookies → https://x.com</strong>
              </li>
              <li>
                Copie <strong>auth_token</strong> e <strong>ct0</strong>
              </li>
            </ol>
            <div className="modal-inputs">
              <input
                className="input"
                placeholder="auth_token"
                value={loginAuthToken}
                onChange={(e) => setLoginAuthToken(e.target.value)}
              />
              <input
                className="input"
                placeholder="ct0"
                value={loginCt0}
                onChange={(e) => setLoginCt0(e.target.value)}
              />
            </div>
            <div className="modal-actions">
              <button
                className="btn btn-primary"
                onClick={salvarCookiesTwitter}
                disabled={!loginAuthToken || !loginCt0 || salvandoLogin}
              >
                {salvandoLogin ? "Salvando..." : "Salvar cookies"}
              </button>
              <button
                className="btn btn-ghost"
                onClick={() => setShowLoginModal(false)}
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
