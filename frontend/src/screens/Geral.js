import { useCallback, useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
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
  FONTES_SOCIAIS,
  LEITURA_DO_PERFIL,
  foiCancelada,
  nomeDaMoeda,
  textoDoAviso,
} from "../constants";
import { moeda as fmtMoeda, numero, percentual, rotuloHora } from "../format";
import { Dica, Pill, Seg, Spinner, Topline, Vazio } from "../components/ui";

const MARGEM = { top: 8, right: 12, left: 0, bottom: 0 };
const EIXO_Y = 74;

const leituraDoScore = (s) =>
  s > 0.1
    ? { tom: "pos", texto: "Otimista" }
    : s < -0.1
      ? { tom: "neg", texto: "Pessimista" }
      : { tom: "", texto: "Neutro" };

export default function Geral({ moeda, setMoeda, notify, autoRefresh, usuario, abrirAcesso, riscos }) {
  const [fonte, setFonte] = useState(
    () => localStorage.getItem("senty_fonte_geral") || "X",
  );
  const [pontos, soPreco, setPontos] = useHistorico();
  const [preco, setPreco] = useState(null);
  const [resumo, setResumo] = useState(null);
  const [carregando, setCarregando] = useState(true);
  const [dataInicio, setDataInicio] = useState("");
  const [dataFim, setDataFim] = useState("");
  const [gerandoPdf, setGerandoPdf] = useState(false);
  const abortar = useRef(null);

  useEffect(() => {
    localStorage.setItem("senty_fonte_geral", fonte);
  }, [fonte]);

  const carregar = useCallback(async () => {
    abortar.current?.abort();
    const controle = new AbortController();
    abortar.current = controle;
    const { signal } = controle;
    setCarregando(true);

    try {
      let lista = (
        await api.historicoSocial(moeda, fonte, dataInicio, dataFim, signal)
      ).pontos || [];
      let apenasPreco = false;
      if (lista.length === 0) {
        lista = (await api.historicoBinance(moeda, dataInicio, dataFim, signal))
          .pontos || [];
        apenasPreco = true;
      }
      setPontos(
        lista.map((p) => ({ ...p, rotulo: rotuloHora(p.timestamp) })),
        apenasPreco,
      );
    } catch (e) {
      if (foiCancelada(e)) return;
      setPontos([], false);
      notify.erro(
        e instanceof ApiError
          ? `Erro ao carregar dados: ${e.message}`
          : "Não foi possível falar com o servidor. Ele está rodando?",
      );
    }

    try {
      setPreco(await api.sentimento(moeda, signal));
    } catch (e) {
      if (!foiCancelada(e)) setPreco(null);
    }
    try {
      setResumo((await api.correlacao(moeda, fonte, signal)).resumo);
    } catch (e) {
      if (!foiCancelada(e)) setResumo(null);
    }
    if (abortar.current === controle) setCarregando(false);
  }, [moeda, fonte, dataInicio, dataFim, notify, setPontos]);

  useEffect(() => {
    carregar();
    return () => abortar.current?.abort();
  }, [carregar]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = setInterval(carregar, 60000);
    return () => clearInterval(id);
  }, [autoRefresh, carregar]);

  const gerarPdfDaHora = async (ponto) => {
    if (!ponto?.timestamp || gerandoPdf) return;
    if (!usuario) {
      notify.aviso("Entre na sua conta para gerar o PDF desta hora.");
      abrirAcesso("entrar");
      return;
    }
    setGerandoPdf(true);
    try {
      const info = await api.gerarRelatorio(
        moeda, fonte, ponto.timestamp, ponto.indice_sentimento,
      );
      window.open(`${API_URL}${info.url}`, "_blank", "noopener");
      notify.ok(`Relatório salvo: ${info.arquivo}`);
    } catch (e) {
      notify.erro(`Falha ao gerar o PDF: ${e.message}`);
    } finally {
      setGerandoPdf(false);
    }
  };

  const variacao = preco?.variacao_percentual;
  const leitura = leituraDoScore(resumo?.score_medio ?? 0);
  const temPosts = !soPreco && pontos.length > 0;
  const nome = nomeDaMoeda(moeda);

  return (
    <section className="screen" aria-label="Visão geral">
      <Topline titulo="Visão geral" moeda={moeda} setMoeda={setMoeda}>
        <Seg
          label="Fonte"
          value={fonte}
          options={FONTES_SOCIAIS}
          onChange={setFonte}
        />
        <label className="hint" htmlFor="g-de">De</label>
        <input id="g-de" type="date" className="sel" value={dataInicio}
          max={dataFim || undefined} onChange={(e) => setDataInicio(e.target.value)} />
        <label className="hint" htmlFor="g-ate">até</label>
        <input id="g-ate" type="date" className="sel" value={dataFim}
          min={dataInicio || undefined} onChange={(e) => setDataFim(e.target.value)} />
        {(dataInicio || dataFim) && (
          <button className="btn sm" type="button"
            onClick={() => { setDataInicio(""); setDataFim(""); }}>
            Limpar datas
          </button>
        )}
        <button className="btn" type="button" onClick={carregar} disabled={carregando}>
          {carregando ? <Spinner /> : "Atualizar"}
        </button>
      </Topline>

      <div className="ov">
        <div className="card hero">
          <div className="hero-head">
            <div className="kpi">
              <span className="l">{nome} · preço atual</span>
              <span className="price">{fmtMoeda(preco?.preco)}</span>
            </div>
            {variacao != null && (
              <Pill tom={variacao >= 0 ? "pos" : "neg"}>
                {percentual(variacao, 2)} na última hora
              </Pill>
            )}
          </div>

          {pontos.length === 0 ? (
            <Vazio
              titulo={carregando ? "Carregando..." : `Sem dados para ${moeda}`}
              texto={
                carregando
                  ? undefined
                  : "Colete posts na aba X e Reddit ou sincronize os preços em Configurações."
              }
            />
          ) : (
            <>
              <div className="chart" role="img" aria-label={`Preço de ${nome} por hora`}>
                <ResponsiveContainer minWidth={0} minHeight={180}>
                  <AreaChart data={pontos} margin={MARGEM}>
                    <defs>
                      <linearGradient id="gradPreco" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="var(--accent)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="rotulo" hide />
                    <YAxis width={EIXO_Y} domain={["auto", "auto"]} tickLine={false}
                      axisLine={false}
                      tickFormatter={(v) => (v == null ? "" : `US$ ${numero(v / 1000, 1)}k`)} />
                    <Tooltip content={<Dica formatar={(v) => fmtMoeda(v)} />} />
                    <Area type="monotone" dataKey="preco" name="Preço" stroke="var(--accent)"
                      strokeWidth={2.2} fill="url(#gradPreco)" dot={false} connectNulls />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {temPosts && (
                <div className="chart sm" role="img" aria-label="Sentimento por hora">
                  <ResponsiveContainer minWidth={0} minHeight={90}>
                    <BarChart data={pontos} margin={MARGEM}>
                      <CartesianGrid vertical={false} />
                      <XAxis dataKey="rotulo" tickLine={false} minTickGap={48} />
                      <YAxis width={EIXO_Y} domain={[-1, 1]} ticks={[-1, 0, 1]}
                        tickLine={false} axisLine={false} />
                      <ReferenceLine y={0} stroke="var(--line)" />
                      <Tooltip content={<Dica formatar={(v, p) =>
                        `${numero(v, 2)} · ${p.payload.total_posts} posts ` +
                        `(${p.payload.positivos}+ / ${p.payload.negativos}− / ${p.payload.neutros}=)`} />} />
                      <Bar dataKey="sentiment_score" name="Sentimento" maxBarSize={14}
                        radius={[2, 2, 2, 2]} style={{ cursor: "pointer" }}
                        onClick={(d) => gerarPdfDaHora(d?.payload || d)}>
                        {pontos.map((p, i) => (
                          <Cell key={i} fill={p.sentiment_score > 0 ? "var(--pos)"
                            : p.sentiment_score < 0 ? "var(--neg)" : "var(--neu)"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              <div className="legend">
                <span style={{ "--c": "var(--accent)" }}>Preço (US$)</span>
                {temPosts && <span style={{ "--c": "var(--pos)" }}>Hora positiva</span>}
                {temPosts && <span style={{ "--c": "var(--neg)" }}>Hora negativa</span>}
              </div>
              <p className="hint">
                {soPreco
                  ? `Sem posts de ${fonte} salvos: mostrando só o preço.`
                  : "Clique numa barra de sentimento para gerar o PDF daquela hora (exige conta). "}
                Horários em UTC.
                {gerandoPdf && <> <Spinner /></>}
              </p>
            </>
          )}
        </div>

        <div className="ov-side">
          <div className="kpis">
            <div className="card kpi">
              <span className="l">Sentimento médio ({fonte})</span>
              <span className="v">{resumo ? numero(resumo.score_medio, 2) : "—"}</span>
              <Pill tom={leitura.tom}>{leitura.texto}</Pill>
              <span className="s">Escala de -1 (só negativo) a +1 (só positivo)</span>
            </div>
            <div className="card kpi">
              <span className="l">Acerto do sentimento sobre o preço</span>
              <span className="v">
                {resumo?.taxa_acerto_pct != null ? `${numero(resumo.taxa_acerto_pct, 1)}%` : "—"}
              </span>
              {resumo && !resumo.amostra_suficiente && <Pill tom="warn">Amostra pequena</Pill>}
              <span className="s">
                {resumo
                  ? `${resumo.acertos} acertos em ${resumo.total_comparavel} horas comparáveis (o ideal é 30 ou mais)`
                  : "Sem dados de correlação"}
              </span>
            </div>
            <div className="card kpi">
              <span className="l">Posts analisados</span>
              <span className="v">{resumo ? numero(resumo.total_posts) : "—"}</span>
              <span className="s">
                {resumo ? `em ${numero(resumo.total_horas_analisadas)} horas com posts` : ""}
              </span>
            </div>
          </div>

          {usuario?.perfil && (
            <div className="card insight">
              <p>
                <strong>Seu perfil ({usuario.perfil}):</strong> {LEITURA_DO_PERFIL[usuario.perfil]}
                {textoDoAviso(usuario.perfil, [moeda], riscos) && <> {textoDoAviso(usuario.perfil, [moeda], riscos)}</>}
              </p>
            </div>
          )}

          {!usuario && (
            <div className="card stack convite">
              <h2>Quer ir além da Visão geral?</h2>
              <p className="hint">
                Com uma conta gratuita você simula investimentos, compara X e Reddit, gera
                relatórios em PDF e recebe leituras adaptadas ao seu perfil de investimento.
              </p>
              <div className="btns">
                <button className="btn primary" type="button" onClick={() => abrirAcesso("cadastro")}>
                  Criar conta
                </button>
                <button className="btn" type="button" onClick={() => abrirAcesso("entrar")}>Entrar</button>
              </div>
            </div>
          )}

          {resumo?.total_posts > 0 && (
            <div className="card insight">
              <p>
                <strong>Leitura rápida:</strong> o sentimento médio do {fonte} sobre {nome} é{" "}
                {numero(resumo.score_medio, 2)} ({leitura.texto.toLowerCase()}).
                {resumo.taxa_acerto_pct != null && (
                  <> Ele acertou a direção do preço em {numero(resumo.taxa_acerto_pct, 0)}% das{" "}
                    {resumo.total_comparavel} horas comparáveis
                    {resumo.amostra_suficiente
                      ? "."
                      : ". Com tão poucas horas, ainda não dá para concluir nada."}</>
                )}
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/** Guarda os pontos do gráfico e se eles trazem só preço (sem posts). */
function useHistorico() {
  const [estado, setEstado] = useState({ pontos: [], soPreco: false });
  const definir = useCallback(
    (pontos, soPreco) => setEstado({ pontos, soPreco }),
    [],
  );
  return [estado.pontos, estado.soPreco, definir];
}
