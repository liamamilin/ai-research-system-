import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Human-friendly relative time for backend timestamps like
 *  "2026-09-19T00:23:28+0800". Falls back to the raw string. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const normalized = iso.replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return iso;
  const diffMs = Date.now() - then;
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} 天前`;
  return iso.slice(0, 10);
}

/** Compact token counts: 1234 -> "1.2k", 2500000 -> "2.50M". */
export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}
