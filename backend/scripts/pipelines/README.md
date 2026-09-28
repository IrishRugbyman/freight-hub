# Pipeline-route ingest scripts

One-shot scripts that built the pipeline geometry behind `/api/pipelines`. They are run by
hand, not by a timer, and several hit slow or rate-limited external sources (Overpass,
EIA/AER/CER ArcGIS, Rextag). Run them from `backend/`:

```bash
.venv/bin/python scripts/pipelines/<script>.py --help
```

Every script resolves `data/` from its own location, so the working directory does not
matter. Run the DuckDB writers while the analytics batch job is not holding the write lock.

| Script | Source | Table in `freight_analytics.duckdb` |
|---|---|---|
| `scrape_rextag.py` | RexTag.com FERC pipeline pages | none: writes `data/rextag_pipelines.json` |
| `ingest_eia_routes.py` | EIA inter/intrastate gas shapefile (Jan 2020), fuzzy-matched to RexTag slugs | `eia_pipeline_routes` |
| `ingest_extend_crosswalk.py` | hand-listed WM -> RexTag mappings | `rextag_wm_crosswalk` |
| `ingest_eia_oil_routes.py` | EIA crude and products pipelines | `eia_oil_pipeline_routes` |
| `ingest_eia_hgl_routes.py` | EIA HGL/NGL pipelines | `eia_oil_pipeline_routes` |
| `ingest_eia_ng_intrastate_routes.py` | EIA intrastate gas pipelines | `eia_oil_pipeline_routes` |
| `ingest_wm_straightline_routes.py` | `pipeline_registry` endpoints (2-point fallback) | `eia_oil_pipeline_routes` |
| `ingest_eu_pipeline_routes.py` | SciGRID_gas IGGIELGN | `eu_pipeline_routes` (replaced) |
| `ingest_global_pipeline_routes.py` | OpenStreetMap via Overpass | `global_pipeline_routes` |
| `ingest_global_pipeline_routes_pass2.py` | OSM, merged super-region graphs (imports pass 1) | `global_pipeline_routes` |
| `ingest_osm_named_pipeline_routes.py` | OSM name-tag matching + way chaining | `global_pipeline_routes` |
| `ingest_aer_pipeline_routes.py` | Alberta Energy Regulator GIS | `global_pipeline_routes` |
| `ingest_cer_pipeline_routes.py` | Canada Energy Regulator | `global_pipeline_routes` |

`ingest_eia_routes.py` needs `fiona`, which is deliberately not a backend dependency
(it pulls in GDAL for a one-off run): `uv pip install fiona` into the venv first.
