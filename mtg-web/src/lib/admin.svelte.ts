import { fetchAdminStatus } from './api';

// Whether this browser holds an admin session. Admin links and controls
// render only when true; the backend enforces access either way.
export const admin = $state({ isAdmin: false });

export async function refreshAdmin(): Promise<void> {
  admin.isAdmin = await fetchAdminStatus();
}
