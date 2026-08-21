CREATE TABLE bid_schema_migrations (
  version INTEGER PRIMARY KEY CHECK (version >= 1),
  name TEXT NOT NULL,
  script_sha256 TEXT NOT NULL CHECK (length(script_sha256) = 64),
  applied_at TEXT NOT NULL,
  application_version TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result = 'applied')
) STRICT;

CREATE TABLE bid_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE bid_projects (
  bid_id TEXT PRIMARY KEY,
  account_id TEXT,
  opportunity_id TEXT,
  workspace_project_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 500),
  buyer TEXT,
  tender_number TEXT,
  lot_name TEXT,
  owner TEXT,
  deadline_at TEXT,
  budget_minor INTEGER CHECK (budget_minor IS NULL OR budget_minor >= 0),
  currency TEXT CHECK (currency IS NULL OR length(currency) = 3),
  status TEXT NOT NULL CHECK (status IN (
    'draft','interpreting','decision_pending','planning','drafting','checking',
    'delivery_pending','delivered','closed','no_bid','cancelled'
  )),
  current_stage TEXT NOT NULL CHECK (current_stage IN (
    'intake','interpretation','decision','planning','drafting','checking','delivery','retrospective'
  )),
  go_no_go TEXT NOT NULL DEFAULT 'pending' CHECK (go_no_go IN ('pending','go','no_go','conditional')),
  decision_reason TEXT,
  summary TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX bid_projects_deadline_idx ON bid_projects(deadline_at, bid_id) WHERE deleted_at IS NULL;
CREATE INDEX bid_projects_account_idx ON bid_projects(account_id, bid_id) WHERE deleted_at IS NULL;
CREATE INDEX bid_projects_workspace_idx ON bid_projects(workspace_project_id, bid_id) WHERE deleted_at IS NULL;

CREATE TABLE bid_documents (
  document_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  role TEXT NOT NULL CHECK (role IN ('tender','addendum','template','reference','company_material','draft','final','receipt')),
  display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 500),
  relative_path TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  byte_size INTEGER NOT NULL CHECK (byte_size BETWEEN 1 AND 33554432),
  media_type TEXT,
  source_status TEXT NOT NULL CHECK (source_status IN ('pending','verified','superseded','rejected')),
  extracted_text_path TEXT,
  page_count INTEGER CHECK (page_count IS NULL OR page_count BETWEEN 1 AND 100000),
  document_version INTEGER NOT NULL DEFAULT 1 CHECK (document_version >= 1),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(bid_id, relative_path, document_version)
) STRICT;

CREATE INDEX bid_documents_bid_idx ON bid_documents(bid_id, role, document_id) WHERE deleted_at IS NULL;

CREATE TABLE bid_milestones (
  milestone_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  milestone_type TEXT NOT NULL,
  title TEXT NOT NULL,
  due_at TEXT,
  owner TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending','in_progress','completed','cancelled','blocked')),
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX bid_milestones_due_idx ON bid_milestones(bid_id, due_at, milestone_id) WHERE deleted_at IS NULL;

CREATE TABLE bid_requirements (
  requirement_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  category TEXT NOT NULL CHECK (category IN ('qualification','technical','commercial','scoring','format','submission','contract','other')),
  mandatory INTEGER NOT NULL DEFAULT 0 CHECK (mandatory IN (0, 1)),
  score_points INTEGER CHECK (score_points IS NULL OR score_points >= 0),
  title TEXT NOT NULL,
  requirement_text TEXT NOT NULL,
  evidence_locator_json TEXT NOT NULL CHECK (json_valid(evidence_locator_json)),
  verification_status TEXT NOT NULL CHECK (verification_status IN ('pending','verified','rejected','superseded')),
  response_status TEXT NOT NULL DEFAULT 'unaddressed' CHECK (response_status IN ('unaddressed','planned','drafted','compliant','deviation','not_applicable')),
  owner TEXT,
  due_at TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX bid_requirements_bid_idx ON bid_requirements(bid_id, mandatory DESC, category, requirement_id) WHERE deleted_at IS NULL;

CREATE TABLE bid_response_matrix (
  response_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  requirement_id TEXT NOT NULL REFERENCES bid_requirements(requirement_id) ON DELETE RESTRICT,
  section_id TEXT,
  response_strategy TEXT,
  material_need TEXT,
  material_status TEXT NOT NULL DEFAULT 'missing' CHECK (material_status IN ('missing','requested','available','verified','not_applicable')),
  owner TEXT,
  due_at TEXT,
  deviation TEXT,
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','in_progress','ready','blocked','not_applicable')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(bid_id, requirement_id)
) STRICT;

CREATE TABLE bid_facts (
  fact_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  category TEXT NOT NULL,
  field_name TEXT NOT NULL,
  value_text TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
  verification_status TEXT NOT NULL CHECK (verification_status IN ('pending','verified','rejected','superseded')),
  affected_sections_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(affected_sections_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(bid_id, category, field_name)
) STRICT;

CREATE TABLE bid_sections (
  section_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  parent_section_id TEXT REFERENCES bid_sections(section_id) ON DELETE RESTRICT,
  order_index INTEGER NOT NULL CHECK (order_index >= 1),
  level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
  title TEXT NOT NULL,
  objective TEXT,
  owner TEXT,
  content_markdown TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','drafting','review','approved','blocked','not_applicable')),
  input_sha256 TEXT CHECK (input_sha256 IS NULL OR length(input_sha256) = 64),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(bid_id, order_index)
) STRICT;

CREATE TABLE bid_checks (
  check_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  rule_id TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  category TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('info','low','medium','high','critical')),
  status TEXT NOT NULL CHECK (status IN ('open','resolved','accepted_risk','false_positive','not_applicable')),
  finding TEXT NOT NULL,
  recommendation TEXT,
  requirement_id TEXT REFERENCES bid_requirements(requirement_id) ON DELETE RESTRICT,
  section_id TEXT REFERENCES bid_sections(section_id) ON DELETE RESTRICT,
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
  input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
  resolved_by TEXT,
  resolved_at TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE INDEX bid_checks_open_idx ON bid_checks(bid_id, severity, check_id) WHERE deleted_at IS NULL AND status='open';

CREATE TABLE bid_risks (
  risk_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  category TEXT NOT NULL,
  risk_text TEXT NOT NULL,
  impact TEXT NOT NULL CHECK (impact IN ('low','medium','high','critical')),
  likelihood TEXT NOT NULL CHECK (likelihood IN ('low','medium','high')),
  status TEXT NOT NULL CHECK (status IN ('open','mitigating','resolved','accepted','cancelled')),
  owner TEXT,
  mitigation_action TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE bid_decisions (
  decision_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  decision_type TEXT NOT NULL CHECK (decision_type IN ('go_no_go','outline','fact_baseline','delivery','risk_acceptance','other')),
  decision TEXT NOT NULL,
  rationale TEXT,
  approved_by TEXT NOT NULL,
  approval_task_id TEXT,
  payload_sha256 TEXT CHECK (payload_sha256 IS NULL OR length(payload_sha256) = 64),
  decided_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
) STRICT;

CREATE TABLE bid_artifacts (
  artifact_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  artifact_type TEXT NOT NULL CHECK (artifact_type IN ('draft_docx','final_docx','preview_pdf','matrix','check_report','package_manifest','other')),
  relative_path TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  byte_size INTEGER NOT NULL CHECK (byte_size >= 1),
  task_id TEXT,
  intent_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('draft','review','approved','ready','superseded','rejected')),
  qa_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(qa_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(bid_id, relative_path)
) STRICT;

CREATE TABLE bid_outcomes (
  outcome_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  result TEXT NOT NULL CHECK (result IN ('won','lost','cancelled','void','pending')),
  amount_minor INTEGER CHECK (amount_minor IS NULL OR amount_minor >= 0),
  currency TEXT CHECK (currency IS NULL OR length(currency) = 3),
  reason TEXT,
  competitor_notes TEXT,
  lessons TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
  decided_at TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(bid_id)
) STRICT;

CREATE TABLE bid_events (
  event_id TEXT PRIMARY KEY,
  bid_id TEXT NOT NULL REFERENCES bid_projects(bid_id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL,
  title TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail_json)),
  actor TEXT NOT NULL,
  task_id TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE INDEX bid_events_timeline_idx ON bid_events(bid_id, created_at DESC, event_id DESC);

CREATE TABLE bid_write_receipts (
  intent_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  logical_tool TEXT NOT NULL CHECK (logical_tool = 'bid.write'),
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
  result_json TEXT NOT NULL CHECK (json_valid(result_json)),
  committed_at TEXT NOT NULL
) STRICT;
