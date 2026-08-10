# Theme Sources and Intake Policy

## Position

A theme is a research anchor, not a database specification.  It gives the
research agent a place to start looking for a business world; the local
operational state, tools, and task instances are constructed later.

Therefore, a theme is not rejected merely because it lacks a particular data
field at intake time.  Requiring a complete schema before research would make
the discovery stage circular.  The correct question at intake is: "Can this
seed plausibly lead to public, reproducible evidence about an object or a
workflow?"

The research agent may subsequently expand a theme, merge it with a related
theme, or reject it when the accessible evidence is too thin.

## Two Gates

| Gate | When | Required evidence | Decision |
| --- | --- | --- | --- |
| Theme intake | Before research | One public source, a recognizable business object or workflow, and an indication that more data may be found | queue research; no complete data model required |
| Environment acceptance | After research and tool construction | Retrieved provenance-bearing data, operational entities/relations, executable tools, one successful reference execution, and a passing 5-run check | retain as a training environment |

This keeps the seed pool broad while keeping the final training environments
strictly executable and verifiable.

## How Agent-World Uses Themes

Agent-World collects a raw theme set from three sources: Smithery MCP server
specifications (about 2.8K), open-source tool documentation (about 0.5K), and
industrial PRDs (about 0.2K).  It then uses each theme to drive a deep-research
agent that gathers and persists local data, before generating/filtering tools
and tasks.  See [Agent-World](https://arxiv.org/abs/2604.18292).

Its paper describes environment taxonomy construction after the environment
ecosystem has been built and filtered.  The figure places clustering in the
broader discovery process, so the safest interpretation is:

1. collect unstructured theme seeds;
2. research data and construct candidate environments;
3. classify the verified environments to organize coverage and reveal gaps.

Taxonomy is thus not a hard prerequisite for web research.  For this project,
we will use a light early label only for routing and diversity accounting, then
write the formal taxonomy after an environment passes acceptance.

## Source Tiers

### A. Tool and API catalogues: primary seed pool

| Source | URL | What it contributes | Use in this project | Caveat |
| --- | --- | --- | --- | --- |
| Smithery / Arcade catalogue | [smithery.ai](https://smithery.ai/) | MCP server descriptions, exposed actions, domains | Extract seed labels and candidate operations; research the underlying public service independently | Catalogue entries and availability can change; do not assume an MCP itself is a reusable data source |
| RapidAPI Hub | [rapidapi.com/hub](https://rapidapi.com/hub) | Large catalogue of real REST API domains and endpoint descriptions | Generate domain seeds and operation hypotheses, then use permitted public sources for the local snapshot | Authentication, cost, quotas, and licensing vary by API |
| ToolBench / ToolLLM | [paper](https://arxiv.org/abs/2307.16789) | Evidence that a broad real-world API catalogue can supply varied tool domains | Use its RapidAPI-style catalogue methodology as a diversity reference, not as data provenance | The benchmark data is not automatically a licensed source for local records |
| MCP-Atlas | [paper](https://arxiv.org/abs/2602.00933) | Real MCP servers and tool-oriented domain coverage | Use only as a taxonomy and evaluation reference | Check the original server/document licence before ingestion |

### B. Open-source business systems: primary seed pool

| Source | URL | Candidate theme families | Why useful |
| --- | --- | --- | --- |
| TheAgentCompany | [GitHub](https://github.com/TheAgentCompany/TheAgentCompany) | software projects, tickets, documents, team communication, file management | The workflows are explicit and backed by self-hostable systems, so they are a strong source of stateful relations |
| WorkArena | [GitHub](https://github.com/ServiceNow/WorkArena) | enterprise knowledge work, service requests, approvals, records | Provides a practical vocabulary for common work workflows and compositional tasks |
| WebArena | [paper](https://arxiv.org/abs/2307.13854) | e-commerce, forums, collaborative development, content management | Demonstrates four reproducible service families with realistic state and external knowledge |
| Public product documentation and open-source READMEs | [GitHub topics](https://github.com/topics) | CRM, inventory, booking, issue tracking, publishing, support, scheduling | Use docs to identify entities and operations; obtain actual records only from allowed public datasets or APIs |

### C. Public data providers: data-discovery partners, not merely themes

| Source | URL | Appropriate use |
| --- | --- | --- |
| Data.gov | [data.gov](https://www.data.gov/) | Find government datasets with explicit licences and structured records |
| World Bank Open Data | [data.worldbank.org](https://data.worldbank.org/) | Public indicators and metadata for data-analysis/service themes |
| OpenStreetMap | [openstreetmap.org](https://www.openstreetmap.org/) | Geographic entities and relationships, subject to Open Database License attribution/share-alike obligations |
| Wikidata | [wikidata.org](https://www.wikidata.org/) | Entity linking, identifiers, and structured factual enrichment under CC0 |

These sources often enter after a seed has been chosen.  For example, an
"urban mobility" seed may be grounded with municipal feeds, OpenStreetMap
places, and an openly licensed operator dataset.

### D. Task and scenario sources: secondary only

| Source | URL | Role |
| --- | --- | --- |
| AppWorld | [paper](https://arxiv.org/abs/2407.18901) | Reference for resettable state, API design, and state-based verification; its nine app domains are useful coverage targets |
| Agent World Model (AWM) | [paper](https://arxiv.org/abs/2602.10090) | Reference for broad everyday-scenario coverage and executable database-backed worlds; it is fully synthetic, so it should not be our primary real-data seed source |
| AgentSynth | [paper](https://arxiv.org/abs/2506.14205) | Later inspiration for natural task framing; never the sole evidence for a real operational environment |

### E. Industrial PRDs: curated, legal-only supplement

Agent-World includes industrial PRDs as one of its seed sources.  We should
only admit PRDs that are public and permitted for this use, such as public
product requirement examples, published API design documents, or openly
licensed project specifications.  Private customer documents, leaked material,
and documents with unclear rights are excluded.

## Recommended Acquisition Order

1. Seed broadly from A and B, because each entry usually suggests both a domain
   and potential tool actions.
2. Deduplicate only obvious near-duplicates at intake; do not reject a seed
   because its expected schema is incomplete.
3. Give every seed a coarse routing label: `public-service`, `business-system`,
   `catalog-api`, `geospatial`, `commerce`, `content`, or `developer-workflow`.
4. Let the research agent identify accessible sources and build a local,
   provenance-bearing state snapshot.
5. Admit only accepted environments into the final taxonomy.  Use the measured
   entity types, relations, mutations, and verified tool paths to classify
   them, rather than trusting the original seed label.
6. Periodically sample new seeds from under-covered capability cells.  This is
   the EnvFactory-style feedback loop: coverage gaps guide expansion rather
   than a purely random catalogue crawl.  See
   [EnvFactory](https://arxiv.org/abs/2605.18703).

## Initial Theme Registry

Store one row per seed before research.  The registry is deliberately small;
the fields after research belong in the environment artifact instead.

```text
theme_id
seed_label
source_type
source_url
licence_or_access_note
evidence_excerpt
coarse_route_label
candidate_entities
candidate_operations
statefulness_hint
data_source_candidates
priority
research_status
```

Suggested `research_status` values are `queued`, `researching`, `expanded`,
`merged`, `rejected_insufficient_evidence`, `candidate_environment`, and
`accepted_environment`.

## First Batch of Seeds

Start with one to three seeds from each family below, not two thousand at once.
They are deliberately broad research anchors.

| Family | Example seed | Likely public data directions |
| --- | --- | --- |
| Mobility | bicycle sharing operations | municipal open-data portals, operator status feeds, OpenStreetMap |
| Civic service | public service request workflow | city open data, public service documentation |
| Commerce | local business catalogue and ordering | openly licensed product/catalogue data, public business registries |
| Developer work | issue and release management | public GitHub repositories, issue trackers, release notes |
| Content operations | public knowledge-base publishing | public documentation sites, version histories, issue trackers |
| Geography | place and facility management | OpenStreetMap, government registries |
| Scheduling | public events and venue availability | municipal calendars, cultural venue datasets |

For every accepted environment, preserve source URLs, retrieval times, licence
notes, normalization decisions, and a resettable local snapshot.  A broad seed
is acceptable; unverifiable or non-executable final data is not.
