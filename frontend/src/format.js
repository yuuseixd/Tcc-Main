/**
 * Formatação de datas e números do dashboard.
 *
 * Tudo é exibido em UTC. O backend agrupa os posts por hora UTC e os
 * relatórios PDF também usam UTC; se a interface convertesse para o fuso do
 * navegador, o mesmo dado apareceria em horas diferentes no gráfico e no PDF.
 */

const UTC = { timeZone: "UTC" };

/** Rótulo curto do eixo X: "02/03 15h". */
export function rotuloHora(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const dia = new Intl.DateTimeFormat("pt-BR", {
    ...UTC,
    day: "2-digit",
    month: "2-digit",
  }).format(d);
  const hora = new Intl.DateTimeFormat("pt-BR", {
    ...UTC,
    hour: "2-digit",
    hour12: false,
  }).format(d);
  return `${dia} ${hora}h`;
}

/** Data e hora completas: "02/03/2026 15:00 UTC". */
export function dataHoraCompleta(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${new Intl.DateTimeFormat("pt-BR", {
    ...UTC,
    dateStyle: "short",
    timeStyle: "short",
  }).format(d)} UTC`;
}

/** Data curta para os cards de tweet: "02 mar, 15:34". */
export function dataCurta(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("pt-BR", {
    ...UTC,
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

export const moeda = (v) =>
  v == null ? "—" : `$${Number(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

export const moedaCurta = (v) =>
  v == null ? "—" : `$${Number(v).toLocaleString("en-US", {
    maximumFractionDigits: 0,
  })}`;

export const percentual = (v, casas = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(casas)}%`;
