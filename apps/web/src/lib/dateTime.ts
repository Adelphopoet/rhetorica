export const DISPLAY_TIME_ZONE = "Europe/Moscow";
export const DISPLAY_TIME_ZONE_LABEL = "MSK";

type DateValue = Date | string | number;

const SHORT_MONTH_NAMES = [
  "янв.",
  "февр.",
  "мар.",
  "апр.",
  "мая",
  "июн.",
  "июл.",
  "авг.",
  "сент.",
  "окт.",
  "нояб.",
  "дек.",
] as const;

function toValidDate(value: DateValue): Date | null {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatInMoscow(
  value: DateValue,
  options: Intl.DateTimeFormatOptions,
  locale = "en-GB"
): string | null {
  const date = toValidDate(value);
  if (!date) {
    return null;
  }

  return new Intl.DateTimeFormat(locale, {
    ...options,
    timeZone: DISPLAY_TIME_ZONE,
  }).format(date);
}

export function formatMoscowShortDateTime(value: DateValue): string | null {
  const date = toValidDate(value);
  if (!date) {
    return null;
  }

  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TIME_ZONE,
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  if (!values.day || !values.month || !values.hour || !values.minute) {
    return null;
  }

  const month = SHORT_MONTH_NAMES[Number(values.month) - 1];
  if (!month) {
    return null;
  }

  return `${values.day} ${month}, ${values.hour}:${values.minute}`;
}

export function getMoscowCalendarDay(value: DateValue): number | null {
  const date = toValidDate(value);
  if (!date) {
    return null;
  }

  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const year = Number(values.year);
  const month = Number(values.month);
  const day = Number(values.day);
  if (![year, month, day].every(Number.isFinite)) {
    return null;
  }
  return Math.floor(Date.UTC(year, month - 1, day) / 86_400_000);
}
