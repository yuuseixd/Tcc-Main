import { useState } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  ComposedChart,
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
  { valor: "fomo", rotulo: "FOMO" },
  { valor: "flat", rotulo: "Flat" },
];
const FONTES = [
  { valor: "X", rotulo: "X" },
  { valor: "Reddit", rotulo: "Reddit" },
];
const CAMPOS_PERFIL = [
  "percentual_lucro_venda",
  "percentual_queda_compra",
  "percentual_perda_aceita",
];
const MARGEM = { top: 8, right: 12, left: 0, bottom: 0 };

const parametrosDoPerfil = (nome) => ({
  ...SIM_DEFAULTS,
  ...Object.fromEntries(CAMPOS_PERFIL.map((c) => [c, PERFIS_INVESTIDOR[nome][c]])),
});

export default function Simulador({ moeda, notify, perfisX, perfilUsuario, riscos }) {
  const perfilInicial = perfilUsuario || "Moderado";
  const [modo, setModo] = useState("flat");
  const [fonte, setFonte] = useState("X");
  const [subs, setSubs] = useState("");
  const [perfil, setPerfil] = useState(perfilInicial);
  const [moedas, setMoedas] = useState(() => [moeda]);
  const [params, setParams] = useState(() => ({ [moeda]: parametrosDoPerfil(perfilInicial) }));
  const [de, setDe] = useState("");
  const [ate, setAte] = useState("");
  const [simulando, setSimulando] = useState(false);
  const [resultados, setResultados] = useState(null);

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

  const simular = async () => {
    const perfis =
      modo === "fomo"
        ? fonte === "X" ? listaDePerfis(perfisX) : listaDePerfis(subs)
        : [];
    if (modo === "fomo" && fonte === "X" && perfis.length === 0) {
      notify.erro("Informe ao menos um perfil do X (em X e Reddit ou Configurações) para o modo FOMO.");
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
    for (const m of moedas) {
      const p = params[m];
      if (Number(p.valor_por_compra) > Number(p.capital_inicial)) {
        notify.erro(`${m}: o valor por compra não pode ser maior que o capital.`);
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
          compras: acc.compras + r.resumo.total_compras,
          vendas: acc.vendas + r.resumo.total_vendas,
          superaram: acc.superaram + (r.resumo.superou_buy_and_hold ? 1 : 0),
          porMoeda: [...acc.porMoeda, { moeda: m, lucro: r.resumo.lucro_total, pct: r.resumo.lucro_total_pct }],
        }),
        { capital: 0, patrimonio: 0, compras: 0, vendas: 0, superaram: 0, porMoeda: [] },
      )
    : null;
  const lucroTotal = total ? total.patrimonio - total.capital : 0;

  return (
    <section className="screen" aria-label="Simulador">
      <Topline titulo="Simulador de investimento">
        <Pill>Dinheiro fictício</Pill>
      </Topline>

      <div className="card stack">
        <div className="fgrid">
          <div className="field">
            <span className="lab">Estratégia</span>
            <Seg label="Estratégia" value={modo} options={MODOS} onChange={setModo} />
            <p className="hint">
              {modo === "fomo"
                ? "FOMO: compra quando o sentimento é positivo e o preço caiu. Usa posts já salvos."
                : "Flat: ignora os posts e compra só pela queda de preço."}
            </p>
          </div>
          <div className="field">
            <span className="lab">Fonte do sentimento (FOMO)</span>
            <Seg label="Fonte" value={fonte} options={FONTES} onChange={setFonte} />
            {fonte === "Reddit" && modo === "fomo" ? (
              <input aria-label="Subreddits" placeholder="Subreddits (vazio = todos)"
                value={subs} onChange={(e) => setSubs(e.target.value)} />
            ) : (
              <p className="hint">
                {fonte === "X"
                  ? `Perfis: ${listaDePerfis(perfisX).join(", ") || "nenhum"}`
                  : "Sem subreddits informados, usa todos os posts salvos."}
              </p>
            )}
          </div>
          <div className="field">
            <label htmlFor="s-de">Período de</label>
            <input id="s-de" type="date" value={de} max={ate || undefined} onChange={(e) => setDe(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="s-ate">até</label>
            <input id="s-ate" type="date" value={ate} min={de || undefined} onChange={(e) => setAte(e.target.value)} />
          </div>
        </div>

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

        <div className="field">
          <span className="lab">Moedas a simular</span>
          <div className="chips">
            {MOEDAS.map((m) => (
              <button key={m.sigla} type="button" className="chip" aria-pressed={moedas.includes(m.sigla)}
                onClick={() => alternarMoeda(m.sigla)}>{m.sigla}</button>
            ))}
          </div>
        </div>

        {textoDoAviso(perfil, moedas, riscos) && (
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
                <tr><th>Moeda</th>{SIM_CAMPOS.map((c) => <th key={c.chave}>{c.rotulo}</th>)}</tr>
              </thead>
              <tbody>
                {moedas.map((m) => (
                  <tr key={m}>
                    <td><b>{m}</b></td>
                    {SIM_CAMPOS.map((c) => (
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
            texto="Escolha o período e as moedas e clique em Simular investimento." />
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
          <div className="card kpi"><span className="l">Compras e vendas</span>
            <span className="v">{total.compras}/{total.vendas}</span>
            <span className="s">somado de todas as moedas</span></div>
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

function BlocoMoeda({ sigla, r }) {
  if (r.vazio) {
    return (
      <div className="card stack">
        <h2>{nomeDaMoeda(sigla)} ({sigla})</h2>
        <div className="callout">{r.mensagem}</div>
      </div>
    );
  }
  const s = r.resumo;
  const base = r.serie_patrimonio[0]?.preco || 1;
  const serie = r.serie_patrimonio.map((p) => ({
    ...p,
    rotulo: rotuloHora(p.timestamp),
    seguro: (r.parametros.capital_inicial * p.preco) / base,
  }));

  return (
    <div className="card stack">
      <div className="row-between">
        <h2>{nomeDaMoeda(sigla)} ({sigla})</h2>
        <Pill tom="acc">
          {r.parametros.modo === "fomo" ? "FOMO" : "Flat"} · {r.parametros.fonte}
        </Pill>
      </div>
      <div className="kpis">
        <div className="kpi"><span className="l">Patrimônio final</span>
          <span className="v">{fmtMoeda(s.patrimonio_final)}</span>
          <span className="s">Caixa mais posições abertas</span></div>
        <div className="kpi"><span className="l">Taxa de acerto</span>
          <span className="v">{s.taxa_acerto_pct != null ? `${numero(s.taxa_acerto_pct, 0)}%` : "—"}</span>
          <span className="s">{fmtMoeda(s.lucro_total)} ({percentual(s.lucro_total_pct)}) no total</span></div>
        <div className="kpi"><span className="l">Comprar e segurar</span>
          <span className="v">{percentual(s.buy_and_hold.lucro_pct)}</span>
          <Pill tom={s.superou_buy_and_hold ? "pos" : "neg"}>
            {s.superou_buy_and_hold ? "Estratégia superou" : "Estratégia ficou atrás"}
          </Pill></div>
        <div className="kpi"><span className="l">Compras e vendas</span>
          <span className="v">{s.total_compras}/{s.total_vendas}</span>
          <span className="s">
            {s.posicoes_abertas_final} posições abertas
            {s.vendas_por_stop_loss > 0 && ` · ${s.vendas_por_stop_loss} por stop-loss`}
          </span></div>
      </div>

      <div>
        <h3>Evolução do patrimônio</h3>
        <p className="hint" style={{ marginBottom: 8 }}>
          Sua estratégia contra comprar tudo no primeiro dia e esperar.
        </p>
        <div className="chart lg" role="img" aria-label="Patrimônio da estratégia e do comprar e segurar">
          <ResponsiveContainer minWidth={0} minHeight={200}>
            <ComposedChart data={serie} margin={MARGEM}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="rotulo" tickLine={false} minTickGap={50} />
              <YAxis width={70} domain={["auto", "auto"]} tickLine={false} axisLine={false}
                tickFormatter={(v) => numero(v, 0)} />
              <Tooltip content={<Dica formatar={(v) => fmtMoeda(v)} />} />
              <ReferenceLine y={Number(r.parametros.capital_inicial)} stroke="var(--line)" strokeDasharray="4 4" />
              <Line type="monotone" dataKey="seguro" name="Comprar e segurar" stroke="var(--neu)"
                strokeWidth={2} strokeDasharray="5 4" dot={false} />
              <Area type="monotone" dataKey="patrimonio" name="Estratégia" stroke="var(--accent)"
                strokeWidth={2.4} fill="var(--accent)" fillOpacity={0.08} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div className="legend">
          <span style={{ "--c": "var(--accent)" }}>Estratégia</span>
          <span style={{ "--c": "var(--neu)" }}>Comprar e segurar</span>
        </div>
      </div>

      {r.trades.length > 0 && (
        <Recolhivel titulo={`Operações (${r.trades.length})`} aberto>
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr><th>Tipo</th><th>Hora (UTC)</th><th>Preço</th><th>Qtd.</th><th>Valor</th><th>Resultado</th></tr>
              </thead>
              <tbody>
                {r.trades.map((t, i) => {
                  const venda = t.tipo === "venda";
                  const perda = t.motivo === "perda";
                  return (
                    <tr key={i} className={venda ? (perda ? "bad" : "ok") : ""}>
                      <td>
                        <Pill tom={venda ? (perda ? "neg" : "pos") : ""}>
                          {venda ? (perda ? "Venda (stop-loss)" : "Venda (lucro)") : "Compra"}
                        </Pill>
                      </td>
                      <td>{dataHoraCompleta(t.timestamp)}</td>
                      <td>{fmtMoeda(t.preco)}</td>
                      <td>{numero(t.qtd, 6)}</td>
                      <td>{fmtMoeda(t.valor)}</td>
                      <td>{venda ? `${fmtMoeda(t.lucro)} (${percentual(t.lucro_pct)})` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Recolhivel>
      )}
    </div>
  );
}
