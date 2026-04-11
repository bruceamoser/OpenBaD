/**
 * REST API client helpers for the OpenBaD backend.
 */

const BASE = '';

export async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`);
  if (!resp.ok) throw new Error(`GET ${path} failed: ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export async function put<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`PUT ${path} failed: ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) throw new Error(`POST ${path} failed: ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export async function del<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, { method: 'DELETE' });
  if (!resp.ok) throw new Error(`DELETE ${path} failed: ${resp.statusText}`);
  return resp.json() as Promise<T>;
}
