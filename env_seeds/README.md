# Smithery environment seeds

`fetch_smithery_servers.py` exports the public `remote=true` and `remote=false`
server catalogues shown at <https://smithery.ai/servers>. It fetches the two
groups separately using Smithery's stable deep-pagination `seed` parameter;
ordinary unseeded searches are capped at 500 candidates.

Configure `SMITHERY_API_KEY` in the repository-root `.env`, then run:

```powershell
python env_seeds/fetch_smithery_servers.py
```

Outputs:

- `smithery_servers.json`: Smithery list records with their original field names,
  sorted by `useCount` descending, then `qualifiedName` ascending. This includes
  both values of `remote` and both values of `verified`.
- `smithery_servers_report.json`: source, filter, pagination, count, and SHA-256
  validation metadata.

The request uses the same default list-level fields returned to
`agent_world_mini/catalog.py::_smithery_servers()`, including `id`,
`qualifiedName`, `displayName`, `description`, `useCount`, `verified`, owner and
deployment metadata. Records are not renamed or projected. The script still
does not request each server's tools or connection details.
