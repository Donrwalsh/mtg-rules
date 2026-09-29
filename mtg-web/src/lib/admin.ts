import { writable } from 'svelte/store';
import { fetchAdminStatus } from './api';

// Whether this browser holds an admin session. Admin links and controls
// render only when true; the backend enforces access either way.
export const isAdmin = writable(false);

export async function refreshAdmin(): Promise<void> {
  isAdmin.set(await fetchAdminStatus());
}
