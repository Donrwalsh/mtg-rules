import { fetchRule } from './api';

export interface RulePreview {
  title: string;
  text: string;
}

// One request per rule per page load; failures are forgotten so a later
// hover can retry.
const cache = new Map<string, Promise<RulePreview | null>>();

export function previewRule(ruleId: string): Promise<RulePreview | null> {
  let preview = cache.get(ruleId);
  if (!preview) {
    preview = fetchRule(ruleId).then(
      (r) => ({
        title: r.heading ? `${r.rule_id} · ${r.heading}` : `Rule ${r.rule_id}`,
        text: r.text
      }),
      () => {
        cache.delete(ruleId);
        return null;
      }
    );
    cache.set(ruleId, preview);
  }
  return preview;
}
