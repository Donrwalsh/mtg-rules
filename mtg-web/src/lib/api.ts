const API_URL = import.meta.env.PUBLIC_API_URL || 'http://localhost:8000';

export interface QueryResult {
  source: string;
  title: string;
  text: string;
  score: number;
}

export interface QueryResponse {
  query: string;
  results: QueryResult[];
  answer: string | null;
}

export async function submitQuery(query: string): Promise<QueryResponse> {
  const resp = await fetch(`${API_URL}/api/v1/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
  });
  if (!resp.ok) {
    throw new Error(`query failed: ${resp.status}`);
  }
  return resp.json();
}

export interface QueryHistoryRow {
  id: number;
  query: string;
  answer: string | null;
  results: QueryResult[];
  model: string;
  error: string | null;
  created_at: string;
}

export async function fetchHistory(limit: number, offset: number): Promise<QueryHistoryRow[]> {
  const resp = await fetch(`${API_URL}/api/v1/queries?limit=${limit}&offset=${offset}`);
  if (!resp.ok) {
    throw new Error(`history fetch failed: ${resp.status}`);
  }
  return resp.json();
}
