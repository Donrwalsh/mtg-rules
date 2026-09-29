// Same-origin: nginx (Docker) and the Vite dev server (npm run dev) proxy
// /api to the backend.
const API_URL = '';

// Keep in step with the backend's MTG_API_MAX_QUERY_CHARS.
export const MAX_QUERY_CHARS = 500;
// Required on cookie-authenticated admin calls (see mtg_api/admin_auth.py).
export const ADMIN_HEADER = 'X-Admin-Request';

export class RateLimitedError extends Error {}

export interface QueryResult {
  source: string;
  title: string;
  text: string;
  score: number;
  match_type?: string;
  oracle_id?: string | null;
  rule_id?: string | null;
  card_name?: string | null;
  published_at?: string | null;
  scryfall_uri?: string | null;
  cited?: boolean;
}

export interface Citation {
  number: number;
  source_type: string; // "rule" | "card" | "ruling"
  title: string;
  rule_id: string | null;
  card_name: string | null;
  oracle_id: string | null;
  text: string;
  // "/rules/{id}" for rules (our own route), a Scryfall URL for cards and rulings.
  url: string | null;
  published_at: string | null;
}

export interface CitationStats {
  cited_count: number;
  invalid_count: number;
  uncited_answer: boolean;
}

export interface QueryResponse {
  query: string;
  results: QueryResult[];
  answer: string | null;
  citations: Citation[];
  rule_references: string[];
  citation_stats: CitationStats;
  // Set when the answer came from the cache: when it was first generated.
  cached_at: string | null;
  // Why there's no answer: this visitor's quota, or the site's daily budget.
  degraded: 'ip_quota' | 'global_budget' | null;
  // AI answers this visitor has left today; null when unlimited.
  answers_remaining: number | null;
}

export async function submitQuery(
  query: string,
  { fresh = false }: { fresh?: boolean } = {}
): Promise<QueryResponse> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (fresh) headers[ADMIN_HEADER] = '1';
  const resp = await fetch(`${API_URL}/api/v1/query`, {
    method: 'POST',
    headers,
    body: JSON.stringify(fresh ? { query, fresh } : { query })
  });
  if (resp.status === 429) {
    throw new RateLimitedError('Too many requests. Wait a few seconds and try again.');
  }
  if (!resp.ok) {
    throw new Error(`query failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchAdminStatus(): Promise<boolean> {
  try {
    const resp = await fetch(`${API_URL}/api/v1/auth/me`);
    return resp.ok && (await resp.json()).is_admin === true;
  } catch {
    return false;
  }
}

export interface QueryHistoryRow {
  id: number;
  query: string;
  answer: string | null;
  results: QueryResult[];
  model: string;
  error: string | null;
  created_at: string;
  // null for rows saved before citations existed.
  citations: Citation[] | null;
  citation_stats: CitationStats | null;
  rule_references: string[] | null;
  cached: boolean;
}

async function adminGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, { headers: { [ADMIN_HEADER]: '1' } });
  if (!resp.ok) {
    throw new Error(`${path} failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchHistory(limit: number, offset: number): Promise<QueryHistoryRow[]> {
  return adminGet(`/api/v1/queries?limit=${limit}&offset=${offset}`);
}

export async function login(password: string): Promise<boolean> {
  const resp = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password })
  });
  if (resp.status === 429) throw new RateLimitedError('Too many attempts. Wait a minute.');
  if (resp.status === 403) return false;
  if (!resp.ok) throw new Error(`login failed: ${resp.status}`);
  return true;
}

export async function logout(): Promise<void> {
  await fetch(`${API_URL}/api/v1/auth/logout`, { method: 'POST' });
}

export interface UsageDay {
  date: string;
  spend_usd: number;
  outcomes: Record<string, number>;
}

export interface UsageBucket {
  ip_bucket: string;
  requests: number;
  answers: number;
  spend_usd: number;
}

export interface UsageSummary {
  budget_usd: number;
  days: UsageDay[]; // oldest first; the last entry is today (UTC)
  cache_hit_rate: number | null;
  top_ip_buckets: UsageBucket[];
}

export async function fetchUsage(): Promise<UsageSummary> {
  return adminGet('/api/v1/admin/usage');
}

export interface RuleSummary {
  rule_id: string;
  text: string;
}

export interface RuleDetail extends RuleSummary {
  ancestors: RuleSummary[];
  subrules: RuleSummary[];
  rules_ingested_at: string | null;
}

export class NotFoundError extends Error {}

export async function fetchRule(ruleId: string): Promise<RuleDetail> {
  const resp = await fetch(`${API_URL}/api/v1/rules/${encodeURIComponent(ruleId)}`);
  if (resp.status === 404) {
    throw new NotFoundError(`rule ${ruleId} not found`);
  }
  if (!resp.ok) {
    throw new Error(`rule fetch failed: ${resp.status}`);
  }
  return resp.json();
}

export function isExternalUrl(url: string | null): boolean {
  return !!url && /^https?:\/\//.test(url);
}
