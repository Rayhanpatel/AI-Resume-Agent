# Dashboards

Two dashboards live in Grafana, in the folder `rayhanpatel.com`: **`rp-overview`** and **`rp-chat`** (built Oct 29, 2026 through Grafana's MCP server). This file is their specification, so they can be rebuilt on a new stack; the panels below are what exists. Datasources: Prometheus `grafanacloud-prom`, Tempo `grafanacloud-traces`, Loki `grafanacloud-logs`.

Notes from building them:
- `chat_starts_total`, `chat_leads_total`, `chat_model_fallbacks_total`, `chat_quota_rejections_total`, `chat_retention_deleted_total` and `chat_time_to_first_word_seconds_*` only exist after the first event of their kind (a web chat start, a lead, a fallback, a refusal, a deletion), so those panels can read "No data" on a quiet stack.
- The A2 result is `synthetic_check_success{check="A2 real answer", source=""}`; `source="manual-test"` values are excluded.
- The success-ratio table multiplies by 100 so the 95/99 thresholds apply.

DASHBOARD 1: "rayhanpatel.com: overview" (uid rp-overview, tags rayhanpatel). Top to bottom:
1. Alert list panel: firing and pending alerts, all folders.
2. Table "Outside checks, last 24 h": per job, success ratio = sum by (job) (increase(probe_all_success_sum[24h])) / sum by (job) (increase(probe_all_success_count[24h])) as percent (green >= 99, orange >= 95, red below); include every Synthetic Monitoring job.
3. Stat "Certificate days left" per job: min by (job) ((probe_ssl_earliest_cert_expiry - time()) / 86400) (red < 7, orange < 21).
4. Stat "Real answer (A2), last run": last_over_time(synthetic_check_success{job="A2 real answer", source=""}[7h]) mapped 1=Pass green, 0=Fail red.
5. Stats "Golden eval": last_over_time(eval_golden_passed[8d]) and last_over_time(eval_golden_cases[8d]) and last_over_time(eval_golden_checked_percent[8d]) (percent).
6. Stat "Database": max(chat_database_up) mapped 1=up green, 0=down red; and chat_database_rows by db_collection_name.
7. Stat "Answers, 24 h": sum by (outcome) (increase(chat_answers_total[24h])).
8. Text panel with links: Chat agent dashboard (/d/rp-chat), Frontend app (/a/grafana-kowalski-app/apps/6310), runbooks (https://github.com/Rayhanpatel/AI-Resume-Agent/tree/main/docs/runbooks), Langfuse (https://us.cloud.langfuse.com).

DASHBOARD 2: "rayhanpatel.com: chat agent" (uid rp-chat, tags rayhanpatel). Time range default last 7 days. Rows:
A. Visitors: time series "Answers per hour by outcome and channel" = sum by (outcome, app_channel) (increase(chat_answers_total[1h])); "Chat starts by outcome" = sum by (outcome) (increase(chat_starts_total[1h])); "Real visitors vs probes" = sum by (app_synthetic) (increase(chat_answers_total[1h])).
B. Speed: "Time to first word, p50/p95 (real visitors)" from chat_time_to_first_word_seconds_bucket{app_synthetic="false"} via histogram_quantile over rate(...[1h]); "Model call duration p95 by model and purpose" from gen_ai_client_operation_duration_seconds_bucket; "HTTP p95 by route" from http_server_duration_milliseconds_bucket grouped by `http_target` (the instrumentation sets no `http_route`).
C. Cost: "Tokens per day by model and purpose" = sum by (gen_ai_request_model, app_purpose) (increase(gen_ai_client_token_usage_sum[1d])) as bars; stat "Tokens, last 24 h" against the 500k budget (threshold orange 400000, red 450000).
D. Quality: "Verification stamps by kind (share)" = sum by (stamp) (increase(chat_verification_stamps_total[1d])) stacked percent; "Fallback model answers" = sum(increase(chat_model_fallbacks_total[1h])); "Decision model errors" = sum(increase(gen_ai_client_operation_duration_seconds_count{app_purpose="decision", error_type!=""}[1h])).
E. Operations: "Quota rejections by scope" = sum by (scope) (increase(chat_quota_rejections_total[1h])); "Leads by outcome" = sum by (outcome) (increase(chat_leads_total[1d])); "Retention deleted by store" = sum by (store) (increase(chat_retention_deleted_total[1d])); "Recent traces" = a Tempo search panel for {resource.service.name="rayhan-agent"}; "Error logs" = Loki logs panel {service_name="rayhan-agent"} |~ "(?i)error|failed|NOT saved".
