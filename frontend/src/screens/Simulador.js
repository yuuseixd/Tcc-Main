import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, ApiError } from "../api";
import {
  MOEDAS,
  PERFIS_INVESTIDOR,
  SIM_CAMPOS,
  SIM_DEFAULTS,
  listaDePerfis,
  nomeDaMoeda,
  textoDoAviso,
} from "../constants";
import { dataHoraCompleta, moeda as fmtMoeda, numero, percentual, rotuloHora } from "../format";
import { Dica, Pill, Recolhivel, Seg, Spinner, Topline, Vazio } from "../components/ui";

const MODOS = [
  { valor: "flat", rotulo: "Flat" },
  { valor: "fomo", rotulo: "FOMO" },
  { valor: "padroes", rotulo: "Padrões gráficos" },
];
// Semanal fica de fora: poucos rompimentos por ano para simular operações.
export const INTERVALOS_SIM = [
  { valor: "4h", rotulo: "4h" },
  { valor: "12h", rotulo: "12h" },
  { valor: "1d", rotulo: "24h" },
];
const CAMPOS_PERFIL = [
  "percentual_lucro_venda",
  "percentual_queda_compra",
  "percentual_perda_aceita",
];
// No modo Padrões, alvo e stop vêm do próprio padrão.
const CAMPOS_PADROES = ["capital_inicial", "valor_por_compra"];
const MAX_DIAS = { fomo: 180, flat: 180, padroes: 730 };
const PRESETS = [
  { dias: 7, rotulo: "7 dias" },
  { dias: 30, rotulo: "30 dias" },
  { dias: 90, rotulo: "90 dias" },
  { dias: 180, rotulo: "6 meses" },
  { dias: 365, rotulo: "1 ano" },
  { dias: 730, rotulo: "2 anos" },
];
const GRAFICOS = [
  { valor: "patrimonio", rotulo: "Patrimônio" },
  { valor: "preco", rotulo: "Preço e operações" },
  { valor: "queda", rotulo: "Quedas" },
];
const MOTIVO = { lucro: "Alvo", perda: "Stop", prazo: "Prazo" };
const MARGEM = { top: 8, right: 12, left: 0, bottom: 0 };
// Abaixo disso a taxa de acerto de um padrão é sorte demais para "melhor".
const MIN_CASOS = 10;

const isoDia = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const periodoDe = (dias) => {
  const fim = new Date();
  const ini = new Date(fim);
  ini.setDate(ini.getDate() - dias);
  return { de: isoDia(ini), ate: isoDia(fim) };
};
const diasEntre = (de, ate) => Math.round((new Date(ate) - new Date(de)) / 86400000);

const parametrosDoPerfil = (nome) => ({
  ...SIM_DEFAULTS,
  ...Object.fromEntries(CAMPOS_PERFIL.map((c) => [c, PERFIS_INVESTIDOR[nome][c]])),
});

/**
 * Padrões ordenados pela vantagem sobre uma entrada qualquer, e o melhor.
 * Usa o backtest das 8 moedas juntas: numa moeda só, cada padrão tem poucas
 * dezenas de casos e o "melhor" mudaria por sorte.
 */
export function rankingSimples(ranking) {
  if (!ranking?.tipos) return { lista: [], melhor: null };
  const nomes = Object.fromEntries((ranking.catalogo || []).map((c) => [c.tipo, c]));
  const lista = Object.entries(ranking.tipos)
    .map(([tipo, r]) => ({ tipo, nome: nomes[tipo]?.nome ?? tipo, direcao: nomes[tipo]?.direcao, st: r.geral }))
    .filter((p) => p.st)
    .sort((a, b) => (b.st.vantagem_pp ?? -Infinity) - (a.st.vantagem_pp ?? -Infinity));
  const melhor = lista.find((p) => p.st.avaliados >= MIN_CASOS && p.st.vantagem_pp != null) || null;
  return { lista, melhor };
}

/** Pares entrada→saída (operações), na ordem de entrada, com resultado acumulado. */
export function operacoes(r) {
  const saidas = Object.fromEntries(r.trades.filter((t) => t.entrada === false).map((t) => [t.posicao_id, t]));
  const abertas = Object.fromEntries((r.posicoes_abertas || []).map((p) => [p.id, p]));
  let acumulado = 0;
  return r.trades
    .filter((t) => t.entrada !== false)
    .map((e) => {
      const s = saidas[e.posicao_id];
      if (s) acumulado += s.lucro;
      return { entrada: e, saida: s, aberta: s ? null : abertas[e.posicao_id], acumulado: s ? acumulado : null };
    });
}

export default function Simulador({ moeda, notify, perfisX, perfilUsuario, riscos }) {
  const perfilInicial = perfilUsuario || "Moderado";
  const [modo, setModo] = useState("flat");
  const fonte = "X";
  const [perfil, setPerfil] = useState(perfilInicial);
  const [moedas, setMoedas] = useState(() => [moeda]);
  const [params, setParams] = useState(() => ({ [moeda]: parametrosDoPerfil(perfilInicial) }));
  const [{ de, ate }, setPeriodo] = useState(() => periodoDe(30));
  const [taxa, setTaxa] = useState(0.1);
  const [slippage, setSlippage] = useState(0.05);
  const [intervalo, setIntervalo] = useState("4h");
  const [padrao, setPadrao] = useState("");
  const [ranking, setRanking] = useState(null);
  const [simulando, setSimulando] = useState(false);
  const [resultados, setResultados] = useState(null);

  // Ranking do backtest para o intervalo: só para indicar o melhor padrão.
  const moedaRef = moedas[0] || moeda;
  useEffect(() => {
    if (modo !== "padroes") return undefined;
    const ctrl = new AbortController();
    api.rankingPadroes(moedaRef, intervalo, ctrl.signal)
      .then(setRanking)
      .catch((e) => { if (e?.name !== "AbortError") setRanking(null); });
    return () => ctrl.abort();
  }, [modo, moedaRef, intervalo]);

  const { lista, melhor } = useMemo(() => rankingSimples(ranking), [ranking]);
  // Cada tempo gráfico começa no seu melhor padrão; a troca manual vale até
  // o usuário mudar de tempo gráfico.
  const melhorTipo = melhor?.tipo;
  useEffect(() => {
    if (melhorTipo) setPadrao(melhorTipo);
  }, [melhorTipo, intervalo]);

  const escolherPerfil = (nome) => {
    setPerfil(nome);
    setParams((atual) =>
      Object.fromEntries(
        Object.entries(atual).map(([m, p]) => [
          m,
          { ...p, ...Object.fromEntries(CAMPOS_PERFIL.map((c) => [c, PERFIS_INVESTIDOR[nome][c]])) },
        ]),
      ),
    );
  };

  const alternarMoeda = (m) => {
    setMoedas((atual) => (atual.includes(m) ? atual.filter((x) => x !== m) : [...atual, m]));
    setParams((atual) => (atual[m] ? atual : { ...atual, [m]: parametrosDoPerfil(perfil) }));
  };

  const editar = (m, campo, valor) =>
    setParams((atual) => ({ ...atual, [m]: { ...atual[m], [campo]: valor } }));

  const camposVisiveis = modo === "padroes" ? SIM_CAMPOS.filter((c) => CAMPOS_PADROES.includes(c.chave)) : SIM_CAMPOS;
  const presets = PRESETS.filter((p) => p.dias <= MAX_DIAS[modo]);
  const diasPeriodo = de && ate ? diasEntre(de, ate) : null;

  const simular = async () => {
    const perfis =
      modo === "fomo"
        ? listaDePerfis(perfisX)
        : [];
    if (modo === "fomo" && perfis.length === 0) {
      notify.erro("Informe ao menos um perfil do X (em X / Twitter ou Configurações) para o modo FOMO.");
      return;
    }
    if (modo === "padroes" && !padrao) {
      notify.erro("Escolha um padrão gráfico para simular.");
      return;
    }
    if (moedas.length === 0) {
      notify.erro("Selecione ao menos uma moeda para simular.");
      return;
    }
    if (!de || !ate) {
      notify.erro("Informe o período da simulação (de e até).");
      return;
    }
    if (diasPeriodo > MAX_DIAS[modo]) {
      notify.erro(`Neste modo o período máximo é de ${MAX_DIAS[modo]} dias.`);
      return;
    }
    for (const m of moedas) {
      const p = params[m];
      if (Number(p.valor_por_compra) > Number(p.capital_inicial)) {
        notify.erro(`${m}: o valor por operação não pode ser maior que o capital.`);
        return;
      }
    }

    setSimulando(true);
    try {
      const entradas = await Promise.all(
        moedas.map(async (m) => {
          const p = params[m];
          try {
            const r = await api.simularInvestimento({
              moeda: m,
              modo,
              fonte,
              perfis: modo === "fomo" && perfis.length ? perfis : undefined,
              data_inicio: de,
              data_fim: ate,
              capital_inicial: Number(p.capital_inicial),
              valor_por_compra: Number(p.valor_por_compra),
              percentual_lucro_venda: Number(p.percentual_lucro_venda),
              percentual_queda_compra: Number(p.percentual_queda_compra),
              percentual_perda_aceita: Number(p.percentual_perda_aceita),
              taxa_operacao_pct: Number(taxa),
              slippage_pct: Number(slippage),
              ...(modo === "padroes" ? { padrao, intervalo } : {}),
            });
            return [m, r];
          } catch (e) {
            return [m, {
              vazio: true,
              mensagem: e instanceof ApiError
                ? `Falha na simulação de ${m}: ${e.message}`
                : `Falha ao simular ${m}.`,
            }];
          }
        }),
      );
      setResultados(Object.fromEntries(entradas));
      const vazias = entradas.filter(([, r]) => r.vazio);
      if (vazias.length === entradas.length) notify.aviso(vazias[0][1].mensagem);
    } finally {
      setSimulando(false);
    }
  };

  const validos = resultados ? Object.entries(resultados).filter(([, r]) => !r.vazio) : [];
  const total = validos.length
    ? validos.reduce(
        (acc, [m, r]) => ({
          capital: acc.capital + r.parametros.capital_inicial,
          patrimonio: acc.patrimonio + r.resumo.patrimonio_final,
          operacoes: acc.operacoes + (r.resumo.operacoes_fechadas ?? r.resumo.total_vendas),
          superaram: acc.superaram + (r.resumo.superou_buy_and_hold ? 1 : 0),
          pior: Math.max(acc.pior, r.resumo.queda_maxima_pct ?? 0),
          porMoeda: [...acc.porMoeda, { moeda: m, lucro: r.resumo.lucro_total, pct: r.resumo.lucro_total_pct }],
        }),
        { capital: 0, patrimonio: 0, operacoes: 0, superaram: 0, pior: 0, porMoeda: [] },
      )
    : null;
  const lucroTotal = total ? total.patrimonio - total.capital : 0;

  return (
    <section className="screen" aria-label="Simulador">
      <Topline titulo="Simulador de investimento">
        <Pill>Dinheiro fictício</Pill>
      </Topline>

      <p className="hint">
        Projeto acadêmico. Resultados passados não garantem resultados futuros e nada aqui é
        recomendação de investimento.
      </p>

      <div className="card stack">
        <div className="field">
          <span className="lab">Estratégia</span>
          <Seg label="Estratégia" value={modo} options={MODOS} onChange={setModo} />
          <p className="hint">
            {modo === "fomo" && "FOMO: compra numa queda de preço quando o sentimento da hora é positivo. Usa posts já salvos."}
            {modo === "flat" && "Flat: ignora os posts e compra só pela queda de preço."}
            {modo === "padroes" && "Padrões: entra no rompimento do padrão escolhido e sai no alvo, no stop ou no prazo do próprio padrão. Padrões de baixa viram venda a descoberto simulada."}
          </p>
        </div>

        {modo === "padroes" && (
          <EscolhaPadrao intervalo={intervalo} setIntervalo={setIntervalo} padrao={padrao}
            setPadrao={setPadrao} lista={lista} melhor={melhor} ranking={ranking} />
        )}

        <div className="fgrid">
          {modo === "fomo" && (
            <div className="field">
              <span className="lab">Sentimento do X</span>
              <p className="hint">Perfis: {listaDePerfis(perfisX).join(", ") || "nenhum"}</p>
            </div>
          )}
          <div className="field">
            <label htmlFor="s-de">Período de</label>
            <input id="s-de" type="date" value={de} max={ate || undefined}
              onChange={(e) => setPeriodo({ de: e.target.value, ate })} />
          </div>
          <div className="field">
            <label htmlFor="s-ate">até</label>
            <input id="s-ate" type="date" value={ate} min={de || undefined}
              onChange={(e) => setPeriodo({ de, ate: e.target.value })} />
          </div>
        </div>
        <div className="chips" role="group" aria-label="Período rápido">
          {presets.map((p) => (
            <button key={p.dias} type="button" className="chip" aria-pressed={diasPeriodo === p.dias}
              onClick={() => setPeriodo(periodoDe(p.dias))}>{p.rotulo}</button>
          ))}
        </div>

        {modo !== "padroes" && (
          <div className="field">
            <span className="lab">Perfil do investidor</span>
            <div className="chips">
              {Object.keys(PERFIS_INVESTIDOR).map((nome) => (
                <button key={nome} type="button" className="chip" aria-pressed={perfil === nome}
                  onClick={() => escolherPerfil(nome)}>{nome}</button>
              ))}
            </div>
            <p className="note">
              {perfilUsuario && perfil === perfilUsuario ? "Seu perfil. " : ""}
              {PERFIS_INVESTIDOR[perfil].nota} (meta {PERFIS_INVESTIDOR[perfil].percentual_lucro_venda}%,
              queda {PERFIS_INVESTIDOR[perfil].percentual_queda_compra}%, stop-loss{" "}
              {PERFIS_INVESTIDOR[perfil].percentual_perda_aceita}%)
            </p>
          </div>
        )}

        <div className="field">
          <span className="lab">Moedas a simular</span>
          <div className="chips">
            {MOEDAS.map((m) => (
              <button key={m.sigla} type="button" className="chip" aria-pressed={moedas.includes(m.sigla)}
                onClick={() => alternarMoeda(m.sigla)}>{m.sigla}</button>
            ))}
          </div>
        </div>

        {modo !== "padroes" && textoDoAviso(perfil, moedas, riscos) && (
          <div className="callout" role="note">{textoDoAviso(perfil, moedas, riscos)}</div>
        )}

        {riscos?.moedas && Object.keys(riscos.moedas).length > 0 && (
          <Recolhivel titulo="Risco medido de cada moeda">
            <p className="hint" style={{ marginBottom: 8 }}>
              {riscos.metodologia?.observacao} Volatilidade: até{" "}
              {riscos.metodologia?.cortes_volatilidade_pct?.baixo_ate}% ao ano é baixo, até{" "}
              {riscos.metodologia?.cortes_volatilidade_pct?.medio_ate}% é médio, acima é alto
              (cortes definidos pelo projeto).
            </p>
            <div className="tbl-wrap">
              <table>
                <thead>
                  <tr><th>Moeda</th><th>Volatilidade ao ano</th><th>Queda máxima</th><th>Dias medidos</th><th>Risco</th></tr>
                </thead>
                <tbody>
                  {MOEDAS.filter((m) => riscos.moedas[m.sigla]).map((m) => {
                    const r = riscos.moedas[m.sigla];
                    return (
                      <tr key={m.sigla}>
                        <td><b>{m.sigla}</b></td>
                        <td>{numero(r.volatilidade_anual_pct, 1)}%</td>
                        <td>{numero(r.queda_maxima_pct, 1)}%</td>
                        <td>{r.dias}</td>
                        <td><Pill tom={r.risco === "alto" ? "neg" : r.risco === "médio" ? "warn" : "pos"}>{r.risco}</Pill></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Recolhivel>
        )}

        {moedas.length > 0 && (
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>Moeda</th>
                  {camposVisiveis.map((c) => (
                    <th key={c.chave}>
                      {modo === "padroes" && c.chave === "valor_por_compra" ? "Por operação (US$)" : c.rotulo}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {moedas.map((m) => (
                  <tr key={m}>
                    <td><b>{m}</b></td>
                    {camposVisiveis.map((c) => (
                      <td key={c.chave}>
                        <input type="number" min={c.min} step={c.step} aria-label={`${c.rotulo} ${m}`}
                          value={params[m]?.[c.chave] ?? ""}
                          onChange={(e) => editar(m, c.chave, e.target.value)} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="fgrid">
          <div className="field">
            <label htmlFor="s-taxa">Taxa por operação (%)</label>
            <input id="s-taxa" type="number" min="0" max="5" step="0.01" value={taxa}
              onChange={(e) => setTaxa(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="s-slip">Slippage (%)</label>
            <input id="s-slip" type="number" min="0" max="5" step="0.01" value={slippage}
              onChange={(e) => setSlippage(e.target.value)} />
          </div>
        </div>
        <p className="note">
          Ordens executam na abertura do candle seguinte ao sinal; alvo e stop são conferidos pela
          máxima e mínima de cada candle. Taxa e slippage incidem na entrada e na saída.
        </p>

        <div className="btns">
          <button className="btn primary" type="button" onClick={simular} disabled={simulando}>
            {simulando ? "Simulando..." : "Simular investimento"}
          </button>
          {simulando && <Spinner />}
        </div>
      </div>

      {!resultados && (
        <div className="card">
          <Vazio titulo="Nenhuma simulação ainda"
            texto="Escolha a estratégia, o período e as moedas e clique em Simular investimento." />
        </div>
      )}

      {total && (
        <div className="kpis">
          <div className="card kpi"><span className="l">Patrimônio total</span>
            <span className="v">{fmtMoeda(total.patrimonio)}</span>
            <span className="s">Capital inicial somado: {fmtMoeda(total.capital)}</span></div>
          <div className="card kpi"><span className="l">Ganho ou perda total</span>
            <span className="v">{fmtMoeda(lucroTotal)}</span>
            <Pill tom={lucroTotal >= 0 ? "pos" : "neg"}>
              {percentual(total.capital ? (lucroTotal / total.capital) * 100 : 0)} sobre o capital
            </Pill></div>
          <div className="card kpi"><span className="l">Superaram comprar e segurar</span>
            <span className="v">{total.superaram}/{total.porMoeda.length}</span>
            <span className="s">moedas simuladas</span></div>
          <div className="card kpi"><span className="l">Operações fechadas</span>
            <span className="v">{total.operacoes}</span>
            <span className="s">Pior queda de uma moeda: {numero(total.pior, 1)}%</span></div>
        </div>
      )}

      {total && total.porMoeda.length > 1 && (
        <div className="card stack">
          <h2>Lucro por moeda</h2>
          <div className="chart sm" style={{ height: 160 }}>
            <ResponsiveContainer minWidth={0} minHeight={120}>
              <BarChart data={total.porMoeda} margin={MARGEM}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="moeda" tickLine={false} />
                <YAxis width={50} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}%`} />
                <ReferenceLine y={0} stroke="var(--line)" />
                <Tooltip content={<Dica formatar={(v, p) => `${percentual(v)} (${fmtMoeda(p.payload.lucro)})`} />} />
                <Bar dataKey="pct" name="Lucro" maxBarSize={40} radius={[3, 3, 3, 3]}>
                  {total.porMoeda.map((p, i) => (
                    <Cell key={i} fill={p.pct >= 0 ? "var(--pos)" : "var(--neg)"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {resultados &&
        Object.entries(resultados).map(([m, r]) => (
          <BlocoMoeda key={m} sigla={m} r={r} />
        ))}
    </section>
  );
}

/** Intervalo + padrão, com uma linha só dizendo qual foi o melhor no backtest. */
function EscolhaPadrao({ intervalo, setIntervalo, padrao, setPadrao, lista, melhor, ranking }) {
  const st = melhor?.st;
  return (
    <div className="fgrid">
      <div className="field">
        <span className="lab">Tempo gráfico</span>
        <Seg label="Tempo gráfico" value={intervalo} options={INTERVALOS_SIM} onChange={setIntervalo} />
      </div>
      <div className="field">
        <label htmlFor="s-padrao">Padrão</label>
        <select id="s-padrao" className="sel" style={{ width: "100%" }} value={padrao}
          onChange={(e) => setPadrao(e.target.value)} disabled={!lista.length}>
          {!lista.length && <option value="">Carregando...</option>}
          {lista.map((p) => (
            <option key={p.tipo} value={p.tipo}>
              {p.tipo === melhor?.tipo ? "★ " : ""}{p.nome} ({p.direcao === "alta" ? "↑" : "↓"})
            </option>
          ))}
        </select>
      </div>
      <div className="field" style={{ gridColumn: "1 / -1" }}>
        {melhor ? (
          <p className="note" aria-live="polite">
            <b>★ Melhor desempenho em {INTERVALOS_SIM.find((i) => i.valor === intervalo)?.rotulo}
            {" "}(backtest nas 8 moedas): {melhor.nome}</b> — atingiu o alvo
            em {numero(st.taxa_acerto_pct, 0)}% das {st.avaliados} vezes, contra{" "}
            {numero(st.baseline_pct, 0)}% de uma entrada qualquer
            {st.vantagem_pp <= 0 ? ". Nenhum padrão superou o acaso neste tempo gráfico." : "."}
          </p>
        ) : (
          <p className="note">
            {ranking === null ? "Sem backtest dos padrões (rode tools/backtest_padroes.py)." : "Sem casos suficientes para indicar o melhor padrão."}
          </p>
        )}
      </div>
    </div>
  );
}

function Estat({ rotulo, valor }) {
  return (
    <div className="estat">
      <span className="l">{rotulo}</span>
      <span className="v">{valor}</span>
    </div>
  );
}

const pontoMarcador = (cor, vazado) => (props) => {
  const { cx, cy, value } = props;
  if (value == null || cx == null || cy == null) return null;
  return (
    <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={5} fill={vazado ? "var(--surface)" : cor}
      stroke={cor} strokeWidth={2} />
  );
};

function BlocoMoeda({ sigla, r }) {
  const [grafico, setGrafico] = useState("patrimonio");
  const linhas = useMemo(() => (r.vazio ? [] : operacoes(r)), [r]);
  const serie = useMemo(() => {
    if (r.vazio) return [];
    const base = r.serie_patrimonio[0]?.preco || 1;
    const capital = r.parametros.capital_inicial * (1 - (r.parametros.taxa_operacao_pct || 0) / 100);
    const entradas = {};
    const saidas = {};
    r.trades.forEach((t) => {
      (t.entrada === false ? saidas : entradas)[t.timestamp] = t.preco;
    });
    let pico = 0;
    let picoBh = 0;
    return r.serie_patrimonio.map((p) => {
      const seguro = (capital * p.preco) / base;
      pico = Math.max(pico, p.patrimonio);
      picoBh = Math.max(picoBh, seguro);
      return {
        ...p,
        rotulo: rotuloHora(p.timestamp),
        seguro,
        entrada: entradas[p.timestamp] ?? null,
        saida: saidas[p.timestamp] ?? null,
        queda: pico ? (p.patrimonio / pico - 1) * 100 : 0,
        quedaBh: picoBh ? (seguro / picoBh - 1) * 100 : 0,
      };
    });
  }, [r]);

  if (r.vazio) {
    return (
      <div className="card stack">
        <h2>{nomeDaMoeda(sigla)} ({sigla})</h2>
        <div className="callout">{r.mensagem}</div>
      </div>
    );
  }
  const s = r.resumo;
  const pr = r.parametros;
  const fechadas = s.operacoes_fechadas ?? s.total_vendas;
  const etiqueta = pr.modo === "padroes"
    ? `${pr.padrao_nome} · ${INTERVALOS_SIM.find((i) => i.valor === pr.intervalo)?.rotulo}`
    : pr.modo === "fomo" ? "FOMO" : "Flat";

  return (
    <div className="card stack">
      <div className="row-between">
        <h2>{nomeDaMoeda(sigla)} ({sigla})</h2>
        <Pill tom="acc">{etiqueta}</Pill>
      </div>
      <div className="kpis">
        <div className="kpi"><span className="l">Lucro líquido</span>
          <span className="v">{fmtMoeda(s.lucro_total)}</span>
          <Pill tom={s.lucro_total >= 0 ? "pos" : "neg"}>{percentual(s.lucro_total_pct)}</Pill></div>
        <div className="kpi"><span className="l">Queda máxima</span>
          <span className="v">{numero(s.queda_maxima_pct, 1)}%</span>
          <span className="s">Comprar e segurar: {numero(s.buy_and_hold.queda_maxima_pct, 1)}%</span></div>
        <div className="kpi"><span className="l">Taxa de acerto</span>
          <span className="v">{s.taxa_acerto_pct != null ? `${numero(s.taxa_acerto_pct, 0)}%` : "—"}</span>
          <span className="s">
            {fechadas} operações fechadas · fator de lucro {s.fator_lucro != null ? numero(s.fator_lucro, 2) : "—"}
          </span></div>
        <div className="kpi"><span className="l">Comprar e segurar</span>
          <span className="v">{percentual(s.buy_and_hold.lucro_pct)}</span>
          <Pill tom={s.superou_buy_and_hold ? "pos" : "neg"}>
            {s.superou_buy_and_hold ? "Estratégia superou" : "Estratégia ficou atrás"}
          </Pill></div>
      </div>

      <div className="stack">
        <Seg label="Gráfico" value={grafico} options={GRAFICOS} onChange={setGrafico} />
        {grafico === "patrimonio" && (
          <>
            <div className="chart lg" role="img" aria-label="Patrimônio da estratégia e do comprar e segurar">
              <ResponsiveContainer minWidth={0} minHeight={200}>
                <ComposedChart data={serie} margin={MARGEM}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="rotulo" tickLine={false} minTickGap={50} />
                  <YAxis width={70} domain={["auto", "auto"]} tickLine={false} axisLine={false}
                    tickFormatter={(v) => numero(v, 0)} />
                  <Tooltip content={<Dica formatar={(v) => fmtMoeda(v)} />} />
                  <ReferenceLine y={Number(pr.capital_inicial)} stroke="var(--line)" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="seguro" name="Comprar e segurar" stroke="var(--neu)"
                    strokeWidth={2} strokeDasharray="5 4" dot={false} />
                  <Area type="monotone" dataKey="patrimonio" name="Estratégia" stroke="var(--accent)"
                    strokeWidth={2} fill="var(--accent)" fillOpacity={0.08} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <div className="legend">
              <span style={{ "--c": "var(--accent)" }}>Estratégia</span>
              <span className="tracejada" style={{ "--c": "var(--neu)" }}>Comprar e segurar</span>
            </div>
          </>
        )}
        {grafico === "preco" && (
          <>
            <div className="chart lg" role="img" aria-label="Preço com as entradas e saídas">
              <ResponsiveContainer minWidth={0} minHeight={200}>
                <ComposedChart data={serie} margin={MARGEM}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="rotulo" tickLine={false} minTickGap={50} />
                  <YAxis width={70} domain={["auto", "auto"]} tickLine={false} axisLine={false}
                    tickFormatter={(v) => numero(v, v < 10 ? 3 : 0)} />
                  <Tooltip content={<Dica formatar={(v) => fmtMoeda(v, v < 10 ? 4 : 2)} />} />
                  <Line type="monotone" dataKey="preco" name="Preço" stroke="var(--neu)" strokeWidth={2} dot={false} />
                  <Line dataKey="entrada" name="Entrada" stroke="none" isAnimationActive={false}
                    dot={pontoMarcador("var(--accent)", false)} activeDot={false} />
                  <Line dataKey="saida" name="Saída" stroke="none" isAnimationActive={false}
                    dot={pontoMarcador("var(--series)", true)} activeDot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <div className="legend">
              <span style={{ "--c": "var(--neu)" }}>Preço</span>
              <span style={{ "--c": "var(--accent)" }}>Entrada</span>
              <span style={{ "--c": "var(--series)" }}>Saída</span>
            </div>
          </>
        )}
        {grafico === "queda" && (
          <>
            <div className="chart lg" role="img" aria-label="Queda a partir do pico do patrimônio">
              <ResponsiveContainer minWidth={0} minHeight={200}>
                <AreaChart data={serie} margin={MARGEM}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="rotulo" tickLine={false} minTickGap={50} />
                  <YAxis width={50} tickLine={false} axisLine={false} tickFormatter={(v) => `${numero(v, 0)}%`} />
                  <Tooltip content={<Dica formatar={(v) => `${numero(v, 2)}%`} />} />
                  <ReferenceLine y={0} stroke="var(--line)" />
                  <Area type="monotone" dataKey="quedaBh" name="Comprar e segurar" stroke="var(--neu)"
                    strokeWidth={2} strokeDasharray="5 4" fill="none" dot={false} />
                  <Area type="monotone" dataKey="queda" name="Estratégia" stroke="var(--neg)"
                    strokeWidth={2} fill="var(--neg)" fillOpacity={0.12} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="legend">
              <span style={{ "--c": "var(--neg)" }}>Estratégia</span>
              <span className="tracejada" style={{ "--c": "var(--neu)" }}>Comprar e segurar</span>
            </div>
          </>
        )}
      </div>

      <Recolhivel titulo="Estatísticas das operações">
        <div className="estats">
          <Estat rotulo="Média por operação"
            valor={s.media_por_operacao != null ? `${fmtMoeda(s.media_por_operacao)} (${percentual(s.media_por_operacao_pct)})` : "—"} />
          <Estat rotulo="Maior ganho" valor={s.maior_ganho != null ? fmtMoeda(s.maior_ganho) : "—"} />
          <Estat rotulo="Maior perda" valor={s.maior_perda != null ? fmtMoeda(s.maior_perda) : "—"} />
          <Estat rotulo="Duração média" valor={s.duracao_media_h != null ? `${numero(s.duracao_media_h, 1)} h` : "—"} />
          <Estat rotulo="Tempo posicionado" valor={`${numero(s.exposicao_pct, 0)}% do período`} />
          <Estat rotulo="Índice de Sharpe" valor={s.sharpe != null ? numero(s.sharpe, 2) : "—"} />
          <Estat rotulo="Saídas por stop" valor={s.vendas_por_stop_loss} />
          {pr.modo === "padroes" && <Estat rotulo="Saídas por prazo" valor={s.saidas_por_prazo} />}
          <Estat rotulo="Taxas pagas" valor={fmtMoeda(s.custos_totais)} />
          <Estat rotulo="Posições abertas no fim" valor={s.posicoes_abertas_final} />
          {pr.modo === "padroes" && s.sem_caixa > 0 && <Estat rotulo="Sinais sem caixa" valor={s.sem_caixa} />}
        </div>
      </Recolhivel>

      {linhas.length > 0 && (
        <Recolhivel titulo={`Operações (${linhas.length})`} aberto>
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr><th>#</th><th>Lado</th><th>Entrada</th><th>Saída</th><th>Duração</th><th>Resultado</th><th>Acumulado</th></tr>
              </thead>
              <tbody>
                {linhas.map(({ entrada: e, saida: x, aberta, acumulado }, i) => (
                  <tr key={e.posicao_id} className={x ? (x.lucro > 0 ? "ok" : "bad") : ""}>
                    <td>{i + 1}</td>
                    <td>{e.lado === "vendido" ? <Pill tom="neg">Vendido</Pill> : <Pill tom="pos">Comprado</Pill>}</td>
                    <td>{dataHoraCompleta(e.timestamp)}<br /><span className="hint">{fmtMoeda(e.preco)}</span></td>
                    <td>
                      {x ? (
                        <>{dataHoraCompleta(x.timestamp)}<br />
                          <span className="hint">{fmtMoeda(x.preco)} · {MOTIVO[x.motivo] ?? x.motivo}</span></>
                      ) : <Pill>Aberta</Pill>}
                    </td>
                    <td>{x ? `${numero(x.duracao_h, 0)} h` : "—"}</td>
                    <td>
                      {x ? `${fmtMoeda(x.lucro)} (${percentual(x.lucro_pct)})`
                        : aberta ? <span className="hint">{fmtMoeda(aberta.valor_atual - aberta.valor_investido)} não realizado</span> : "—"}
                    </td>
                    <td>{acumulado != null ? fmtMoeda(acumulado) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Recolhivel>
      )}
    </div>
  );
}
