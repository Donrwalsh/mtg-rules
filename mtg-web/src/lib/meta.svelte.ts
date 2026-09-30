import { fetchMeta, MAX_QUERY_CHARS, type Meta } from './api';

// Public facts the page states. Defaults hold until (or if never) the API answers.
export const meta = $state<Meta>({
  answers_per_day: null,
  max_query_chars: MAX_QUERY_CHARS,
  rules_as_of: null
});

let started = false;

export function loadMeta(): void {
  if (started) return;
  started = true;
  fetchMeta()
    .then((m) => Object.assign(meta, m))
    .catch(() => {
      started = false; // try again on the next page visit
    });
}
