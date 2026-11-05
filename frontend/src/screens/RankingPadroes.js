import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { foiCancelada, MOEDAS } from "../constants";
import { numero } from "../format";
import { Pill, Seg, Vazio } from "../components/ui";
import { INTERVALOS, PillDirecao } from "./AnalisadorGraficos";

const TODAS = "todas";
const ORDENS = [
  { valor: "vantagem", rotulo: "Vantagem sobre o acaso" },
  { valor: "acerto", rotulo: "Taxa de acerto" },
];

/** Grau de evidência de uma linha do ranking. */
export function evidencia(r, comparacoes) {
  if (!r || !r.avaliados) return { tom: "", texto: "Sem dados" };
  if (!r.amostra_suficiente) return { tom: "", texto: "Amostra pequena" };
  // Bonferroni: com muitas comparações, alguma dá p < 0,05 por acaso.
  if (r.significativo && r.p_valor < 0.05 / comparacoes) return { tom: "pos", texto: "Forte" };
  if (r.significativo) return { tom: "warn", texto: "Significativo" };
  if (r.vantagem_pp != null && r.vantagem_pp < 0) return { tom: "neg", texto: "Pior que o acaso" };
  return { tom: "", texto: "Inconclusivo" };
}

export default function RankingPadroes({ moeda, onEscolher }) {
  const [intervalo, setIntervalo] = useState("4h");
  // "todas" junta as 8 moedas do projeto; uma sigla filtra só aquela moeda.
  const [amostra, setAmostra] = useState(TODAS);
  const moedaConsultada = amostra === TODAS ? moeda : amostra;
  const [ordem, setOrdem] = useState("vantagem");
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState(null);

  useEffect(() => {
    const controle = new AbortController();
    setErro(null);
    api.rankingPadroes(moedaConsultada, intervalo, controle.signal)
      .then((r) => setDados(r?.tipos ? r : null))
      .catch((e) => {
        if (foiCancelada(e)) return;
        setDados(null);
        setErro(e instanceof ApiError ? e.message : "Não foi possível falar com o servidor.");
      });
    return () => controle.abort();
  }, [moedaConsultada, intervalo]);

  const linhas = useMemo(() => {
    if (!dados) return [];
    const metrica = ordem === "vantagem" ? "vantagem_pp" : "taxa_acerto_pct";
    const todas = dados.catalogo.map((c) => ({
      ...c, r: amostra === TODAS ? dados.tipos[c.tipo]?.geral : dados.tipos[c.tipo]?.moeda,
    }));
    // Amostras pequenas vão para o fim: com poucos casos a taxa é ruído.
    const nota = (l) => [l.r?.amostra_suficiente ? 1 : 0, l.r?.[metrica] ?? -Infinity];
    return todas.sort((a, b) => {
      const [sa, va] = nota(a);
      const [sb, vb] = nota(b);
      return sb - sa || vb - va;
    });
  }, [dados, amostra, ordem]);

  // 12 padrões x 3 intervalos testados no backtest.
  const comparacoes = (dados?.catalogo.length ?? 12) * INTERVALOS.length;
  const melhor = linhas.find((l) => l.r?.amostra_suficiente && (l.r.vantagem_pp ?? 0) > 0);

  return (
    <div className="card stack">
      <div>
        <h2>Ranking dos padrões gráficos</h2>
        <p className="hint">
          Qual leitura de gráfico mais acertou no histórico, por intervalo. "Acerto" = atingir o alvo
          antes do stop. A vantagem compara com uma entrada qualquer com o mesmo alvo e stop: padrões
          com alvo curto acertam mais por geometria, não por mérito.
        </p>
      </div>

      <div className="btns">
        <Seg label="Intervalo do ranking" value={intervalo} options={INTERVALOS} onChange={setIntervalo} />
        <select className="sel" aria-label="Moeda do ranking" value={amostra}
          onChange={(e) => setAmostra(e.target.value)}>
          <option value={TODAS}>Todas as moedas</option>
          {MOEDAS.map((m) => (
            <option key={m.sigla} value={m.sigla}>{m.nome} ({m.sigla})</option>
          ))}
        </select>
        <Seg label="Ordenar por" value={ordem} options={ORDENS} onChange={setOrdem} />
      </div>

      {!dados ? (
        <Vazio titulo={erro ? "Ranking indisponível" : "Carregando o ranking..."} texto={erro || undefined} />
      ) : (
        <>
          {melhor && (
            <div className="card insight">
              <p>
                <strong>Melhor em {INTERVALOS.find((i) => i.valor === intervalo).rotulo}:</strong>{" "}
                {melhor.nome} — acertou {numero(melhor.r.taxa_acerto_pct, 1)}% contra{" "}
                {numero(melhor.r.baseline_pct, 1)}% de uma entrada qualquer
                ({numero(melhor.r.vantagem_pp, 1)} pontos a mais, {numero(melhor.r.avaliados)} casos
                {amostra === TODAS ? `, ${dados.moedas.length} moedas` : `, só ${amostra}`}).
                {" "}Evidência: {evidencia(melhor.r, comparacoes).texto.toLowerCase()}.
              </p>
            </div>
          )}
          {!melhor && (
            <div className="callout">
              Sem conclusão em {INTERVALOS.find((i) => i.valor === intervalo).rotulo}
              {amostra === TODAS ? "" : ` para ${amostra}`}: nenhum padrão tem 30 casos ou mais com
              vantagem sobre o acaso. {intervalo === "1w"
                ? "No semanal o histórico (desde 2017) rende poucos padrões."
                : "Tente \"Todas as moedas\" para uma amostra maior."}
            </div>
          )}
          {/* Vantagem e evidência logo após o nome: no celular as demais rolam. */}
          <div className="tbl-wrap" style={{ maxHeight: "none" }}>
            <table>
              <thead>
                <tr>
                  <th>#</th><th>Padrão</th><th>Vantagem</th><th>Evidência</th><th>Acerto</th>
                  <th>Entrada qualquer</th><th>Casos</th><th>p-valor</th>
                </tr>
              </thead>
              <tbody>
                {linhas.map((l, k) => {
                  const ev = evidencia(l.r, comparacoes);
                  return (
                    <tr key={l.tipo} tabIndex={0} role="button" aria-pressed={false}
                      aria-label={`Ver ${l.nome} no gráfico de ${intervalo}`}
                      className={`clicavel ${ev.tom === "pos" || ev.tom === "warn" ? "ok" : ev.tom === "neg" ? "bad" : ""}`}
                      onClick={() => onEscolher(l.tipo, intervalo, moedaConsultada)}
                      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onEscolher(l.tipo, intervalo, moedaConsultada)}>
                      <td>{k + 1}</td>
                      <td style={{ whiteSpace: "normal", minWidth: 150 }}><PillDirecao direcao={l.direcao} /> {l.nome}</td>
                      <td>{l.r?.vantagem_pp != null
                        ? `${l.r.vantagem_pp > 0 ? "+" : ""}${numero(l.r.vantagem_pp, 1)} pp` : "—"}</td>
                      <td><Pill tom={ev.tom}>{ev.texto}</Pill></td>
                      <td>{l.r?.taxa_acerto_pct != null ? `${numero(l.r.taxa_acerto_pct, 1)}%` : "—"}</td>
                      <td>{l.r?.baseline_pct != null ? `${numero(l.r.baseline_pct, 1)}%` : "—"}</td>
                      <td>{numero(l.r?.avaliados ?? 0)}</td>
                      <td>{l.r?.p_valor != null ? numero(l.r.p_valor, 3) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="hint">
            "Forte" = significativo mesmo após a correção de Bonferroni para {comparacoes} comparações
            (p &lt; {numero(0.05 / comparacoes, 4)}); "Significativo" = p &lt; 0,05, mas pode ser acaso
            entre tantos testes. Amostras com menos de 30 casos vão para o fim. Clique num padrão para
            vê-lo no gráfico abaixo. Backtest de {dados.gerado_em ? new Date(dados.gerado_em).toLocaleDateString("pt-BR") : "—"}.
          </p>
        </>
      )}
    </div>
  );
}
