export type PluralForms = readonly [string, string] | readonly [string, string, string];

function getPluralIndex(count: number, forms: PluralForms): number {
  const n = Math.abs(count);
  const mod10 = n % 10;
  const mod100 = n % 100;

  if (mod10 === 1 && mod100 !== 11) {
    return 0;
  }
  if (forms.length > 2 && mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) {
    return 1;
  }
  return forms.length > 2 ? 2 : 1;
}

export function pluralize(count: number, forms: PluralForms): string {
  return forms[getPluralIndex(count, forms)];
}