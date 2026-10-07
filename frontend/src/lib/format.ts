const dateTime = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit"
});
const dateOnly = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
const integer = new Intl.NumberFormat("ru-RU");

export const DASH = "—";

/** «07.10.2026, 15:14» in the viewer's time zone; a dash for nothing. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return DASH;
  }
  const moment = new Date(value);
  return Number.isNaN(moment.getTime()) ? value : dateTime.format(moment);
}

/** A calendar day as written: a date without a time is not shifted by the time zone. */
export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return DASH;
  }
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (day) {
    return `${day[3]}.${day[2]}.${day[1]}`;
  }
  const moment = new Date(value);
  return Number.isNaN(moment.getTime()) ? value : dateOnly.format(moment);
}

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : integer.format(value);
}

export function formatConfidence(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : value.toFixed(2);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) {
    return DASH;
  }
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours) {
    return `${hours} ч ${minutes} мин`;
  }
  return minutes ? `${minutes} мин ${rest} с` : `${rest} с`;
}

/** Only a web address becomes a link: the URL is scraped text. */
export function externalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}
