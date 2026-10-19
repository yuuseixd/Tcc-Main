import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, API_URL, ApiError } from "../api";
import {
  INTERVALOS_COLETA,
  SUBREDDITS_DEFAULT,
  foiCancelada,
  listaDePerfis,
} from "../constants";
import { dataCurta, numero, percentual } from "../format";
import {
  Dica,
  Pill,
  Recolhivel,
  Seg,
  Spinner,
  Topline,
  Vazio,
  corSentimento,
  rotuloSentimento,
} from "../components/ui";

const VISOES = [
  { valor: "X", rotulo: "X" },
  { valor: "Reddit", rotulo: "Reddit" },
  { valor: "Comparar", rotulo: "Comparar" },
];
const MARGEM = { top: 8, right: 8, left: 0, bottom: 0 };

export default function Redes({
  moeda, setMoeda, notify, perfisX, setPerfisX, twitterOk, abrirLogin,
  redditOauth, autoColeta, setAutoColeta,
}) {
  const [visao, setVisao] = useState(
    () => localStorage.getItem("senty_visao_redes") || "X",
  );
  useEffect(() => {
    localStorage.setItem("senty_visao_redes", visao);
  }, [visao]);

  return (
    <section className="screen" aria-label="X e Reddit">
      <Topline titulo="X e Reddit" moeda={moeda} setMoeda={setMoeda}>
        <Seg label="Visão" value={visao} options={VISOES} onChange={setVisao} />
      </Topline>
      {visao === "Comparar" ? (
        <Comparar moeda={moeda} notify={notify} />
      ) : (
        <FonteView
          key={visao}
          fonte={visao}
          moeda={moeda}
          notify={notify}
          perfisX={perfisX}
          setPerfisX={setPerfisX}
          twitterOk={twitterOk}
          abrirLogin={abrirLogin}
          redditOauth={redditOauth}
          autoColeta={autoColeta}
          setAutoColeta={setAutoColeta}
        />
      )}
    </section>
  );
}

/* ── X ou Reddit: coleta, correlação e feed ─────────────────────────── */

function FonteView({
  fonte, moeda, notify, perfisX, setPerfisX, twitterOk, abrirLogin,
  redditOauth, autoColeta, setAutoColeta,
}) {
  const ehX = fonte === "X";
  const [corr, setCorr] = useState(null);
  const [carregando, setCarregando] = useState(true);
  const [coletando, setColetando] = useState(false);
  const [feed, setFeed] = useState([]);
  const [feedCarregando, setFeedCarregando] = useState(false);
  const [feedDe, setFeedDe] = useState("");
  const [feedAte, setFeedAte] = useState("");
  const [gerandoPdf, setGerandoPdf] = useState(false);
  const abortar = useRef(null);

  const carregarCorr = useCallback(async () => {
    abortar.current?.abort();
    const controle = new AbortController();
    abortar.current = controle;
    setCarregando(true);
    try {
      setCorr(await api.correlacao(moeda, fonte, controle.signal));
    } catch (e) {
      if (!foiCancelada(e)) setCorr(null);
    } finally {
      if (abortar.current === controle) setCarregando(false);
    }
  }, [moeda, fonte]);

  useEffect(() => {
    carregarCorr();
    return () => abortar.current?.abort();
  }, [carregarCorr]);

  const coletar = useCallback(
    async (silencioso = false) => {
      const perfis = listaDePerfis(perfisX);
      if (ehX && perfis.length === 0) {
        notify.erro("Informe ao menos um perfil do X.");
        return;
      }
      if (!silencioso) setColetando(true);
      try {
        const dados = ehX
          ? await api.coletarX({ moeda, perfis, limite_por_perfil: 100 })
          : await api.coletarReddit({
              moeda,
              subreddits: SUBREDDITS_DEFAULT[moeda] || ["CryptoCurrency"],
              limite_por_sub: 25,
              ordenacao: "new",
            });
        notify.ok(`${silencioso ? "[Auto] " : ""}${dados.mensagem}`);
        notify.aviso(dados.avisos?.length ? dados.avisos.join(" · ") : null);
        carregarCorr();
      } catch (e) {
        if (!silencioso) {
          notify.erro(
            e instanceof ApiError
              ? `Falha na coleta: ${e.message}`
              : "Falha ao coletar. Verifique o servidor.",
          );
        }
      } finally {
        if (!silencioso) setColetando(false);
      }
    },
    [ehX, moeda, perfisX, notify, carregarCorr],
  );

  useEffect(() => {
    if (autoColeta <= 0) return undefined;
    const id = setInterval(() => coletar(true), autoColeta * 60 * 1000);
    return () => clearInterval(id);
  }, [autoColeta, coletar]);

  const carregarFeed = async () => {
    const perfis = listaDePerfis(perfisX);
    if (perfis.length === 0) {
      notify.erro("Informe ao menos um perfil do X.");
      return;
    }
    setFeedCarregando(true);
    try {
      const dados = await api.feedX({
        perfis,
        limite_por_perfil: 100,
        data_inicio: feedDe || undefined,
        data_fim: feedAte || undefined,
      });
      setFeed(dados.tweets || []);
      if (!(dados.tweets || []).length && !(dados.avisos || []).length) {
        notify.aviso("Nenhum tweet retornado para esses perfis nesse período.");
      } else {
        notify.aviso(dados.avisos?.length ? dados.avisos.join(" · ") : null);
      }
    } catch (e) {
      notify.erro(`Falha ao carregar o feed: ${e.message}`);
    } finally {
      setFeedCarregando(false);
    }
  };

  const gerarPdf = async () => {
    if (gerandoPdf) return;
    setGerandoPdf(true);
    try {
      const info = await api.gerarRelatorioCorrelacao(moeda, fonte);
      window.open(`${API_URL}${info.url}`, "_blank", "noopener");
      notify.ok(`Relatório salvo: ${info.arquivo}`);
    } catch (e) {
      notify.erro(`Falha ao gerar o PDF: ${e.message}`);
    } finally {
      setGerandoPdf(false);
    }
  };

  const r = corr?.resumo;
  const pontos = corr?.pontos || [];
  const comVariacao = pontos.filter((p) => p.variacao_preco != null);
  // Eixo do preço simétrico em torno de zero, para o zero dos dois eixos coincidir.
  const limitePreco = Math.max(
    0.5,
    Math.ceil(Math.max(0, ...comVariacao.map((p) => Math.abs(p.variacao_preco))) * 2) / 2,
  );

  return (
    <>
      <div className="card stack">
        {ehX ? (
          <>
            <div className="row-between">
              <h2>Coleta do X</h2>
              <Pill tom={twitterOk ? "warn" : "neg"}>
                {twitterOk ? "Coleta ao vivo instável" : "Cookies não configurados"}
              </Pill>
            </div>
            <p className="hint">
              A coleta ao vivo do X está instável. As análises usam os posts já salvos no banco.
            </p>
            <div className="fgrid">
              <div className="field">
                <label htmlFor="x-perfis">Perfis (separados por vírgula)</label>
                <input id="x-perfis" value={perfisX}
                  onChange={(e) => setPerfisX(e.target.value)}
                  placeholder="whale_alert, elonmusk" />
              </div>
              <div className="field">
                <label htmlFor="x-de">Feed de</label>
                <input id="x-de" type="date" value={feedDe} max={feedAte || undefined}
                  onChange={(e) => setFeedDe(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="x-ate">até</label>
                <input id="x-ate" type="date" value={feedAte} min={feedDe || undefined}
                  onChange={(e) => setFeedAte(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="x-auto">Coleta automática</label>
                <select id="x-auto" value={autoColeta}
                  onChange={(e) => setAutoColeta(parseInt(e.target.value, 10))}>
                  {INTERVALOS_COLETA.map((i) => (
                    <option key={i.valor} value={i.valor}>{i.rotulo}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="btns">
              <button className="btn" type="button" onClick={carregarFeed} disabled={feedCarregando}>
                {feedCarregando ? "Carregando..." : "Carregar feed"}
              </button>
              <button className="btn primary" type="button" onClick={() => coletar(false)} disabled={coletando}>
                {coletando ? "Analisando..." : "Analisar e salvar"}
              </button>
              {!twitterOk && (
                <button className="btn" type="button" onClick={abrirLogin}>Configurar cookies</button>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="row-between">
              <h2>Coleta do Reddit</h2>
              <Pill tom={redditOauth ? "pos" : "warn"}>
                {redditOauth ? "Chaves configuradas" : "Sem chaves do Reddit"}
              </Pill>
            </div>
            <p className="hint">
              {redditOauth
                ? "A coleta usa o acesso oficial do Reddit."
                : "Sem as chaves de acesso, o Reddit costuma recusar a coleta (erro 403). Enquanto isso, as análises usam os posts já salvos."}
            </p>
            <div className="chips">
              {(SUBREDDITS_DEFAULT[moeda] || ["CryptoCurrency"]).map((s) => (
                <Pill key={s} tom="acc">r/{s}</Pill>
              ))}
            </div>
            <div className="field" style={{ maxWidth: 260 }}>
              <label htmlFor="r-auto">Coleta automática</label>
              <select id="r-auto" value={autoColeta}
                onChange={(e) => setAutoColeta(parseInt(e.target.value, 10))}>
                {INTERVALOS_COLETA.map((i) => (
                  <option key={i.valor} value={i.valor}>{i.rotulo}</option>
                ))}
              </select>
            </div>
            <div className="btns">
              <button className="btn primary" type="button" onClick={() => coletar(false)} disabled={coletando}>
                {coletando ? "Coletando..." : "Coletar Reddit agora"}
              </button>
            </div>
          </>
        )}
      </div>

      <div className="card stack">
        <div className="row-between">
          <div>
            <h2>Correlação: sentimento e preço</h2>
            <p className="hint">
              {r?.taxa_acerto_pct != null
                ? `Taxa de acerto ${numero(r.taxa_acerto_pct, 1)}% em ${r.total_comparavel} horas`
                : "Sem horas comparáveis ainda"}
            </p>
          </div>
          <div className="btns">
            {carregando && <Spinner />}
            <button className="btn primary sm" type="button" onClick={gerarPdf}
              disabled={gerandoPdf || pontos.length === 0}>
              {gerandoPdf ? "Gerando..." : "Gerar PDF"}
            </button>
          </div>
        </div>

        {pontos.length === 0 ? (
          <Vazio
            titulo={carregando ? "Carregando..." : `Sem posts de ${fonte} para ${moeda}`}
            texto={carregando ? undefined : "Colete posts acima para ver a correlação."}
          />
        ) : (
          <>
            {r.total_comparavel > 0 && !r.amostra_suficiente && (
              <div className="callout">
                Amostra pequena ({r.total_comparavel} horas comparáveis). A taxa de acerto é
                indicativa e ainda não sustenta conclusão estatística. Colete mais dados.
              </div>
            )}
            {r.total_comparavel > 0 && (
              <div className="stat-row">
                <div className="stat ok"><span className="l">Acertos</span><span className="v">{r.acertos}</span><span className="s">Sentimento previu a direção</span></div>
                <div className="stat bad"><span className="l">Erros</span><span className="v">{r.erros}</span><span className="s">Não correspondeu</span></div>
                <div className="stat"><span className="l">Taxa de acerto</span><span className="v">{numero(r.taxa_acerto_pct, 1)}%</span><span className="s">de {r.total_comparavel} horas comparáveis</span></div>
                <div className="stat"><span className="l">Retorno após positivo</span><span className="v">{percentual(r.retorno_medio_apos_positivo, 3)}</span><span className="s">média em 1 hora</span></div>
                <div className="stat"><span className="l">Retorno após negativo</span><span className="v">{percentual(r.retorno_medio_apos_negativo, 3)}</span><span className="s">média em 1 hora</span></div>
              </div>
            )}

            <div className="chart lg" role="img" aria-label="Sentiment Score e variação de preço por hora">
              <ResponsiveContainer minWidth={0} minHeight={200}>
                <BarChart data={comVariacao.length ? comVariacao : pontos} margin={MARGEM}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="hora" tickLine={false} minTickGap={40} />
                  <YAxis yAxisId="sent" width={46} domain={[-1, 1]} tickLine={false} axisLine={false}
                    tickFormatter={(v) => numero(v, 1)} />
                  <YAxis yAxisId="preco" orientation="right" width={58} tickLine={false} axisLine={false}
                    domain={[-limitePreco, limitePreco]}
                    tickFormatter={(v) => percentual(v, 1)} />
                  <ReferenceLine yAxisId="sent" y={0} stroke="var(--line)" />
                  <Tooltip content={<Dica formatar={(v, p) =>
                    p.dataKey === "sentiment_score" ? numero(v, 2) : percentual(v, 3)} />} />
                  <Bar yAxisId="sent" dataKey="sentiment_score" name="Sentiment Score"
                    fill="var(--series)" maxBarSize={14} radius={[2, 2, 2, 2]} />
                  <Bar yAxisId="preco" dataKey="variacao_preco" name="Variação do preço"
                    maxBarSize={14} radius={[2, 2, 2, 2]}>
                    {(comVariacao.length ? comVariacao : pontos).map((p, i) => (
                      <Cell key={i} fill={p.variacao_preco >= 0 ? "var(--pos)" : "var(--neg)"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="legend">
              <span style={{ "--c": "var(--series)" }}>Sentiment Score (esquerda)</span>
              <span style={{ "--c": "var(--pos)" }}>Preço subiu (direita)</span>
              <span style={{ "--c": "var(--neg)" }}>Preço caiu (direita)</span>
            </div>

            <Recolhivel titulo={`Detalhamento por hora (${pontos.length})`}>
              <div className="tbl-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Hora (UTC)</th><th>Score</th><th>Posts</th><th>Direção</th>
                      <th>Var. preço</th><th>Ret. 1h</th><th>Ret. 4h</th><th>Resultado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...pontos].reverse().map((p) => (
                      <tr key={p.timestamp}
                        className={p.acertou === true ? "ok" : p.acertou === false ? "bad" : ""}>
                        <td>{p.hora}</td>
                        <td>{numero(p.sentiment_score, 2)}</td>
                        <td>{p.total} ({p.positivos}+/{p.negativos}−)</td>
                        <td><Pill tom={corSentimento(p.sentimento_direcao)}>{rotuloSentimento(p.sentimento_direcao)}</Pill></td>
                        <td>{percentual(p.variacao_preco, 3)}</td>
                        <td>{percentual(p.retorno_1h, 3)}</td>
                        <td>{percentual(p.retorno_4h, 3)}</td>
                        <td>
                          {p.acertou === true && <Pill tom="pos">Acerto</Pill>}
                          {p.acertou === false && <Pill tom="neg">Erro</Pill>}
                          {p.acertou == null && <Pill>—</Pill>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Recolhivel>
          </>
        )}
      </div>

      {ehX && feed.length > 0 && (
        <div className="card">
          <div className="row-between" style={{ marginBottom: 6 }}>
            <h2>Timeline do X</h2>
            <Pill>{feed.length} tweets</Pill>
          </div>
          {feed.map((tw, i) => (
            <div className="tweet" key={tw.tweet_id || i}>
              <div className="tw-top">
                <div className="av">
                  {tw.avatar ? <img src={tw.avatar} alt="" /> :
                    (tw.nome_exibicao || tw.perfil || "?")[0].toUpperCase()}
                </div>
                <b>{tw.nome_exibicao || tw.perfil}</b>
                <span>{tw.perfil}</span>
                <span>{dataCurta(tw.timestamp)}</span>
              </div>
              <p className="tw-text">{tw.texto}</p>
              <div className="btns">
                {tw.sentimento && (
                  <Pill tom={corSentimento(tw.sentimento)}>
                    {rotuloSentimento(tw.sentimento)}
                    {tw.score_bert != null && ` (${numero(tw.score_bert, 2)})`}
                  </Pill>
                )}
                <span className="hint">
                  {tw.replies} respostas · {tw.retweets} reposts · {tw.likes} curtidas
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ── Comparar X e Reddit ────────────────────────────────────────────── */

function Comparar({ moeda, notify }) {
  const [dados, setDados] = useState(null);
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    const controle = new AbortController();
    setCarregando(true);
    api
      .comparacaoFontes(moeda, controle.signal)
      .then(setDados)
      .catch((e) => {
        if (foiCancelada(e)) return;
        setDados(null);
        notify.erro(`Falha ao comparar as redes: ${e.message}`);
      })
      .finally(() => {
        if (!controle.signal.aborted) setCarregando(false);
      });
    return () => controle.abort();
  }, [moeda, notify]);

  if (!dados) {
    return (
      <div className="card">
        <Vazio titulo={carregando ? "Carregando..." : "Sem dados para comparar"} />
      </div>
    );
  }

  const { fontes, comparacao: c, serie } = dados;
  const X = fontes.X, R = fontes.Reddit;
  const parcela = (f) => {
    const total = f.posts.positivos + f.posts.negativos + f.posts.neutros;
    return total ? (f.posts.positivos / total) * 100 : 0;
  };

  const Metrica = ({ titulo, sub, a, b, formatar, maximo }) => (
    <div className="card metric">
      <h3>{titulo}<small>{sub}</small></h3>
      {[["X", a, ""], ["Reddit", b, "reddit"]].map(([nome, v, cls]) => (
        <div className="row" key={nome}>
          <b>{nome}</b>
          <div className={`bar ${cls}`}>
            <i style={{ width: `${Math.max(2, Math.min(100, ((v ?? 0) / maximo) * 100))}%` }} />
          </div>
          <span className="val">{v == null ? "—" : formatar(v)}</span>
        </div>
      ))}
    </div>
  );

  // O X tem posts de anos atrás; mostrar tudo achata o gráfico. A janela começa
  // na primeira hora com posts do Reddit (ou nas últimas 100 horas, se não houver).
  const primeiraDoReddit = serie.findIndex((p) => p.Reddit != null);
  const visiveis = primeiraDoReddit >= 0 ? serie.slice(primeiraDoReddit) : serie.slice(-100);
  const rotuloData = (ts) => {
    const d = new Date(ts);
    return Number.isNaN(d.getTime())
      ? ""
      : new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC", day: "2-digit", month: "2-digit", year: "2-digit" }).format(d);
  };

  const retornoMax = Math.max(
    0.5,
    Math.abs(X.resumo.retorno_medio_apos_positivo ?? 0),
    Math.abs(R.resumo.retorno_medio_apos_positivo ?? 0),
  );

  return (
    <>
      {c.aviso && <div className="callout">{c.aviso}</div>}
      <div className="stat-row">
        <div className="stat"><span className="l">Horas com posts nas duas redes</span><span className="v">{c.horas_em_comum} de 10</span></div>
        <div className="stat"><span className="l">Concordância entre redes</span><span className="v">{c.concordancia_pct != null ? `${numero(c.concordancia_pct, 0)}%` : "sem dados"}</span></div>
        <div className="stat"><span className="l">Correlação entre redes</span><span className="v">{c.correlacao_sentimento != null ? numero(c.correlacao_sentimento, 2) : "sem dados"}</span></div>
      </div>
      <div className="two">
        <Metrica titulo="Acerto da direção do preço" sub="% das horas comparáveis"
          a={X.resumo.taxa_acerto_pct} b={R.resumo.taxa_acerto_pct}
          formatar={(v) => `${numero(v, 1)}%`} maximo={100} />
        <Metrica titulo="Retorno após hora positiva" sub="% em 1 hora"
          a={X.resumo.retorno_medio_apos_positivo} b={R.resumo.retorno_medio_apos_positivo}
          formatar={(v) => `${numero(v, 2)}%`} maximo={retornoMax} />
        <Metrica titulo="Parcela de posts positivos" sub="do total de cada rede"
          a={parcela(X)} b={parcela(R)} formatar={(v) => `${numero(v, 1)}%`} maximo={100} />
      </div>

      {visiveis.length > 0 && (
        <div className="card stack">
          <div>
            <h2>Sentimento hora a hora</h2>
            <p className="hint">
              Cada ponto é o Sentiment Score de uma hora em que a rede teve posts
              {primeiraDoReddit >= 0
                ? `, a partir de ${rotuloData(visiveis[0].timestamp)} (primeiros posts do Reddit).`
                : " (últimas 100 horas com posts)."}
            </p>
          </div>
          <div className="chart" role="img" aria-label="Sentiment Score do X e do Reddit por hora">
            <ResponsiveContainer minWidth={0} minHeight={180}>
              <LineChart data={visiveis} margin={MARGEM}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="timestamp" tickLine={false} minTickGap={50} tickFormatter={rotuloData} />
                <YAxis width={46} domain={[-1, 1]} tickLine={false} axisLine={false}
                  tickFormatter={(v) => numero(v, 1)} />
                <ReferenceLine y={0} stroke="var(--line)" />
                <Tooltip content={<Dica formatar={(v) => numero(v, 2)} />} />
                <Line dataKey="X" name="X" stroke="var(--accent)" strokeWidth={0}
                  dot={{ r: 3.5, fill: "var(--accent)", stroke: "none" }} isAnimationActive={false} />
                <Line dataKey="Reddit" name="Reddit" stroke="var(--series)" strokeWidth={0}
                  dot={{ r: 3.5, fill: "var(--series)", stroke: "none" }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="legend">
            <span style={{ "--c": "var(--accent)" }}>X</span>
            <span style={{ "--c": "var(--series)" }}>Reddit</span>
          </div>
        </div>
      )}
      <p className="hint">
        O X tem {numero(X.resumo.total_posts)} posts salvos e o Reddit tem {numero(R.resumo.total_posts)}.
        Com mais coleta do Reddit, esta tela fica mais confiável.
      </p>
    </>
  );
}
