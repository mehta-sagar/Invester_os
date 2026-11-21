-- Core spine only. Each later phase adds its own migration. Forward-only.
CREATE TABLE source_registry (
  source_id TEXT PRIMARY KEY, family TEXT, publisher TEXT, dataset TEXT, access_method TEXT,
  url_pattern TEXT, rate_limit_s DOUBLE, known_at_rule TEXT, edge_used TEXT, status TEXT, last_success TIMESTAMP);
CREATE TABLE manifest (
  sha256 TEXT, source_id TEXT, path TEXT, url TEXT, fetched_at TIMESTAMP, bytes BIGINT,
  http_status INTEGER, content_type TEXT, era TEXT, task_key TEXT, PRIMARY KEY (sha256, path));
CREATE TABLE ledger (
  task_key TEXT, stage TEXT, outcome TEXT, code_version TEXT, attempted_at TIMESTAMP, detail TEXT,
  PRIMARY KEY (task_key, stage, code_version));
CREATE TABLE entity_master (entity_id TEXT PRIMARY KEY, issuer_code TEXT, first_name_seen TEXT);
CREATE TABLE security_master (
  security_id TEXT PRIMARY KEY, entity_id TEXT, instrument_class TEXT,
  first_seen DATE, last_seen DATE, listed_on DATE, first_seen_censored BOOLEAN, last_seen_censored BOOLEAN);
CREATE TABLE identifier_history (
  security_id TEXT, id_type TEXT, id_value TEXT, exchange TEXT, valid_from DATE, valid_to DATE, evidence_sha256 TEXT);
CREATE TABLE prices_raw (
  security_id TEXT, exchange TEXT, trade_date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
  prev_close DOUBLE, volume BIGINT, traded_value DOUBLE, trades BIGINT, delivery_qty BIGINT, src_sha256 TEXT,
  PRIMARY KEY (security_id, exchange, trade_date));
CREATE TABLE adjustments (
  security_id TEXT, ex_date DATE, action_type TEXT, factor DOUBLE, known_at TIMESTAMP, src_sha256 TEXT);
