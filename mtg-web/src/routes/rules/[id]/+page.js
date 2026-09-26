// Rule IDs can't be enumerated at build time, so this route is served by the
// SPA fallback (index.html) -- nginx's try_files covers hard refreshes.
export const prerender = false;
