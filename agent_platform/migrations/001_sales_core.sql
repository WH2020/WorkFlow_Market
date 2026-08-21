CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY CHECK (version >= 1),
  name TEXT NOT NULL,
  script_sha256 TEXT NOT NULL CHECK (length(script_sha256) = 64),
  applied_at TEXT NOT NULL,
  application_version TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result = 'applied')
) STRICT;

CREATE TABLE store_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE accounts (
  account_id TEXT PRIMARY KEY,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 500),
  normalized_name TEXT NOT NULL CHECK (length(normalized_name) BETWEEN 1 AND 500),
  region TEXT,
  sector TEXT,
  owner TEXT,
  lifecycle_stage TEXT,
  health TEXT,
  budget_path TEXT,
  summary TEXT,
  project_id TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX accounts_normalized_name_idx ON accounts(normalized_name, account_id);
CREATE INDEX accounts_owner_idx ON accounts(owner, account_id) WHERE deleted_at IS NULL;

CREATE TABLE contacts (
  contact_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 500),
  organization TEXT,
  title TEXT,
  email TEXT,
  phone TEXT,
  identity_status TEXT NOT NULL CHECK (identity_status IN ('confirmed', 'legacy_text', 'unknown')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE account_contacts (
  account_contact_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE RESTRICT,
  role TEXT,
  influence_level TEXT,
  decision_role TEXT,
  relationship_status TEXT,
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(account_id, contact_id, role)
) STRICT;

CREATE INDEX account_contacts_account_idx ON account_contacts(account_id, account_contact_id);

CREATE TABLE opportunities (
  opportunity_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  name TEXT NOT NULL,
  owner TEXT,
  stage TEXT,
  health TEXT,
  amount_min_minor INTEGER,
  amount_max_minor INTEGER,
  currency TEXT CHECK (currency IS NULL OR length(currency) = 3),
  expected_decision_at TEXT,
  win_hypothesis TEXT,
  loss_reason TEXT,
  next_stage_condition TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  CHECK (amount_min_minor IS NULL OR amount_min_minor >= 0),
  CHECK (amount_max_minor IS NULL OR amount_max_minor >= 0),
  CHECK (amount_min_minor IS NULL OR amount_max_minor IS NULL OR amount_min_minor <= amount_max_minor)
) STRICT;

CREATE INDEX opportunities_account_idx ON opportunities(account_id, opportunity_id);

CREATE TABLE sources (
  source_id TEXT PRIMARY KEY,
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 1000),
  url TEXT,
  publisher TEXT,
  published_date TEXT,
  accessed_date TEXT,
  region TEXT,
  topic TEXT,
  source_type TEXT,
  quality TEXT,
  exposure_status TEXT,
  status TEXT NOT NULL,
  limitations TEXT,
  notes TEXT,
  legacy_key_facts TEXT,
  legacy_important_quotes TEXT,
  legacy_interpretation TEXT,
  file_path TEXT,
  file_sha256 TEXT CHECK (file_sha256 IS NULL OR length(file_sha256) = 64),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX sources_topic_idx ON sources(topic, source_id) WHERE deleted_at IS NULL;

CREATE TABLE activities (
  activity_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  opportunity_id TEXT REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT,
  salesperson_id TEXT,
  occurred_at TEXT NOT NULL,
  channel TEXT,
  activity_type TEXT,
  summary TEXT NOT NULL,
  participants_text TEXT,
  source_id TEXT REFERENCES sources(source_id) ON DELETE RESTRICT,
  source_path TEXT,
  source_sha256 TEXT CHECK (source_sha256 IS NULL OR length(source_sha256) = 64),
  evidence_status TEXT NOT NULL DEFAULT 'pending' CHECK (evidence_status IN ('pending', 'verified', 'missing_file', 'rejected')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX activities_account_time_idx ON activities(account_id, occurred_at DESC, activity_id);

CREATE TABLE commitments (
  commitment_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  opportunity_id TEXT REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT,
  source_activity_id TEXT REFERENCES activities(activity_id) ON DELETE RESTRICT,
  direction TEXT NOT NULL CHECK (direction IN ('customer_to_us', 'us_to_customer', 'mutual', 'unknown')),
  commitment_text TEXT NOT NULL,
  due_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'fulfilled', 'overdue', 'cancelled', 'unknown')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE risks (
  risk_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  opportunity_id TEXT REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT,
  risk_text TEXT NOT NULL,
  category TEXT,
  impact TEXT,
  likelihood TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  owner TEXT,
  mitigation_action TEXT,
  source_id TEXT REFERENCES sources(source_id) ON DELETE RESTRICT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE signals (
  signal_id TEXT PRIMARY KEY,
  account_id TEXT REFERENCES accounts(account_id) ON DELETE RESTRICT,
  signal_type TEXT NOT NULL CHECK (signal_type IN ('overdue_action', 'stale_account', 'commitment_due', 'missing_critical_field', 'resource_deadline')),
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  trigger_json TEXT NOT NULL CHECK (json_valid(trigger_json)),
  evidence_version_hash TEXT NOT NULL CHECK (length(evidence_version_hash) = 64),
  fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  resolved_at TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE UNIQUE INDEX signals_active_fingerprint_idx ON signals(fingerprint) WHERE resolved_at IS NULL AND deleted_at IS NULL;

CREATE TABLE actions (
  action_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  opportunity_id TEXT REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT,
  source_activity_id TEXT REFERENCES activities(activity_id) ON DELETE RESTRICT,
  source_signal_id TEXT REFERENCES signals(signal_id) ON DELETE RESTRICT,
  source_task_id TEXT,
  action_text TEXT NOT NULL,
  owner TEXT,
  due_at TEXT,
  priority TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  origin TEXT NOT NULL CHECK (origin IN ('manual', 'accepted_suggestion', 'imported', 'workflow')),
  completion_evidence TEXT,
  completed_at TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX actions_account_due_idx ON actions(account_id, due_at, action_id) WHERE deleted_at IS NULL;

CREATE TABLE resource_requests (
  request_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  opportunity_id TEXT REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT,
  action_id TEXT REFERENCES actions(action_id) ON DELETE RESTRICT,
  salesperson_id TEXT,
  requested_at TEXT NOT NULL,
  resource_type TEXT,
  request_summary TEXT NOT NULL,
  business_reason TEXT,
  deadline TEXT,
  owner TEXT,
  status TEXT,
  decision TEXT,
  decision_reason TEXT,
  approval_receipt_id TEXT,
  decided_at TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX resource_requests_account_idx ON resource_requests(account_id, request_id);

CREATE TABLE sales_assets (
  asset_id TEXT PRIMARY KEY,
  asset_type TEXT NOT NULL,
  title TEXT NOT NULL,
  scope TEXT NOT NULL,
  account_id TEXT REFERENCES accounts(account_id) ON DELETE RESTRICT,
  opportunity_id TEXT REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT,
  audience_role TEXT,
  sales_stage TEXT,
  use_case TEXT,
  owner TEXT,
  status TEXT,
  authorization_status TEXT,
  deidentification_status TEXT,
  source_path TEXT,
  source_sha256 TEXT CHECK (source_sha256 IS NULL OR length(source_sha256) = 64),
  source_status TEXT NOT NULL DEFAULT 'pending' CHECK (source_status IN ('verified', 'pending', 'missing_file', 'rejected')),
  legacy_evidence_refs TEXT,
  last_validated_at TEXT,
  next_review_at TEXT,
  usage_feedback TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE evidence_refs (
  evidence_ref_id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  field_name TEXT,
  source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT,
  locator_json TEXT NOT NULL CHECK (json_valid(locator_json)),
  claim_kind TEXT NOT NULL CHECK (claim_kind IN ('fact', 'analysis', 'hypothesis', 'unknown')),
  verification_status TEXT NOT NULL CHECK (verification_status IN ('pending', 'verified', 'rejected', 'superseded')),
  note TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  superseded_at TEXT,
  deleted_at TEXT
) STRICT;

CREATE INDEX evidence_refs_entity_idx ON evidence_refs(entity_type, entity_id, evidence_ref_id);

CREATE TABLE action_suggestions (
  suggestion_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  signal_id TEXT REFERENCES signals(signal_id) ON DELETE RESTRICT,
  suggestion_text TEXT NOT NULL,
  suggested_owner TEXT,
  suggested_due_at TEXT,
  model_id TEXT,
  model_parameters_json TEXT CHECK (model_parameters_json IS NULL OR json_valid(model_parameters_json)),
  status TEXT NOT NULL CHECK (status IN ('proposed', 'accepted', 'edited_and_accepted', 'ignored', 'expired')),
  user_feedback TEXT,
  accepted_action_id TEXT REFERENCES actions(action_id) ON DELETE RESTRICT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE plays (
  play_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  applicable_scenarios TEXT,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE play_versions (
  play_version_id TEXT PRIMARY KEY,
  play_id TEXT NOT NULL REFERENCES plays(play_id) ON DELETE RESTRICT,
  version_label TEXT NOT NULL,
  definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
  definition_sha256 TEXT NOT NULL CHECK (length(definition_sha256) = 64),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(play_id, version_label)
) STRICT;

CREATE TABLE play_runs (
  play_run_id TEXT PRIMARY KEY,
  play_version_id TEXT NOT NULL REFERENCES play_versions(play_version_id) ON DELETE RESTRICT,
  account_id TEXT REFERENCES accounts(account_id) ON DELETE RESTRICT,
  project_id TEXT,
  input_summary TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE task_links (
  task_link_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  account_id TEXT REFERENCES accounts(account_id) ON DELETE RESTRICT,
  opportunity_id TEXT REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT,
  play_run_id TEXT REFERENCES play_runs(play_run_id) ON DELETE RESTRICT,
  project_id TEXT,
  relation_type TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY,
  relative_path TEXT NOT NULL,
  artifact_type TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  task_id TEXT,
  account_id TEXT REFERENCES accounts(account_id) ON DELETE RESTRICT,
  opportunity_id TEXT REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT,
  status TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE write_receipts (
  intent_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  logical_tool TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
  status TEXT NOT NULL CHECK (status IN ('committed', 'reverted')),
  result_json TEXT NOT NULL CHECK (json_valid(result_json)),
  committed_at TEXT NOT NULL
) STRICT;

CREATE TABLE import_batches (
  batch_id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
  mode TEXT NOT NULL CHECK (mode IN ('preflight', 'staging')),
  status TEXT NOT NULL CHECK (status IN ('scanned', 'staged', 'blocked', 'failed', 'activated', 'completed')),
  source_files_json TEXT NOT NULL CHECK (json_valid(source_files_json)),
  source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 64),
  total_rows INTEGER NOT NULL CHECK (total_rows >= 0),
  imported_rows INTEGER NOT NULL CHECK (imported_rows >= 0),
  skipped_rows INTEGER NOT NULL CHECK (skipped_rows >= 0),
  quarantined_rows INTEGER NOT NULL CHECK (quarantined_rows >= 0),
  failed_rows INTEGER NOT NULL CHECK (failed_rows >= 0),
  cutover_ready INTEGER NOT NULL CHECK (cutover_ready IN (0, 1)),
  approved_task_id TEXT,
  report_sha256 TEXT CHECK (report_sha256 IS NULL OR length(report_sha256) = 64),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE import_rows (
  import_row_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES import_batches(batch_id) ON DELETE RESTRICT,
  source_name TEXT NOT NULL,
  row_number INTEGER NOT NULL CHECK (row_number >= 2),
  row_sha256 TEXT NOT NULL CHECK (length(row_sha256) = 64),
  entity_type TEXT,
  entity_id TEXT,
  result TEXT NOT NULL CHECK (result IN ('imported', 'skipped_duplicate', 'quarantined', 'failed')),
  error_code TEXT,
  error_message TEXT,
  raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
  created_at TEXT NOT NULL,
  UNIQUE(batch_id, source_name, row_number)
) STRICT;

CREATE INDEX import_rows_batch_result_idx ON import_rows(batch_id, result, source_name, row_number);
