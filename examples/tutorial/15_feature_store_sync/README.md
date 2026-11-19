# 15 · Feature Store: BigQuery Features Synced & Served Online by Vertex AI (`v1.0.0`)

**What you learn:** how to provision a Bigtable-backed Vertex AI Feature Online Store, bind a BigQuery offline table as a Feature View, trigger synchronization with `FeatureStoreAdminClient`, and fetch sub-10ms online features in an agent tool via `FeatureStoreOnlineClient`.

```
BigQuery agentops_tutorial.dealer_features ──► Feature View (bq:// source) ──sync──► Feature Online Store (Bigtable)
   entity_id, feature_timestamp,                        │                                   │
   sales_30d_eur, open_backorders, country              └─ FeatureStoreAdminClient          └─ FeatureStoreOnlineClient
                                                           .trigger_sync()                      .read_online_features()
```

## SDK calls used

| Call | What it does |
|---|---|
| `FeatureStoreAdminClient(project_id, location)` | Provisions stores/views and orchestrates syncs against real Vertex AI APIs (zero fake/dry-run fallback) |
| `admin.ensure_online_store(store_id, *, min_nodes=1, max_nodes=1, wait_for_completion=True)` | Idempotently provisions the Bigtable-backed Feature Online Store |
| `admin.ensure_feature_view(store_id, view_id, *, bigquery_uri="bq://...", entity_id_columns=("entity_id",), wait_for_completion=True)` | Binds an existing BigQuery feature table (`bigquery.Client`) to the online store |
| `admin.trigger_sync(store_id, view_id, *, wait_for_completion=True)` | Triggers BigQuery-to-Bigtable batch replication and polls until completion |
| `FeatureStoreOnlineClient(project_id, location).read_online_features(store_id, view_id, *, entity_ids, features)` | Serves low-latency feature dictionaries by entity key (`read_online_features` / `aread_online_features`) |
| `InMemoryFeatureOnlineStore` | Explicit in-memory double implementing `FeatureOnlineStorePort` for offline tests |

In `main.py`, the offline table `agentops_tutorial.dealer_features` is created and seeded explicitly via `google.cloud.bigquery.Client` before calling `FeatureStoreAdminClient.ensure_feature_view(...)`.

## GCP resources used and cost

| Resource | Why | Access needed |
|---|---|---|
| BigQuery dataset `agentops_tutorial`, table `dealer_features` | Offline feature source (`europe-west4`) | `roles/bigquery.dataEditor` + `roles/bigquery.jobUser` |
| Vertex AI Feature Online Store `agentops_tutorial_online_store` (1 Bigtable node — **billed while active**) | Online feature serving | `roles/aiplatform.featureStoreAdmin` to provision, `roles/aiplatform.featureStoreUser` to read |
| Feature View `dealer_features_view` | Binding + sync state | Same as above |

Both the BigQuery dataset and the Vertex AI Feature Online Store must reside in the same region (`europe-west4`).

## Run

```bash
python examples/tutorial/15_feature_store_sync/main.py
```

Expected: seeds `2` rows in `agentops_tutorial.dealer_features`, ensures `agentops_tutorial_online_store` is `ACTIVE`, syncs `dealer_features_view` (`SUCCEEDED`), and prints the online feature values fetched for `dealer-4711`. The very first store creation and sync take several minutes.
