# Glossary

Domain terms as the code and the docs use them. The full glossary is
tracked in [#24](https://github.com/Donrwalsh/mtg-rules/issues/24).

**Answer allowance**: the permission for one request to have an LLM
answer written, and paying for it: the gate, a generation slot and a
usage row. Lives in `mtg-api/src/mtg_api/allowance.py`.

**Admission**: one request's gate decision. It ends in exactly one way:
served from the cache, retrieval-only, or an answer started.

**Spend**: one answer being written. Holds its generation slot and its
worst-case reservation until it is settled (real cost) or cancelled (never
started, no cost), then closed (slot freed).

**Generation slot**: one of the in-memory places for an answer being
written right now. Capped in total (`max_concurrent_generations`) and per
IP bucket (`max_concurrent_generations_per_ip`; admins aren't counted per
IP). No free slot means HTTP 429.
