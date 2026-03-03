import { useCallback, useEffect, useState, useRef } from "react";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  ReferenceLine,
} from "recharts";
import "./App.css";

const API = "http://127.0.0.1:8000";
const MOEDAS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "LINK"];

const FONTES = {
  api: { label: "Binance (ao vivo)", icon: "\u{1F4CA}" },
  db: { label: "Histórico (SQLite)", icon: "\u{1F4BE}" },
  reddit: { label: "Reddit", icon: "\u{1F534}" },
  x: { label: "X / Twitter", icon: "\u{1F426}" },
};

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
  const [perfisX, setPerfisX] = useState("whale_alert, cabortopcripto");

  const [textoAnalise, setTextoAnalise] = useState("");
  const [resultadoAnalise, setResultadoAnalise] = useState(null);
  const [analisando, setAnalisando] = useState(false);

  const [coletando, setColetando] = useState(false);
  const [coletaMsg, setColetaMsg] = useState(null);

  const [autoRefresh, setAutoRefresh] = useState(true);
  const intervalRef = useRef(null);

  const [showLoginModal, setShowLoginModal] = useState(false);
  const [loginAuthToken, setLoginAuthToken] = useState("");
  const [loginCt0, setLoginCt0] = useState("");

  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [correlacao, setCorrelacao] = useState(null);
  const [correlacaoLoading, setCorrelacaoLoading] = useState(false);
  const [gerandoPdf, setGerandoPdf] = useState(false);

  const [dataInicio, setDataInicio] = useState("");
  const [dataFim, setDataFim] = useState("");
  const [sincronizando, setSincronizando] = useState(false);

  // ── Data fetching ──────────────────────────────────────────────

  const checkApiHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API}/`);
      const data = await res.json();
      setApiOnline(data.status === "ok");
      setTwitterOk(data.twitter_cookies || false);
    } catch {
      setApiOnline(false);
    }
  }, []);

  const carregarDados = useCallback(async (m, f, di, df) => {
    setLoading(true);
    setErro(null);
    try {
      const dateParams =
        (di ? `&data_inicio=${di}` : "") + (df ? `&data_fim=${df}` : "");

      const urlHist =
        f === "api"
          ? `${API}/historico-sentimento?moeda=${m}${dateParams}`
          : f === "db"
            ? `${API}/historico-db?moeda=${m}${dateParams}`
            : f === "x"
              ? `${API}/historico-social?moeda=${m}&fonte=X${dateParams}`
              : `${API}/historico-social?moeda=${m}&fonte=Reddit${dateParams}`;

      const resHist = await fetch(urlHist);
      if (!resHist.ok) throw new Error(`Erro ${resHist.status}`);
      const dadosHist = await resHist.json();

      let mapaPrecoPorHora = {};
      if (f === "reddit" || f === "x") {
        try {
          const resPreco = await fetch(
            `${API}/historico-sentimento?moeda=${m}`,
          );
          const dadosPreco = await resPreco.json();
          (dadosPreco.pontos || []).forEach((p) => {
            const key = new Date(p.timestamp).toLocaleTimeString("pt-BR", {
              hour: "2-digit",
              minute: "2-digit",
            });
            mapaPrecoPorHora[key] = p.preco;
          });
        } catch {
          /* price overlay is optional */
        }
      }

      const formatados = (dadosHist.pontos || []).map((ponto) => {
        const key = new Date(ponto.timestamp).toLocaleTimeString("pt-BR", {
          hour: "2-digit",
          minute: "2-digit",
        });
        return {
          timestamp: key,
          timestamp_raw: ponto.timestamp,
          preco:
            f === "reddit" || f === "x"
              ? (mapaPrecoPorHora[key] ?? null)
              : ponto.preco,
          indice_sentimento: ponto.indice_sentimento,
          total_posts: ponto.total_posts,
          positivos: ponto.positivos,
          negativos: ponto.negativos,
          neutros: ponto.neutros,
        };
      });

      setHistorico(formatados);

      const resSent = await fetch(`${API}/sentimento?moeda=${m}`);
      if (resSent.ok) setSentimento(await resSent.json());
    } catch (e) {
      console.error(e);
      setErro("Erro ao carregar dados. Verifique se o backend está rodando.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { checkApiHealth(); }, [checkApiHealth]);
  useEffect(() => { carregarDados(moeda, fonte, dataInicio, dataFim); }, [moeda, fonte, dataInicio, dataFim, carregarDados]);

  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(() => carregarDados(moeda, fonte, dataInicio, dataFim), 60000);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh, moeda, fonte, dataInicio, dataFim, carregarDados]);

  // ── Handlers ───────────────────────────────────────────────────

  const carregarFeedX = async () => {
    setFeedLoading(true);
    setErro(null);
    try {
      const listaPerfis = perfisX.split(",").map((p) => p.trim().replace("@", "")).filter(Boolean);
      const res = await fetch(`${API}/feed/x`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ perfis: listaPerfis, limite_por_perfil: 30 }),
      });
      if (!res.ok) { const err = await res.json(); throw new Error(err.detail || "Erro"); }
      const data = await res.json();
      setFeedTweets(data.tweets || []);
    } catch (e) {
      setErro(`Falha ao carregar feed: ${e.message}`);
    } finally {
      setFeedLoading(false);
    }
  };

  const coletarReddit = async () => {
    setColetando(true);
    setColetaMsg(null);
    try {
      const res = await fetch(`${API}/coletar/reddit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          moeda,
          subreddits: SUBREDDITS_DEFAULT[moeda] || ["CryptoCurrency"],
          limite_por_sub: 25,
          ordenacao: "new",
        }),
      });
      const data = await res.json();
      setColetaMsg(data.mensagem || "Coleta finalizada!");
      await carregarDados(moeda, "reddit", dataInicio, dataFim);
    } catch { setErro("Falha ao coletar Reddit."); }
    finally { setColetando(false); }
  };

  const coletarX = async () => {
    setColetando(true);
    setColetaMsg(null);
    try {
      const listaPerfis = perfisX.split(",").map((p) => p.trim().replace("@", "")).filter(Boolean);
      const res = await fetch(`${API}/coletar/x`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ moeda, perfis: listaPerfis, limite_por_perfil: 20 }),
      });
      const data = await res.json();
      setColetaMsg(data.mensagem || "Coleta finalizada!");
      await carregarDados(moeda, "x", dataInicio, dataFim);
    } catch { setErro("Falha ao coletar X."); }
    finally { setColetando(false); }
  };

  const analisarTexto = async () => {
    if (!textoAnalise.trim()) return;
    setAnalisando(true);
    setResultadoAnalise(null);
    try {
      const res = await fetch(`${API}/analisar-texto`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto: textoAnalise, moeda }),
      });
      if (!res.ok) throw new Error("Erro");
      setResultadoAnalise(await res.json());
    } catch { setErro("Falha ao analisar texto."); }
    finally { setAnalisando(false); }
  };

  const salvarCookiesTwitter = async () => {
    try {
      const res = await fetch(`${API}/login/x`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auth_token: loginAuthToken, ct0: loginCt0 }),
      });
      if (!res.ok) throw new Error("Erro");
      setTwitterOk(true);
      setShowLoginModal(false);
      setLoginAuthToken("");
      setLoginCt0("");
    } catch { setErro("Falha ao salvar cookies."); }
  };

  // ── Correlação ─────────────────────────────────────────────────

  const carregarCorrelacao = useCallback(async (m, f) => {
    if (f !== "reddit" && f !== "x") return;
    setCorrelacaoLoading(true);
    try {
      const fonteParam = f === "x" ? "X" : "Reddit";
      const res = await fetch(`${API}/correlacao?moeda=${m}&fonte=${fonteParam}`);
      if (res.ok) {
        const data = await res.json();
        setCorrelacao(data);
      }
    } catch { /* optional */ }
    finally { setCorrelacaoLoading(false); }
  }, []);

  useEffect(() => {
    if (fonte === "reddit" || fonte === "x") {
      carregarCorrelacao(moeda, fonte);
    } else {
      setCorrelacao(null);
    }
  }, [moeda, fonte, carregarCorrelacao]);

  // ── Gerar PDF ao clicar numa barra ──────────────────────────────

  const gerarPdfPorHora = async (data) => {
    if (!data?.timestamp_raw || gerandoPdf) return;
    setGerandoPdf(true);
    try {
      const fonteParam = fonte === "x" ? "X" : "Reddit";
      const indice = data.indice_sentimento != null ? data.indice_sentimento : "";
      const url = `${API}/gerar-relatorio?moeda=${moeda}&fonte=${fonteParam}&hora=${encodeURIComponent(data.timestamp_raw)}&indice=${indice}`;
      const res = await fetch(url, { method: "POST" });
      if (!res.ok) throw new Error("Erro ao gerar relatório");
      const info = await res.json();

      // Abrir o PDF gerado no backend (servido como static file)
      window.open(`${API}${info.url}`, "_blank");
      setColetaMsg(`PDF salvo em: ${info.caminho_completo}`);
    } catch (e) {
      setErro(`Falha ao gerar PDF: ${e.message}`);
    } finally {
      setGerandoPdf(false);
    }
  };

  // ── Visual helpers ─────────────────────────────────────────────

  const corSent = (s) => s === "positivo" ? "#22c55e" : s === "negativo" ? "#ef4444" : s === "nulo" ? "#64748b" : "#eab308";
  const sentAtualCor = corSent(sentimento?.sentimento_atual);
  const variacao = sentimento?.variacao_percentual || 0;
  const variacaoCor = variacao > 0 ? "#22c55e" : variacao < 0 ? "#ef4444" : "#9ca3af";

  const sentIndex = sentimento?.indice_sentimento || 0.5;
  const pieData = [
    { name: "Positivo", value: sentIndex },
    { name: "Negativo", value: 1 - sentIndex },
  ];

  return (
    <div className="app">
      {/* MOBILE HAMBURGER */}
      <button className="hamburger" onClick={() => setSidebarOpen(!sidebarOpen)}>
        {sidebarOpen ? "\u2715" : "\u2630"}
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
                onClick={() => { setMoeda(m); setSidebarOpen(false); }}
              >
                <span className="coin-name">{m}</span>
                {moeda === m && sentimento && (
                  <span className="coin-price">${sentimento.preco?.toLocaleString("en-US")}</span>
                )}
              </button>
            ))}
          </div>

          <div className="sidebar-status">
            <p className="sidebar-label">Status</p>
            <div className="status-item">
              <span className={`status-dot ${apiOnline ? "status-dot--ok" : "status-dot--err"}`} />
              API {apiOnline ? "Online" : "Offline"}
            </div>
            <div className="status-item">
              <span className={`status-dot ${twitterOk ? "status-dot--ok" : "status-dot--warn"}`} />
              Twitter{" "}
              {twitterOk ? "OK" : (
                <button className="link-btn" onClick={() => setShowLoginModal(true)}>Configurar</button>
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
            <h1>Dashboard <span className="highlight">{moeda}/USDT</span></h1>
            <p className="subtitle">Análise de sentimento em tempo real com inteligência artificial</p>
          </div>
          <div className="header-right">
            <label className="auto-refresh">
              <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh
            </label>
            <button className="btn btn-ghost" onClick={() => carregarDados(moeda, fonte, dataInicio, dataFim)} disabled={loading}>
              ↻ Atualizar
            </button>
          </div>
        </header>

        {/* FONTE DE DADOS */}
        <section className="controls">
          <div className="toggle-group">
            {Object.entries(FONTES).map(([key, { label, icon }]) => (
              <button
                key={key}
                className={`toggle-button ${fonte === key ? "toggle-button--active" : ""}`}
                onClick={() => setFonte(key)}
              >
                {icon} {label}
              </button>
            ))}
          </div>

          {/* Filtro de data */}
          <div className="date-filter">
            <label className="date-filter__label">
              De:
              <input
                type="date"
                className="date-filter__input"
                value={dataInicio}
                onChange={(e) => setDataInicio(e.target.value)}
              />
            </label>
            <label className="date-filter__label">
              Até:
              <input
                type="date"
                className="date-filter__input"
                value={dataFim}
                onChange={(e) => setDataFim(e.target.value)}
              />
            </label>
            {(dataInicio || dataFim) && (
              <button
                className="btn btn-ghost btn--sm"
                onClick={() => { setDataInicio(""); setDataFim(""); }}
              >
                Limpar datas
              </button>
            )}
            {fonte === "db" && (
              <button
                className="btn btn-primary btn--sm"
                disabled={sincronizando}
                onClick={async () => {
                  setSincronizando(true);
                  try {
                    const res = await fetch(`${API}/sync-binance?moeda=${moeda}&dias=7`, { method: "POST" });
                    const data = await res.json();
                    setColetaMsg(`Sincronização: ${data.novos} novos pontos salvos.`);
                    carregarDados(moeda, fonte, dataInicio, dataFim);
                  } catch (e) {
                    setErro("Erro ao sincronizar: " + e.message);
                  } finally {
                    setSincronizando(false);
                  }
                }}
              >
                {sincronizando ? "Sincronizando..." : "\u{1F504} Sincronizar Binance (7d)"}
              </button>
            )}
          </div>
        </section>

        {/* TOASTS */}
        {erro && (
          <div className="toast toast--error">
            <span>⚠ {erro}</span>
            <button className="toast-close" onClick={() => setErro(null)}>✕</button>
          </div>
        )}
        {coletaMsg && (
          <div className="toast toast--success">
            <span>✓ {coletaMsg}</span>
            <button className="toast-close" onClick={() => setColetaMsg(null)}>✕</button>
          </div>
        )}

        {/* CARDS */}
        <section className="cards">
          <div className="card card-accent">
            <div className="card-icon">{"\u{1F4B0}"}</div>
            <div>
              <p className="card-label">Preço Atual</p>
              <p className="card-value">
                {sentimento ? `$${sentimento.preco?.toLocaleString("en-US")}` : "\u2014"}
              </p>
              <p className="card-extra" style={{ color: variacaoCor }}>
                {variacao > 0 ? "\u25B2" : variacao < 0 ? "\u25BC" : "\u2014"}{" "}
                {Math.abs(variacao).toFixed(2)}%
              </p>
            </div>
          </div>

          <div className="card">
            <div className="card-icon">{"\u{1F9E0}"}</div>
            <div>
              <p className="card-label">Sentimento (Candle)</p>
              <p className="card-value" style={{ color: sentAtualCor, textTransform: "capitalize" }}>
                {sentimento?.sentimento_atual || "\u2014"}
              </p>
              <p className="card-extra">Índice: {sentimento?.indice_sentimento ?? "—"}</p>
            </div>
          </div>

          <div className="card">
            <div className="card-icon">⏰</div>
            <div>
              <p className="card-label">Última Atualização</p>
              <p className="card-value small">
                {sentimento?.ultimo_update
                  ? new Date(sentimento.ultimo_update).toLocaleString("pt-BR")
                  : "\u2014"}
              </p>
            </div>
          </div>

          <div className="card card-gauge">
            <p className="card-label">Gauge de Sentimento</p>
            <div className="gauge-chart">
              <PieChart width={120} height={70}>
                <Pie
                  data={pieData}
                  cx={60} cy={65}
                  startAngle={180} endAngle={0}
                  innerRadius={40} outerRadius={55}
                  paddingAngle={2} dataKey="value"
                >
                  <Cell fill="#22c55e" />
                  <Cell fill="#ef4444" />
                </Pie>
              </PieChart>
              <span className="gauge-label">{(sentIndex * 100).toFixed(0)}%</span>
            </div>
          </div>
        </section>

        {/* GRÁFICO */}
        <section className="chart-section">
          <div className="chart-header">
            <div>
              <h2>{fonte === "api" || fonte === "db" ? "Histórico de Preço" : "Histórico — Preço × Sentimento"}</h2>
              <span className="chart-pill">{FONTES[fonte]?.icon} {FONTES[fonte]?.label}</span>
            </div>
            {loading && <span className="spinner" />}
          </div>

          {historico.length === 0 ? (
            <div className="chart-wrapper">
              <p className="no-data">
                Nenhum dado encontrado para {moeda} + {FONTES[fonte]?.label}.
                {fonte === "reddit" && " Clique em 'Coletar Reddit' para buscar posts."}
                {fonte === "x" && " Clique em 'Coletar X' para buscar tweets."}
              </p>
            </div>
          ) : (fonte === "api" || fonte === "db") ? (
            /* ── Binance / DB: apenas preço ── */
            <div className="chart-wrapper">
              <ResponsiveContainer>
                <AreaChart data={historico}>
                  <defs>
                    <linearGradient id="gradPreco" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#60a5fa" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#60a5fa" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="timestamp" stroke="#64748b" tick={{ fontSize: 12 }} />
                  <YAxis stroke="#60a5fa" tickFormatter={(v) => v == null ? "" : `$${Number(v).toLocaleString()}`} tick={{ fontSize: 12 }} />
                  <Tooltip
                    contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", borderRadius: 12, boxShadow: "0 8px 32px rgba(0,0,0,0.5)" }}
                    itemStyle={{ fontSize: 13 }}
                    formatter={(v) => v != null ? [`$${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2 })}`, "Preço"] : ["-", "Preço"]}
                  />
                  <Legend wrapperStyle={{ fontSize: 13 }} />
                  <Area type="monotone" dataKey="preco" name="Preço (USD)" stroke="#60a5fa" strokeWidth={2} fill="url(#gradPreco)" dot={false} connectNulls={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            /* ── Reddit / X: dois gráficos empilhados ── */
            <div className="chart-dual">
              {/* Gráfico de Preço */}
              <div className="chart-wrapper chart-wrapper--half">
                <p className="chart-sublabel">Preço (USD)</p>
                <ResponsiveContainer>
                  <AreaChart data={historico}>
                    <defs>
                      <linearGradient id="gradPreco2" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#60a5fa" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#60a5fa" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="timestamp" stroke="#64748b" tick={{ fontSize: 11 }} />
                    <YAxis stroke="#60a5fa" tickFormatter={(v) => v == null ? "" : `$${Number(v).toLocaleString()}`} tick={{ fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", borderRadius: 12 }}
                      formatter={(v) => v != null ? [`$${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2 })}`, "Preço"] : ["-", "Preço"]}
                    />
                    <Area type="monotone" dataKey="preco" name="Preço (USD)" stroke="#60a5fa" strokeWidth={2} fill="url(#gradPreco2)" dot={false} connectNulls />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {/* Gráfico de Sentimento */}
              <div className="chart-wrapper chart-wrapper--half">
                <p className="chart-sublabel">
                  Sentimento (0% negativo — 50% neutro — 100% positivo)
                  {gerandoPdf && <span className="spinner spinner--inline" />}
                  <span className="chart-sublabel-hint">Clique numa barra para gerar relatório PDF</span>
                </p>
                <ResponsiveContainer>
                  <BarChart data={historico}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="timestamp" stroke="#64748b" tick={{ fontSize: 11 }} />
                    <YAxis stroke="#34d399" domain={[0, 1]} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} tick={{ fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", borderRadius: 12 }}
                      formatter={(v, name, props) => {
                        const p = props?.payload;
                        return [
                          `${(v * 100).toFixed(1)}% (${p?.total_posts || 0} posts: ${p?.positivos || 0}+ / ${p?.negativos || 0}- / ${p?.neutros || 0}=)`,
                          "Sentimento"
                        ];
                      }}
                    />
                    <ReferenceLine y={0.5} stroke="#eab308" strokeDasharray="3 3" label={{ value: "Neutro", fill: "#eab308", fontSize: 11, position: "right" }} />
                    <Bar
                      dataKey="indice_sentimento"
                      name="Sentimento"
                      radius={[4, 4, 0, 0]}
                      maxBarSize={40}
                      style={{ cursor: "pointer" }}
                      onClick={(data) => {
                        if (data?.payload) gerarPdfPorHora(data.payload);
                      }}
                    >
                      {historico.map((entry, index) => {
                        const val = entry.indice_sentimento;
                        const color = val > 0.6 ? "#22c55e" : val < 0.4 ? "#ef4444" : "#eab308";
                        return <Cell key={`cell-${index}`} fill={color} fillOpacity={0.8} />;
                      })}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </section>

        {/* CORRELAÇÃO SENTIMENTO vs PREÇO */}
        {(fonte === "reddit" || fonte === "x") && correlacao && correlacao.pontos?.length > 0 && (
          <section className="chart-section correlation-section">
            <div className="chart-header">
              <div>
                <h2>{"\u{1F4CA}"} Correlação: Sentimento vs Preço</h2>
                <span className="chart-pill">
                  {correlacao.resumo?.taxa_acerto_pct != null
                    ? `Taxa de acerto: ${correlacao.resumo.taxa_acerto_pct}%`
                    : "Sem dados comparáveis ainda"}
                </span>
              </div>
              {correlacaoLoading && <span className="spinner" />}
            </div>

            {/* Cards de resumo */}
            {correlacao.resumo?.total_comparavel > 0 && (
              <div className="corr-summary">
                <div className="corr-card corr-card--acerto">
                  <span className="corr-card-value">{correlacao.resumo.acertos}</span>
                  <span className="corr-card-label">Acertos</span>
                  <span className="corr-card-desc">Sentimento previu direção correta</span>
                </div>
                <div className="corr-card corr-card--erro">
                  <span className="corr-card-value">{correlacao.resumo.erros}</span>
                  <span className="corr-card-label">Erros</span>
                  <span className="corr-card-desc">Sentimento não correspondeu</span>
                </div>
                <div className="corr-card corr-card--taxa">
                  <span className="corr-card-value">{correlacao.resumo.taxa_acerto_pct}%</span>
                  <span className="corr-card-label">Taxa de Acerto</span>
                  <span className="corr-card-desc">De {correlacao.resumo.total_comparavel} horas comparáveis</span>
                </div>
              </div>
            )}

            {/* Gráfico comparativo */}
            <div className="chart-wrapper">
              <ResponsiveContainer>
                <BarChart data={correlacao.pontos}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="hora" stroke="#64748b" tick={{ fontSize: 11 }} />
                  <YAxis yAxisId="sent" stroke="#a78bfa" domain={[0, 1]} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} tick={{ fontSize: 11 }} />
                  <YAxis yAxisId="preco" orientation="right" stroke="#60a5fa" tickFormatter={(v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`} tick={{ fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", borderRadius: 12 }}
                    formatter={(value, name) => {
                      if (name === "Sentimento") return [`${(value * 100).toFixed(1)}%`, name];
                      if (name === "Variação Preço") return [`${value > 0 ? "+" : ""}${value.toFixed(3)}%`, name];
                      return [value, name];
                    }}
                    labelFormatter={(label) => `Hora: ${label}`}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <ReferenceLine yAxisId="sent" y={0.5} stroke="#eab308" strokeDasharray="3 3" />
                  <ReferenceLine yAxisId="preco" y={0} stroke="#64748b" strokeDasharray="3 3" />
                  <Bar yAxisId="sent" dataKey="sentimento_medio" name="Sentimento" radius={[4, 4, 0, 0]} maxBarSize={30}>
                    {correlacao.pontos.map((entry, index) => {
                      const val = entry.sentimento_medio;
                      const color = val > 0.6 ? "#a78bfa" : val < 0.4 ? "#f472b6" : "#fbbf24";
                      return <Cell key={`cs-${index}`} fill={color} fillOpacity={0.7} />;
                    })}
                  </Bar>
                  <Bar yAxisId="preco" dataKey="variacao_preco" name="Variação Preço" radius={[4, 4, 0, 0]} maxBarSize={30}>
                    {correlacao.pontos.map((entry, index) => {
                      const v = entry.variacao_preco;
                      const color = v > 0 ? "#22c55e" : v < 0 ? "#ef4444" : "#64748b";
                      return <Cell key={`cp-${index}`} fill={color} fillOpacity={0.7} />;
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Tabela detalhada */}
            <div className="corr-table-wrapper">
              <table className="corr-table">
                <thead>
                  <tr>
                    <th>Hora</th>
                    <th>Sentimento</th>
                    <th>Direção Sent.</th>
                    <th>Var. Preço</th>
                    <th>Direção Preço</th>
                    <th>Resultado</th>
                  </tr>
                </thead>
                <tbody>
                  {correlacao.pontos.map((p, i) => (
                    <tr key={i} className={p.acertou === true ? "corr-row--ok" : p.acertou === false ? "corr-row--fail" : ""}>
                      <td>{p.hora}</td>
                      <td>{(p.sentimento_medio * 100).toFixed(1)}%</td>
                      <td>
                        <span className={`corr-dir corr-dir--${p.sentimento_direcao}`}>
                          {p.sentimento_direcao === "positivo" ? "▲ Positivo" : p.sentimento_direcao === "negativo" ? "▼ Negativo" : "● Neutro"}
                        </span>
                      </td>
                      <td>{p.variacao_preco != null ? `${p.variacao_preco > 0 ? "+" : ""}${p.variacao_preco.toFixed(3)}%` : "—"}</td>
                      <td>
                        <span className={`corr-dir corr-dir--${p.preco_direcao === "subiu" ? "positivo" : p.preco_direcao === "desceu" ? "negativo" : "neutro"}`}>
                          {p.preco_direcao === "subiu" ? "▲ Subiu" : p.preco_direcao === "desceu" ? "▼ Desceu" : p.preco_direcao === "estável" ? "● Estável" : "—"}
                        </span>
                      </td>
                      <td>
                        {p.acertou === true && <span className="corr-badge corr-badge--ok">✓ Acerto</span>}
                        {p.acertou === false && <span className="corr-badge corr-badge--fail">✗ Erro</span>}
                        {p.acertou == null && <span className="corr-badge corr-badge--na">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* AÇÕES DE COLETA */}
        {fonte === "reddit" && (
          <section className="action-bar">
            <button className="btn btn-primary" onClick={coletarReddit} disabled={coletando}>
              {coletando ? "Coletando..." : "\uD83D\uDD34 Coletar Reddit agora"}
            </button>
            <span className="action-hint">
              Subreddits: {(SUBREDDITS_DEFAULT[moeda] || []).join(", ")}
            </span>
          </section>
        )}

        {fonte === "x" && (
          <section className="action-bar action-bar--col">
            <div className="input-group">
              <label>Perfis do X (separados por vírgula):</label>
              <input
                className="input"
                type="text"
                value={perfisX}
                onChange={(e) => setPerfisX(e.target.value)}
                placeholder="whale_alert, elonmusk, VitalikButerin"
              />
            </div>
            <div className="btn-row">
              <button className="btn btn-primary" disabled={feedLoading} onClick={carregarFeedX}>
                {feedLoading ? "Carregando..." : "\uD83D\uDC26 Carregar Feed"}
              </button>
              <button className="btn btn-secondary" onClick={coletarX} disabled={coletando}>
                {coletando ? "Analisando..." : "\uD83E\uDDE0 Analisar e salvar"}
              </button>
            </div>
          </section>
        )}

        {/* AN\u00C1LISE DE TEXTO LIVRE */}
        <section className="analise-section">
          <h2>{"\u{1F9E0}"} Análise de Texto Livre</h2>
          <p className="analise-desc">Cole qualquer texto e o modelo BERT vai analisar o sentimento.</p>
          <div className="analise-box">
            <textarea
              className="textarea"
              value={textoAnalise}
              onChange={(e) => setTextoAnalise(e.target.value)}
              placeholder="Cole aqui uma notícia, tweet, comentário do Reddit..."
              rows={4}
            />
            <button className="btn btn-primary" onClick={analisarTexto} disabled={analisando || !textoAnalise.trim()}>
              {analisando ? "Analisando..." : "Analisar com BERT"}
            </button>
            {resultadoAnalise && (
              <div className="analise-result">
                <div className="analise-badge" style={{ backgroundColor: corSent(resultadoAnalise.sentimento) }}>
                  {resultadoAnalise.sentimento}
                </div>
                <div className="analise-stats">
                  <span>Índice: <strong>{resultadoAnalise.indice}</strong></span>
                  <span>Score BERT: <strong>{resultadoAnalise.score_bert}</strong></span>
                  <span>Label: <strong>{resultadoAnalise.label_bert}</strong></span>
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
                      <span className="tweet-name">{tw.nome_exibicao || tw.perfil}</span>
                      <span className="tweet-handle">{tw.perfil}</span>
                      <span className="tweet-dot">·</span>
                      <span className="tweet-time">
                        {new Date(tw.timestamp).toLocaleString("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
                      </span>
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
                      <span className="tweet-sentiment" style={{ backgroundColor: corSent(tw.sentimento) }}>
                        {tw.sentimento === "nulo" ? "não-crypto" : `${tw.sentimento} (${tw.score_bert})`}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>

      {/* TWITTER LOGIN MODAL */}
      {showLoginModal && (
        <div className="modal-overlay" onClick={() => setShowLoginModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{"\u{1F426}"} Configurar Twitter</h3>
            <p className="modal-desc">
              Para coletar tweets, forneça cookies de autenticação do Twitter:
            </p>
            <ol className="modal-steps">
              <li>Abra <strong>x.com</strong> no Chrome e faça login</li>
              <li>Pressione <strong>F12</strong> → aba <strong>Application</strong></li>
              <li>Menu lateral: <strong>Cookies → https://x.com</strong></li>
              <li>Copie <strong>auth_token</strong> e <strong>ct0</strong></li>
            </ol>
            <div className="modal-inputs">
              <input className="input" placeholder="auth_token" value={loginAuthToken} onChange={(e) => setLoginAuthToken(e.target.value)} />
              <input className="input" placeholder="ct0" value={loginCt0} onChange={(e) => setLoginCt0(e.target.value)} />
            </div>
            <div className="modal-actions">
              <button className="btn btn-primary" onClick={salvarCookiesTwitter} disabled={!loginAuthToken || !loginCt0}>Salvar cookies</button>
              <button className="btn btn-ghost" onClick={() => setShowLoginModal(false)}>Cancelar</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
