// Shared by the Leads routes (SPEC §20.5): the masked columns of frontend.leads and CSV encoding.

export const LEAD_COLUMNS = `id, created_at, status, score, score_value, reason, next_action, callback_after,
  interest_model, customer_display, phone_masked, language, summary`;

export const CSV_HEADER = [
  'created_at', 'status', 'score', 'score_value', 'reason', 'next_action', 'callback_after',
  'interest_model', 'customer_display', 'phone_masked', 'language', 'summary',
] as const; // prettier-ignore

// A spreadsheet runs cells starting with =, +, -, @ (or tab/CR) as formulas: prefix them with ' (CSV injection).
export function csvCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  let text = value instanceof Date ? value.toISOString() : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(rows: Record<string, unknown>[]): string {
  const lines = [
    CSV_HEADER.join(','),
    ...rows.map((r) => CSV_HEADER.map((k) => csvCell(r[k])).join(',')),
  ];
  return lines.join('\r\n') + '\r\n';
}
