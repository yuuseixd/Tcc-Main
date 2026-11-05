import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  LabelList,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, ApiError } from "../api";
import { foiCancelada, nomeDaMoeda } from "../constants";
import { numero, percentual, rotuloHora } from "../format";
import { Pill, Seg, Spinner, Vazio } from "../components/ui";

// Valores no formato da Binance; 4h é o mínimo (abaixo disso é ruído).
export const INTERVALOS = [
  { valor: "4h", rotulo: "4h" },
  { valor: "12h", rotulo: "12h" },
  { valor: "1d", rotulo: "24h" },
  { valor: "1w", rotulo: "Semana" },
];
const HORAS = { "4h": 4, "12h": 12, "1d": 24, "1w": 168 };
/** Nome do intervalo para frases ("candle de 24h", "candle semanal"). */
const nomeIntervalo = (v) => (v === "1w" ? "semanal" : `de ${INTERVALOS.find((i) => i.valor === v)?.rotulo ?? v}`);
const MARGEM = { top: 20, right: 12, bottom: 4, left: 4 };

/** Seta e rótulo da direção esperada do padrão. */
export function PillDirecao({ direcao }) {
  return direcao === "alta"
    ? <Pill tom="pos">↑ Alta</Pill>
    : <Pill tom="neg">↓ Baixa</Pill>;
}

/** Casas decimais pelo tamanho do preço: BTC sem centavos, DOGE com 4 casas. */
const preco = (v) =>
  v == null ? "—" : `US$ ${numero(v, v >= 1000 ? 0 : v >= 1 ? 2 : 4)}`;

/** "30/10 16h" nos intervalos intradiários; "30/10/26" no diário e semanal. */
function rotuloData(iso, intervalo) {
  if (!iso) return "";
  if (intervalo === "4h" || intervalo === "12h") return rotuloHora(iso);
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "UTC", day: "2-digit", month: "2-digit", year: "2-digit",
  }).format(new Date(iso));
}

const ativo = (p) => p.status === "em_formacao" || p.desfecho === "em_andamento";
/** +1 para padrões de alta, -1 para os de baixa (alvo abaixo da entrada). */
const sinal = (p) => (p.direcao === "baixa" ? -1 : 1);
/** Identifica um padrão entre recargas e trocas de tipo. */
const chave = (p) => `${p.tipo}:${p.i_ultimo_pivo}`;

/** Rótulo e tom do estado de um padrão. */
function situacao(p) {
  if (p.status === "em_formacao") return { tom: "warn", texto: "Em formação" };
  if (p.status === "invalidado") return { tom: "neg", texto: "Invalidado" };
  if (p.status === "expirado") return { tom: "", texto: "Expirou sem romper" };
  if (p.desfecho === "em_andamento") return { tom: "pos", texto: "Confirmado · em andamento" };
  if (p.desfecho === "alvo") return { tom: "pos", texto: "Atingiu o alvo" };
  if (p.desfecho === "stop") return { tom: "neg", texto: "Bateu no stop" };
  return { tom: "", texto: "Sem desfecho" };
}

/** Linhas, pivôs, projeção e janela visível do padrão escolhido. */
function geometria(dados, p) {
  const n = dados.candles.length;
  if (!p) return { ini: Math.max(0, n - 200), fim: n - 1 };

  const largura = Math.max(1, p.i_ultimo_pivo - p.pivos[0].i);
  const folga = Math.max(10, Math.round(largura * 0.6));
  const iProjecao = p.status === "em_formacao" ? n - 1 : p.i_rompimento;
  const projecao = p.alvo != null && iProjecao != null
    ? { x1: iProjecao, x2: iProjecao + (p.horizonte ?? largura), y1: p.gatilho, y2: p.alvo }
    : null;
  // Linhas de suporte/resistência vão até o rompimento (ou até agora); o
  // mastro da flâmula fica do tamanho dele.
  const iFimLinha = p.i_rompimento ?? p.i_status ?? n - 1;
  const linhas = p.linhas.map((l) => {
    const em = (i) => l.p1 + ((l.p2 - l.p1) / (l.i2 - l.i1 || 1)) * (i - l.i1);
    const fim = l.nome === "Mastro" ? l.i2 : Math.max(l.i2, iFimLinha);
    return { nome: l.nome, segmento: [{ x: l.i1, y: l.p1 }, { x: fim, y: em(fim) }] };
  });
  const fimDados = Math.min(n - 1, (p.i_desfecho ?? p.i_status ?? p.i_ultimo_pivo) + folga);

  return {
    ini: Math.max(0, p.pivos[0].i - folga),
    fim: Math.max(fimDados, projecao ? Math.min(projecao.x2, n - 1 + largura) : 0),
    fundos: p.pivos.filter((v) => v.tipo === "fundo").map((v) => ({ i: v.i, c: v.preco, rotulo: v.rotulo })),
    topos: p.pivos.filter((v) => v.tipo === "topo").map((v) => ({ i: v.i, c: v.preco, rotulo: v.rotulo })),
    linhas,
    projecao,
  };
}

function DicaPreco({ active, label, candles, intervalo }) {
  if (!active || label == null) return null;
  const c = candles[label];
  if (!c) return null;
  return (
    <div className="tip">
      <span className="hint">{rotuloData(c.t, intervalo)} UTC</span>
      <span>Fechamento: <b>{preco(c.c)}</b></span>
    </div>
  );
}

export default function AnalisadorGraficos({ moeda, notify, tipo, setTipo, intervalo, setIntervalo }) {
  const [dados, setDados] = useState(null);
  const [carregando, setCarregando] = useState(false);
  const [escolhido, setEscolhido] = useState(null);
  const abortar = useRef(null);

  const carregar = useCallback(async () => {
    abortar.current?.abort();
    const controle = new AbortController();
    abortar.current = controle;
    setCarregando(true);
    try {
      const r = await api.padroes(moeda, intervalo, controle.signal);
      // Sem candles não há o que desenhar: trata como "sem dados".
      setDados(r?.candles?.length ? r : null);
      setEscolhido(null);
    } catch (e) {
      if (foiCancelada(e)) return;
      setDados(null);
      notify.erro(e instanceof ApiError
        ? `Falha na leitura do gráfico: ${e.message}`
        : "Não foi possível falar com o servidor. Ele está rodando?");
    }
    if (abortar.current === controle) setCarregando(false);
  }, [moeda, intervalo, notify]);

  useEffect(() => {
    carregar();
    return () => abortar.current?.abort();
  }, [carregar]);

  const catalogo = dados?.catalogo ?? [];
  const info = catalogo.find((c) => c.tipo === tipo);
  const alta = info?.direcao !== "baixa";
  const todos = useMemo(() => dados?.padroes ?? [], [dados]);
  const padroes = useMemo(() => todos.filter((p) => p.tipo === tipo), [todos, tipo]);
  const atual = [...padroes].reverse().find(ativo);
  // A escolha só vale se for deste tipo; senão, o ativo ou o mais recente.
  const padrao = padroes.find((p) => chave(p) === escolhido) ?? atual ?? padroes[padroes.length - 1];
  const ativosAgora = todos.filter(ativo);
  const geo = useMemo(() => (dados ? geometria(dados, padrao) : null), [dados, padrao]);

  const pontos = useMemo(() => {
    if (!dados || !geo) return [];
    return dados.candles.slice(geo.ini, geo.fim + 1).map((c, k) => ({ i: geo.ini + k, c: c.c }));
  }, [dados, geo]);

  const dominioY = useMemo(() => {
    if (!dados || !geo) return ["auto", "auto"];
    const janela = dados.candles.slice(geo.ini, geo.fim + 1);
    let min = Math.min(...janela.map((c) => c.l ?? c.c));
    let max = Math.max(...janela.map((c) => c.h ?? c.c));
    for (const v of [padrao?.alvo, padrao?.invalidacao]) {
      if (v != null) { min = Math.min(min, v); max = Math.max(max, v); }
    }
    const folga = (max - min) * 0.07;
    // Folga dos dois lados: rótulos de fundos ficam abaixo, os de topos acima.
    return [min - folga * 2, max + folga * 2];
  }, [dados, geo, padrao]);

  const ultimo = dados?.candles[dados.candles.length - 1]?.c;
  const corAlvo = alta ? "var(--pos)" : "var(--neg)";
  const bt = dados?.backtest;
  const nome = nomeDaMoeda(moeda);
  const alterar = (novoTipo) => setTipo(novoTipo);

  return (
    <div className="card stack">
      <div>
        <h2>Analisador de gráficos</h2>
        <p className="hint">
          Procura padrões de price action nos candles da Binance e projeta o alvo pela regra do
          movimento medido. A confiabilidade mostrada é medida no histórico, não presumida.
        </p>
      </div>

      <div className="btns">
        <select className="sel" aria-label="Padrão gráfico" value={tipo}
          onChange={(e) => alterar(e.target.value)}>
          {["alta", "baixa"].map((d) => (
            <optgroup key={d} label={d === "alta" ? "Expectativa de alta" : "Expectativa de baixa"}>
              {catalogo.filter((c) => c.direcao === d).map((c) => (
                <option key={c.tipo} value={c.tipo}>{c.nome}</option>
              ))}
            </optgroup>
          ))}
          {catalogo.length === 0 && <option value={tipo}>Carregando padrões...</option>}
        </select>
        <Seg label="Intervalo" value={intervalo} options={INTERVALOS} onChange={setIntervalo} />
        <button className="btn" type="button" onClick={carregar} disabled={carregando}>
          {carregando ? <Spinner /> : "Atualizar"}
        </button>
      </div>

      {!dados ? (
        <Vazio titulo={carregando ? "Lendo o gráfico..." : "Sem dados"} />
      ) : (
        <>
          {info && (
            <p className="hint">
              <PillDirecao direcao={info.direcao} /> {info.descricao} ({info.categoria})
            </p>
          )}

          <div className="stack">
            <span className="hint">
              {ativosAgora.length
                ? `Ativos agora em ${moeda} ${intervalo} (${ativosAgora.length}):`
                : `Nenhum padrão ativo agora em ${moeda} ${intervalo}.`}
            </span>
            {new Set(ativosAgora.map((p) => p.direcao)).size > 1 && (
              <div className="callout">
                Sinais conflitantes: há padrões de alta e de baixa ativos ao mesmo tempo. Compare a
                confiabilidade de cada um no ranking antes de tirar conclusões.
              </div>
            )}
            {ativosAgora.length > 0 && (
              <div className="btns">
                {ativosAgora.map((p, k) => {
                  const c = catalogo.find((x) => x.tipo === p.tipo);
                  return (
                    <button key={k} type="button" className="chip" aria-pressed={p === padrao}
                      onClick={() => {
                        alterar(p.tipo);
                        setEscolhido(chave(p));
                      }}>
                      {p.direcao === "alta" ? "↑" : "↓"} {c?.nome ?? p.tipo} · {situacao(p).texto.toLowerCase()}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="kpis">
            <div className="card kpi">
              <span className="l">{nome} · preço atual</span>
              <span className="v">{preco(ultimo)}</span>
              {atual
                ? <Pill tom={situacao(atual).tom}>{situacao(atual).texto}</Pill>
                : <Pill>Nenhum {info?.nome ?? "padrão"} ativo</Pill>}
            </div>
            {atual && (
              <>
                <div className="card kpi">
                  <span className="l">
                    {atual.status === "em_formacao" ? "Gatilho (linha de rompimento)" : "Entrada (rompimento)"}
                  </span>
                  <span className="v">{preco(atual.status === "em_formacao" ? atual.gatilho : atual.entrada)}</span>
                  <span className="s">
                    {atual.status === "em_formacao"
                      ? `${percentual((atual.gatilho / ultimo - 1) * 100, 1)} do preço atual; confirma ao fechar ${alta ? "acima" : "abaixo"}`
                      : `rompeu em ${rotuloData(dados.candles[atual.i_rompimento].t, intervalo)}`}
                  </span>
                </div>
                <div className="card kpi">
                  <span className="l">Alvo projetado</span>
                  <span className="v">{preco(atual.alvo)}</span>
                  <span className="s">{percentual((atual.alvo / ultimo - 1) * 100, 1)} do preço atual</span>
                </div>
                <div className="card kpi">
                  <span className="l">Invalidação (stop)</span>
                  <span className="v">{preco(atual.invalidacao)}</span>
                  <span className="s">{percentual((atual.invalidacao / ultimo - 1) * 100, 1)} do preço atual</span>
                </div>
              </>
            )}
          </div>

          <div className="chart lg" role="img"
            aria-label={`Preço de ${nome} em ${intervalo} com o padrão ${info?.nome ?? ""} destacado`}>
            <ResponsiveContainer minWidth={0} minHeight={220}>
              <ComposedChart data={pontos} margin={MARGEM}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="i" type="number" domain={[geo.ini, geo.fim]} allowDataOverflow
                  tickLine={false} minTickGap={56}
                  tickFormatter={(i) => rotuloData(dados.candles[i]?.t, intervalo)} />
                <YAxis width={70} domain={dominioY} allowDataOverflow tickLine={false} axisLine={false}
                  tickFormatter={(v) => numero(v, v >= 1000 ? 0 : v >= 1 ? 2 : 4)} />
                <Tooltip content={<DicaPreco candles={dados.candles} intervalo={intervalo} />} />
                {geo.projecao && (
                  <ReferenceArea x1={geo.projecao.x1} x2={geo.projecao.x2}
                    y1={geo.projecao.y1} y2={geo.projecao.y2}
                    fill={corAlvo} fillOpacity={0.12} stroke="none" ifOverflow="hidden" />
                )}
                {padrao?.alvo != null && (
                  <ReferenceLine y={padrao.alvo} stroke={corAlvo} strokeDasharray="5 4"
                    label={{ value: `Alvo ${preco(padrao.alvo)}`, position: alta ? "insideTopRight" : "insideBottomRight", fill: "var(--muted)" }} />
                )}
                {padrao?.invalidacao != null && (
                  <ReferenceLine y={padrao.invalidacao} stroke="var(--warn)" strokeDasharray="5 4"
                    label={{ value: "Invalidação", position: alta ? "insideBottomRight" : "insideTopRight", fill: "var(--muted)" }} />
                )}
                {geo.linhas?.map((l, k) => (
                  <ReferenceLine key={k} segment={l.segmento} stroke="var(--series)"
                    strokeWidth={2} strokeOpacity={l.nome === "Mastro" ? 0.45 : 1} ifOverflow="hidden" />
                ))}
                <Line type="linear" dataKey="c" name="Fechamento" stroke="var(--accent)"
                  strokeWidth={2} dot={false} isAnimationActive={false} />
                {geo.fundos?.length > 0 && (
                  <Scatter data={geo.fundos} dataKey="c" fill="var(--fg)" stroke="var(--surface)"
                    strokeWidth={2} isAnimationActive={false}>
                    <LabelList dataKey="rotulo" position="bottom" offset={10} fill="var(--fg)" />
                  </Scatter>
                )}
                {geo.topos?.length > 0 && (
                  <Scatter data={geo.topos} dataKey="c" fill="var(--fg)" stroke="var(--surface)"
                    strokeWidth={2} isAnimationActive={false}>
                    <LabelList dataKey="rotulo" position="top" offset={10} fill="var(--fg)" />
                  </Scatter>
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="legend">
            <span style={{ "--c": "var(--accent)" }}>Fechamento</span>
            {padrao && <span style={{ "--c": "var(--series)" }}>
              {padrao.linhas.map((l) => l.nome).join(" · ")}</span>}
            {padrao?.alvo != null && <span className="tracejada" style={{ "--c": corAlvo }}>Alvo projetado</span>}
            {padrao?.invalidacao != null && <span className="tracejada" style={{ "--c": "var(--warn)" }}>Invalidação</span>}
          </div>
          <p className="hint">
            {padrao
              ? <>Mostrando {info?.nome ?? "o padrão"} formado em {rotuloData(padrao.timestamp_formacao, intervalo)}{" "}
                  ({situacao(padrao).texto.toLowerCase()}). A faixa colorida vai do gatilho ao alvo; os
                  pontos marcam as mínimas e máximas dos candles, e a linha, o fechamento.</>
              : `Nenhum ${info?.nome ?? "padrão"} nos últimos ${numero(dados.candles.length)} candles de ${intervalo}.`}
            {" "}Horários em UTC.
          </p>

          <Previsao atual={atual} bt={bt?.tipos?.[tipo]?.geral} moedas={bt?.moedas}
            intervalo={intervalo} ultimo={ultimo} alta={alta} />

          {padroes.length > 0 && (
            <div className="stack">
              <h3>{info?.nome} no período ({padroes.length})</h3>
              <p className="hint">
                Últimos {numero(dados.candles.length)} candles de {intervalo}. Clique numa linha para ver o padrão no gráfico.
                Alvo e stop são a variação do preço a partir da entrada. O retorno é o da operação na
                direção do padrão (positivo = a favor), no fechamento do candle do desfecho.
              </p>
              <div className="tbl-wrap">
                <table>
                  <thead>
                    <tr><th>Formado em</th><th>Situação</th><th>Entrada</th><th>Alvo</th><th>Stop</th><th>Retorno</th></tr>
                  </thead>
                  <tbody>
                    {padroes.map((p, k) => k).reverse().map((k) => {
                      const p = padroes[k];
                      const s = situacao(p);
                      return (
                        <tr key={k} tabIndex={0} role="button" aria-pressed={padrao === p}
                          className={`clicavel ${padrao === p ? "sel" : ""} ${s.tom === "pos" ? "ok" : s.tom === "neg" ? "bad" : ""}`}
                          onClick={() => setEscolhido(chave(p))}
                          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setEscolhido(chave(p))}>
                          <td>{rotuloData(p.timestamp_formacao, intervalo)}</td>
                          <td>{s.texto}</td>
                          <td>{preco(p.entrada)}</td>
                          <td>{p.alvo_pct != null ? percentual(sinal(p) * p.alvo_pct, 1) : p.alvo != null ? preco(p.alvo) : "—"}</td>
                          <td>{p.stop_pct != null ? percentual(-sinal(p) * p.stop_pct, 1) : "—"}</td>
                          <td>{p.retorno_pct != null ? percentual(p.retorno_pct, 1) : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <Backtest bt={bt} tipo={tipo} nome={info?.nome} moeda={moeda} intervalo={intervalo} />

          <p className="hint">
            Leitura técnica automatizada para fins acadêmicos. Não é recomendação de investimento.
          </p>
        </>
      )}
    </div>
  );
}

/** Frase da projeção: o que precisa acontecer e o que o histórico diz. */
function Previsao({ atual, bt, moedas, intervalo, ultimo, alta }) {
  if (!atual) return null;
  const historico = bt?.taxa_acerto_pct != null && (
    <>
      {" "}No backtest em {intervalo} ({numero(bt.avaliados)} padrões confirmados, {moedas?.length} moedas),{" "}
      <b>{numero(bt.taxa_acerto_pct, 1)}%</b> atingiram o alvo antes do stop
      (IC 95%: {numero(bt.ic95_pct[0], 0)}–{numero(bt.ic95_pct[1], 0)}%), contra{" "}
      {numero(bt.baseline_pct, 1)}% de uma entrada qualquer com o mesmo alvo e stop
      {bt.significativo
        ? ` — o padrão foi melhor que o acaso (p = ${numero(bt.p_valor, 3)}).`
        : ` — sem evidência de que o padrão seja melhor que o acaso (p = ${numero(bt.p_valor, 3)}).`}
      {bt.candles_medios_ate_alvo != null && (
        <> Quando acertou, levou em média {numero(bt.candles_medios_ate_alvo, 0)} candles
          (~{numero((bt.candles_medios_ate_alvo * HORAS[intervalo]) / 24, 1)} dias).</>
      )}
    </>
  );
  const lado = alta ? "acima" : "abaixo";
  const contrario = alta ? "abaixo" : "acima";

  return (
    <div className="card insight">
      <p>
        <strong>Projeção:</strong>{" "}
        {atual.status === "em_formacao"
          ? <>se um candle {nomeIntervalo(intervalo)} fechar {lado} de {preco(atual.gatilho)}, o padrão se confirma e o
              alvo projetado é {preco(atual.alvo)} ({percentual((atual.alvo / ultimo - 1) * 100, 1)} do preço
              atual). Fechar {contrario} de {preco(atual.invalidacao)} antes disso invalida o padrão.</>
          : <>o padrão rompeu em {preco(atual.entrada)}; o alvo é {preco(atual.alvo)}{" "}
              ({percentual((atual.alvo / atual.entrada - 1) * 100, 1)} da entrada) e o stop{" "}
              {preco(atual.invalidacao)} ({percentual((atual.invalidacao / atual.entrada - 1) * 100, 1)}).</>}
        {historico}
      </p>
    </div>
  );
}

function Backtest({ bt, tipo, nome, moeda, intervalo }) {
  if (!bt) {
    return (
      <p className="hint">
        Backtest ainda não gerado. Rode <code>python tools/backtest_padroes.py</code> no backend para
        medir a confiabilidade dos padrões no histórico.
      </p>
    );
  }
  const r = bt.tipos?.[tipo];
  if (!r) return null;
  const linhas = [
    { rotulo: `Todas (${bt.moedas.length} moedas)`, r: r.geral },
    ...(r.moeda ? [{ rotulo: moeda, r: r.moeda }] : []),
  ];
  return (
    <div className="stack">
      <h3>Confiabilidade medida · {nome} ({intervalo})</h3>
      <p className="hint">
        Acerto = atingir o alvo antes do stop, dentro de 2x a largura do padrão. "Entrada qualquer" usa o
        mesmo alvo e stop a partir de candles sem padrão. Gerado em {rotuloData(bt.gerado_em, "1d")}
        {bt.periodo_moeda && <> · {moeda}: {bt.periodo_moeda}</>}.
      </p>
      <div className="tbl-wrap">
        <table>
          <thead>
            <tr><th>Amostra</th><th>Avaliados</th><th>Acerto</th><th>IC 95%</th><th>Entrada qualquer</th><th>p-valor</th></tr>
          </thead>
          <tbody>
            {linhas.map(({ rotulo, r: x }) => (
              <tr key={rotulo} className={x.significativo ? "ok" : ""}>
                <td>{rotulo}</td>
                <td>{numero(x.avaliados)}</td>
                <td>{x.taxa_acerto_pct != null ? `${numero(x.taxa_acerto_pct, 1)}%` : "—"}</td>
                <td>{x.ic95_pct ? `${numero(x.ic95_pct[0], 0)}–${numero(x.ic95_pct[1], 0)}%` : "—"}</td>
                <td>{x.baseline_pct != null ? `${numero(x.baseline_pct, 1)}%` : "—"}</td>
                <td>{x.p_valor != null ? numero(x.p_valor, 3) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
