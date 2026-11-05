import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, API_URL, ApiError } from "../api";
import {
  INTERVALOS_COLETA,
  estadoColetaX,
  foiCancelada,
  listaDePerfis,
} from "../constants";
import { dataCurta, numero, percentual } from "../format";
import {
  Dica,
  Pill,
  Recolhivel,
  Spinner,
  Topline,
  Vazio,
  corSentimento,
  rotuloSentimento,
} from "../components/ui";

const MARGEM = { top: 8, right: 8, left: 0, bottom: 0 };
const FONTE = "X";

export default function Redes({
  moeda, setMoeda, notify, perfisX, setPerfisX, twitterOk, xColeta, abrirLogin,
  autoColeta, setAutoColeta, atualizarSaude,
}) {
  return (
    <section className="screen" aria-label="X / Twitter">
      <Topline titulo="X / Twitter" moeda={moeda} setMoeda={setMoeda} />
      <FonteView
        fonte={FONTE}
        moeda={moeda}
        notify={notify}
        perfisX={perfisX}
        setPerfisX={setPerfisX}
        twitterOk={twitterOk}
        xColeta={xColeta}
        atualizarSaude={atualizarSaude}
        abrirLogin={abrirLogin}
        autoColeta={autoColeta}
        setAutoColeta={setAutoColeta}
      />
    </section>
  );
}

/* ── X: coleta, correlação e feed ───────────────────────────────────── */

function FonteView({
  fonte, moeda, notify, perfisX, setPerfisX, twitterOk, xColeta, abrirLogin,
  autoColeta, setAutoColeta, atualizarSaude,
}) {
  const [corr, setCorr] = useState(null);
  const [carregando, setCarregando] = useState(true);
  const [coletando, setColetando] = useState(false);
  const [feed, setFeed] = useState([]);
  const [feedCarregando, setFeedCarregando] = useState(false);
  const [feedDe, setFeedDe] = useState("");
  const [feedAte, setFeedAte] = useState("");
  // Período da análise: filtra o gráfico, a tabela e o "PDF do período".
  const [corrDe, setCorrDe] = useState("");
  const [corrAte, setCorrAte] = useState("");
  const [gerandoPdf, setGerandoPdf] = useState(null);
  const abortar = useRef(null);
  const temPeriodo = Boolean(corrDe || corrAte);

  const carregarCorr = useCallback(async () => {
    abortar.current?.abort();
    const controle = new AbortController();
    abortar.current = controle;
    setCarregando(true);
    try {
      setCorr(await api.correlacao(moeda, fonte, corrDe, corrAte, controle.signal));
    } catch (e) {
      if (!foiCancelada(e)) setCorr(null);
    } finally {
      if (abortar.current === controle) setCarregando(false);
    }
  }, [moeda, fonte, corrDe, corrAte]);

  useEffect(() => {
    carregarCorr();
    return () => abortar.current?.abort();
  }, [carregarCorr]);

  const coletar = useCallback(
    async (silencioso = false) => {
      const perfis = listaDePerfis(perfisX);
      if (perfis.length === 0) {
        notify.erro("Informe ao menos um perfil do X.");
        return;
      }
      if (!silencioso) setColetando(true);
      try {
        const dados = await api.coletarX({ moeda, perfis, limite_por_perfil: 100 });
        notify.ok(`${silencioso ? "[Auto] " : ""}${dados.mensagem}`);
        notify.aviso(dados.avisos?.length ? dados.avisos.join(" · ") : null);
        carregarCorr();
        atualizarSaude?.();
      } catch (e) {
        if (!silencioso) {
          notify.erro(
            e instanceof ApiError
              ? `Falha na coleta: ${e.message}`
              : "Falha ao coletar. Verifique o servidor.",
          );
        }
        atualizarSaude?.();
      } finally {
        if (!silencioso) setColetando(false);
      }
    },
    [moeda, perfisX, notify, carregarCorr, atualizarSaude],
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
      atualizarSaude?.();
    }
  };

  // tipo "periodo" usa as datas da análise; "completo" ignora o filtro.
  const gerarPdf = async (tipo) => {
    if (gerandoPdf) return;
    setGerandoPdf(tipo);
    try {
      const info = tipo === "periodo"
        ? await api.gerarRelatorioCorrelacao(moeda, fonte, corrDe, corrAte)
        : await api.gerarRelatorioCorrelacao(moeda, fonte);
      window.open(`${API_URL}${info.url}`, "_blank", "noopener");
      notify.ok(`Relatório salvo: ${info.arquivo}`);
    } catch (e) {
      notify.erro(`Falha ao gerar o PDF: ${e.message}`);
    } finally {
      setGerandoPdf(null);
    }
  };

  const statusX = estadoColetaX(twitterOk, xColeta);
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
        <>
            <div className="row-between">
              <h2>Coleta do X</h2>
              <Pill tom={statusX.tom}>{statusX.rotulo}</Pill>
            </div>
            <p className="hint">{statusX.dica}</p>
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
            <button className="btn primary sm" type="button" onClick={() => gerarPdf("periodo")}
              disabled={Boolean(gerandoPdf) || !temPeriodo || pontos.length === 0}
              title={temPeriodo ? "PDF apenas do período selecionado" : "Escolha um período abaixo"}>
              {gerandoPdf === "periodo" ? "Gerando..." : "PDF do período"}
            </button>
            <button className="btn sm" type="button" onClick={() => gerarPdf("completo")}
              disabled={Boolean(gerandoPdf)} title="PDF com toda a análise salva no banco">
              {gerandoPdf === "completo" ? "Gerando..." : "PDF completo"}
            </button>
          </div>
        </div>

        <div className="btns">
          <label className="hint" htmlFor={`c-de-${fonte}`}>Período da análise: de</label>
          <input id={`c-de-${fonte}`} type="date" className="sel" value={corrDe}
            max={corrAte || undefined} onChange={(e) => setCorrDe(e.target.value)} />
          <label className="hint" htmlFor={`c-ate-${fonte}`}>até</label>
          <input id={`c-ate-${fonte}`} type="date" className="sel" value={corrAte}
            min={corrDe || undefined} onChange={(e) => setCorrAte(e.target.value)} />
          {temPeriodo && (
            <button className="btn sm" type="button"
              onClick={() => { setCorrDe(""); setCorrAte(""); }}>
              Limpar datas
            </button>
          )}
        </div>

        {pontos.length === 0 ? (
          <Vazio
            titulo={carregando ? "Carregando..."
              : temPeriodo ? `Sem posts de ${fonte} nesse período`
              : `Sem posts de ${fonte} para ${moeda}`}
            texto={carregando ? undefined
              : temPeriodo ? "Ajuste as datas ou gere o PDF completo."
              : "Colete posts acima para ver a correlação."}
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
                <div className="stat ok"><span className="l">Acertos</span><span className="v">{r.acertos}</span><span className="s">Mesma direção do candle da hora</span></div>
                <div className="stat bad"><span className="l">Erros</span><span className="v">{r.erros}</span><span className="s">Direções opostas</span></div>
                <div className="stat"><span className="l">Taxa de acerto</span><span className="v">{numero(r.taxa_acerto_pct, 1)}%</span>
                  <span className="s">
                    {r.ic95_pct ? `IC 95%: ${numero(r.ic95_pct[0], 0)}–${numero(r.ic95_pct[1], 0)}% · ` : ""}
                    p = {numero(r.p_valor, 3)} {r.significativo ? "(significativo)" : "(não significativo)"}
                  </span></div>
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

            {corr?.analise_preditiva?.length > 0 && (
              <div className="stack">
                <h3>O sentimento antecipa o preço?</h3>
                <p className="hint">
                  "Mesma hora" compara com o candle da própria hora (reação). Os horizontes +1h, +4h
                  e +24h partem do fechamento da hora dos posts. Acerto significativo exige p &lt; 0,05
                  e vencer as estratégias ingênuas medidas nas mesmas horas.
                </p>
                <div className="tbl-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Horizonte</th><th>Horas</th><th>Acerto</th><th>IC 95%</th><th>p-valor</th>
                        <th>"Sempre sobe"</th><th>Momentum</th><th>Pearson (p)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {corr.analise_preditiva.map((h) => (
                        <tr key={h.horizonte_h} className={h.significativo ? "ok" : ""}>
                          <td>{h.rotulo}</td>
                          <td>{h.total_comparavel}</td>
                          <td>{h.taxa_acerto_pct != null ? `${numero(h.taxa_acerto_pct, 1)}%` : "—"}</td>
                          <td>{h.ic95_pct ? `${numero(h.ic95_pct[0], 0)}–${numero(h.ic95_pct[1], 0)}%` : "—"}</td>
                          <td>{h.p_valor != null ? numero(h.p_valor, 3) : "—"}</td>
                          <td>{h.baseline_sempre_sobe_pct != null ? `${numero(h.baseline_sempre_sobe_pct, 1)}%` : "—"}</td>
                          <td>{h.baseline_momentum_pct != null ? `${numero(h.baseline_momentum_pct, 1)}%` : "—"}</td>
                          <td>{h.pearson != null ? `${numero(h.pearson, 2)} (${numero(h.pearson_p_valor, 2)})` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="callout">{corr.conclusao}</div>
              </div>
            )}

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

      {feed.length > 0 && (
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
