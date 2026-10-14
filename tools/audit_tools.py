#!/usr/bin/env python3
"""Tools for the Production Readiness System Audit.

Commands:
  init       create a run directory with a unique RUN_ID and capture the start baseline
  runs       list existing runs under audit-results/ with version, phase and closure status
  baseline   capture project state or compare it with an earlier state (including side effects)
  workspace  create and check an isolated working copy outside the project
  run        execute a command in the working copy and save the full log as evidence
  stop       stop an active run command, such as a service started for E2E tests
  cleanup    remove a working copy; only with a marker, no overlap with the original, without following links
  envkeys    show keys and value shapes of .env files without printing values
  validate   check audit.json against the audit-core/2 core data contract
  checkpoint create or verify the immutable interim checkpoint
  selftest   run positive and negative controls of these tools
  example    print a minimal valid audit.json

Standard library only, no network access. The project is only read. Writes go to
results, evidence and working copy directories. In the working copy, `workspace`
creates a local Git repository with a snapshot commit using plumbing commands,
without hooks or a signature, because it is not a commit of user work and must
have no side effects. The checks cover form and consistency - not the truth,
strength of evidence or completeness of an audit.
"""
from __future__ import annotations

import argparse
import codecs
import copy
import fnmatch
import hashlib
import json
import locale
import os
import posixpath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from stat import S_ISLNK
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import unquote

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

TOOL_VERSION = '3.0.0'
PROMPT_VERSION = '5.0'
SCHEMA_VERSION = 'audit-core/2'
DEFAULT_RESULTS_DIRNAME = 'audit-results'
WORKSPACE_MARKER = '.audit-workspace.json'
PACKAGE_DIR = Path(__file__).resolve().parent.parent

LOCKFILE_NAMES = frozenset({
    'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lock', 'bun.lockb',
    'deno.lock', 'poetry.lock', 'uv.lock', 'Pipfile.lock', 'pdm.lock', 'Cargo.lock', 'go.sum',
    'Gemfile.lock', 'composer.lock', 'packages.lock.json', 'gradle.lockfile', 'pubspec.lock', 'mix.lock',
    'flake.lock', 'Package.resolved', 'Podfile.lock',
})
# Large dependency or build trees: only summarized when tracking side effects.
DEPENDENCY_DIRS = frozenset({'node_modules', '.venv', 'venv', 'bower_components', '.pnpm-store', 'target',
                             '.gradle'})
# Filesystem mode only (no Git ignore rules available).
FS_EXCLUDED_ANYWHERE = frozenset({'.git', '.hg', '.svn', 'node_modules', '.venv', 'venv', '__pycache__',
                                  '.mypy_cache', '.pytest_cache', '.ruff_cache', '.tox', '.nox', '.parcel-cache',
                                  '.turbo', 'bower_components', '.pnpm-store'})
FS_EXCLUDED_TOP_LEVEL = frozenset({'dist', 'build', 'target', 'coverage', '.next', '.nuxt', '.svelte-kit', '.cache',
                                   '.gradle', '.terraform', '.idea', '.vs'})
# Credential files: withheld from the workspace unless tracked in Git or explicitly included.
CREDENTIAL_FILE_PATTERNS = (
    '.env', '.env.*', '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore', 'id_rsa*', 'id_ed25519*',
    'id_ecdsa*', '*.kdbx', 'credentials*.json', 'service-account*.json', '.npmrc', '.pypirc', '.netrc',
    '.git-credentials', '.htpasswd', '.pgpass', '.my.cnf', 'kubeconfig*', '*.tfstate', '*.tfstate.*', '*.ovpn',
    '.dockercfg', 'auth.json',
)
SECRET_HINT_PATTERNS = ('*secret*', '*credential*', '*.tfvars')
SECRET_FILE_ALLOWED = frozenset({'.env.example', '.env.sample', '.env.template', '.env.dist', '.env.defaults'})
SENSITIVE_ENV_NAME = re.compile(
    r'(SECRET|TOKEN|PASSW|PASSPHRASE|PASSCODE|CREDENTIAL|PRIVATE|API[_-]?KEY|ACCESS[_-]?KEY|[._-]KEY$|_KEY_|^KEY$|'
    r'(?:ENCRYPTION|SIGNING|MASTER|HMAC|SESSION|JWT|CIPHER|LICENSE|CLIENT)[_.-]?KEY|COOKIE|_PAT$|(?:^|[._-])PIN$|'
    r'_SK$|^SK_|_PWD$|(?:^|[._-])PASS$|DSN$|CONNECTION_STRING|WEBHOOK|DATABASE_URL|_DB_URL$|_URI$|REDIS_URL|AMQP_URL)',
    re.IGNORECASE,
)
# Variables that point at real accounts, clusters or databases; not passed to audit runs by default.
STRIPPED_ENV_PREFIXES = (
    'AWS_', 'AZURE_', 'ARM_', 'GOOGLE_', 'GCLOUD_', 'CLOUDSDK_', 'GCP_', 'KUBE', 'DOCKER_HOST', 'DOCKER_CONTEXT',
    'DOCKER_CERT_PATH', 'DOCKER_TLS', 'VAULT_', 'TF_VAR_', 'HEROKU_', 'NETLIFY_', 'VERCEL_', 'FLY_', 'RAILWAY_',
    'SUPABASE_', 'FIREBASE_', 'SENTRY_', 'STRIPE_', 'TWILIO_', 'SENDGRID_', 'OPENAI_', 'ANTHROPIC_', 'MISTRAL_',
    'COHERE_', 'GROQ_', 'HF_', 'HUGGING', 'PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASS', 'PGSERVICE',
    'MYSQL_', 'MONGO', 'REDIS_', 'DATABASE_', 'NPM_TOKEN', 'NODE_AUTH_TOKEN', 'GITHUB_TOKEN', 'GH_TOKEN',
    'GITLAB_', 'CI_JOB_TOKEN',
)
NEVER_STRIPPED_ENV = frozenset({'PATH', 'PATHEXT', 'SYSTEMROOT', 'COMSPEC', 'TEMP', 'TMP', 'HOME', 'USERPROFILE',
                                'APPDATA', 'LOCALAPPDATA', 'PWD', 'SHELL', 'LANG'})
# Environment only: do not broaden the assignment scanner's name grammar.
SENSITIVE_ENV_ONLY_NAME = re.compile(r'(?:^|[._-])AUTH$|^GIT_CONFIG_(?:PARAMETERS|COUNT|KEY_\d+|VALUE_\d+)$'
                                     r'|^DOCKER_AUTH_CONFIG$|^BUNDLE_\w*__',  # Bundler: BUNDLE_<HOST>=user:pass
                                     re.IGNORECASE)
ENV_SWITCH_VALUES = frozenset({'0', '1', 'true', 'false', 'yes', 'no', 'on', 'off'})
# Count and key names of GIT_CONFIG_* are stripped together with their values but are no secrets themselves.
GIT_CONFIG_STRUCTURE_NAME = re.compile(r'^GIT_CONFIG_(?:COUNT|KEY_\d+)$', re.IGNORECASE)
PROFILE_POINTER_ENV = frozenset({'DOCKER_CONFIG', 'NPM_CONFIG_USERCONFIG', 'NPM_CONFIG_GLOBALCONFIG', 'CARGO_HOME',
                                'GRADLE_USER_HOME', 'PIP_CONFIG_FILE', 'UV_CONFIG_FILE', 'POETRY_CONFIG_DIR',
                                'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM', 'GIT_ASKPASS', 'SSH_ASKPASS',
                                'SSH_AUTH_SOCK', 'GNUPGHOME', 'NETRC', 'CURL_HOME', 'GH_CONFIG_DIR', 'REGISTRY_AUTH_FILE',
                                'COMPOSER_HOME', 'DBUS_SESSION_BUS_ADDRESS'})
JSON_AUTH_VALUE = re.compile(r'"auth"\s*:\s*"[A-Za-z0-9+/=]{12,}"')  # docker config.json style
TOKEN_PATTERNS = (
    ('GITHUB_TOKEN', r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b'),
    ('GITHUB_PAT', r'\bgithub_pat_[A-Za-z0-9_]{22,}\b'),
    ('STRIPE_LIVE_KEY', r'\b(?:sk|rk)_live_[A-Za-z0-9]{10,}\b'),
    ('ANTHROPIC_KEY', r'\bsk-ant-[A-Za-z0-9_-]{20,}\b'),
    ('OPENAI_STYLE_KEY', r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b'),
    ('AWS_ACCESS_KEY', r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    ('SLACK_TOKEN', r'\bxox[abposr]-[A-Za-z0-9-]{10,}\b'),
    ('GOOGLE_API_KEY', r'\bAIza[0-9A-Za-z_-]{35}\b'),
    ('JWT', r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'),
)
URL_CREDENTIALS = (r'(?P<prefix>\b[a-zA-Z][a-zA-Z0-9+.-]{0,30}://[^/\s:@\'"]{0,256}:)'
                   r'(?P<secret>[^@\s/\'"]{1,256})(?=@)')
PRIVATE_KEY_BEGIN = re.compile(rb'-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----')  # PEM and PGP
PRIVATE_KEY_END = re.compile(rb'-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----')
PRIVATE_KEY_MATERIAL = re.compile(rb'-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]{0,200}?[A-Za-z0-9+/]{40,}')
# A name with an assignment, as config dumps, debug output or `docker compose config` print it. The name starts outside
# paths and words (so `tests/test_token.py::test_x` or `api_key.ts:12:` stay intact), `==` is a comparison and `=>` an
# assignment (PHP, Ruby). A `:` without a blank or quote after it (`tight`) and a blank before `:` (`gap`) count only in
# context, see assignment_in_context. A digit, `:` or `/` after a tight `:` never counts (`app.py:12`, `host.test:8443`,
# `https://`). A YAML tag (`!!str`) before the value is skipped. Bounded name parts keep long lines linear.
ASSIGNMENT_NAME = re.compile(
    r'(?<![A-Za-z0-9_./\\])(?P<name>[A-Za-z0-9_][A-Za-z0-9_-]{0,63}(?P<dotted>(?:\.[A-Za-z0-9_-]{1,63}){1,8})?)'
    r'(?:\\?["\'])?(?P<gap>[ \t]*)(?P<sep>=>|=(?![=>])|:(?=[ \t"\']|\\")|(?P<tight>:(?![\d\s:/\\])))'
    r'[ \t]*(?:!!?[A-Za-z0-9_]+[ \t]+)?')
# Indentation, quotes and list or object starts before a name at the beginning of a line.
LINE_LEAD = re.compile(r'[ \t"\'{\[,-]*')
# The value after a sensitive name: quoted (also triple-quoted or escaped as in JSON inside a string), the first line of
# a YAML block scalar, or unquoted up to the next blank, so passwords with `$ ( ) # *` are masked whole.
ASSIGNED_VALUE = re.compile(
    r'"{3}(?P<tdq>[^"\r\n]{4,})"{3}|\'{3}(?P<tsq>[^\'\r\n]{4,})\'{3}'
    r'|[|>][-+]?[0-9]?[ \t]*\r?\n[ \t]+(?P<block>\S[^\r\n]{3,})'
    r'|\\"(?P<edq>(?:[^"\\\r\n]|\\\\(?:\\\\|\\"|[^"\\\r\n])){4,})\\"'  # escaped JSON with its own escapes
    r'|"(?P<dq>[^"\r\n]{4,})"|\'(?P<sq>[^\'\r\n]{4,})\'|(?P<uvalue>[^\s"\'`]{4,})')
ASSIGNED_VALUE_GROUPS = ('tdq', 'tsq', 'block', 'edq', 'dq', 'sq', 'uvalue')
BRACKET = re.compile(r'\x1b\[|[()\[\]{}]')  # an ANSI colour code is no bracket
BRACKET_PAIRS = {')': '(', ']': '[', '}': '{'}
# An Authorization header, as `curl -v`, HTTP client debug logs or .http files print it; the scheme stays readable.
AUTH_HEADER = re.compile(r'(?i)(?<![A-Za-z0-9])(?P<prefix>authorization["\']?[ \t]*[:=][ \t]*["\']?'
                         r'(?:(?:bearer|basic|token|negotiate|apikey|key)[ \t]+)?)(?P<value>[^\s"\'`,;]{4,})')
# Values after a sensitive name that are no secret: literals, type names, keywords and status words of log lines.
NON_SECRET_VALUES = frozenset({
    'true', 'false', 'null', 'none', 'nil', 'yes', 'no', 'required', 'optional', 'undefined', 'string', 'str',
    'bytes', 'number', 'boolean', 'bool', 'object', 'secretstr', 'done', 'passed', 'failed', 'expired', 'invalid',
    'valid', 'missing', 'present', 'refreshed', 'revoked', 'rotated', 'unset', 'found', 'created', 'deleted',
    'updated', 'generated', 'accepted', 'rejected', 'denied', 'granted', 'incorrect', 'success', 'error', 'pending',
    'enabled', 'disabled', 'delivered', 'received', 'skipped', 'redacted', 'hidden', 'masked', 'empty', 'unknown',
    'bearer', 'basic', 'await', 'lambda', 'function'})
# Kept because they reveal no secret and are evidence: placeholders and variable references, code (calls, subscripts,
# attributes of known objects, type names), plain URLs under connection names (DATABASE_URL, *_URI) and paths, unless
# a URL or path part looks generated or the name stands for a password.
PLACEHOLDER_VALUE = re.compile(r'\*+|x{3,}|X{3,}|\.{3,}|%[A-Za-z_][A-Za-z0-9_]*%|<(?:[a-z][a-z0-9 _.-]*|[A-Z][A-Z0-9 _.-]*)>'
                               r'|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Z_][A-Z0-9_]*|\$?\{\{.*\}\}')
CODE_VALUE = re.compile(r'(?:(?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*|[a-z_][A-Za-z0-9_]*)'
                        r'(?:\((?![0-9])[\w.,"\'\[\]() *=+/-]*|\[(?:["\'A-Za-z_][\w.,"\'\[\] ]*)?)'
                        r'|(?:self|cls|this|settings|config|conf|cfg|env|options|opts|args|params|props|request|req'
                        r'|ctx|context|app|user|os|process)(?:\.[A-Za-z_][A-Za-z0-9_]*)+[),;]*'
                        r'|[A-Z][a-z]+(?:[A-Z][a-z]+)*\[[A-Za-z_][A-Za-z0-9_\[\], .|]*\]')
PLAIN_URL_VALUE = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://[^@\s?#;=]*')
LOCAL_PATH_VALUE = re.compile(r'(?:[A-Za-z]:)?[/\\][a-z0-9_.\-/\\]*')
# Names that make any URL value a secret; names of file locations; names of passwords (numbers and paths are masked).
STRONG_SECRET_NAME = re.compile(r'SECRET|TOKEN|PASSW|PASSPHRASE|PASSCODE|CREDENTIAL|PRIVATE|KEY|COOKIE|WEBHOOK|DSN'
                                r'|_PAT$|_SK$|^SK_|_PWD$|(?:^|[._-])PASS$|(?:^|[._-])PIN$', re.IGNORECASE)
PATH_NAME = re.compile(r'(?:FILE|PATH|DIR|FOLDER|LOCATION|MOUNT)S?$', re.IGNORECASE)
FILE_PATH_VALUE = re.compile(r'(?:[A-Za-z]:)?[\w.~-]*(?:[/\\][\w.~-]+)+[/\\]?|[\w.~-]+\.[A-Za-z0-9]{1,5}')
PASSWORD_NAME = re.compile(r'(?:PASSW(?:OR)?D|PASSPHRASE|PASSCODE|PWD|(?:^|[._-])PASS|(?:^|[._-])PIN)$', re.IGNORECASE)
# Names that describe a secret instead of holding one (`password_policy=strict`, `token_type=Bearer`, `secretName`) and
# keys that are identifiers (`routing_key`, `cacheKey`). Header names stay out: `TOKEN_HEADER` may hold a header value.
DESCRIPTIVE_NAME = re.compile(
    r'(?:[._-](?i:type|source|policy|backend|provider|mode|kind|format|algorithm|alg|hasher|field|name|length|ttl'
    r'|lifetime|status|count|version|scanning|enabled|strategy)|(?<=[a-z])(?:Type|Source|Policy|Backend|Provider'
    r'|Mode|Kind|Format|Algorithm|Hasher|Field|Name|Length|Ttl|TTL|Lifetime|Status|Count|Version|Enabled'
    r'|Strategy)|(?i:routing|cache|sort|partition|primary|foreign|idempotency|object|lookup|dedup|group)'
    r'[._-]?(?i:key))$')
ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$')
CODE_EXTENSIONS = frozenset({'.py', '.pyi', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.mts', '.cts', '.go', '.rs',
                             '.java', '.kt', '.kts', '.cs', '.rb', '.php', '.swift', '.scala', '.vue', '.svelte',
                             '.astro', '.dart', '.ex', '.exs', '.sh', '.ps1'})
TOOLING_FILE_PATTERNS = ('conftest.py', 'pytest.ini', 'tox.ini', 'tsconfig*.json', 'jsconfig.json', 'package.json',
                         'pyproject.toml', 'setup.cfg', 'jest.config.*', 'vitest.config.*', 'playwright.config.*',
                         '.eslintrc*', 'eslint.config.*', 'babel.config.*')
# Inert forms recommended for audit tests and reports; tooling patterns do not apply to them.
INERT_SUFFIXES = frozenset({'.txt', '.patch', '.diff', '.log', '.md', '.zip'})

APPLICABILITY = {'APPLICABLE', 'NOT_APPLICABLE', 'UNKNOWN'}
EXECUTION = {'PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'SKIPPED'}
PURPOSE = {'ACCEPTANCE_REGRESSION', 'BUG_REPRODUCTION', 'CHARACTERIZATION', 'HARNESS_CONTROL', 'INVESTIGATION'}
METHOD = {'STATIC', 'UNIT', 'CONTRACT', 'INTEGRATION', 'E2E', 'PRODUCTION_LIKE', 'MANUAL'}
BASIS = {'OBSERVED', 'INFERRED', 'ASSUMED', 'UNKNOWN'}
CLAIM_ASSESSMENT = {'SUPPORTED', 'VIOLATED', 'INCONCLUSIVE', 'NOT_TESTED'}
FINDING_ASSESSMENT = {'CONFIRMED', 'UNCONFIRMED', 'DISPROVEN'}
FINDING_TYPE = {'DEFECT', 'MISSING_IMPLEMENTATION', 'UNMET_PROMISE', 'FALSE_CLAIM', 'OPERABILITY_GAP',
                'MISSING_EVIDENCE', 'TEST_QUALITY', 'CLEANUP'}
CAUSE = {'CONFIRMED', 'INFERRED', 'UNKNOWN'}
FINDING_STATE = {'OPEN', 'CANDIDATE_FIXED', 'VERIFIED_FIXED', 'ACCEPTED_RISK', 'SUPERSEDED'}
FRESHNESS = {'CURRENT', 'STALE', 'UNKNOWN'}
TASK_TYPE = {'IMPLEMENT', 'INVESTIGATE', 'VERIFY', 'HUMAN'}
TIER = {'T1', 'T2', 'T3'}
FULFILLMENT = {'DEMONSTRATED', 'VIOLATED', 'NOT_DEMONSTRATED'}
GATE_STATUS = {'MET', 'VIOLATED', 'NOT_DEMONSTRATED'}
VISION_STATUS = {'EVIDENCED', 'PARTIAL', 'CONFLICTED'}
TEST_VALUE = {'CARRYING', 'WEAK', 'MISLEADING', 'REDUNDANT', 'ORPHANED', 'DISABLED_OR_FLAKY'}
DOC_VERDICT = {'TRUE', 'FALSE', 'OUTDATED', 'UNVERIFIABLE'}
AUDIT_STATUS = {'COMPLETE_FOR_DEFINED_SCOPE', 'PARTIAL', 'BLOCKED'}
RELEASE_STATUS = {'NO_GO', 'NOT_DEMONSTRATED', 'GO_FOR_DEFINED_SCOPE'}
SEVERITY = {'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'}
PRIORITY = {'P0', 'P1', 'P2', 'P3'}
CONFIDENCE = {'HIGH', 'MEDIUM', 'LOW'}
RUN_TYPE = {'INITIAL', 'REAUDIT', 'PROMPT_UPGRADE'}
DEPTH = {'INVENTORIED', 'STATIC', 'EXECUTED'}
BINDING = {'BINDING', 'CLAIMED', 'DERIVED', 'ASSUMED', 'OPEN_DECISION', 'EXCLUDED', 'ROADMAP'}
SIZE_PROFILE = {'S', 'M', 'L'}
BOOL = {True, False}
DOMAINS = [chr(code) for code in range(ord('A'), ord('S') + 1)]
FINDING_CATEGORY = (set(DOMAINS) - {'H'}) | {'SECURITY'}
RUN_LOG_METHODS = {'UNIT', 'CONTRACT', 'INTEGRATION', 'PRODUCTION_LIKE'}
END_TO_END_METHODS = {'CONTRACT', 'INTEGRATION', 'E2E', 'PRODUCTION_LIKE', 'MANUAL'}
UI_EVIDENCE_KINDS = {'UI', 'SCREENSHOT', 'TRACE', 'VIDEO', 'HAR'}
REVIEW_METHOD = {'READ_CODE', 'RAN_TESTS', 'COMMENT_ONLY'}
REVIEW_OUTCOME = {'UPHELD', 'LIMITED', 'DISPUTED', 'UNAVAILABLE'}
SECURITY_STATUS = {'PENDING', 'IN_PROGRESS', 'DONE', 'BLOCKED', 'NOT_APPLICABLE', 'NOT_PERFORMED'}
SECURITY_RELEVANCE = {'IDENTITY', 'ACCESS_RULES', 'EXTERNAL_INPUT', 'SECRETS_AND_KEYS', 'PERSONAL_DATA',
                      'OUTBOUND_CALLS', 'BUILD_AND_DEPENDENCIES', 'AI_TOOL_USE', 'EXECUTED_CONTENT'}

TOP_LEVEL_REQUIRED = [
    'schema_version', 'prompt_version', 'run_id', 'run_type', 'previous_run_ids', 'baseline', 'scope', 'sources',
    'vision', 'objectives', 'invariants', 'environments', 'components', 'requirements', 'journeys',
    'domain_matrix', 'claims', 'checks', 'evidence', 'findings', 'tasks', 'release_gates', 'audit_status',
    'release_status', 'residual_risks', 'next_actions', 'security_review', 'security_scope',
]
ID_REGISTERS = ['sources', 'objectives', 'invariants', 'environments', 'components', 'requirements', 'journeys',
                'claims', 'checks', 'evidence', 'findings', 'tasks', 'release_gates', 'security_scope']
OPTIONAL_ID_REGISTERS = ['test_assessments', 'doc_claims']
# field -> (allowed values, nullable)
REGISTER_SPEC: dict[str, dict[str, Any]] = {
    'security_scope': {'required': ['ref', 'component_ids', 'journey_ids', 'relevance', 'reason', 'check_ids'],
                       'enums': {}},
    'sources': {'required': ['ref', 'authority'], 'enums': {}},
    'objectives': {'required': ['statement', 'source_ids', 'fulfillment', 'claim_ids'],
                   'enums': {'fulfillment': (FULFILLMENT, False)}},
    'invariants': {'required': ['statement', 'source_ids'], 'enums': {}},
    'environments': {'required': ['kind', 'authorized', 'authorization_basis', 'used'],
                     'enums': {'authorized': (BOOL, True), 'used': (BOOL, False)}},
    'components': {'required': ['ref', 'depth'], 'enums': {'depth': (DEPTH, False)}},
    'requirements': {'required': ['statement', 'binding', 'source_ids', 'check_ids', 'gap'],
                     'enums': {'binding': (BINDING, False)}},
    'journeys': {'required': ['name', 'tier', 'objective_ids', 'check_ids', 'status', 'gap'],
                 'enums': {'tier': (TIER, False), 'status': (CLAIM_ASSESSMENT, False)}},
    'claims': {'required': ['statement', 'basis', 'assessment', 'evidence_ids', 'counter_evidence_ids'],
               'enums': {'basis': (BASIS, False), 'assessment': (CLAIM_ASSESSMENT, False)}},
    'checks': {'required': ['title', 'purpose', 'method', 'required_for_audit', 'required_for_release',
                            'required_reason', 'execution', 'exit_code', 'claim_ids', 'evidence_ids',
                            'catch_up_plan'],
               'enums': {'purpose': (PURPOSE, False), 'execution': (EXECUTION, False),
                         'required_for_audit': (BOOL, True), 'required_for_release': (BOOL, True)}},
    'evidence': {'required': ['kind', 'captured_at_utc', 'freshness', 'limits'],
                 'enums': {'freshness': (FRESHNESS, False)}},
    'findings': {'required': ['title', 'type', 'category', 'assessment', 'state', 'severity', 'priority', 'confidence',
                              'release_blocker', 'cause_status', 'claim_ids', 'evidence_ids', 'code_refs',
                              'task_ids', 'file'],
                 'enums': {'type': (FINDING_TYPE, False), 'assessment': (FINDING_ASSESSMENT, False),
                           'state': (FINDING_STATE, False), 'severity': (SEVERITY, True),
                           'priority': (PRIORITY, True), 'confidence': (CONFIDENCE, True),
                           'release_blocker': (BOOL, True), 'cause_status': (CAUSE, True)}},
    'tasks': {'required': ['type', 'title', 'finding_ids', 'depends_on', 'wave', 'file'],
              'enums': {'type': (TASK_TYPE, False)}},
    'release_gates': {'required': ['statement', 'status', 'claim_ids', 'finding_ids'],
                      'enums': {'status': (GATE_STATUS, False)}},
    'test_assessments': {'required': ['ref', 'class', 'reason'], 'enums': {'class': (TEST_VALUE, False)}},
    'doc_claims': {'required': ['source', 'statement', 'verdict', 'reason'],
                   'enums': {'verdict': (DOC_VERDICT, False)}},
}
REF_TARGETS = {
    'source_ids': 'sources', 'objective_ids': 'objectives', 'invariant_ids': 'invariants',
    'environment_ids': 'environments', 'component_ids': 'components', 'requirement_ids': 'requirements',
    'journey_ids': 'journeys', 'claim_ids': 'claims', 'check_ids': 'checks', 'evidence_ids': 'evidence',
    'counter_evidence_ids': 'evidence', 'finding_ids': 'findings', 'task_ids': 'tasks', 'depends_on': 'tasks',
    'gate_ids': 'release_gates', 'doc_claim_ids': 'doc_claims', 'countercheck_evidence_ids': 'evidence',
    'decision_source_ids': 'sources',
}


class ToolError(Exception):
    """Error with a message meant for the command line."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_new(path: Path, data: Any) -> None:
    """Write JSON to a new file; existing evidence is never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, 'x', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    except FileExistsError as exc:
        raise ToolError(f'File already exists and will not be overwritten: {path}') from exc


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None and value is not False


def id_list(value: Any) -> list[str]:
    """Only the string IDs of a reference list; malformed entries are reported elsewhere."""
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def rel_posix(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def is_within(path: Path, root: Path) -> bool:
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def is_excluded(rel: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        prefix = pattern.rstrip('/')
        if rel == prefix or rel.startswith(prefix + '/') or fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


def has_glob_magic(pattern: str) -> bool:
    return any(char in pattern for char in '*?[')


def is_credential_file(rel: str) -> bool:
    name = rel.rsplit('/', 1)[-1].lower()
    if name in SECRET_FILE_ALLOWED:
        return False
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in CREDENTIAL_FILE_PATTERNS)


def looks_secret(rel: str) -> bool:
    name = rel.rsplit('/', 1)[-1].lower()
    if name in SECRET_FILE_ALLOWED:
        return False
    return is_credential_file(rel) or any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_HINT_PATTERNS)


def find_workspace_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / WORKSPACE_MARKER).is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- snapshots

def git_available() -> bool:
    return shutil.which('git') is not None


def run_git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> bytes:
    proc = subprocess.run(['git', '--no-optional-locks', *args], cwd=str(cwd), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        message = proc.stderr.decode('utf-8', 'replace').strip()
        raise ToolError(f'git {" ".join(args)} failed: {message}')
    return proc.stdout


def find_git_marker(project: Path) -> Path | None:
    for candidate in (project, *project.parents):
        if (candidate / '.git').exists():
            return candidate
    return None


def split_z(raw: bytes) -> list[str]:
    return [item for item in raw.decode('utf-8', 'surrogateescape').split('\0') if item]


def walk_files(root: Path, prefix: str, anywhere: frozenset[str], top_level: frozenset[str],
               excluded: list[str] | None = None) -> list[str]:
    """Files below root (relative, with prefix); symlinked directories count as entries."""
    result: list[str] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        depth_zero = current_path == root
        kept = []
        for name in dirs:
            rel = (current_path / name).relative_to(root).as_posix()
            if name in anywhere or (depth_zero and name in top_level):
                if excluded is not None:
                    excluded.append(prefix + rel)
                continue
            if (current_path / name).is_symlink():
                result.append(prefix + rel)
                continue
            kept.append(name)
        dirs[:] = kept
        result.extend(prefix + (current_path / name).relative_to(root).as_posix() for name in files)
    return result


def collect_git(root: Path, prefix: str, depth: int) -> list[tuple[str, bool]]:
    """(path, tracked) for tracked and untracked, not ignored files; nested repositories file by file."""
    tracked = set(split_z(run_git(['ls-files', '-z', '--cached'], root)))
    others = split_z(run_git(['ls-files', '-z', '--others', '--exclude-standard'], root))
    result: list[tuple[str, bool]] = []
    for rel in sorted(tracked | set(others)):
        path = root / rel
        name = prefix + rel.rstrip('/')
        if path.is_dir() and not path.is_symlink():
            result.extend(expand_nested(path, name + '/', depth + 1, rel in tracked))
        else:
            result.append((name, rel in tracked))
    return result


def expand_nested(path: Path, prefix: str, depth: int, tracked_parent: bool) -> list[tuple[str, bool]]:
    """Submodules and untracked nested repositories: list their own tracked/untracked files."""
    if depth > 6:
        return []
    if (path / '.git').exists() and git_available():
        try:
            nested = collect_git(path, prefix, depth)
        except ToolError:
            nested = None
        if nested is not None:
            # Files of an untracked nested repository are untracked from the project's point of view.
            return [(rel, tracked and tracked_parent) for rel, tracked in nested]
    files = walk_files(path, prefix, FS_EXCLUDED_ANYWHERE, frozenset())
    return [(rel, False) for rel in files]


def digest_entry(path: Path) -> str:
    if path.is_symlink():
        return 'symlink:' + sha256_bytes(os.readlink(path).encode('utf-8', 'surrogateescape'))
    if path.is_dir():
        return 'dir'
    return sha256_file(path)


def stat_signature(path: Path) -> str:
    """Content-free state of an ignored entry: size and modification time, summarized for big trees."""
    try:
        if path.is_symlink():
            return 'symlink:' + sha256_bytes(os.readlink(path).encode('utf-8', 'surrogateescape'))
        if path.is_file():
            stat = path.stat()
            return f'file:{stat.st_size}:{stat.st_mtime_ns}'
        if path.is_dir():
            if path.name in DEPENDENCY_DIRS:
                stat = path.stat()
                return f'dir-summary:{sum(1 for _ in os.scandir(path))}:{stat.st_mtime_ns}'
            digest, count = hashlib.sha256(), 0
            for current, dirs, files in os.walk(path):
                current_path = Path(current)
                dirs[:] = sorted(d for d in dirs if d not in DEPENDENCY_DIRS and not (current_path / d).is_symlink())
                for name in sorted(files):
                    file_path = current_path / name
                    try:
                        stat = file_path.lstat()
                    except OSError:
                        continue
                    line = f'{file_path.relative_to(path).as_posix()}\t{stat.st_size}\t{stat.st_mtime_ns}\n'
                    digest.update(line.encode('utf-8', 'surrogateescape'))
                    count += 1
            return f'dir:{count}:{digest.hexdigest()}'
    except OSError as exc:
        return f'error:{exc.__class__.__name__}'
    return 'missing'


def git_state(project: Path) -> dict[str, str]:
    """Hashes of Git metadata that a careless command could change in the original repository."""
    def digest(args: list[str]) -> str:
        try:
            return sha256_bytes(run_git(args, project))
        except ToolError:
            return 'unavailable'
    hooks_dir = Path(run_git(['rev-parse', '--git-path', 'hooks'], project).decode().strip())
    if not hooks_dir.is_absolute():
        hooks_dir = project / hooks_dir
    hooks = []
    if hooks_dir.is_dir():
        hooks = [f'{entry.name}\t{sha256_file(entry)}' for entry in sorted(hooks_dir.iterdir()) if entry.is_file()]
    return {
        'refs': digest(['for-each-ref', '--format=%(refname) %(objectname)']),
        'head': digest(['rev-parse', '--symbolic-full-name', 'HEAD']) + ':' + digest(['rev-parse', '-q', '--verify',
                                                                                     'HEAD']),
        'stash': digest(['stash', 'list']),
        'worktrees': digest(['worktree', 'list', '--porcelain']),
        'config_local': digest(['config', '--local', '--list']),
        'index': digest(['ls-files', '-s', '-z']),
        'hooks': sha256_bytes('\n'.join(hooks).encode('utf-8')),
    }


def default_excludes(project: Path, results_dir: Path | None) -> list[str]:
    patterns = []
    results = results_dir if results_dir is not None else project / DEFAULT_RESULTS_DIRNAME
    for candidate in (results, PACKAGE_DIR):
        rel = rel_posix(candidate, project)
        if rel and rel != '.':
            patterns.append(rel)
    return patterns


def take_snapshot(project: Path, *, results_dir: Path | None = None, extra_excludes: Iterable[str] = (),
                  mode: str = 'auto', fs_default_excludes: bool = True,
                  with_side_effects: bool = True) -> tuple[dict[str, Any], dict[str, bool | None]]:
    """Fingerprint of the product files plus content-free state of ignored files and Git metadata."""
    project = project.resolve()
    if not project.is_dir():
        raise ToolError(f'Project directory not found: {project}')
    excludes = default_excludes(project, results_dir) + [p.replace('\\', '/') for p in extra_excludes]
    notes: list[str] = []
    if mode == 'auto':
        marker = find_git_marker(project)
        mode = 'git' if marker and git_available() else 'filesystem'
        if marker and mode == 'filesystem':
            notes.append('Git repository detected, but git is unavailable: filesystem mode.')
        if mode == 'git' and marker != project:
            # The project lives inside a repository above it; that repository's ignore rules decide what counts.
            ignored = subprocess.run(['git', '--no-optional-locks', 'check-ignore', '-q', '.'], cwd=str(project),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if ignored:
                mode = 'filesystem'
                notes.append(f'The project is inside a parent Git repository ({marker}) that ignores it: '
                             'filesystem mode.')
            else:
                notes.append(f'Git repository above the project ({marker}): its ignore rules determine the '
                             'captured state; check file_count against the expected project size.')
    excluded_dirs: list[str] = []
    git_info: dict[str, Any] | None = None
    if mode == 'git':
        entries = collect_git(project, '', 0)
        try:
            head: str | None = run_git(['rev-parse', '-q', '--verify', 'HEAD'], project).decode().strip() or None
        except ToolError:
            head = None
        changed = split_z(run_git(['diff', '--name-only', '-z', 'HEAD'] if head
                                  else ['diff', '--name-only', '-z', '--cached'], project))
        git_info = {
            'head': head,
            'branch': run_git(['rev-parse', '--abbrev-ref', 'HEAD'], project).decode().strip() if head else None,
            'toplevel': run_git(['rev-parse', '--show-toplevel'], project).decode().strip(),
            'uncommitted_changes': len(changed), 'uncommitted_paths': changed[:200],
        }
    else:
        anywhere = FS_EXCLUDED_ANYWHERE if fs_default_excludes else frozenset({'.git', '.hg', '.svn'})
        top_level = FS_EXCLUDED_TOP_LEVEL if fs_default_excludes else frozenset()
        entries = [(rel, None) for rel in walk_files(project, '', anywhere, top_level, excluded_dirs)]
    files: dict[str, str] = {}
    tracked: dict[str, bool | None] = {}
    deleted: list[str] = []
    total = 0
    for rel, is_tracked in entries:
        if is_excluded(rel, excludes):
            continue
        path = project / rel
        if not path.exists() and not path.is_symlink():
            deleted.append(rel)
            continue
        files[rel] = digest_entry(path)
        tracked[rel] = is_tracked
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    if git_info is not None:
        git_info['untracked_files'] = sum(1 for value in tracked.values() if value is False)
        git_info['deleted_tracked_files'] = deleted[:200]
    if not files:
        notes.append('No project files captured: check project root, mode and exclusions before the run '
                     'builds on this baseline.')
    fingerprint = sha256_bytes(''.join(f'{rel}\t{files[rel]}\n' for rel in sorted(files)).encode('utf-8',
                                                                                                'surrogateescape'))
    side_state: dict[str, str] = {}
    meta_state: dict[str, str] | None = None
    if with_side_effects:
        if mode == 'git':
            ignored = split_z(run_git(['ls-files', '-z', '--others', '--ignored', '--exclude-standard', '--directory'],
                                      project))
            meta_state = git_state(project)
        else:
            ignored = excluded_dirs
        for raw in ignored:
            rel = raw.rstrip('/')
            if rel and not is_excluded(rel, excludes):
                side_state[rel] = stat_signature(project / rel)
    snapshot = {
        'tool': 'audit_tools.py', 'tool_version': TOOL_VERSION, 'captured_at_utc': utc_now(),
        'project_root': str(project), 'mode': mode, 'git': git_info, 'excludes': excludes,
        'excluded_dirs': excluded_dirs[:200], 'notes': notes, 'file_count': len(files), 'total_bytes': total,
        'content_fingerprint': fingerprint,
        'lockfiles': [{'path': rel, 'sha256': files[rel]} for rel in sorted(files)
                      if rel.rsplit('/', 1)[-1] in LOCKFILE_NAMES],
        'files': files,
        'ignored_state': side_state if with_side_effects else None,
        'git_state': meta_state,
    }
    return snapshot, tracked


def snapshot_summary(snap: dict[str, Any]) -> dict[str, Any]:
    git_info = snap.get('git') or {}
    return {
        'mode': snap['mode'], 'project_root': snap['project_root'], 'content_fingerprint': snap['content_fingerprint'],
        'file_count': snap['file_count'], 'total_bytes': snap['total_bytes'], 'head': git_info.get('head'),
        'uncommitted_changes': git_info.get('uncommitted_changes'), 'untracked_files': git_info.get('untracked_files'),
        'ignored_entries_tracked': len(snap.get('ignored_state') or {}), 'excluded_dirs': snap.get('excluded_dirs', []),
        'lockfiles': [item['path'] for item in snap['lockfiles']], 'excludes': snap['excludes'],
        'notes': snap.get('notes', []),
    }


def compare_snapshots(old: dict[str, Any], new: dict[str, Any], limit: int = 50) -> dict[str, Any]:
    before, after = old.get('files', {}), new.get('files', {})
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
    locks_old = {item['path']: item['sha256'] for item in old.get('lockfiles', [])}
    locks_new = {item['path']: item['sha256'] for item in new.get('lockfiles', [])}
    result: dict[str, Any] = {
        'identical': not (added or removed or changed),
        'content_fingerprint_old': old.get('content_fingerprint'),
        'content_fingerprint_new': new.get('content_fingerprint'),
        'head_old': (old.get('git') or {}).get('head'), 'head_new': (new.get('git') or {}).get('head'),
        'counts': {'added': len(added), 'removed': len(removed), 'changed': len(changed)},
        'added': added[:limit], 'removed': removed[:limit], 'changed': changed[:limit],
        'lockfiles_changed': sorted(k for k in set(locks_old) | set(locks_new) if locks_old.get(k) != locks_new.get(k)),
        'excludes_differ': old.get('excludes') != new.get('excludes'),
        'mode_differs': old.get('mode') != new.get('mode'),
    }
    side: dict[str, Any] = {}
    old_ignored, new_ignored = old.get('ignored_state'), new.get('ignored_state')
    if old_ignored is None or new_ignored is None:
        side['ignored_files'] = 'not comparable (older baseline without side effect data)'
    else:
        ignored_changes = sorted(k for k in set(old_ignored) | set(new_ignored) if old_ignored.get(k) != new_ignored.get(k))
        if ignored_changes:
            side['ignored_files'] = ignored_changes[:limit]
    old_git, new_git = old.get('git_state'), new.get('git_state')
    if old_git is not None or new_git is not None:
        old_git, new_git = old_git or {}, new_git or {}
        git_changes = sorted(k for k in set(old_git) | set(new_git) if old_git.get(k) != new_git.get(k))
        if git_changes:
            side['git_metadata'] = git_changes
    result['side_effects'] = side
    result['clean'] = result['identical'] and not side
    return result


def snapshot_from_args(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, bool | None]]:
    results = Path(args.results_dir) if getattr(args, 'results_dir', None) else None
    return take_snapshot(Path(args.project), results_dir=results, extra_excludes=args.exclude, mode=args.mode,
                         fs_default_excludes=not args.no_default_excludes)


def inherit_reference_options(args: argparse.Namespace, reference: dict[str, Any]) -> None:
    """Compare like with like: without own options, use the excludes and mode of the reference (init --exclude)."""
    if not args.exclude:
        results = Path(args.results_dir).resolve() if args.results_dir else None
        defaults = default_excludes(Path(args.project).resolve(), results)
        args.exclude = [e for e in reference.get('excludes', []) if isinstance(e, str) and e not in defaults]
    if args.mode == 'auto' and reference.get('mode') in ('git', 'filesystem'):
        args.mode = reference['mode']


def cmd_baseline(args: argparse.Namespace) -> int:
    old = None
    if args.compare:
        try:
            old = read_json(Path(args.compare))
        except (OSError, ValueError) as exc:
            raise ToolError(f'Comparison file unreadable: {exc}') from exc
        if not isinstance(old, dict):
            raise ToolError('Comparison file contains no baseline state.')
        inherit_reference_options(args, old)
    snap, _ = snapshot_from_args(args)
    summary = snapshot_summary(snap)
    if args.out:
        write_json_new(Path(args.out), snap)
        summary['out'] = str(Path(args.out).resolve())
    exit_code = 0
    if old is not None:
        summary['compare'] = compare_snapshots(old, snap)
        exit_code = 0 if summary['compare']['clean'] else 1
    print_json(summary)
    return exit_code


def cmd_init(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    results = Path(args.results_dir).resolve() if args.results_dir else project / DEFAULT_RESULTS_DIRNAME
    snap, _ = take_snapshot(project, results_dir=results, extra_excludes=args.exclude, mode=args.mode,
                            fs_default_excludes=not args.no_default_excludes)
    head = (snap.get('git') or {}).get('head')
    short = head[:7] if head else ('nocommit' if snap['mode'] == 'git' else 'nogit')
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%MZ')
    run_id, counter = f'{stamp}_{short}', 2
    while (results / run_id).exists():
        run_id, counter = f'{stamp}_{short}-{counter}', counter + 1
    run_dir = results / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for sub in ('findings', 'tasks', 'evidence/baseline', 'evidence/logs', 'evidence/audit-tests', 'evidence/ui'):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    baseline_rel = 'evidence/baseline/baseline-start.json'
    write_json_new(run_dir / baseline_rel, snap)
    now = utc_now()
    write_json_new(run_dir / 'RUN-STATE.json', {
        'run_id': run_id, 'schema_version': SCHEMA_VERSION, 'prompt_version': PROMPT_VERSION, 'phase': 'P0',
        'created_at_utc': now, 'updated_at_utc': now, 'closed_at_utc': None, 'project_root': str(project),
        'baseline': {'content_fingerprint': snap['content_fingerprint'], 'head': head, 'file': baseline_rel},
        'run_type': None, 'previous_run_ids': [], 'authorization': None, 'workspace': None, 'size_profile': None,
        'checks': {'done': [], 'open': [], 'blocked': []}, 'running_resources': [], 'invalidations': [],
        'next_steps': ['Record authorizations from the start request verbatim',
                       'Check previous runs in audit-results/ and determine the run type',
                       'Record capabilities, tools and size profile'],
    })
    print_json({'run_id': run_id, 'run_dir': str(run_dir), 'baseline': str(run_dir / baseline_rel),
                **snapshot_summary(snap)})
    return 0


# --------------------------------------------------------------------------- workspace

def symlink_is_safe(link: Path, target: str, project: Path) -> bool:
    """Only relative links that stay inside the project can be recreated without reaching the original."""
    if os.path.isabs(target) or re.match(r'^[A-Za-z]:', target) or target.startswith(('\\\\', '//')):
        return False
    resolved = Path(os.path.normpath(link.parent / target))
    return is_within(resolved, project)


def scan_secret_content(path: Path) -> list[str]:
    """Names of credential patterns found in a small text file (values are never returned)."""
    try:
        if path.stat().st_size > 2_000_000:
            return []
        data = path.read_bytes()
    except OSError:
        return []
    if b'\0' in data[:8192]:
        return []
    hits = []
    if re.search(URL_CREDENTIALS.encode(), data):
        hits.append('URL_CREDENTIALS')
    if PRIVATE_KEY_BEGIN.search(data):
        hits.append('PRIVATE_KEY')
    hits.extend(name for name, pattern in TOKEN_PATTERNS if name != 'JWT' and re.search(pattern.encode(), data))
    return hits


def looks_like_placeholder(value: str) -> bool:
    """Obvious test values: a run of 10 ascending (abcdefghij, 0123456789) or identical (xxxxxxxxxx) characters,
    or the documented AWS example suffix. Random keys practically never show these."""
    if value.upper().endswith('EXAMPLE'):
        return True
    ascending = same = 1
    for prev, cur in zip(value, value[1:]):
        ascending = ascending + 1 if ord(cur) == ord(prev) + 1 else 1
        same = same + 1 if cur == prev else 1
        if ascending >= 10 or same >= 10:
            return True
    return False


def secret_pattern_hits(text: str, limit: int | None = None) -> tuple[list[str], list[str]]:
    """Credential patterns in audit output: (strong: provider keys, private keys; weak: JWT, URL credentials,
    provider-key shapes whose every match is an obvious placeholder). With `limit`, only matches ending there count:
    a window cut in the middle of a token would match where the whole text does not."""
    strong, weak = [], []
    end = len(text) if limit is None else limit
    for name, pattern in TOKEN_PATTERNS:
        values = [m.group(0) for m in re.finditer(pattern, text) if m.end() <= end]
        if name == 'JWT':
            weak.extend(['JWT'] if values else [])
        elif any(not looks_like_placeholder(v) for v in values):
            strong.append(name)
        elif values:
            weak.append(f'{name} (Placeholder?)')
    raw = text.encode('utf-8', 'replace')
    raw_end = len(text[:end].encode('utf-8', 'replace'))
    if any(m.end() <= raw_end for m in PRIVATE_KEY_MATERIAL.finditer(raw)):
        strong.append('PRIVATE_KEY')
    elif any(m.end() <= raw_end for m in PRIVATE_KEY_BEGIN.finditer(raw)):
        weak.append('PRIVATE_KEY_HEADER')
    if any(m.end() <= end and not is_placeholder_url_credential(m, text) for m in re.finditer(URL_CREDENTIALS, text)):
        weak.append('URL_CREDENTIALS')
    return strong, weak


def result_secret_hits(path: Path) -> tuple[list[str], list[str]]:
    """Bounded text scan, including UTF-16; overlap covers tokens, URL credentials and PEM prefixes."""
    strong, weak = set(), set()
    with open(path, 'rb') as handle:
        sample = handle.read(8192)
        encoding = 'utf-8'
        if sample[:2] in (b'\xff\xfe', b'\xfe\xff'):
            encoding = 'utf-16'
        elif len(sample) >= 16 and b'\0' in sample:
            even, odd = sample[::2], sample[1::2]
            if odd.count(0) / len(odd) > 0.3 and even.count(0) / len(even) < 0.05:
                encoding = 'utf-16-le'
            elif even.count(0) / len(even) > 0.3 and odd.count(0) / len(odd) < 0.05:
                encoding = 'utf-16-be'
            else:
                return [], []
        elif b'\0' in sample:
            return [], []
        decoder = codecs.getincrementaldecoder(encoding)(errors='replace')
        overlap = ''
        raw = sample
        while raw:
            text = overlap + decoder.decode(raw)
            # matches near the window end are rescanned with full context from the overlap
            found_strong, found_weak = secret_pattern_hits(text, limit=max(0, len(text) - 1024))
            strong.update(found_strong)
            weak.update(found_weak)
            overlap = text[-4096:]
            raw = handle.read(65536)
        found_strong, found_weak = secret_pattern_hits(overlap + decoder.decode(b'', final=True))
        strong.update(found_strong)
        weak.update(found_weak)
    return sorted(strong), sorted(weak - strong)


# German default-password spellings are scanner data; retain them to preserve secret detection behavior.
URL_DEFAULT_SECRETS = frozenset({'password', 'pass', 'passwd', 'pwd', 'secret', 'postgres', 'mysql', 'test', 'user',
                                 'admin', 'root', 'changeme', 'example', 'dev', 'passwort', 'kennwort', 'geheim'})
URL_MASKED_SECRET = re.compile(r'\[REDACTED.*|\*+|[xX]+|\.\.\.|\$\{[^}]*\}|<[^>]*>|\{\{.*\}\}|%[A-Z_]+%')
RESERVED_EXAMPLE_HOST = re.compile(r'(?:[a-z0-9-]+\.)*(?:example(?:\.(?:com|org|net))?|test|invalid)|host', re.IGNORECASE)


def is_placeholder_url_credential(match: re.Match[str], text: str) -> bool:
    """URL credentials that reveal nothing: masked or templated, a publicly known default password, or a reserved
    example host (RFC 2606/6761)."""
    secret = match.group('secret')
    if URL_MASKED_SECRET.fullmatch(secret) or secret.lower() in URL_DEFAULT_SECRETS:
        return True
    host = re.match(r'@([^/\s\'":?#]*)', text[match.end():match.end() + 260])
    return bool(RESERVED_EXAMPLE_HOST.fullmatch(host.group(1) if host else ''))


def assignment_in_context(text: str, match: re.Match[str], line_start: int, depth: int) -> bool:
    """A tight `:` counts at the start of a line (`password:value` in INI dumps, `spring.datasource.password:value` in
    .properties) and, after a name without dots, inside brackets (Go and Viper dumps `{Host:db Password:...}`,
    `map[password:...]`), after a blank before a capitalized name, or after a quote before a header name
    (`-H "X-Api-Key:value"`). A blank before `:` counts at the start of a line
    or before a quoted value. So `WARNING:app.token:Token expired`, pytest's `app.token:token.py:42 ...` and Spring's
    `c.a.TokenService : Issuing token` stay intact."""
    if match.group('sep') != ':':
        return True
    # beyond 64 characters of indentation a name is not at the start of its line; the bound keeps long lines linear
    at_line_start = (match.start() - line_start <= 64
                     and LINE_LEAD.fullmatch(text, line_start, match.start()) is not None)
    if match.group('tight') is not None:
        if match.group('dotted') or at_line_start:
            return at_line_start
        before = text[match.start() - 1:match.start()]
        if before in ('"', "'"):  # a header in a quoted argument: curl -H "X-Api-Key:value"
            return '-' in match.group('name')
        return (depth > 0 and before in ('{', '[', ' ', '\t', ',')
                or before in (' ', '\t') and match.group('name')[0].isupper())
    if match.group('gap') and text[match.end():match.end() + 1] not in ('"', "'", '\\'):
        return at_line_start
    return True


def unclosed_end(text: str, start: int, end: int, opened: dict[str, int]) -> int:
    """End of an unquoted value without the closing brackets that close what was opened before the name
    (`{Password:x}`, `f(password=x)`); a bracket the secret itself ends with stays masked."""
    counts = {char: text.count(char, start, end) for char in '()[]{}'}
    available = dict(opened)
    while end - start > 1 and text[end - 1] in ')]}':
        closer = text[end - 1]
        opener = '([{'[')]}'.index(closer)]
        if available[opener] <= 0 or counts[opener] >= counts[closer]:
            break
        available[opener], counts[closer], end = available[opener] - 1, counts[closer] - 1, end - 1
    return end


def sensitive_assignments(text: str) -> Iterator[tuple[str, int, int]]:
    """(name, start, end) of each secret value assigned to a sensitive name. Values that stay readable are searched like
    any other text, so `msg="login with password=..."` and `login(password=...)` are caught; masked values are skipped,
    and readable values are at most 200 characters long, which keeps long lines linear."""
    skip = line_start = scanned = 0
    opened = dict.fromkeys('([{', 0)
    for match in ASSIGNMENT_NAME.finditer(text):
        start = match.start()
        newline = text.rfind('\n', scanned, start)
        if newline >= 0:
            line_start, opened = newline + 1, dict.fromkeys('([{', 0)
        # brackets opened on this line before the name, in order: a stray closer never cancels a later opener
        for bracket in BRACKET.finditer(text, max(scanned, line_start), start):
            char = bracket.group()
            if char in opened:
                opened[char] += 1
            elif char in BRACKET_PAIRS:
                opened[BRACKET_PAIRS[char]] = max(0, opened[BRACKET_PAIRS[char]] - 1)
        depth = opened['['] + opened['{']
        scanned, name = start, match.group('name')
        if (start < skip or not SENSITIVE_ENV_NAME.search(name)
                or not assignment_in_context(text, match, line_start, depth)):
            continue
        value = ASSIGNED_VALUE.match(text, match.end())
        if value:
            group = next(g for g in ASSIGNED_VALUE_GROUPS if value.group(g) is not None)
            end = value.end(group)
            if group == 'uvalue':
                end = unclosed_end(text, value.start(group), end, opened)
            if is_sensitive_assignment(name, text[value.start(group):end]):
                skip = end
                yield name, value.start(group), end


def looks_generated(value: str) -> bool:
    """A part that looks like a generated token: two digit-to-letter changes (k9x2m7q4, 7f3c2a9d), one in a mixed-case
    part (Kx9mPq), or a long mixed-case run."""
    for part in re.split(r'[/\\:.?#=&@,;+~()\[\]{}"\' -]', value):
        changes = len(re.findall(r'[0-9][A-Za-z]', part)) if len(part) >= 6 else 0
        mixed = bool(re.search('[a-z]', part) and re.search('[A-Z]', part))
        if changes >= 2 or changes and mixed or len(part) >= 16 and re.search('[a-z][A-Z]', part):
            return True
    return False


def is_sensitive_assignment(name: str, value: str) -> bool:
    """Whether a value assigned to a sensitive name is masked. Kept as evidence that reveals no secret: status words,
    placeholders, code, numbers, connection URLs, file paths and values of descriptive names, unless they look
    generated or are long; numbers and paths after password names are masked."""
    bare = value.rstrip(',;')
    if len(bare) > 200:
        return True
    if '[REDACTED' in bare or bare.lower().rstrip('.') in NON_SECRET_VALUES:
        return False
    if looks_generated(bare):
        return True
    if bare.isdigit():
        return bool(PASSWORD_NAME.search(name))
    if DESCRIPTIVE_NAME.search(name):
        return False
    if ((bare.startswith(('process.env', 'os.environ', 'env(', 'getenv(')) and '=' not in bare)
            or PLACEHOLDER_VALUE.fullmatch(bare) or CODE_VALUE.fullmatch(bare)):
        return False
    if PLAIN_URL_VALUE.fullmatch(bare):
        return bool(STRONG_SECRET_NAME.search(name))
    if PATH_NAME.search(name) and FILE_PATH_VALUE.fullmatch(bare):
        return False
    if LOCAL_PATH_VALUE.fullmatch(bare):
        return bool(PASSWORD_NAME.search(name))
    return True


def is_secret_header_value(value: str) -> bool:
    return ('[REDACTED' not in value and value.lower() not in NON_SECRET_VALUES
            and not PLACEHOLDER_VALUE.fullmatch(value) and not CODE_VALUE.fullmatch(value))


def mask_sensitive_assignments(text: str) -> tuple[str, int]:
    """Replace values assigned to sensitive names (DB_PASSWORD=..., "api_key": "...") and Authorization header values
    by a marker."""
    count = 0

    def header(match: re.Match[str]) -> str:
        nonlocal count
        if not is_secret_header_value(match.group('value')):
            return match.group(0)
        count += 1
        return match.group('prefix') + '[REDACTED:Authorization]'
    text = AUTH_HEADER.sub(header, text)
    parts, last = [], 0
    for name, start, end in sensitive_assignments(text):
        parts += [text[last:start], f'[REDACTED:{name}]']
        last, count = end, count + 1
    return ''.join(parts) + text[last:], count


def worth_recording(value: str) -> bool:
    """Values that `run` masks wherever they appear: long and mixed enough not to hit words, names or paths."""
    classes = sum(bool(re.search(p, value)) for p in ('[a-z]', '[A-Z]', '[0-9]', '[^A-Za-z0-9]'))
    return len(value) >= 8 and classes >= 2 and not re.fullmatch(r'[A-Za-z]+(?:[-_.][A-Za-z]+)*', value)


def recordable(name: str, value: str) -> bool:
    """Passphrases count under password names; URLs and paths only with a generated part."""
    if (PLAIN_URL_VALUE.fullmatch(value) or LOCAL_PATH_VALUE.fullmatch(value)) and not looks_generated(value):
        return False
    return worth_recording(value) or bool(PASSWORD_NAME.search(name)) and len(value) >= 12


def assignment_secrets(text: str) -> list[tuple[str, str]]:
    """(name, value) of literal secret values assigned to sensitive names or sent as Authorization header, safe to mask
    wherever they appear."""
    found = [(name, text[start:end]) for name, start, end in sensitive_assignments(text)
             if recordable(name, text[start:end])]
    return found + [('Authorization', m.group('value')) for m in AUTH_HEADER.finditer(text)
                    if worth_recording(m.group('value')) and is_secret_header_value(m.group('value'))]


def read_text_file(path: Path, limit: int = 2_000_000) -> str | None:
    """Text of a small file (UTF-8 or UTF-16 with BOM); None for binary, large or unreadable files."""
    try:
        if path.stat().st_size > limit:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16', 'replace')
    if b'\0' in data[:8192]:
        return None
    return data.decode('utf-8', 'replace')


def original_path_needles(project: Path) -> list[re.Pattern[str]]:
    """Spellings of the project's absolute path that would let code in a copy reach the original."""
    root = str(project.resolve()).rstrip('\\/')
    variants = {root, root.replace('\\', '/'), root.replace('\\', '\\\\')}
    drive = re.match(r'^([A-Za-z]):[\\/](.*)$', root)
    if drive:  # Git Bash and WSL spellings of a Windows path
        rest = drive.group(2).replace('\\', '/')
        variants |= {f'/{drive.group(1)}/{rest}', f'/mnt/{drive.group(1)}/{rest}'}
    variants |= {v.replace(' ', '%20') for v in variants if ' ' in v}
    flags = re.IGNORECASE if os.name == 'nt' else 0  # Unicode-aware, so uppercase umlauts match lowercase umlauts
    # The path has to end at the match: <root>-old or <root>.bak is a sibling, not the original.
    return [re.compile(re.escape(v) + r'(?![\w.-])', flags) for v in sorted(variants) if len(v) > 3]


def mentions_original_path(path: Path, needles: list[re.Pattern[str]]) -> bool:
    text = read_text_file(path)
    return text is not None and any(needle.search(text) for needle in needles)


def make_git_snapshot(dest: Path) -> dict[str, Any]:
    if not git_available():
        return {'created': False, 'reason': 'git unavailable'}
    identity = {'GIT_AUTHOR_NAME': 'audit-workspace', 'GIT_AUTHOR_EMAIL': 'audit-workspace@localhost',
                'GIT_COMMITTER_NAME': 'audit-workspace', 'GIT_COMMITTER_EMAIL': 'audit-workspace@localhost'}
    try:
        run_git(['init', '-q'], dest)
        for key, value in (('core.autocrlf', 'false'), ('core.safecrlf', 'false')):
            run_git(['config', key, value], dest)
        exclude = dest / '.git' / 'info' / 'exclude'
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude, 'a', encoding='utf-8') as handle:
            handle.write(f'\n{WORKSPACE_MARKER}\n.audit-home/\n')
        run_git(['add', '-A'], dest)
        tree = run_git(['write-tree'], dest).decode().strip()
        commit = run_git(['commit-tree', tree, '--no-gpg-sign', '-m', 'audit workspace snapshot'], dest,
                         env={**os.environ, **identity}).decode().strip()
        run_git(['update-ref', 'HEAD', commit], dest)
    except ToolError as exc:
        return {'created': False, 'reason': str(exc)}
    return {'created': True, 'head': commit,
            'hint': 'Side effects of own runs: git status or git diff in the working copy.'}


def copy_file_or_link(src: Path, dst: Path, rel: str, project: Path, notes: list[dict[str, str]],
                      unsafe: list[dict[str, str]]) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        target = os.readlink(src)
        if not symlink_is_safe(src, target, project):
            unsafe.append({'path': rel, 'target': target})
            return False
        try:
            os.symlink(target, dst, target_is_directory=src.is_dir())
        except OSError:
            if not src.is_file():
                unsafe.append({'path': rel, 'target': target, 'reason': 'Directory link cannot be created'})
                return False
            shutil.copy2(src, dst)
            notes.append({'path': rel, 'note': 'Symlink created as a copy'})
        return True
    shutil.copy2(src, dst)
    return True


def cmd_workspace(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    dest = Path(args.dest).resolve()
    if is_within(dest, project):
        raise ToolError('The working copy must be outside the project tree so project tools do '
                        'not discover it.')
    if is_within(project, dest):
        raise ToolError('The project must not be inside the destination directory.')
    if dest.exists() and any(dest.iterdir()):
        raise ToolError(f'Destination directory is not empty: {dest}')
    expected: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None
    if args.expect_baseline:
        try:
            reference = read_json(Path(args.expect_baseline))
        except (OSError, ValueError) as exc:
            raise ToolError(f'Start baseline unreadable: {exc}') from exc
        if not isinstance(reference, dict):
            raise ToolError('The start baseline contains no baseline state.')
        inherit_reference_options(args, reference)  # init --exclude applies to the copy as well
    snap, tracked = snapshot_from_args(args)
    if reference is not None:
        diff = compare_snapshots(reference, snap)
        expected = {'file': str(Path(args.expect_baseline).resolve()), 'matches': diff['identical'],
                    'changed': diff['changed'][:20], 'added': diff['added'][:20], 'removed': diff['removed'][:20],
                    'excludes_differ': diff['excludes_differ']}
    dest.mkdir(parents=True, exist_ok=True)
    withheld: set[str] = set()
    errors, notes, unsafe, copied, included = [], [], [], [], []
    for rel, digest in snap['files'].items():
        if is_credential_file(rel) and tracked.get(rel) is not True:
            withheld.add(rel)
            continue
        if digest == 'dir':
            (dest / rel).mkdir(parents=True, exist_ok=True)
            continue
        try:
            if copy_file_or_link(project / rel, dest / rel, rel, project, notes, unsafe):
                copied.append(rel)
        except OSError as exc:
            errors.append({'path': rel, 'error': str(exc)})
    for pattern in args.include or []:
        explicit = not has_glob_magic(pattern)
        for src in sorted(project.glob(pattern)):
            if not is_within(src, project):
                continue
            candidates = [src] if not src.is_dir() or src.is_symlink() else [
                Path(current) / name for current, _dirs, names in os.walk(src) for name in names]
            for path in candidates:
                rel = path.relative_to(project).as_posix()
                if rel in copied or rel in included:
                    continue
                if is_credential_file(rel) and not (explicit and path == src):
                    withheld.add(rel)
                    continue
                try:
                    if copy_file_or_link(path, dest / rel, rel, project, notes, unsafe):
                        included.append(rel)
                        withheld.discard(rel)
                except OSError as exc:
                    errors.append({'path': rel, 'error': str(exc)})
    mismatches = []
    for rel in copied:
        digest = snap['files'][rel]
        if digest.startswith('symlink:'):
            continue
        target = dest / rel
        if not target.is_file() or sha256_file(target) != digest:
            mismatches.append(rel)
    content_hits, original_refs, value_files = [], [], []
    needles = original_path_needles(project)
    for rel in sorted({*copied, *included}):
        path = dest / rel
        if path.is_file() and not path.is_symlink():
            names = scan_secret_content(path)
            if names:
                content_hits.append({'path': rel, 'patterns': names})
            file_text = read_text_file(path)
            if file_text is not None and assignment_secrets(file_text):
                value_files.append(rel)
            if mentions_original_path(path, needles):
                original_refs.append(rel)
    write_json_new(dest / WORKSPACE_MARKER, {
        'tool_version': TOOL_VERSION, 'created_at_utc': utc_now(), 'project_root': snap['project_root'],
        'baseline_fingerprint': snap['content_fingerprint'],
        # run masks the values assigned to sensitive names in these files; the marker holds paths only
        'secret_value_files': value_files[:1000],
    })
    unsafe_paths = {item['path'] for item in unsafe}
    copy_ok = not errors and not mismatches and not unsafe_paths and (expected is None or expected['matches'])
    summary = {
        'dest': str(dest), 'project_root': snap['project_root'], 'mode': snap['mode'],
        'baseline_fingerprint': snap['content_fingerprint'], 'files_copied': len(copied),
        'expected_baseline': expected,
        'matches_baseline': copy_ok and not withheld,
        'matches_baseline_except_withheld': copy_ok,
        'mismatches': mismatches[:50], 'errors': errors[:50],
        'withheld_credential_files': sorted(withheld), 'unsafe_symlinks_not_created': unsafe[:50],
        'explicitly_included': included[:200],
        'secret_like_files_copied': sorted(rel for rel in {*copied, *included} if looks_secret(rel)),
        'secret_content_hits': content_hits[:100], 'secret_value_files': value_files[:100],
        'original_path_refs': original_refs[:100],
        'excluded_dirs': snap.get('excluded_dirs', []),
        'notes': notes[:50], 'lockfiles': [item['path'] for item in snap['lockfiles']],
        'git_snapshot': {'created': False, 'reason': 'disabled'} if args.no_git_snapshot
        else make_git_snapshot(dest),
        'marker': str(dest / WORKSPACE_MARKER), 'created_at_utc': utc_now(),
    }
    hints = []
    if expected is not None and not expected['matches']:
        hints.append('The project has changed since the start baseline. Check affected evidence and citations; '
                     'if files of open findings changed, create a re-audit run (master prompt §16).')
    elif expected is None:
        hints.append('Without --expect-baseline, only the copy is verified against the current project state, not against '
                     'the start baseline of the run.')
    if withheld:
        hints.append('Credential files were not copied. Create synthetic values for tests or '
                     'deliberately include non-secret files with --include.')
    if unsafe:
        hints.append('Symlinks targeting paths outside the project were not created so writes cannot reach the '
                     'original.')
    if summary['secret_like_files_copied'] or content_hits or value_files:
        hints.append('The copy contains files with secret names or patterns. Check before each run and replace real '
                     'credentials with synthetic values; a committed secret is itself a finding.')
    if original_refs:
        hints.append('Files in the copy reference the absolute path of the original (original_path_refs). Code, scripts '
                     'or configuration would read or write in the original from the copy: check before each run '
                     'and redirect in the copy or do not run the command.')
    if snap.get('excluded_dirs'):
        hints.append('Directories were excluded in filesystem mode (excluded_dirs); include them if needed with '
                     '--include.')
    summary['hints'] = hints
    if args.record:
        write_json_new(Path(args.record), summary)
    print_json(summary)
    return 0 if summary['matches_baseline_except_withheld'] else 1


# --------------------------------------------------------------------------- run and stop

if os.name == 'nt':
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                    ctypes.POINTER(wintypes.DWORD)]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes',
                                                            'write_bytes', 'other_bytes')]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [('per_process_user_time', ctypes.c_int64), ('per_job_user_time', ctypes.c_int64),
                    ('limit_flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                    ('max_working_set', ctypes.c_size_t), ('active_process_limit', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority_class', wintypes.DWORD),
                    ('scheduling_class', wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', _BasicLimits), ('io', _IoCounters), ('process_memory_limit', ctypes.c_size_t),
                    ('job_memory_limit', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t),
                    ('peak_job_memory', ctypes.c_size_t)]

    class _Accounting(ctypes.Structure):
        _fields_ = [('total_user_time', ctypes.c_int64), ('total_kernel_time', ctypes.c_int64),
                    ('period_user_time', ctypes.c_int64), ('period_kernel_time', ctypes.c_int64),
                    ('page_faults', wintypes.DWORD), ('total_processes', wintypes.DWORD),
                    ('active_processes', wintypes.DWORD), ('terminated_processes', wintypes.DWORD)]


def pid_alive(pid: int) -> bool:
    if os.name == 'nt':
        handle = _kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = wintypes.DWORD()
        ok = _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        _kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    stat = Path(f'/proc/{pid}/stat')
    try:
        if stat.is_file() and stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
            return False
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def posix_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def posix_start_time(pid: int) -> float | None:
    """Start of a process in epoch seconds: /proc (Linux), else `ps -o etime=` (macOS, BSD); None if unknown."""
    stat = Path(f'/proc/{pid}/stat')
    try:
        if stat.is_file():
            ticks = int(stat.read_text().rsplit(')', 1)[1].split()[19])
            boot = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines()
                        if line.startswith('btime '))
            return boot + ticks / os.sysconf('SC_CLK_TCK')
        elapsed = subprocess.run(['ps', '-o', 'etime=', '-p', str(pid)], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, timeout=10).stdout.strip()
    except (OSError, ValueError, IndexError, StopIteration, subprocess.SubprocessError):
        return None
    match = re.fullmatch(r'(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)', elapsed)
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return time.time() - (((days * 24 + hours) * 60 + minutes) * 60 + seconds)


def terminate_aborted_command(info: dict[str, Any]) -> str:
    """Ends what survived a hard-killed run: 'killed', 'reused' (the PID now belongs to another process; the command
    is gone) or 'unverified' (identity or termination not confirmed: nothing is killed or cleared)."""
    try:
        start = datetime.fromisoformat(info['started_at_utc'].replace('Z', '+00:00')).timestamp()
        end = datetime.fromisoformat(info.get('command_started_before_utc',
                                             info['started_at_utc']).replace('Z', '+00:00')).timestamp()
        pid = info['pid']
        if end < start or end - start > 30:
            return 'unverified'
    except (KeyError, TypeError, ValueError, AttributeError):
        return 'unverified'
    if os.name == 'nt':
        if not pid_alive(pid):
            return 'killed'
        handle = _kernel32.OpenProcess(0x1000 | 0x0001, False, pid)
        if not handle:
            return 'unverified' if pid_alive(pid) else 'killed'
        try:
            creation, exit_time, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (creation, exit_time, kernel, user))):
                return 'unverified'
            created = ((creation.dwHighDateTime << 32) + creation.dwLowDateTime) / 10_000_000 - 11644473600
            if not start <= created <= end + 1:  # old markers have whole-second timestamps
                return 'reused'
            if not _kernel32.TerminateProcess(handle, 1):
                return 'unverified'
        finally:
            _kernel32.CloseHandle(handle)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.1)
        return 'unverified' if pid_alive(pid) else 'killed'
    try:
        leader = pid_alive(pid) and os.getpgid(pid) == pid
    except ProcessLookupError:
        leader = False
    except OSError:
        return 'unverified'
    if leader:
        created = posix_start_time(pid)
        if created is None:
            return 'unverified'  # without a start time, a reused PID cannot be told apart
        if not start - 2 <= created <= end + 2:  # btime and etime round to seconds
            return 'reused'
    elif pid_alive(pid):
        return 'reused'  # alive but no longer leading its own group
    elif not posix_group_alive(pid):
        return 'killed'
    # A PID is not handed out while a process group with that ID lives, so this group is still the command's.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return 'killed'
        except OSError:
            return 'unverified'
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and posix_group_alive(pid):
            time.sleep(0.1)
        if not posix_group_alive(pid):
            return 'killed'
    return 'unverified'

class ProcessReaper:
    """Terminates a command's whole process tree, including detached leftovers.

    Windows: a job object with KILL_ON_JOB_CLOSE; POSIX: the command's own session/process group.
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self.job = self._create_job(proc.pid) if os.name == 'nt' else None

    @staticmethod
    def _create_job(pid: int) -> Any:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.basic.limit_flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        handle = _kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        ok = bool(handle) and _kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        ok = ok and _kernel32.AssignProcessToJobObject(job, handle)
        if handle:
            _kernel32.CloseHandle(handle)
        if not ok:
            _kernel32.CloseHandle(job)
            return None
        return job

    def leftovers(self) -> int | None:
        """Processes still alive in the tree (None if unknown)."""
        if self.job is not None:
            info = _Accounting()
            if _kernel32.QueryInformationJobObject(self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                return int(info.active_processes)
            return None
        if os.name == 'nt':
            return None
        try:
            os.killpg(self.proc.pid, 0)
        except (ProcessLookupError, PermissionError):
            return 0
        return 1

    def kill_all(self) -> None:
        if self.job is not None:
            _kernel32.TerminateJobObject(self.job, 1)
            return
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.killpg(self.proc.pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.1)
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def close(self) -> None:
        if self.job is not None:
            _kernel32.CloseHandle(self.job)
            self.job = None


def workspace_secret_values(workspace_root: Path | None) -> dict[str, str]:
    """Literal values assigned to sensitive names in the copy's files that workspace recorded (re-read now)."""
    if workspace_root is None:
        return {}
    try:
        marker = read_json(workspace_root / WORKSPACE_MARKER)
    except (OSError, ValueError):
        return {}
    values: dict[str, str] = {}
    rels = marker.get('secret_value_files', []) if isinstance(marker, dict) else []
    for rel in rels if isinstance(rels, list) else []:
        target = workspace_root / str(rel)
        file_text = read_text_file(target) if is_within(target, workspace_root) else None
        for name, value in assignment_secrets(file_text or ''):
            if value not in values.values():
                values[f'{name}#{len(values) + 1}'] = value
    return values


def sensitive_env_value(value: str) -> bool:
    return bool(re.search(URL_CREDENTIALS, value) or PRIVATE_KEY_BEGIN.search(value.encode('utf-8', 'replace'))
                or JSON_AUTH_VALUE.search(value)
                or any(re.search(pattern, value) for _, pattern in TOKEN_PATTERNS))


def should_strip(name: str, value: str = '') -> bool:
    upper = name.upper()
    if sensitive_env_value(value):
        return True
    if upper in NEVER_STRIPPED_ENV:
        return False
    return (upper.startswith(STRIPPED_ENV_PREFIXES) or bool(SENSITIVE_ENV_ONLY_NAME.search(upper))
            or bool(SENSITIVE_ENV_NAME.search(upper)) and value.lower() not in ENV_SWITCH_VALUES)


def sensitive_values(env: dict[str, str], forced: Iterable[str], min_length: int = 6) -> dict[str, str]:
    forced_names = {name.upper() for name in forced}
    values = {}
    for name, value in env.items():
        upper = name.upper()
        if not value:
            continue
        if upper in forced_names:
            values[name] = value
        # The prefix list only strips: values like PGHOST=localhost must stay readable in logs.
        elif len(value) >= min_length and (sensitive_env_value(value) or upper not in NEVER_STRIPPED_ENV
                                           and not GIT_CONFIG_STRUCTURE_NAME.match(upper) and (
                SENSITIVE_ENV_ONLY_NAME.search(upper)
                or SENSITIVE_ENV_NAME.search(upper) and value.lower() not in ENV_SWITCH_VALUES | {'null', 'none'})):
            values[name] = value
    return values


class Redactor:
    """Masks secret values and credential patterns in command lines (text) and output (bytes)."""

    def __init__(self, values: dict[str, str]) -> None:
        patterns: list[tuple[str, str]] = []
        for name, value in values.items():
            marker = f'[REDACTED:{name}]'
            patterns.append((value, marker))
            # Multi-line secrets (e.g. PEM keys) appear line by line in output.
            patterns.extend((line.strip(), marker) for line in value.splitlines() if len(line.strip()) >= 16)
        patterns.sort(key=lambda item: -len(item[0]))
        self._text = patterns
        encodings = sorted({'utf-8', 'utf-16-le', 'utf-16-be', locale.getpreferredencoding(False) or 'utf-8'})
        self._bytes: list[tuple[bytes, bytes]] = []
        for value, marker in patterns:
            for encoding in encodings:
                try:
                    pair = (value.encode(encoding), marker.encode(encoding, 'replace'))
                except (UnicodeEncodeError, LookupError):
                    continue
                if pair[0] and pair not in self._bytes:
                    self._bytes.append(pair)
        self._url_text = re.compile(URL_CREDENTIALS)
        self._url_bytes = re.compile(URL_CREDENTIALS.encode())
        self._tokens_text = [(name, re.compile(pattern)) for name, pattern in TOKEN_PATTERNS]
        self._tokens_bytes = [(name.encode(), re.compile(pattern.encode())) for name, pattern in TOKEN_PATTERNS]

    def redact_text(self, value: str) -> str:
        for secret, marker in self._text:
            value = value.replace(secret, marker)
        value = self._url_text.sub(lambda m: m.group('prefix') + '[REDACTED]', value)
        for name, pattern in self._tokens_text:
            value = pattern.sub(f'[REDACTED:{name}]', value)
        return mask_sensitive_assignments(value)[0]

    def redact_bytes(self, value: bytes) -> tuple[bytes, int]:
        hits = 0
        for secret, marker in self._bytes:
            count = value.count(secret)
            if count:
                hits += count
                value = value.replace(secret, marker)
        value, count = self._url_bytes.subn(lambda m: m.group('prefix') + b'[REDACTED]', value)
        hits += count
        for name, pattern in self._tokens_bytes:
            value, count = pattern.subn(b'[REDACTED:' + name + b']', value)
            hits += count
        # surrogateescape keeps bytes of other encodings exactly as emitted
        masked, count = mask_sensitive_assignments(value.decode('utf-8', 'surrogateescape'))
        if count:
            value, hits = masked.encode('utf-8', 'surrogateescape'), hits + count
        return value, hits


def unique_stem(directory: Path, stem: str) -> str:
    candidate, counter = stem, 2
    while any((directory / f'{candidate}{suffix}').exists() for suffix in ('.log', '.meta.json', '.running.json')):
        candidate, counter = f'{stem}-{counter}', counter + 1
    return candidate


def resolve_executable(command: str, cwd: Path, env: dict[str, str]) -> str | None:
    if os.name == 'nt' and re.match(r'[A-Za-z]:(?![\\/])', command):
        raise ToolError(f'Drive-relative command {command!r}: use an absolute path or a path relative to the working copy '
                        'instead.')
    cwd = cwd.resolve()
    names = [command]
    if os.name == 'nt':
        extensions = [ext for ext in env.get('PATHEXT', '.COM;.EXE;.BAT;.CMD').split(';') if ext]
        if not any(command.lower().endswith(ext.lower()) for ext in extensions):
            names = [command + ext for ext in extensions] or [command]
    if '/' in command or '\\' in command:
        directories = [cwd]
    else:
        directories = ([cwd] if os.name == 'nt' else []) + [
            Path(entry.strip('"') if os.name == 'nt' else entry) if entry else cwd
            for entry in env.get('PATH', os.defpath).split(os.pathsep)]
    workspace = find_workspace_root(cwd)
    original = read_json(workspace / WORKSPACE_MARKER).get('project_root') if workspace else None
    for directory in directories:
        for name in names:
            candidate = Path(os.path.normpath(cwd / directory / name))
            if not candidate.is_file() or (os.name != 'nt' and not os.access(candidate, os.X_OK)):
                continue
            if original and is_within(candidate, Path(original)):
                raise ToolError(f'Command is in the original project: {candidate.resolve()}. Use a file in the working copy '
                                'or set PATH to the copy with --env.')
            return str(candidate)
    return None


def find_run_dir(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / 'RUN-STATE.json').is_file():
            return candidate
    return None


def build_run_env(args: argparse.Namespace, workspace_root: Path | None) -> tuple[dict[str, str], list[str],
                                                                                    dict[str, str]]:
    inherit = {name.upper() for name in args.inherit or []}
    env = dict(os.environ)
    stripped = sorted(name for name, value in env.items() if name.upper() not in inherit and (
        should_strip(name, value) or workspace_root is not None and args.isolated_home
        and name.upper() in PROFILE_POINTER_ENV))
    for name in stripped:
        del env[name]
    overrides: dict[str, str] = {}
    if args.ci:
        overrides['CI'] = 'true'
    if args.no_color:
        overrides.update({'NO_COLOR': '1', 'FORCE_COLOR': '0', 'TERM': 'dumb'})
    if workspace_root is not None:
        overrides['COMPOSE_PROJECT_NAME'] = 'audit-' + sha256_bytes(str(workspace_root).encode())[:8]
        if args.isolated_home:
            home = workspace_root / '.audit-home'
            for name, sub in (('HOME', ''), ('USERPROFILE', ''), ('APPDATA', 'AppData/Roaming'),
                              ('LOCALAPPDATA', 'AppData/Local'), ('XDG_CONFIG_HOME', '.config'),
                              ('XDG_CACHE_HOME', '.cache'), ('XDG_DATA_HOME', '.local/share')):
                target = home / sub if sub else home
                target.mkdir(parents=True, exist_ok=True)
                overrides[name] = str(target)
            overrides.update({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'credential.helper',
                              'GIT_CONFIG_VALUE_0': '', 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'})
    for item in args.env or []:
        if '=' not in item:
            raise ToolError(f'--env expects NAME=VALUE, received: {item!r}')
        name, value = item.split('=', 1)
        overrides[name] = value
    env.update(overrides)
    return env, stripped, overrides


def cmd_run(args: argparse.Namespace, command: list[str]) -> int:
    if not command:
        raise ToolError('No command specified. Usage: run --id ID --evidence-dir DIR --cwd WORKING_COPY -- command ...')
    if not ID_PATTERN.match(args.id):
        raise ToolError('Invalid --id (allowed: letters, digits, period, underscore, hyphen).')
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd().resolve()
    if not cwd.is_dir():
        raise ToolError(f'Working directory not found: {cwd}')
    workspace_root = find_workspace_root(cwd)
    if workspace_root is None and not args.outside_workspace:
        raise ToolError(f'{cwd} is not in a working copy (marker {WORKSPACE_MARKER} is missing). Commands run in the '
                        'working copy (--cwd). Only read-only commands may run outside it with --outside-workspace.')
    evidence_dir = Path(args.evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stem = unique_stem(evidence_dir, args.id)
    log_path, meta_path = evidence_dir / f'{stem}.log', evidence_dir / f'{stem}.meta.json'
    running_path, stop_path = evidence_dir / f'{stem}.running.json', evidence_dir / f'{stem}.stop'
    env, stripped, overrides = build_run_env(args, workspace_root)
    redactor = Redactor({**workspace_secret_values(workspace_root),
                         **sensitive_values({**os.environ, **overrides}, args.redact or []),
                         **sensitive_values(overrides, args.redact or [], min_length=4)})
    executable = None
    if args.shell:
        shell_command = command[0] if len(command) == 1 else (
            subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command))
        popen_command: Any = shell_command
        shown_command: Any = redactor.redact_text(shell_command)
    else:
        executable = resolve_executable(command[0], cwd, env)
        shown_command = [redactor.redact_text(part) for part in command]
        if executable is None:
            meta = {'id': args.id, 'command': shown_command, 'cwd': str(cwd), 'error': 'command_not_found',
                    'resolved_executable': None,
                    'started_at_utc': utc_now(), 'exit_code': None, 'tool_version': TOOL_VERSION}
            write_json_new(meta_path, meta)
            print_json({**meta, 'meta': str(meta_path)})
            return 127
        popen_command = [executable, *command[1:]]
    popen_kwargs: dict[str, Any] = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        cwd=str(cwd), env=env, shell=args.shell)
    if os.name == 'nt':
        popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs['start_new_session'] = True
    tail: deque[str] = deque(maxlen=max(args.tail, 0))
    counters = {'lines': 0, 'redactions': 0, 'non_utf8_lines': 0}
    started_at, started = utc_now(), time.monotonic()
    timed_out = stopped = orphaned_output = False
    exit_code: int | None = None
    leftovers: int | None = None
    # The log keeps the raw output bytes (only secrets masked); decoding is for display only.
    with open(log_path, 'wb') as log:
        proc = subprocess.Popen(popen_command, **popen_kwargs)
        reaper = ProcessReaper(proc)
        try:
            write_json_new(running_path, {'id': args.id, 'stem': stem, 'pid': proc.pid, 'runner_pid': os.getpid(),
                                          'started_at_utc': started_at,
                                          'command_started_before_utc': datetime.now(timezone.utc).isoformat(),
                                          'resolved_executable': redactor.redact_text(executable) if executable else None,
                                          'command': shown_command, 'cwd': str(cwd)})
        except ToolError:
            pass

        def pump() -> None:
            in_key = False
            try:
                for raw in iter(proc.stdout.readline, b''):
                    if in_key or PRIVATE_KEY_BEGIN.search(raw):
                        in_key = not PRIVATE_KEY_END.search(raw)
                        ending = b'\r\n' if raw.endswith(b'\r\n') else (b'\n' if raw.endswith(b'\n') else b'')
                        masked, hits = b'[REDACTED:PRIVATE_KEY]' + ending, 1
                    else:
                        masked, hits = redactor.redact_bytes(raw)
                    log.write(masked)
                    counters['lines'] += 1
                    counters['redactions'] += hits
                    try:
                        shown = masked.decode('utf-8')
                    except UnicodeDecodeError:
                        counters['non_utf8_lines'] += 1
                        shown = masked.decode('utf-8', 'replace')
                    tail.append(shown.rstrip('\r\n'))
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        deadline = started + args.timeout if args.timeout and args.timeout > 0 else None
        while True:
            code = proc.poll()
            if code is not None:
                exit_code = code
                break
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True
            elif stop_path.exists():
                stopped = True
            if timed_out or stopped:
                reaper.kill_all()
                try:
                    exit_code = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    exit_code = None
                break
            time.sleep(0.2)
        leftovers = reaper.leftovers()
        if leftovers:
            reaper.kill_all()
        reaper.close()
        reader.join(timeout=10)
        if reader.is_alive():
            orphaned_output = True
            reader.join(timeout=5)
    for control in (running_path, stop_path):
        try:
            control.unlink()
        except OSError:
            pass
    ended_at, duration = utc_now(), round(time.monotonic() - started, 3)
    log_sha = sha256_file(log_path)
    meta = {
        'id': args.id, 'tool': 'audit_tools.py', 'tool_version': TOOL_VERSION, 'command': shown_command,
        'shell': args.shell, 'cwd': str(cwd), 'workspace_root': str(workspace_root) if workspace_root else None,
        'resolved_executable': redactor.redact_text(executable) if executable else None,
        'outside_workspace': workspace_root is None, 'stripped_env': stripped,
        'env_overrides': {k: f'[REDACTED:{k}]' if sensitive_values(
            {k: v}, args.redact or [], min_length=1) else redactor.redact_text(v) for k, v in overrides.items()},
        'started_at_utc': started_at, 'ended_at_utc': ended_at, 'duration_s': duration,
        'exit_code': None if timed_out else exit_code, 'timed_out': timed_out, 'stopped': stopped,
        'leftover_processes_terminated': leftovers, 'background_output_holder': orphaned_output,
        'log': log_path.name, 'log_sha256': log_sha, 'log_bytes': log_path.stat().st_size,
        'log_lines': counters['lines'], 'redactions': counters['redactions'],
        'log_encoding': 'raw bytes as emitted; secrets masked', 'non_utf8_lines': counters['non_utf8_lines'],
        'python': sys.version.split()[0], 'platform': sys.platform,
    }
    write_json_new(meta_path, meta)
    summary: dict[str, Any] = {
        'id': args.id, 'exit_code': meta['exit_code'], 'timed_out': timed_out, 'stopped': stopped,
        'duration_s': duration, 'log': str(log_path), 'meta': str(meta_path), 'log_sha256': log_sha,
        'log_lines': counters['lines'], 'redactions': counters['redactions'],
        'non_utf8_lines': counters['non_utf8_lines'], 'leftover_processes_terminated': leftovers,
        'stripped_env': stripped,
    }
    run_dir = find_run_dir(evidence_dir)
    if run_dir is not None:
        summary['evidence_entry'] = {'kind': 'LOG', 'path': log_path.relative_to(run_dir).as_posix(),
                                     'sha256': log_sha, 'captured_at_utc': started_at, 'freshness': 'CURRENT',
                                     'limits': '<add limits of the claim>'}
    print_json(summary)
    if tail:
        print(f'--- last {len(tail)} lines ---')
        print('\n'.join(tail))
    if timed_out:
        return 124
    return exit_code if exit_code is not None else 1


def cmd_stop(args: argparse.Namespace) -> int:
    evidence_dir = Path(args.evidence_dir).resolve()
    targets = []
    for marker in sorted(evidence_dir.glob('*.running.json')):
        try:
            info = read_json(marker)
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and info.get('id') == args.id:
            targets.append(info)
    if not targets:
        raise ToolError(f'No active run with ID {args.id} in {evidence_dir}.')
    results, complete = [], True
    for info in targets:
        stem = info['stem']
        (evidence_dir / f'{stem}.stop').write_text(utc_now(), encoding='utf-8')
        meta_path = evidence_dir / f'{stem}.meta.json'
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline and not meta_path.exists():
            time.sleep(0.2)
        if meta_path.exists():
            time.sleep(0.2)
            meta = read_json(meta_path)
            results.append({key: meta.get(key) for key in ('id', 'exit_code', 'stopped', 'duration_s', 'log',
                                                           'log_sha256', 'leftover_processes_terminated')})
        elif (isinstance(info.get('pid'), int)
              and not (isinstance(info.get('runner_pid'), int) and pid_alive(info['runner_pid']))
              and not meta_path.exists()):
            outcome = terminate_aborted_command(info)
            if outcome == 'unverified':
                complete = False
                results.append({'stem': stem, 'pid': info['pid'], 'status': 'run without completion; process identity '
                                'not safely confirmed or termination failed; process and markers remain'})
                continue
            # No meta will follow a hard-killed runner. An unfinished log is never evidence.
            for leftover in (evidence_dir / f'{stem}.running.json', evidence_dir / f'{stem}.stop'):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            ended = 'PID reassigned in the meantime, command terminated' if outcome == 'reused' else 'Command terminated'
            results.append({'stem': stem, 'pid': info['pid'],
                            'status': f'run ended without completion ({ended}, no metadata); the log is not '
                                      'evidence - rerun the check'})
        else:
            complete = False
            results.append({'stem': stem, 'pid': info.get('pid'), 'status': 'no response; check the process'})
    print_json(results)
    return 0 if complete else 1


# --------------------------------------------------------------------------- envkeys

def classify_value(value: str) -> str:
    text = value.strip().strip('"').strip("'")
    lower = text.lower()
    if not text:
        return 'empty'
    if re.fullmatch(r'\$\{?[A-Za-z_][A-Za-z0-9_]*\}?', text):
        return 'Reference to another variable'
    if any(marker in lower for marker in ('changeme', 'change_me', 'your_', 'your-', 'example', 'placeholder', 'xxx',
                                          'todo', '<', 'dummy', 'replace')):
        return 'Placeholder'
    if re.search(URL_CREDENTIALS, text):
        return 'URL with credentials'
    if '://' in text:
        return 'URL without credentials'
    if re.match(r'(sk|pk|rk)_test_', text):
        return 'Test key'
    for name, pattern in TOKEN_PATTERNS:
        if re.search(pattern, text):
            return f'looks real ({name})'
    if lower in {'true', 'false', 'yes', 'no', 'on', 'off'} or re.fullmatch(r'-?\d+(\.\d+)?', text):
        return 'Switch or number'
    if len(text) >= 20 and re.search(r'[A-Za-z]', text) and re.search(r'\d', text):
        return 'looks real (long string)'
    return 'set'


def cmd_envkeys(args: argparse.Namespace) -> int:
    rows = []
    for name in args.files:
        path = Path(name)
        try:
            lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
        except OSError as exc:
            raise ToolError(f'File unreadable: {exc}') from exc
        # Continuation lines of a multi-line value (quoted value, PEM block) are value, never keys.
        open_quote: str | None = None
        in_pem = False
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if open_quote is not None:
                if open_quote in stripped:
                    open_quote = None
                continue
            if in_pem:
                in_pem = not PRIVATE_KEY_END.search(stripped.encode('utf-8', 'replace')) and '-----END ' not in stripped
                continue
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('-----BEGIN '):
                in_pem = '-----END ' not in stripped
                continue
            if stripped.startswith('export '):
                stripped = stripped[len('export '):].strip()
            if '=' not in stripped:
                continue
            key, value = stripped.split('=', 1)
            key, value = key.strip(), value.strip()
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', key):
                continue
            multiline = value[:1] in ('"', "'") and value.count(value[0]) == 1
            if multiline:
                open_quote = value[0]
            elif '-----BEGIN ' in value and '-----END ' not in value:
                in_pem, multiline = True, True
            rows.append({'file': str(path), 'line': number, 'key': key,
                         'shape': 'multiline value' if multiline else classify_value(value),
                         'length': None if multiline else len(value.strip('"').strip("'"))})
    print_json({'note': 'Values are never printed; only keys and value shapes.', 'keys': rows})
    return 0


# --------------------------------------------------------------------------- cleanup

def is_link_or_junction(path: Path) -> bool:
    """Symlink, or on Windows any reparse point such as a directory junction."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def remove_entry(path: Path) -> None:
    """One file or directory tree; read-only files are made writable first."""
    if path.is_dir() and not is_link_or_junction(path):
        remove_tree(path)
        return
    try:
        path.unlink()
    except OSError:
        try:
            os.chmod(path, 0o700)
            path.unlink()
        except OSError:
            pass


def is_mount_point(path: Path, root_device: int) -> bool:
    """A POSIX mount point below the copy (another device). On Windows mounted volumes are reparse points, which
    is_link_or_junction already catches."""
    if os.name == 'nt':
        return False
    try:
        return os.lstat(path).st_dev != root_device
    except OSError:
        return False


def links_and_mounts(root: Path) -> list[Path]:
    """Links, junctions and mount points below root; the walk does not enter them."""
    found, device = [], os.lstat(root).st_dev
    for current, dirs, names in os.walk(root, followlinks=False):
        directories, stop = set(dirs), set()
        for name in dirs + names:
            entry = Path(current) / name
            if is_link_or_junction(entry) or (name in directories and is_mount_point(entry, device)):
                found.append(entry)
                stop.add(name)
        dirs[:] = [d for d in dirs if d not in stop]
    return found


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Remove a work copy made by `workspace`; refuses anything else. Links inside are removed, never followed, and the
    marker goes last, so an interrupted cleanup can be repeated or measured with --dry-run."""
    dest = Path(args.dest).absolute()
    if not dest.is_dir() or is_link_or_junction(dest):
        raise ToolError(f'Not a directory or is a link: {dest}. Only real working copies are removed.')
    dest = dest.resolve()
    if dest.parent == dest or is_within(Path.home(), dest):
        raise ToolError(f'{dest} is a drive or share root or contains the user home directory; '
                        'nothing removed.')
    if not args.dry_run and is_within(Path.cwd(), dest):
        raise ToolError('The current working directory is in the copy; leave it first. Nothing removed.')
    try:
        marker = read_json(dest / WORKSPACE_MARKER)
    except (OSError, ValueError) as exc:
        raise ToolError(f'No readable marker {WORKSPACE_MARKER} in {dest}: only copies from workspace are '
                        f'removed ({exc}).') from exc
    original = marker.get('project_root') if isinstance(marker, dict) else None
    if not isinstance(original, str) or not marker.get('baseline_fingerprint'):
        raise ToolError('The marker is not from workspace (project_root or baseline_fingerprint is missing).')
    original_path, project = Path(original).resolve(), Path(args.project).resolve()
    if project != original_path:
        raise ToolError(f'The copy belongs to {original_path}, not to {project}.')
    if is_within(dest, original_path) or is_within(original_path, dest):
        raise ToolError(f'Working copy and original overlap ({original_path}); nothing removed.')
    files = size = 0
    links = links_and_mounts(dest)
    skipped = set(links)
    for current, dirs, names in os.walk(dest, followlinks=False):
        for name in names:
            entry = Path(current) / name
            if entry not in skipped:
                try:
                    info = entry.stat()
                except OSError:
                    continue
                files, size = files + 1, size + info.st_size
        dirs[:] = [d for d in dirs if Path(current) / d not in skipped]
    result = {'dest': str(dest), 'project_root': str(original_path), 'files': files, 'bytes': size,
              'links_removed_not_followed': len(links), 'dry_run': bool(args.dry_run)}
    if args.dry_run:
        print_json(result)
        return 0
    # Links, junctions and mount points first, as links: their targets (possibly the original or a shared cache) stay
    # untouched. If one cannot be removed, nothing else is deleted.
    for entry in sorted(links, key=lambda p: len(p.parts), reverse=True):
        try:  # Windows removes directory links and junctions with rmdir, POSIX links with unlink
            if os.name == 'nt' and entry.is_dir():
                os.rmdir(entry)
            else:
                entry.unlink()
        except OSError:
            pass
    remaining_links = links_and_mounts(dest)
    if remaining_links:
        result.update(removed=False, links_not_removed=[str(p) for p in remaining_links[:20]],
                      hint='Links or mount points could not be removed; nothing else deleted. Check and '
                           'retry.')
        print_json(result)
        return 1
    for child in list(dest.iterdir()):
        if child.name != WORKSPACE_MARKER:
            remove_entry(child)
    if not [child for child in dest.iterdir() if child.name != WORKSPACE_MARKER]:
        remove_entry(dest / WORKSPACE_MARKER)
        try:
            dest.rmdir()
        except OSError:
            pass
    result['removed'] = not dest.exists()
    if not result['removed']:
        result['hint'] = ('Parts of the copy remain, for example because a process still uses them. The marker is '
                          'retained: stop the processes and retry; --dry-run shows what remains.')
    print_json(result)
    return 0 if result['removed'] else 1


# --------------------------------------------------------------------------- runs

def version_tuple(value: Any) -> tuple[int, ...] | None:
    match = re.match(r'\s*[vV]?(\d+)(?:\.(\d+))?', str(value) if value is not None else '')
    return (int(match.group(1)), int(match.group(2) or 0)) if match else None


def status_value(value: Any) -> Any:
    """Accept scalar verdicts and stored objects such as {"value": "PARTIAL", "reason": ...}."""
    if isinstance(value, dict):
        value = value.get('value', value.get('status'))
    return value if isinstance(value, (str, type(None))) else None


def security_closure_allowed(data: dict[str, Any], state: dict[str, Any], run: Path) -> bool:
    """Terminal review state, or a supported successor that links back to this run."""
    review = data.get('security_review')
    if isinstance(review, dict) and review.get('status') in ('DONE', 'NOT_APPLICABLE', 'NOT_PERFORMED', 'BLOCKED'):
        return True
    successor_id = state.get('superseded_by_run')
    if (not isinstance(successor_id, str) or not ID_PATTERN.fullmatch(successor_id)
            or successor_id == data.get('run_id') or successor_id == run.name):
        return False
    successor = run.parent / successor_id
    if not is_within(successor, run.parent) or successor.resolve() == run.resolve():
        return False
    found = False
    for name in ('RUN-STATE.json', 'audit.json'):
        path = successor / name
        if not path.is_file():
            continue
        try:
            linked = read_json(path)
        except (OSError, ValueError):
            return False
        if (not isinstance(linked, dict) or linked.get('run_id') != successor_id
                or linked.get('schema_version') != SCHEMA_VERSION):
            return False
        found = found or data.get('run_id') in id_list(linked.get('previous_run_ids'))
    return found


def describe_run(directory: Path, *, inspect_live: bool = True) -> dict[str, Any] | None:
    """Facts about one run directory; None if it is no audit run (e.g. a folder of implementation notes)."""
    loaded: dict[str, Any] = {}
    problems = []
    for name in ('RUN-STATE.json', 'audit.json'):
        if (directory / name).is_file():
            try:
                value = read_json(directory / name)
                loaded[name] = value if isinstance(value, dict) else {}
            except (OSError, ValueError) as exc:
                loaded[name] = {}
                problems.append(f'{name} unreadable: {exc}')
            else:
                if not isinstance(value, dict):
                    problems.append(f'{name} does not contain a JSON object.')
    if not loaded:
        return None
    state, audit = loaded.get('RUN-STATE.json', {}), loaded.get('audit.json', {})
    corrupt = len(problems) == 2
    raw_version = state.get('prompt_version') or audit.get('prompt_version')
    version = str(raw_version) if isinstance(raw_version, (int, float)) else status_value(raw_version)
    parsed = version_tuple(version)
    report = (directory / 'REPORT.md').is_file()
    audit_status = status_value(audit.get('audit_status'))
    schema = audit.get('schema_version', state.get('schema_version'))
    compatible = schema == SCHEMA_VERSION
    if corrupt:
        status = 'CORRUPT'
    elif state.get('closed_at_utc'):
        status = 'OPEN' if compatible and not security_closure_allowed(audit, state, directory) else 'CLOSED'
    elif 'closed_at_utc' in state or (parsed is not None and parsed >= (3, 1)):
        status = 'OPEN'  # explicit closure field or supported identifier requires a closure marker
    elif audit_status in AUDIT_STATUS and report:
        status = 'LEGACY_CLOSED_INFERRED'  # stored run has no explicit closure field
    else:
        status = 'LEGACY_OPEN_INFERRED'
    baseline = audit.get('baseline') if isinstance(audit.get('baseline'), dict) else {}
    stamp = re.match(r'(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})Z', directory.name)
    created = (status_value(state.get('created_at_utc')) or status_value(baseline.get('captured_at_utc'))
               or (f'{stamp.group(1)}T{stamp.group(2)}:{stamp.group(3)}:00Z' if stamp else None)
               or datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc).replace(microsecond=0)
               .isoformat().replace('+00:00', 'Z'))
    live_valid = False
    top_type_error = False
    live_codes: set[str] = set()
    if inspect_live and compatible and audit:
        try:
            live_issues, _ = validate_audit(audit, directory, None, final=False)
            live_codes = live_issues.codes()
            top_type_error = any(e['code'] == 'E_TYPE' and e['where'] in TOP_LEVEL_REQUIRED for e in live_issues.errors)
            live_valid = not live_issues.errors
        except (OSError, ValueError, TypeError):
            live_codes = {'E_TYPE'}
    checkpoint = (verify_checkpoint(directory) if inspect_live else
                  {'state': 'PRESENT_UNVERIFIED' if (directory / 'checkpoints/interim').is_dir() else 'ABSENT'})
    phase = re.match(r'^P(\d+)\b', str(state.get('phase', '')))
    before_interim = phase is not None and int(phase[1]) < 7
    project_changed = bool(live_codes & {'E_CODE_REF_STALE', 'E_SOURCE_STALE', 'E_CODE_REF_FILE'})
    malformed = bool(problems) or not compatible or top_type_error or any(key not in audit for key in TOP_LEVEL_REQUIRED)
    if corrupt and not inspect_live:
        guidance = 'CORRUPT live state; inspect this run and verify its interim checkpoint for possible recovery. Restoring discards all progress since the interim checkpoint.'
    elif not inspect_live:
        guidance = 'Continue from the persisted phase; inspect this run before resuming.'
    elif project_changed:
        guidance = 'Continue / revalidate; project changed since checkpoint: follow re-audit rules. Do not restore over live progress.'
    elif live_codes and not malformed:
        guidance = 'Repair live state, then revalidate; preserve progress and continue from the persisted phase.'
    elif before_interim and compatible:
        guidance = 'Continue from the persisted phase; complete and revalidate live state before the interim checkpoint.'
    elif malformed and not before_interim and checkpoint['state'] == 'VALID':
        guidance = 'Live state malformed or schema-invalid. Offer the verified interim checkpoint; restoring discards all progress since the interim checkpoint. Restore its files to live state using temp files + atomic rename, keep the last valid state, then validate before resuming P8/P9.'
    elif corrupt:
        guidance = 'CORRUPT live state; no verified checkpoint is available. Repair live state, then revalidate.'
    elif not compatible:
        guidance = 'Cannot resume with this package; preserve artifacts and create a new linked audit/re-audit run. Never change the schema identifier.'
    elif malformed:
        guidance = 'Repair live state, then revalidate; no verified interim checkpoint is available.'
    else:
        guidance = 'Continue from live state and persisted phase; revalidate unresolved checks and verify the interim checkpoint before P8.'
    if not compatible and not inspect_live and not corrupt:
        guidance = 'Cannot resume with this package; preserve artifacts and create a new linked audit/re-audit run. Never change the schema identifier.'
    return {'dir': directory.name, 'run_id': status_value(state.get('run_id')) or status_value(audit.get('run_id')),
            'schema_version': schema, 'resumable_with_package':
                (checkpoint['state'] == 'VALID' if inspect_live else None) if corrupt else compatible,
            'live_state': 'CORRUPT' if corrupt else (('VALID' if live_valid else 'INVALID_OR_INCOMPLETE') if inspect_live else 'NOT_CHECKED'),
            'live_validation_errors': sorted(live_codes) if inspect_live else None, 'resume_guidance': guidance,
            'security_review': {'status': status_value((audit.get('security_review') or {}).get('status'))
                                if isinstance(audit.get('security_review'), dict) else None},
            'checkpoint': checkpoint,
            'prompt_version': version, 'run_type': status_value(audit.get('run_type') or state.get('run_type')),
            'phase': state.get('phase'), 'created_at_utc': created, 'updated_at_utc': state.get('updated_at_utc'),
            'closed_at_utc': state.get('closed_at_utc'), 'status': status, 'audit_status': audit_status,
            'release_status': status_value(audit.get('release_status')),
            'previous_run_ids': audit.get('previous_run_ids') or state.get('previous_run_ids'),
            'has_report': report, 'problems': problems}


def cmd_runs(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    results = Path(args.results_dir).resolve() if args.results_dir else project / DEFAULT_RESULTS_DIRNAME
    runs, other = [], []
    if results.is_dir():
        for directory in sorted(p for p in results.iterdir() if p.is_dir()):
            info = describe_run(directory, inspect_live=False)
            if info is None:
                other.append(directory.name)
            else:
                runs.append(info)
    runs.sort(key=lambda item: str(item['created_at_utc']))
    open_runs = [r['dir'] for r in runs if r['status'] in ('OPEN', 'LEGACY_OPEN_INFERRED', 'CORRUPT')]
    closed_runs = [r['dir'] for r in runs if r['status'] in ('CLOSED', 'LEGACY_CLOSED_INFERRED')]
    latest_resumable = next((r['dir'] for r in reversed(runs) if r['resumable_with_package'] and r['dir'] in open_runs), None)
    selected = latest_resumable or (open_runs[-1] if open_runs else None)
    for index, info in enumerate(runs):
        if args.inspect_all or info['dir'] == selected:
            runs[index] = describe_run(results / info['dir']) or info
    latest_resumable = next((r['dir'] for r in reversed(runs) if r['resumable_with_package'] and r['dir'] in open_runs), None)
    notes = []
    if any(r['status'].startswith('LEGACY') for r in runs):
        notes.append('Runs without an explicit closure field may have LEGACY_* status inferred from audit_status and REPORT.md '
                     'and must be clarified with the user if uncertain.')
    if len(open_runs) > 1:
        notes.append('Multiple unclosed runs: clarify which one is intended before resuming.')
    if other:
        notes.append('Other directories without RUN-STATE.json and audit.json (other_dirs) are not audit runs.')
    print_json({'results_dir': str(results), 'exists': results.is_dir(), 'package_prompt_version': PROMPT_VERSION,
                'runs': runs, 'latest_open': open_runs[-1] if open_runs else None,
                'latest_closed': closed_runs[-1] if closed_runs else None,
                'latest_resumable': latest_resumable, 'other_dirs': other, 'notes': notes})
    return 0


# --------------------------------------------------------------------------- validate

class Issues:
    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []

    def error(self, code: str, where: str, message: str) -> None:
        self.errors.append({'code': code, 'where': where, 'message': message})

    def warning(self, code: str, where: str, message: str) -> None:
        self.warnings.append({'code': code, 'where': where, 'message': message})

    def codes(self) -> set[str]:
        return {item['code'] for item in self.errors}


def check_enum(issues: Issues, where: str, value: Any, allowed: set, nullable: bool) -> None:
    if value is None:
        if not nullable:
            issues.error('E_ENUM', where, f'Value missing; allowed: {", ".join(sorted(map(str, allowed)))}.')
        return
    valid = isinstance(value, bool) if allowed is BOOL else isinstance(value, str) and value in allowed
    if not valid:
        issues.error('E_ENUM', where, f'Invalid value {value!r}; allowed: {", ".join(sorted(map(str, allowed)))}.')


def normalize_ws(text: str) -> str:
    return ' '.join(text.split())


def tally(entries: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        key = str(entry.get(field))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def project_path_of(ref: Any) -> str | None:
    """Project-relative path in a reference such as 'docs/prd.md#goal', 'src/a.ts:12-14' or 'tests/x.py::test_y'.

    None for everything else: URLs, prose, prefixed references like 'run:VISION.md' or 'user:StartRequest',
    absolute paths and files of the results directory.
    """
    if not isinstance(ref, str):
        return None
    text = ref.strip()
    if not text or '://' in text or any(char.isspace() for char in text) or has_glob_magic(text):
        return None
    head = text.split(':', 1)[0]
    if ':' in text and '/' not in head and '\\' not in head and '.' not in head:
        return None  # prefixed reference or Windows drive letter
    text = re.sub(r':\d+(-\d+)?$', '', text.split('#', 1)[0].split('::', 1)[0]).replace('\\', '/')
    if text.startswith('./'):
        text = text[2:]
    text = text.rstrip('/')
    if text == DEFAULT_RESULTS_DIRNAME or text.startswith(DEFAULT_RESULTS_DIRNAME + '/'):
        return None
    if '/' in text or re.fullmatch(r'[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,7}', text):
        return text
    return None


def utc_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', value)
    try:
        stamp = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except ValueError:
        return None


def canonical_run_path(value: str) -> str:
    return posixpath.normpath(value.replace('\\', '/'))


def validate_audit(data: Any, run_dir: Path, project: Path | None,
                   final: bool = False) -> tuple[Issues, dict[str, Any]]:
    issues = Issues()

    def soft(code: str, where: str, message: str) -> None:
        """Hard error in the final check; warning while the run is in progress."""
        if final:
            issues.error(code, where, message)
        else:
            issues.warning('W' + code[1:], where, message)

    if not isinstance(data, dict):
        issues.error('E_TYPE', 'audit.json', 'Top level must be a JSON object.')
        return issues, {}
    if data.get('schema_version') != SCHEMA_VERSION:
        issues.error('E_SCHEMA_VERSION', 'schema_version',
                     f'Expected {SCHEMA_VERSION!r}, found {data.get("schema_version")!r}. '
                     'Cannot resume with this package; preserve artifacts and create a new linked audit/re-audit run. '
                     'Never convert a run by changing its schema identifier.')
        return issues, {}
    run_root = run_dir.resolve()
    evidence_root = run_root / 'evidence'
    for key in TOP_LEVEL_REQUIRED:
        if key not in data:
            issues.error('E_MISSING_FIELD', key, 'Required field missing (use null with a reason for unknown values).')
    if data.get('prompt_version') != PROMPT_VERSION:
        issues.warning('W_PROMPT_VERSION', 'prompt_version',
                       f'Tool is for {PROMPT_VERSION}, found {data.get("prompt_version")!r}.')
    if 'run_type' in data:
        check_enum(issues, 'run_type', data['run_type'], RUN_TYPE, nullable=False)
    for key in ('previous_run_ids', 'residual_risks', 'next_actions'):
        if key in data and not isinstance(data[key], list):
            issues.error('E_TYPE', key, 'List expected.')

    # Baseline binding: code citations and paths must belong to the audited state.
    baseline_snapshot: dict[str, Any] | None = None
    baseline_file = evidence_root / 'baseline' / 'baseline-start.json'
    if baseline_file.is_file():
        try:
            loaded = read_json(baseline_file)
            baseline_snapshot = loaded if isinstance(loaded, dict) and isinstance(loaded.get('files'), dict) else None
        except (OSError, ValueError):
            baseline_snapshot = None
        if baseline_snapshot is None:
            issues.error('E_BASELINE', 'evidence/baseline/baseline-start.json', 'Start baseline unreadable.')
    baseline = data.get('baseline') if isinstance(data.get('baseline'), dict) else {}
    fingerprint = baseline.get('fingerprint')
    if 'baseline' in data and 'captured_at_utc' not in baseline:
        issues.error('E_MISSING_FIELD', 'baseline.captured_at_utc', 'Required field missing.')
    if baseline_snapshot is not None:
        if fingerprint != baseline_snapshot.get('content_fingerprint'):
            issues.error('E_BASELINE_MISMATCH', 'baseline.fingerprint',
                         'Differs from the content_fingerprint of the start baseline.')
    else:
        if 'baseline' in data and not (isinstance(fingerprint, str) and re.fullmatch(r'[0-9a-f]{64}', fingerprint)):
            issues.error('E_BASELINE', 'baseline.fingerprint', 'SHA-256 fingerprint expected.')
        soft('E_BASELINE_FILE_MISSING', 'evidence/baseline/baseline-start.json',
             'Start baseline missing; code citations and paths are not bound to the audited state (use init).')
    baseline_files: dict[str, str] = baseline_snapshot.get('files', {}) if baseline_snapshot else {}
    baseline_dirs = {rel.rsplit('/', depth)[0] for rel in baseline_files for depth in range(1, rel.count('/') + 1)}
    if project is None:
        root = (baseline_snapshot or {}).get('project_root') or baseline.get('project_root')
        if isinstance(root, str) and Path(root).is_dir():
            project = Path(root)
    project_root = project.resolve() if project else None
    if project_root is None:
        soft('E_PROJECT_REQUIRED', 'project', 'Project root unknown; code citations and sources cannot be checked '
             '(--project <original-project>).')
    elif find_workspace_root(project_root) is not None:
        issues.error('E_PROJECT_IS_WORKSPACE', 'project', 'Citations are checked against the original project, not '
                     'against the mutable working copy.')

    scope = data.get('scope')
    if isinstance(scope, dict):
        for key in ('in_scope', 'out_of_scope', 'scope_gaps', 'size_profile'):
            if key not in scope:
                issues.error('E_MISSING_FIELD', f'scope.{key}', 'Required field missing.')
        check_enum(issues, 'scope.size_profile', scope.get('size_profile'), SIZE_PROFILE, nullable=True)
    elif 'scope' in data:
        issues.error('E_TYPE', 'scope', 'Object expected.')
    vision = data.get('vision') if isinstance(data.get('vision'), dict) else {}
    if 'vision' in data and not isinstance(data.get('vision'), dict):
        issues.error('E_TYPE', 'vision', 'Object expected.')
    elif 'vision' in data:
        check_enum(issues, 'vision.status', vision.get('status'), VISION_STATUS, nullable=False)
        for key in ('statement', 'open_questions'):
            if key not in vision:
                issues.error('E_MISSING_FIELD', f'vision.{key}', 'Required field missing.')

    registers: dict[str, list[dict[str, Any]]] = {}
    owner: dict[str, str] = {}
    for name in ID_REGISTERS + OPTIONAL_ID_REGISTERS:
        value = data.get(name)
        registers[name] = []
        if value is None:
            continue
        if not isinstance(value, list):
            issues.error('E_TYPE', name, 'List expected.')
            continue
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                issues.error('E_TYPE', f'{name}[{index}]', 'Object expected.')
                continue
            entry_id = entry.get('id')
            if not isinstance(entry_id, str) or not entry_id.strip():
                issues.error('E_ID_MISSING', f'{name}[{index}]', 'Entry without id.')
                continue
            if entry_id in owner:
                issues.error('E_DUP_ID', f'{name}[{entry_id}]', f'ID already assigned in {owner[entry_id]}.')
                continue
            owner[entry_id] = name
            registers[name].append(entry)
    by_id = {name: {entry['id']: entry for entry in entries} for name, entries in registers.items()}
    claims, checks, evidence = by_id['claims'], by_id['checks'], by_id['evidence']
    findings, tasks, objectives = by_id['findings'], by_id['tasks'], by_id['objectives']

    for name, entries in registers.items():
        spec = REGISTER_SPEC[name]
        for entry in entries:
            where = f'{name}[{entry["id"]}]'
            for field in spec['required']:
                if field not in entry:
                    issues.error('E_MISSING_FIELD', f'{where}.{field}', 'Required field missing (use null for unknown values).')
            for field, (allowed, nullable) in spec['enums'].items():
                if field in entry:
                    check_enum(issues, f'{where}.{field}', entry[field], allowed, nullable)

    def check_refs(where: str, entry: dict[str, Any]) -> None:
        for field, target in REF_TARGETS.items():
            if field not in entry or entry[field] is None:
                continue
            refs = entry[field]
            if not isinstance(refs, list):
                issues.error('E_TYPE', f'{where}.{field}', 'List of IDs expected.')
                continue
            for ref in refs:
                if not isinstance(ref, str):
                    issues.error('E_TYPE', f'{where}.{field}', f'ID expected as text, found {ref!r}.')
                elif ref not in by_id[target]:
                    issues.error('E_REF', f'{where}.{field}', f'{ref!r} does not exist in {target}.')
        superseded_by = entry.get('superseded_by')
        if superseded_by is not None and (not isinstance(superseded_by, str) or superseded_by not in findings):
            issues.error('E_REF', f'{where}.superseded_by', f'{superseded_by!r} does not exist in findings.')

    for name, entries in registers.items():
        for entry in entries:
            check_refs(f'{name}[{entry["id"]}]', entry)

    matrix = data.get('domain_matrix')
    matrix_rows: list[dict[str, Any]] = []
    if isinstance(matrix, list):
        seen: set[str] = set()
        for index, row in enumerate(matrix):
            if not isinstance(row, dict) or not isinstance(row.get('domain'), str) or not row['domain']:
                issues.error('E_DOMAIN', f'domain_matrix[{index}]', 'Entry without a valid domain field.')
                continue
            domain = row['domain']
            where = f'domain_matrix[{domain}]'
            if domain in seen:
                issues.error('E_DOMAIN', where, 'Domain assessed more than once.')
            seen.add(domain)
            matrix_rows.append(row)
            check_enum(issues, f'{where}.applicability', row.get('applicability'), APPLICABILITY, nullable=False)
            if row.get('applicability') in ('NOT_APPLICABLE', 'UNKNOWN') and not nonempty(row.get('reason')):
                issues.error('E_DOMAIN_REASON', where, 'NOT_APPLICABLE/UNKNOWN requires a specific reason.')
            if row.get('applicability') == 'APPLICABLE' and not id_list(row.get('check_ids')):
                issues.warning('W_DOMAIN_UNCHECKED', where, 'Applicable domain without checks.')
            check_refs(where, row)
        for domain in DOMAINS:
            if domain not in seen:
                issues.error('E_DOMAIN', 'domain_matrix', f'Domain {domain} missing.')
    elif 'domain_matrix' in data:
        issues.error('E_TYPE', 'domain_matrix', 'List expected.')

    code_ref_count = 0

    def check_code_refs(where: str, refs: Any) -> bool:
        """Validate citations against the start baseline; True if all are valid and there is at least one."""
        nonlocal code_ref_count
        if refs is None or refs == []:
            return False
        if not isinstance(refs, list):
            issues.error('E_TYPE', f'{where}.code_refs', 'List expected.')
            return False
        valid = True
        for index, ref in enumerate(refs):
            at = f'{where}.code_refs[{index}]'
            if not isinstance(ref, dict):
                issues.error('E_CODE_REF_FORMAT', at, 'Object with path, line_start, line_end, excerpt expected.')
                valid = False
                continue
            path_value, start, end, excerpt = (ref.get('path'), ref.get('line_start'), ref.get('line_end'),
                                               ref.get('excerpt'))
            if not (isinstance(path_value, str) and path_value and type(start) is int and type(end) is int
                    and 1 <= start <= end and isinstance(excerpt, str)):
                issues.error('E_CODE_REF_FORMAT', at, 'path, line_start <= line_end (starting at 1) and excerpt required.')
                valid = False
                continue
            if end - start + 1 > 40:
                issues.error('E_CODE_REF_SPAN', at, 'Line range exceeds 40 lines; cite the supporting location precisely.')
                valid = False
                continue
            normalized = normalize_ws(excerpt)
            if len(normalized.replace(' ', '')) < 15:
                issues.error('E_CODE_REF_TRIVIAL', at, 'Excerpt too short (under 15 characters); expand the range.')
                valid = False
                continue
            strong, weak = secret_pattern_hits(excerpt)
            if strong:
                issues.error('E_SECRET_IN_EXCERPT', at, f'Excerpt contains credentials ({", ".join(strong)}); '
                             'end the excerpt before the value; a partial line is enough.')
                valid = False
                continue
            if weak:
                issues.warning('W_SECRET_LIKE_IN_EXCERPT', at, f'Excerpt contains {", ".join(weak)}; check whether a '
                               'real value is cited, and if so end the excerpt before the value.')
            rel = path_value.replace('\\', '/')
            if baseline_files and rel not in baseline_files:
                issues.error('E_CODE_REF_NOT_IN_BASELINE', at, f'{rel} does not belong to the audited project state.')
                valid = False
                continue
            if project_root is None or baseline_snapshot is None:
                valid = False
                continue
            target = project_root / rel
            if not is_within(target, project_root) or not target.is_file():
                issues.error('E_CODE_REF_FILE', at, f'File not found in project: {rel}')
                valid = False
                continue
            if baseline_files and sha256_file(target) != baseline_files[rel]:
                issues.error('E_CODE_REF_STALE', at, f'{rel} has changed since the start baseline.')
                valid = False
                continue
            # Lines as grep -n, git blame and editors count them: only \n ends a line (not form feed or U+2028).
            lines = target.read_text(encoding='utf-8', errors='replace').split('\n')
            lines = [line[:-1] if line.endswith('\r') else line for line in lines]
            if lines and lines[-1] == '':
                lines.pop()
            if end > len(lines):
                issues.error('E_CODE_REF_RANGE', at, f'{rel} has only {len(lines)} lines.')
                valid = False
                continue
            window = lines[start - 1:end]
            inside = normalized in normalize_ws(' '.join(window))
            # The excerpt has to touch the first and the last cited line, so line numbers are exact.
            loose = len(window) > 1 and (normalized in normalize_ws(' '.join(window[1:]))
                                         or normalized in normalize_ws(' '.join(window[:-1])))
            if not inside or loose:
                issues.error('E_CODE_REF', at, f'Excerpt does not match {rel}:{start}-{end} exactly.')
                valid = False
                continue
            code_ref_count += 1
        return valid

    log_exit: dict[str, Any] = {}
    verifiable: set[str] = set()
    qualifying: set[str] = set()
    observed: set[str] = set()
    baseline_time = utc_stamp((baseline_snapshot or {}).get('captured_at_utc'))

    def within_run(value: Any) -> bool:
        stamp = utc_stamp(value)
        return stamp is not None and baseline_time is not None and baseline_time <= stamp <= datetime.now(timezone.utc)

    for item in registers['evidence']:
        where = f'evidence[{item["id"]}]'
        path_value = item.get('path')
        if item.get('freshness') == 'STALE':
            historical = True
        else:
            historical = False
        if path_value:
            target = run_root / canonical_run_path(str(path_value))
            if not is_within(target, evidence_root):
                issues.error('E_EVIDENCE_PATH', where, 'Evidence files belong under evidence/ in the run directory.')
                continue
            if not target.is_file():
                issues.error('E_EVIDENCE_MISSING', where, f'File missing: {path_value}')
                continue
            if not nonempty(item.get('sha256')):
                issues.error('E_EVIDENCE_HASH_MISSING', where, 'sha256 missing.')
                continue
            actual = sha256_file(target)
            if actual != str(item['sha256']).lower():
                issues.error('E_EVIDENCE_HASH', where, f'sha256 does not match {path_value}.')
                continue
            if item.get('kind') == 'LOG':
                meta_path = target.with_name(target.name[:-4] + '.meta.json') if target.name.endswith('.log') else None
                meta = None
                if meta_path is not None and meta_path.is_file():
                    try:
                        meta = read_json(meta_path)
                    except (OSError, ValueError):
                        meta = None
                if not isinstance(meta, dict):
                    issues.error('E_LOG_META_MISSING', where, 'LOG without metadata from audit_tools.py run; logs come '
                                 'from real command output.')
                    continue
                if meta.get('log_sha256') != actual:
                    issues.error('E_LOG_META_HASH', where, 'Metadata does not belong to this log file.')
                    continue
                ended = meta.get('ended_at_utc') or meta.get('started_at_utc')
                ended_stamp = utc_stamp(ended)
                if item.get('freshness') == 'CURRENT' and ended_stamp and baseline_time and ended_stamp < baseline_time:
                    issues.error('E_EVIDENCE_PREDATES_BASELINE', where,
                                 'Log predates the start baseline of this run; mark as STALE or regenerate.')
                    continue
                log_exit[item['id']] = meta.get('exit_code')
            verifiable.add(item['id'])
            current = item.get('freshness') == 'CURRENT' and within_run(item.get('captured_at_utc'))
            if (item.get('kind') == 'LOG' and current and meta.get('tool') == 'audit_tools.py'
                    and nonempty(meta.get('command')) and within_run(meta.get('started_at_utc')) and within_run(ended)):
                qualifying.add(item['id'])
                observed.add(item['id'])
            observation = item.get('observation')
            if (item.get('kind') in UI_EVIDENCE_KINDS and current and isinstance(observation, dict)
                    and all(isinstance(observation.get(k), str) and observation[k].strip()
                            for k in ('observer', 'description'))):
                qualifying.add(item['id'])
                observed.add(item['id'])
        elif not nonempty(item.get('ref')):
            issues.error('E_EVIDENCE_REF', where, 'Neither path (with sha256) nor ref specified.')
        if 'code_refs' in item and not historical and check_code_refs(where, item.get('code_refs')):
            verifiable.add(item['id'])
            if item.get('freshness') == 'CURRENT':
                qualifying.add(item['id'])
        if item.get('kind') == 'STATIC_REF' and not item.get('code_refs'):
            issues.warning('W_STATIC_REF_UNVERIFIED', where, 'Static evidence without verifiable code_refs.')

    verified_finding_refs: set[str] = set()
    for finding in registers['findings']:
        where = f'findings[{finding["id"]}]'
        if finding.get('state') in ('VERIFIED_FIXED', 'SUPERSEDED'):
            continue  # historical citations refer to an earlier state
        if check_code_refs(where, finding.get('code_refs')):
            verified_finding_refs.add(finding['id'])

    for kind, entries in (('findings', registers['findings']), ('tasks', registers['tasks'])):
        for entry in entries:
            where = f'{kind}[{entry["id"]}]'
            file_value = entry.get('file')
            if file_value:
                target = run_root / str(file_value)
                if not is_within(target, run_root) or not target.is_file():
                    issues.error('E_FILE_MISSING', where, f'File missing: {file_value}')
            elif kind == 'tasks':
                soft('E_TASK_FILE', where, 'Every package requires a file under tasks/.')
            elif entry.get('assessment') == 'CONFIRMED':
                soft('E_FINDING_FILE', where, 'Confirmed findings require a file under findings/.')

    if baseline_files:
        path_fields = (('sources', 'ref'), ('components', 'ref'), ('test_assessments', 'ref'), ('doc_claims', 'source'))
        for register, field in path_fields:
            for entry in registers[register]:
                rel = project_path_of(entry.get(field))
                if rel is not None and rel not in baseline_files and rel not in baseline_dirs:
                    issues.error('E_PATH_REF', f'{register}[{entry["id"]}].{field}',
                                 f'{rel} does not exist in the audited project state.')
        for source in registers['sources']:
            rel = project_path_of(source.get('ref'))
            excerpt = source.get('excerpt')
            if rel in baseline_files and isinstance(excerpt, str) and excerpt.strip() and project_root is not None:
                target = project_root / rel
                if not target.is_file():
                    continue
                if sha256_file(target) != baseline_files[rel]:
                    issues.error('E_SOURCE_STALE', f'sources[{source["id"]}]',
                                 f'{rel} has changed since the start baseline.')
                elif normalize_ws(excerpt) not in normalize_ws(target.read_text(encoding='utf-8', errors='replace')):
                    issues.error('E_SOURCE_EXCERPT', f'sources[{source["id"]}].excerpt',
                                 f'Excerpt does not occur verbatim in {rel}.')

    graph = {task['id']: [d for d in id_list(task.get('depends_on')) if d in tasks] for task in registers['tasks']}
    indegree = {node: len(deps) for node, deps in graph.items()}
    dependents: dict[str, list[str]] = {node: [] for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            dependents[dep].append(node)
    queue = [node for node, degree in indegree.items() if degree == 0]
    while queue:
        node = queue.pop()
        for follower in dependents[node]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                queue.append(follower)
    cyclic = sorted(node for node, degree in indegree.items() if degree > 0)
    if cyclic:
        issues.error('E_TASK_CYCLE', 'tasks', f'Cyclic dependencies: {", ".join(cyclic)}')

    for check in registers['checks']:
        where = f'checks[{check["id"]}]'
        execution, method = check.get('execution'), check.get('method')
        evidence_ids = id_list(check.get('evidence_ids'))
        for flag in ('required_for_audit', 'required_for_release'):
            if flag in check and check[flag] is None and not nonempty(check.get('required_reason')):
                issues.error('E_REQUIRED_NULL', f'{where}.{flag}', 'null requires a reason in required_reason.')
        if method is not None and method not in METHOD:
            issues.warning('W_METHOD', f'{where}.method', f'Unknown method {method!r}.')
        exit_code = check.get('exit_code')
        logged = [log_exit[eid] for eid in evidence_ids if eid in log_exit]
        if exit_code is not None and type(exit_code) is not int:
            issues.error('E_TYPE', f'{where}.exit_code', 'Integer or null expected.')
        if method in ('STATIC', 'MANUAL') and exit_code is not None and exit_code not in logged:
            issues.error('E_STATIC_EXIT', where, 'Static/manual checks have an exit code only when a '
                         'run log proves it.')
        if execution in ('PASS', 'FAIL'):
            if not evidence_ids:
                issues.error('E_CHECK_EVIDENCE', where, 'A PASS/FAIL result requires evidence_ids.')
            elif not any(eid in verifiable for eid in evidence_ids):
                issues.error('E_CHECK_EVIDENCE_UNVERIFIABLE', where, 'No verifiable evidence (file with hash, run log '
                             'or code_refs).')
            if method in RUN_LOG_METHODS and not any(eid in log_exit for eid in evidence_ids):
                issues.error('E_CHECK_NEEDS_RUN_LOG', where, f'{method} result requires a log from audit_tools.py run.')
            if method == 'E2E' and not any(eid in log_exit or (evidence.get(eid, {}).get('kind') in UI_EVIDENCE_KINDS
                                                               and eid in verifiable) for eid in evidence_ids):
                issues.error('E_CHECK_NEEDS_RUN_LOG', where, 'E2E result requires a run log or UI evidence.')
        if exit_code is not None and logged and exit_code not in logged:
            issues.error('E_EXIT_MISMATCH', where, f'exit_code {exit_code} contradicts the log ({logged}).')
        if execution == 'BLOCKED' and not evidence_ids:
            issues.error('E_BLOCKED_UNEVIDENCED', where, 'BLOCKED requires evidence of the attempt or blocker.')
        if (execution in ('BLOCKED', 'NOT_RUN', 'SKIPPED')
                and (check.get('required_for_audit') is not False or check.get('required_for_release') is not False)
                and not nonempty(check.get('catch_up_plan'))):
            issues.error('E_CATCH_UP', where, 'An unexecuted mandatory check requires a catch-up plan.')

    for claim in registers['claims']:
        if claim.get('assessment') in ('SUPPORTED', 'VIOLATED') and not id_list(claim.get('evidence_ids')):
            issues.error('E_CLAIM_EVIDENCE', f'claims[{claim["id"]}]', 'SUPPORTED/VIOLATED requires evidence_ids.')
    for requirement in registers['requirements']:
        where = f'requirements[{requirement["id"]}]'
        binding = requirement.get('binding')
        if binding in ('BINDING', 'CLAIMED', 'DERIVED') and not id_list(requirement.get('source_ids')):
            issues.error('E_UNSOURCED_REQUIREMENT', where, 'BINDING/CLAIMED/DERIVED requires source_ids.')
        if binding in ('BINDING', 'CLAIMED') and not id_list(requirement.get('check_ids')) and not nonempty(
                requirement.get('gap')):
            issues.error('E_REQ_TRACE', where, 'Binding requirement without a check and without a named gap.')
        if binding in ('EXCLUDED', 'ROADMAP') and not id_list(requirement.get('source_ids')):
            issues.error('E_UNSOURCED_EXCLUSION', where, 'Exclusion or roadmap requires a supporting source.')
        if binding == 'BINDING' and id_list(requirement.get('check_ids')) and not any(
                checks.get(cid, {}).get('required_for_audit') is True for cid in id_list(requirement.get('check_ids'))):
            soft('E_REQUIRED_MISSING', where, 'None of the checks of a binding requirement is mandatory for the audit.')
    critical_objectives = {o['id'] for o in registers['objectives'] if o.get('critical') is not False}
    for objective in registers['objectives']:
        where = f'objectives[{objective["id"]}]'
        if not id_list(objective.get('source_ids')):
            issues.error('E_UNSOURCED_OBJECTIVE', where, 'An objective requires source_ids.')
        if objective.get('critical') is False and not id_list(objective.get('source_ids')):
            issues.error('E_UNSOURCED_NONCRITICAL', where, 'critical: false requires a supporting source.')
        if objective.get('fulfillment') == 'DEMONSTRATED' and not any(
                claims.get(cid, {}).get('assessment') == 'SUPPORTED' for cid in id_list(objective.get('claim_ids'))):
            issues.error('E_OBJECTIVE_SUPPORT', where, 'DEMONSTRATED requires at least one SUPPORTED claim.')
    for invariant in registers['invariants']:
        if not id_list(invariant.get('source_ids')):
            issues.error('E_UNSOURCED_INVARIANT', f'invariants[{invariant["id"]}]', 'An invariant requires source_ids.')
    for journey in registers['journeys']:
        where = f'journeys[{journey["id"]}]'
        check_ids = id_list(journey.get('check_ids'))
        if journey.get('tier') == 'T1' and not check_ids and not nonempty(journey.get('gap')):
            issues.error('E_JOURNEY_TRACE', where, 'T1 journey without a check and without a gap.')
        if journey.get('tier') in ('T2', 'T3') and set(id_list(journey.get('objective_ids'))) & critical_objectives:
            issues.error('E_TIER_DOWNGRADE', where, 'A journey that supports a core objective is T1.')
        if journey.get('tier') == 'T1' and check_ids and not any(
                checks.get(cid, {}).get('required_for_audit') is True
                and checks.get(cid, {}).get('required_for_release') is True for cid in check_ids):
            soft('E_REQUIRED_MISSING', where, 'None of the checks of a T1 journey is mandatory for both audit and release.')
    for environment in registers['environments']:
        if environment.get('authorized') is True and environment.get('used') is False and not nonempty(
                environment.get('unused_reason')):
            soft('E_ENV_UNUSED', f'environments[{environment["id"]}]',
                 'Authorized environment unused without unused_reason.')

    run_type = data.get('run_type')
    for finding in registers['findings']:
        where = f'findings[{finding["id"]}]'
        category = finding.get('category')
        if not isinstance(category, str) or category not in FINDING_CATEGORY:
            issues.error('E_FINDING_CATEGORY', f'{where}.category', 'Required category: domain A-S except H, or SECURITY. Use SECURITY for every P8 finding and the early secret-in-product finding.')
        assessment, state = finding.get('assessment'), finding.get('state')
        evidence_ids = id_list(finding.get('evidence_ids'))
        if assessment == 'CONFIRMED':
            for field in ('severity', 'priority', 'confidence', 'cause_status'):
                if finding.get(field) is None:
                    issues.error('E_FINDING_FIELDS', f'{where}.{field}', 'Required for CONFIRMED.')
            if finding.get('confidence') == 'LOW':
                issues.error('E_FINDING_CONFIDENCE', where, 'CONFIRMED with confidence LOW is contradictory.')
            if finding['id'] not in verified_finding_refs and not any(eid in qualifying for eid in evidence_ids) and state not in (
                    'VERIFIED_FIXED', 'SUPERSEDED'):
                issues.error('E_FINDING_EVIDENCE', where, 'CONFIRMED requires current baseline-bound code_refs, '
                             'a run-backed log, or a stored UI observation with recorded origin and capture time.')
            if (finding.get('release_blocker') is True or finding.get('severity') == 'CRITICAL') and finding.get('confidence') != 'HIGH':
                issues.error('E_BLOCKER_CONFIDENCE', where, 'CRITICAL and release blockers require HIGH confidence.')
            control = finding.get('release_control')
            control_valid = False
            if isinstance(control, dict):
                check_refs(f'{where}.release_control', control)
                linked = any(gid in by_id['release_gates'] for gid in id_list(control.get('gate_ids'))) or any(
                    by_id['requirements'].get(rid, {}).get('source_ids') for rid in id_list(control.get('requirement_ids')))
                control_valid = (linked and isinstance(control.get('malfunction'), str) and bool(control['malfunction'].strip())
                                 and any(eid in qualifying for eid in id_list(control.get('evidence_ids'))))
            if finding.get('release_blocker') is True and (finding.get('type') in ('MISSING_EVIDENCE', 'CLEANUP')
                    or finding.get('type') == 'TEST_QUALITY' and not control_valid):
                issues.error('E_GAP_AS_BLOCKER', where, 'Coverage gaps and cleanup cannot be blockers; TEST_QUALITY '
                             'requires a linked release control with an evidenced malfunction.')
            if state in ('OPEN', 'CANDIDATE_FIXED'):
                target = finding.get('target')
                target_valid = False
                if isinstance(target, dict):
                    check_refs(f'{where}.target', target)
                    target_fields = ('requirement_ids', 'objective_ids', 'invariant_ids', 'doc_claim_ids')
                    refs = [(REF_TARGETS[field], ref) for field in target_fields for ref in id_list(target.get(field))]
                    sourced = bool(refs) and all(by_id[reg].get(ref, {}).get('source_ids') for reg, ref in refs)
                    necessity = target.get('necessity')
                    necessary = isinstance(necessity, dict) and all(
                        isinstance(necessity.get(k), str) and necessity[k].strip() for k in ('actor', 'reachable_path', 'harm'))
                    target_valid = (sourced or necessary) and all(
                        by_id[reg].get(ref, {}).get('source_ids') for reg, ref in refs)
                if finding.get('type') in ('DEFECT', 'MISSING_IMPLEMENTATION', 'UNMET_PROMISE', 'FALSE_CLAIM', 'OPERABILITY_GAP') and not target_valid:
                    soft('E_FINDING_TARGET', where, 'An active confirmed finding needs a sourced target or necessity '
                         '{actor, reachable_path, harm}.')
                if finding.get('severity') in ('CRITICAL', 'HIGH') or finding.get('release_blocker') is True:
                    review = finding.get('review')
                    review_valid = isinstance(review, dict) and all(
                        isinstance(review.get(k), str) and review[k].strip() for k in ('reviewer', 'method', 'outcome'))
                    if isinstance(review, dict):
                        check_refs(f'{where}.review', review)
                        review_valid = (review_valid and review.get('method') in REVIEW_METHOD
                                        and review.get('outcome') in REVIEW_OUTCOME)
                        if review.get('outcome') == 'UNAVAILABLE':
                            review_valid = (review_valid and isinstance(review.get('reason'), str)
                                            and bool(review['reason'].strip()) and review.get('method') == 'COMMENT_ONLY')
                        if review.get('outcome') == 'DISPUTED':
                            review_valid = review_valid and any(eid in qualifying for eid in id_list(review.get('countercheck_evidence_ids')))
                    if not review_valid:
                        soft('E_FINDING_REVIEW', where, 'Record reviewer, method and outcome; UNAVAILABLE needs a reason '
                             'and COMMENT_ONLY; DISPUTED while CONFIRMED needs qualifying countercheck_evidence_ids.')
            if state == 'OPEN' and not id_list(finding.get('task_ids')):
                soft('E_FINDING_TASK', where, 'Open confirmed finding without a package.')
            if (finding.get('severity') == 'CRITICAL' and finding.get('release_blocker') is False
                    and not nonempty(finding.get('release_blocker_reason'))):
                issues.error('E_BLOCKER_REASON', where, 'CRITICAL without a release blocker requires release_blocker_reason.')
        if assessment == 'UNCONFIRMED' and state == 'OPEN' and not any(
                tasks.get(tid, {}).get('type') == 'INVESTIGATE' for tid in id_list(finding.get('task_ids'))):
            soft('E_FINDING_INVESTIGATE', where, 'Open unconfirmed finding without an INVESTIGATE package.')
        if assessment == 'DISPROVEN':
            if not id_list(finding.get('counter_evidence_ids')) and not nonempty(finding.get('disproof')):
                issues.error('E_DISPROVEN', where, 'DISPROVEN requires counter_evidence_ids or disproof.')
            if state in ('CANDIDATE_FIXED', 'VERIFIED_FIXED'):
                issues.error('E_STATE_CONFLICT', where, 'A fixed defect is not DISPROVEN.')
        if state == 'ACCEPTED_RISK':
            acceptance = finding.get('acceptance')
            if not (isinstance(acceptance, dict) and nonempty(acceptance.get('authorized_by'))
                    and nonempty(acceptance.get('scope')) and nonempty(acceptance.get('date'))
                    and id_list(acceptance.get('source_ids'))
                    and all(sid in by_id['sources'] for sid in id_list(acceptance.get('source_ids')))):
                issues.error('E_ACCEPTED_RISK', where, 'ACCEPTED_RISK requires acceptance with authorized_by, scope, date '
                             'and source_ids that refer to the decision.')
        if state == 'SUPERSEDED' and not (finding.get('superseded_by') or nonempty(finding.get('superseded_reason'))):
            issues.error('E_SUPERSEDED', where, 'SUPERSEDED requires superseded_by or superseded_reason.')
        if state == 'VERIFIED_FIXED' and not evidence_ids:
            issues.error('E_VERIFIED_FIXED', where, 'VERIFIED_FIXED requires verification evidence.')
        if run_type == 'INITIAL' and state in ('CANDIDATE_FIXED', 'VERIFIED_FIXED'):
            issues.error('E_FIXED_IN_INITIAL', where, 'An initial run does not change the product; *_FIXED is available only in a '
                         're-audit.')
        for task_id in id_list(finding.get('task_ids')):
            task = tasks.get(task_id)
            if task and finding['id'] not in id_list(task.get('finding_ids')):
                issues.warning('W_LINK', where, f'{task_id} does not refer back to {finding["id"]}.')

    def is_open_blocker(finding: dict[str, Any]) -> bool:
        return (finding.get('assessment') == 'CONFIRMED' and finding.get('release_blocker') is True
                and finding.get('state') in ('OPEN', 'CANDIDATE_FIXED'))

    for gate in registers['release_gates']:
        where = f'release_gates[{gate["id"]}]'
        if gate.get('status') == 'VIOLATED' and not any(
                is_open_blocker(findings.get(fid, {})) for fid in id_list(gate.get('finding_ids'))):
            issues.error('E_GATE_VIOLATED_UNFOUNDED', where,
                         'VIOLATED requires a confirmed, open blocker finding.')
        if gate.get('status') == 'MET' and not id_list(gate.get('claim_ids')):
            issues.error('E_GATE_MET_UNFOUNDED', where, 'MET requires claim_ids.')
        supporting = [c for c in registers['checks'] if set(id_list(c.get('claim_ids'))) & set(id_list(gate.get('claim_ids')))]
        if supporting and not any(c.get('required_for_release') is True for c in supporting):
            soft('E_REQUIRED_MISSING', where, 'None of the checks of this gate is mandatory for release.')

    audit_status, release_status = data.get('audit_status'), data.get('release_status')
    security = data.get('security_review')
    security_valid = isinstance(security, dict)
    security = security if security_valid else {}
    security_status = security.get('status')
    required_security = ('status', 'reason', 'started_at_utc', 'completed_at_utc', 'executed_by')
    security_valid = (security_valid and all(k in security for k in required_security)
                      and isinstance(security_status, str) and security_status in SECURITY_STATUS
                      and isinstance(security.get('reason'), str))
    started, completed = utc_stamp(security.get('started_at_utc')), utc_stamp(security.get('completed_at_utc'))
    start_pair = ((security.get('started_at_utc') is None and security.get('executed_by') is None)
                  or (within_run(security.get('started_at_utc')) and isinstance(security.get('executed_by'), str)
                      and bool(security['executed_by'].strip())))
    if security_status == 'PENDING':
        security_valid = security_valid and start_pair and security.get('completed_at_utc') is None
    elif security_status in ('IN_PROGRESS', 'DONE', 'BLOCKED'):
        security_valid = (security_valid and within_run(security.get('started_at_utc'))
                          and isinstance(security.get('executed_by'), str) and bool(security['executed_by'].strip()))
        if security_status == 'DONE':
            security_valid = (security_valid and within_run(security.get('completed_at_utc'))
                              and started is not None and completed is not None and started <= completed)
        else:
            security_valid = security_valid and security.get('completed_at_utc') is None
        if security_status == 'BLOCKED':
            security_valid = security_valid and bool(security.get('reason'))
    elif security_status == 'NOT_APPLICABLE':
        security_valid = (security_valid and security.get('started_at_utc') is None and security.get('executed_by') is None
                          and within_run(security.get('completed_at_utc')))
    elif security_status == 'NOT_PERFORMED':
        security_valid = (security_valid and start_pair and within_run(security.get('completed_at_utc'))
                          and (started is None or completed is not None and started <= completed))
    if not security_valid:
        issues.error('E_SECURITY_REVIEW', 'security_review', 'Required security review object/status/fields or status-dependent timestamps are invalid.')
    scope_valid = isinstance(data.get('security_scope'), list)
    for area in registers['security_scope']:
        ids = id_list(area.get('check_ids'))
        valid = (all(isinstance(area.get(k), str) and area[k].strip() for k in ('ref', 'reason'))
                 and isinstance(area.get('relevance'), str) and area['relevance'] in SECURITY_RELEVANCE)
        valid = valid and bool(ids) and all(checks.get(cid, {}).get('required_for_audit') is True
                                             and checks.get(cid, {}).get('required_for_release') is True for cid in ids)
        scope_valid = scope_valid and valid
    if isinstance(data.get('security_scope'), list):
        scope_valid = scope_valid and len(registers['security_scope']) == len(data['security_scope'])
    if not scope_valid:
        issues.error('E_SECURITY_SCOPE', 'security_scope', 'Required list: each area needs ref, reason, relevance and existing checks mandatory for audit and release.')
    if security_status == 'DONE':
        security_checks = {cid for area in registers['security_scope'] for cid in id_list(area.get('check_ids'))}
        security_checks.update(cid for row in matrix_rows if row.get('domain') == 'H'
                               for cid in id_list(row.get('check_ids')) if checks.get(cid, {}).get('required_for_audit') is True
                               or checks.get(cid, {}).get('required_for_release') is True)
        security_checks.update(c['id'] for c in registers['checks'] if c.get('security') is True
                               and (c.get('required_for_audit') is True or c.get('required_for_release') is True))
        done_checks = scope_valid and all(checks.get(cid, {}).get('execution') in ('PASS', 'FAIL') and any(
            eid in qualifying for eid in id_list(checks.get(cid, {}).get('evidence_ids'))) for cid in security_checks)
        if not done_checks:
            issues.error('E_SECURITY_DONE_CHECKS', 'security_review', 'DONE requires all mandatory security checks resolved with current qualifying evidence.')
        security_valid = security_valid and done_checks
    if security_status == 'NOT_APPLICABLE':
        na_valid = (isinstance(security.get('reason'), str) and bool(security['reason'].strip()) and data.get('security_scope') == []
                    and any(row.get('domain') == 'H' and row.get('applicability') == 'NOT_APPLICABLE'
                            and nonempty(row.get('reason')) and not id_list(row.get('check_ids')) for row in matrix_rows))
        if not na_valid:
            issues.error('E_SECURITY_NA_REASON', 'security_review', 'NOT_APPLICABLE needs a project reason, empty scope and consistent domain H assessment.')
        security_valid = security_valid and na_valid
    if security_status == 'NOT_PERFORMED':
        check_refs('security_review', security)
        decision = id_list(security.get('decision_source_ids'))
        decision_tasks = id_list(security.get('task_ids'))
        if not (nonempty(security.get('reason')) and decision and all(
                isinstance(by_id['sources'].get(sid, {}).get('ref'), str)
                and by_id['sources'][sid]['ref'].startswith('user:') for sid in decision)
                and any(tasks.get(tid, {}).get('type') in ('HUMAN', 'VERIFY') for tid in decision_tasks)
                and audit_status == 'PARTIAL'):
            issues.error('E_SECURITY_NOT_PERFORMED', 'security_review', 'NOT_PERFORMED needs a user-decision source, a HUMAN/VERIFY task reference and PARTIAL audit status.')
    security_complete = security_valid and scope_valid and security_status in ('DONE', 'NOT_APPLICABLE')
    if release_status == 'GO_FOR_DEFINED_SCOPE' and not security_complete:
        issues.error('E_GO_SECURITY_PENDING', 'release_status', 'GO requires a valid DONE or NOT_APPLICABLE security review.')
    if audit_status == 'COMPLETE_FOR_DEFINED_SCOPE' and not security_complete:
        issues.error('E_COMPLETE_SECURITY_PENDING', 'audit_status', 'COMPLETE requires a valid DONE or NOT_APPLICABLE security review.')
    if 'audit_status' in data:
        check_enum(issues, 'audit_status', audit_status, AUDIT_STATUS, nullable=not final)
    if 'release_status' in data:
        check_enum(issues, 'release_status', release_status, RELEASE_STATUS, nullable=not final)
    if not final and (audit_status is None or release_status is None):
        issues.warning('W_STATUS_OPEN', 'audit_status/release_status', 'Verdict still open (interim state).')
    open_blockers = [f['id'] for f in registers['findings'] if is_open_blocker(f)]
    if open_blockers and release_status not in (None, 'NO_GO'):
        issues.error('E_NO_GO_REQUIRED', 'release_status',
                     f'Open confirmed release blockers require NO_GO: {", ".join(open_blockers)}')
    if release_status == 'NO_GO' and not open_blockers:
        issues.error('E_NO_GO_UNFOUNDED', 'release_status', 'NO_GO without a confirmed open release blocker.')
    if release_status == 'GO_FOR_DEFINED_SCOPE':
        if audit_status != 'COMPLETE_FOR_DEFINED_SCOPE':
            issues.error('E_GO_INCOMPLETE', 'release_status', 'GO requires a fully processed audit scope.')
        if not critical_objectives:
            issues.error('E_GO_NO_OBJECTIVE', 'objectives', 'GO without a demonstrated critical core objective.')
        if not registers['release_gates']:
            issues.error('E_GO_NO_GATES', 'release_gates', 'GO without defined release gates.')
        for gate in registers['release_gates']:
            where = f'release_gates[{gate["id"]}]'
            if gate.get('status') != 'MET':
                issues.error('E_GO_GATE', where, 'GO requires every gate to be MET.')
                continue
            for claim_id in id_list(gate.get('claim_ids')):
                claim = claims.get(claim_id)
                if claim is None:
                    continue
                if claim.get('assessment') != 'SUPPORTED':
                    issues.error('E_GO_CLAIM', where, f'{claim_id} is not SUPPORTED.')
                items = [evidence.get(eid) for eid in id_list(claim.get('evidence_ids'))]
                if not items or any(item is None or item.get('freshness') != 'CURRENT' for item in items):
                    issues.error('E_GO_STALE', where, f'{claim_id} relies on missing or non-current evidence.')
                carrying = [c for c in registers['checks'] if claim_id in id_list(c.get('claim_ids'))
                            and c.get('purpose') == 'ACCEPTANCE_REGRESSION' and c.get('execution') == 'PASS'
                            and (c.get('method') != 'STATIC' or any(
                                eid in log_exit or evidence.get(eid, {}).get('code_refs') and eid in verifiable
                                for eid in id_list(c.get('evidence_ids'))))]
                if not carrying:
                    issues.error('E_GO_SUPPORT', where, f'{claim_id} has no passed ACCEPTANCE_REGRESSION check '
                                 'with verifiable evidence (reproducers, characterization, investigation and harness controls '
                                 'do not support authorization).')
        for check in registers['checks']:
            if check.get('required_for_release') is True:
                executed = check.get('execution') in ('PASS', 'FAIL')
                if not executed or (check.get('purpose') == 'ACCEPTANCE_REGRESSION' and check.get('execution') != 'PASS'):
                    issues.error('E_GO_REQUIRED_CHECK', f'checks[{check["id"]}]',
                                 'Mandatory release check not executed or acceptance check not PASS.')
            if check.get('required_for_release') is None:
                issues.error('E_GO_REQUIRED_NULL', f'checks[{check["id"]}]', 'Release obligation unresolved.')
        for objective in registers['objectives']:
            if objective['id'] in critical_objectives and objective.get('fulfillment') != 'DEMONSTRATED':
                issues.error('E_GO_OBJECTIVE', f'objectives[{objective["id"]}]', 'Critical core objective not demonstrated.')
        if vision.get('status') == 'CONFLICTED':
            issues.error('E_GO_VISION', 'vision.status', 'GO with a conflicting vision.')
        elif vision.get('status') == 'PARTIAL':
            issues.warning('W_GO_VISION', 'vision.status', 'GO with a partially evidenced vision: justify relevance.')
        for journey in registers['journeys']:
            if journey.get('tier') == 'T1' and journey.get('status') == 'VIOLATED':
                issues.error('E_GO_JOURNEY', f'journeys[{journey["id"]}]', 'GO with a violated T1 journey.')
        for finding in registers['findings']:
            if finding.get('assessment') == 'UNCONFIRMED' and finding.get('state') == 'OPEN':
                # An open suspicion of a critical defect is diagnostics the audit still owes (§15).
                if finding.get('severity') == 'CRITICAL' or finding.get('priority') == 'P0':
                    issues.error('E_GO_OPEN_SUSPICION', f'findings[{finding["id"]}]',
                                 'Open unconfirmed CRITICAL or P0 suspicion prevents GO; resolve it first.')
                elif finding.get('severity') == 'HIGH' or finding.get('priority') == 'P1':
                    issues.warning('W_GO_OPEN_SUSPICION', f'findings[{finding["id"]}]',
                                   'Justify GO despite an open HIGH or P1 suspicion.')
            if finding.get('assessment') != 'CONFIRMED' or finding.get('state') not in ('OPEN', 'CANDIDATE_FIXED'):
                continue
            if finding.get('release_blocker') is None:
                issues.error('E_GO_BLOCKER_UNKNOWN', f'findings[{finding["id"]}]', 'Blocker status unresolved.')
            if finding.get('severity') == 'CRITICAL' or finding.get('priority') in ('P0', 'P1'):
                issues.error('E_GO_OPEN_SEVERE', f'findings[{finding["id"]}]',
                             'Open CRITICAL or P0/P1 finding (P1 = before release) prevents GO.')
    if audit_status == 'COMPLETE_FOR_DEFINED_SCOPE':
        for check in registers['checks']:
            if check.get('required_for_audit') is True and check.get('execution') in ('BLOCKED', 'NOT_RUN', 'SKIPPED'):
                issues.error('E_COMPLETE_GAP', f'checks[{check["id"]}]', 'Mandatory audit check not executed.')
            if check.get('required_for_audit') is None:
                issues.error('E_COMPLETE_REQUIRED_NULL', f'checks[{check["id"]}]', 'Audit obligation unresolved.')
        for row in matrix_rows:
            if row.get('applicability') == 'UNKNOWN':
                issues.error('E_COMPLETE_DOMAIN', f'domain_matrix[{row["domain"]}]', 'UNKNOWN prevents COMPLETE.')
            if row.get('applicability') == 'APPLICABLE' and not id_list(row.get('check_ids')):
                issues.error('E_COMPLETE_DOMAIN_UNCHECKED', f'domain_matrix[{row["domain"]}]',
                             'Applicable domain without checks prevents COMPLETE.')
        for journey in registers['journeys']:
            if journey.get('tier') == 'T1' and not any(
                    checks.get(cid, {}).get('execution') in ('PASS', 'FAIL')
                    and checks.get(cid, {}).get('method') in END_TO_END_METHODS
                    and any(eid in observed for eid in id_list(checks.get(cid, {}).get('evidence_ids')))
                    for cid in id_list(journey.get('check_ids'))):
                issues.error('E_COMPLETE_T1', f'journeys[{journey["id"]}]',
                             'T1 journey without an executed check close to end-to-end (not only STATIC/UNIT).')
        if vision.get('status') == 'CONFLICTED':
            issues.warning('W_COMPLETE_VISION', 'vision.status', 'Justify COMPLETE with a conflicting vision.')
        if any(f.get('assessment') == 'UNCONFIRMED' and f.get('state') == 'OPEN' for f in registers['findings']):
            issues.warning('W_COMPLETE_UNCONFIRMED', 'findings', 'Justify open unconfirmed findings with COMPLETE.')

    if run_root.is_dir():
        live_paths = []
        for parent, directories, names in os.walk(run_root):
            if Path(parent) == run_root:
                directories[:] = [name for name in directories if name != 'checkpoints']
            live_paths.extend(Path(parent) / name for name in names)
        for path in sorted(live_paths):
            if not path.is_file():
                continue
            name, suffix = path.name.lower(), path.suffix.lower()
            rel_run = path.relative_to(run_root).as_posix()
            try:
                strong, weak = result_secret_hits(path)
            except OSError:
                strong, weak = [], []
                issues.warning('W_SECRET_SCAN_UNREADABLE', rel_run, 'Secret check: file unreadable.')
            if strong or weak:
                # run logs cannot be edited afterwards; a hit there means: re-run with --redact, keep the old log out
                is_run_log = suffix == '.log' and path.with_name(path.name[:-4] + '.meta.json').is_file()
                if strong and not is_run_log:
                    issues.error('E_SECRET_IN_RESULTS', rel_run, f'Contains credentials ({", ".join(strong)}); '
                                 'Redact the value; describe the location without the value.')
                elif strong or weak:
                    issues.warning('W_SECRET_LIKE_IN_RESULTS', rel_run,
                                   f'Contains {", ".join(strong + weak)}; check and redact real values'
                                   + (' (Log: do not edit; rerun the check instead; master prompt §6).'
                                      if is_run_log else '.'))
            if suffix in INERT_SUFFIXES:
                continue
            if suffix in CODE_EXTENSIONS or any(fnmatch.fnmatchcase(name, p) for p in TOOLING_FILE_PATTERNS):
                issues.error('E_DISCOVERABLE_FILE', rel_run,
                             'Project tools could discover this file; store it as .txt or a patch.')

    for name in ('REPORT.md', 'VISION.md', 'RESUME.md', 'RUN-STATE.json'):
        if not (run_root / name).is_file():
            if final:
                issues.error('E_FINAL_FILE', name, 'Required artifact missing.')
            else:
                issues.warning('W_FILE_PENDING', name, 'Artifact not yet present.')
    if final:
        def text_of(rel: str) -> str | None:
            """Content of an existing artifact; None if it is missing (reported elsewhere). Empty files count."""
            target = run_root / rel
            try:
                return target.read_text(encoding='utf-8-sig', errors='replace') if is_within(target, run_root) else None
            except OSError:
                return None
        report = text_of('REPORT.md')
        for needle in ('Executive summary', str(audit_status), str(release_status)):
            if report is not None and needle not in report:
                issues.error('E_FINAL_CONTENT', 'REPORT.md', f'Does not contain {needle!r} (executive summary with verdict).')
        if report is not None and not re.search(r'^#{1,6}\s+(?:\d+(?:\.\d+)*\.?\s+)?Security review\b[^\n]*$', report, flags=re.MULTILINE | re.IGNORECASE):
            issues.error('E_FINAL_CONTENT', 'REPORT.md', 'Security review section heading missing.')
        vision_text = text_of('VISION.md')
        if vision_text is not None and str(vision.get('status')) not in vision_text:
            issues.error('E_FINAL_CONTENT', 'VISION.md', 'Vision status missing.')
        resume = text_of('RESUME.md')
        if resume is not None and not resume.strip():
            issues.error('E_FINAL_CONTENT', 'RESUME.md', 'Required artifact is empty.')
        for finding in registers['findings']:
            if finding.get('assessment') == 'CONFIRMED' and finding.get('file'):
                content = text_of(str(finding['file']))
                if content is not None and finding['id'] not in content:
                    issues.error('E_FINAL_CONTENT', f'findings[{finding["id"]}]', 'File does not contain the finding ID.')
        for task in registers['tasks']:
            if task.get('file'):
                content = text_of(str(task['file']))
                if content is not None and (task['id'] not in content
                                or (task.get('type') != 'HUMAN' and 'candidate_complete' not in content)):
                    issues.error('E_FINAL_CONTENT', f'tasks[{task["id"]}]',
                                 'File does not contain the ID or copyable implementation request.')
    run_state_path = run_root / 'RUN-STATE.json'
    if run_state_path.is_file():
        try:
            run_state = read_json(run_state_path)
        except ValueError as exc:
            issues.error('E_RUN_STATE', 'RUN-STATE.json', f'Invalid JSON: {exc}')
        else:
            missing = [k for k in ('run_id', 'phase', 'updated_at_utc', 'next_steps')
                       if not isinstance(run_state, dict) or k not in run_state]
            if missing:
                issues.error('E_RUN_STATE', 'RUN-STATE.json', f'Fields missing: {", ".join(missing)}')
            elif run_state.get('run_id') != data.get('run_id'):
                issues.warning('W_RUN_ID', 'RUN-STATE.json', 'run_id differs from audit.json.')
            if (isinstance(run_state, dict) and run_state.get('closed_at_utc')
                    and not security_closure_allowed(data, run_state, run_root)):
                issues.error('E_RUN_SECURITY_CLOSURE', 'RUN-STATE.json',
                             'Closed run requires DONE, NOT_APPLICABLE, NOT_PERFORMED or BLOCKED security review, '
                             'or a supported superseding run linked through previous_run_ids.')
            if final and isinstance(run_state, dict):
                authorization = run_state.get('authorization')
                if not isinstance(authorization, list) or not all(isinstance(item, str) and item.strip()
                                                                  for item in authorization):
                    issues.error('E_RUN_AUTHORIZATION', 'RUN-STATE.json', 'authorization missing or not a list of '
                                 'verbatim quotations; record authorizations from the start request, without authorizations use an empty '
                                 'list (master prompt §16).')
                if not nonempty(run_state.get('closed_at_utc')):
                    issues.warning('W_RUN_NOT_CLOSED', 'RUN-STATE.json', 'closed_at_utc missing; the final check '
                                   'runs before closure (master prompt §16); P7 remains open. Record closure only at P9 or a valid supersession.')

    confirmed = [f for f in registers['findings'] if f.get('assessment') == 'CONFIRMED']
    counts: dict[str, Any] = {
        'audit_status': audit_status, 'release_status': release_status, 'open_release_blockers': open_blockers,
        'security_review': {'status': security_status},
        'findings': {'total': len(registers['findings']), 'assessment': tally(registers['findings'], 'assessment'),
                     'state': tally(registers['findings'], 'state'), 'type': tally(registers['findings'], 'type'),
                     'severity_confirmed': tally(confirmed, 'severity'),
                     'confirmed_open': tally([f for f in confirmed if f.get('state') in ('OPEN', 'CANDIDATE_FIXED')], 'severity'),
                     'unconfirmed_open': sum(f.get('assessment') == 'UNCONFIRMED' and f.get('state') == 'OPEN'
                                             for f in registers['findings'])},
        'checks': {'total': len(registers['checks']), 'execution': tally(registers['checks'], 'execution'),
                   'purpose': tally(registers['checks'], 'purpose'), 'method': tally(registers['checks'], 'method'),
                   'required_for_audit': tally(registers['checks'], 'required_for_audit'),
                   'required_for_release': tally(registers['checks'], 'required_for_release')},
        'objectives': tally(registers['objectives'], 'fulfillment'),
        'journeys': {'tier': tally(registers['journeys'], 'tier'), 'status': tally(registers['journeys'], 'status')},
        'requirements': tally(registers['requirements'], 'binding'),
        'components': tally(registers['components'], 'depth'),
        'environments': {'authorized': tally(registers['environments'], 'authorized'),
                         'used': tally(registers['environments'], 'used')},
        'domain_matrix': tally(matrix_rows, 'applicability'),
        'claims': tally(registers['claims'], 'assessment'),
        'evidence': tally(registers['evidence'], 'freshness'),
        'tasks': {'type': tally(registers['tasks'], 'type'), 'wave': tally(registers['tasks'], 'wave')},
        'release_gates': tally(registers['release_gates'], 'status'),
        'code_refs_checked': code_ref_count,
    }
    if registers['test_assessments']:
        counts['test_assessments'] = tally(registers['test_assessments'], 'class')
    if registers['doc_claims']:
        counts['doc_claims'] = tally(registers['doc_claims'], 'verdict')
    return issues, counts


def markdown_links(text: str) -> list[str]:
    """Artifact links in Markdown prose; code examples are not references."""
    prose = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker:
            fence = marker[1]
        else:
            prose.append(line)
    text = re.sub(r'(?<!`)(`+)(?!`)(.*?)\1(?!`)', '', ''.join(prose), flags=re.DOTALL)
    return re.findall(r'!?\[[^\]]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)', text)


def checkpoint_files(run: Path, data: dict[str, Any]) -> tuple[set[str], list[dict[str, str]], set[str]]:
    """Stored current-run files plus an explicit inventory of uncopied references."""
    found = {'audit.json', 'REPORT.md', 'RUN-STATE.json', 'VISION.md', 'RESUME.md',
             'evidence/baseline/baseline-start.json'}
    omitted: set[tuple[str, str, str]] = set()
    directories: set[str] = set()

    def list_or_empty(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def omit(value: str, source: str, reason: str) -> None:
        omitted.add((source, value, reason))

    def add(value: str, source: str = 'audit.json', *, markdown: bool = False) -> None:
        reference = value
        value = value.strip('<>')
        if not value or value.startswith('#'):
            return
        value = value.split('#', 1)[0].split('?', 1)[0]
        value = canonical_run_path(unquote(value) if markdown else value)
        if Path(value).is_absolute() or re.match(r'^(?:[A-Za-z]:[\\/]|[\\/]{2})', value):
            omit(reference, source, 'absolute path')
            return
        if re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', value):
            omit(reference, source, 'URL or external scheme')
            return
        target = run / (Path(source).parent if markdown else Path('.')) / value
        if not is_within(target, run):
            omit(reference, source, 'outside current run')
            return
        if is_within(target, run / 'checkpoints'):
            omit(reference, source, 'checkpoint artifact')
            return
        if target.is_dir():
            directories.add(target.resolve().relative_to(run.resolve()).as_posix())
            omit(reference, source, 'directory')
            return
        if not target.is_file():
            raise ToolError(f'Checkpoint artifact missing inside current run: {reference}')
        found.add(target.resolve().relative_to(run.resolve()).as_posix())

    for register, field in (('evidence', 'path'), ('findings', 'file'), ('tasks', 'file')):
        for record in list_or_empty(data.get(register)):
            if isinstance(record, dict) and isinstance(record.get(field), str):
                add(record[field])
    baseline = data.get('baseline')
    if isinstance(baseline, dict) and isinstance(baseline.get('file'), str):
        add(baseline['file'])
    for register, field in (('sources', 'ref'), ('components', 'ref'), ('evidence', 'ref'),
                            ('security_scope', 'ref'), ('test_assessments', 'ref'), ('doc_claims', 'source')):
        for record in list_or_empty(data.get(register)):
            item = record.get(field) if isinstance(record, dict) else None
            if not isinstance(item, str):
                continue
            reference = unquote(item.split('#', 1)[0].split('?', 1)[0])
            if reference.startswith('run:'):
                path = canonical_run_path(reference[4:])
                first, sep, rest = path.partition('/')
                if sep and first == data.get('run_id'):
                    add(rest)
                elif sep and (first in id_list(data.get('previous_run_ids')) or re.match(r'^\d{4}-\d{2}-\d{2}T', first)):
                    omit(item, 'audit.json', 'previous-run reference')
                else:
                    add(path)
            else:
                omit(item, 'audit.json', 'project or external reference')
    for register in ('evidence', 'findings'):
        for record in list_or_empty(data.get(register)):
            for ref in list_or_empty(record.get('code_refs')) if isinstance(record, dict) else []:
                if isinstance(ref, dict) and isinstance(ref.get('path'), str):
                    omit(ref['path'], 'audit.json', 'project or external reference')
    scanned: set[str] = set()
    while found - scanned:
        rel = sorted(found - scanned)[0]
        scanned.add(rel)
        path = run / rel
        if not path.is_file() or not is_within(path, run):
            raise ToolError(f'Checkpoint artifact missing: {rel}')
        if path.suffix == '.md':
            for link in markdown_links(path.read_text(encoding='utf-8-sig')):
                add(link, rel, markdown=True)
        if path.suffix == '.log' and path.with_suffix('.meta.json').is_file():
            add(path.with_suffix('.meta.json').relative_to(run).as_posix())
    return found, [{'source': source, 'reference': reference, 'reason': reason}
                   for source, reference, reason in sorted(omitted)], directories


def verify_checkpoint(run: Path) -> dict[str, Any]:
    directory = run / 'checkpoints' / 'interim'
    if not directory.exists():
        return {'state': 'ABSENT'}
    try:
        manifest = read_json(directory / 'manifest.json')
        snapshot = directory / 'files'
        data = read_json(snapshot / 'audit.json')
        if not isinstance(manifest, dict) or not isinstance(data, dict):
            raise ToolError('Checkpoint manifest and audit.json must be objects.')
        if (manifest.get('format') != 'audit-interim-checkpoint/1'
                or manifest.get('schema_version') != SCHEMA_VERSION
                or manifest.get('package') != 'production-readiness-audit'
                or not all(isinstance(manifest.get(k), str) and manifest[k] for k in ('prompt_version', 'tool_version'))
                or manifest.get('run_id') != data.get('run_id')
                or manifest.get('baseline') != data.get('baseline')
                or manifest.get('validation') != {'result': 'PASS', 'final': True, 'errors': 0}):
            raise ToolError('Checkpoint identity or validation metadata mismatch.')
        saved_state = read_json(snapshot / 'RUN-STATE.json')
        if not isinstance(saved_state, dict) or saved_state.get('run_id') != manifest['run_id']:
            raise ToolError('Checkpoint RUN-STATE.json run_id mismatch.')
        for name in ('RUN-STATE.json', 'audit.json'):
            try:
                live = read_json(run / name)
            except (OSError, ValueError):
                continue  # Recovery must work when a live file was interrupted.
            if isinstance(live, dict) and live.get('run_id') is not None and live['run_id'] != manifest['run_id']:
                raise ToolError(f'Checkpoint does not belong to this run: {name} run_id mismatch.')
        hashes = manifest.get('sha256')
        actual_files = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob('*') if p.is_file()}
        not_snapshotted = manifest.get('not_snapshotted', [])
        if not isinstance(not_snapshotted, list) or not all(isinstance(item, dict) for item in not_snapshotted):
            raise ToolError('Checkpoint not_snapshotted inventory malformed.')
        expected, omitted, directories = checkpoint_files(snapshot, data)
        if not isinstance(hashes, dict) or set(hashes) != actual_files or set(hashes) != expected:
            raise ToolError('Checkpoint artifact inventory mismatch.')
        if 'not_snapshotted' in manifest and not_snapshotted != omitted:
            raise ToolError('Checkpoint not_snapshotted inventory mismatch.')
        expected_entries = {'manifest.json'} | {(Path('files') / rel).as_posix() for rel in expected | directories}
        expected_entries |= {parent.as_posix() for rel in expected_entries for parent in Path(rel).parents if parent != Path('.')}
        expected_entries = {'interim'} | {'interim/' + rel for rel in expected_entries}
        actual_entries = set()
        for parent, dirs, files in os.walk(directory.parent):
            if Path(parent) == directory.parent:
                dirs[:] = [name for name in dirs if not name.startswith('.interim-')]
            actual_entries.update((Path(parent) / name).relative_to(directory.parent).as_posix() for name in dirs + files)
        if actual_entries != expected_entries:
            raise ToolError('Checkpoint directory inventory mismatch.')
        for rel, digest in hashes.items():
            path = snapshot / rel
            if not is_within(path, snapshot) or sha256_file(path) != digest:
                raise ToolError(f'Checkpoint hash mismatch: {rel}')
        return {'state': 'VALID', 'directory': str(directory), 'run_id': manifest['run_id'], 'files': len(hashes)}
    except (OSError, ValueError, TypeError, ToolError) as exc:
        return {'state': 'INVALID', 'directory': str(directory), 'error': str(exc)}


def create_checkpoint(run: Path, project: Path | None) -> dict[str, Any]:
    leftovers = sorted(path for path in (run / 'checkpoints').glob('.interim-*') if path.is_dir())
    if leftovers:
        raise ToolError('Remove leftover checkpoint staging directories before creating a checkpoint: '
                        + ', '.join(str(path) for path in leftovers))
    data = read_json(run / 'audit.json')
    if not isinstance(data, dict) or not isinstance(data.get('security_review'), dict):
        raise ToolError('Checkpoint requires an audit object with security_review.')
    if data['security_review'].get('status') != 'PENDING' or data.get('audit_status') != 'PARTIAL':
        raise ToolError('Interim checkpoint requires PARTIAL with security review PENDING.')
    destination = run / 'checkpoints' / 'interim'
    if destination.exists():
        raise ToolError('Interim checkpoint already exists; never overwrite it.')
    files, not_snapshotted, directories = checkpoint_files(run, data)
    hashes = {rel: sha256_file(run / rel) for rel in sorted(files)}
    manifest = {'format': 'audit-interim-checkpoint/1', 'package': 'production-readiness-audit',
                'schema_version': SCHEMA_VERSION, 'prompt_version': PROMPT_VERSION, 'tool_version': TOOL_VERSION,
                'run_id': data['run_id'], 'baseline': data['baseline'],
                'validation': {'result': 'PASS', 'final': True, 'errors': 0}, 'sha256': hashes,
                'not_snapshotted': not_snapshotted}
    staging = destination.with_name('.interim-' + os.urandom(8).hex())
    staging.mkdir(parents=True)
    try:
        for rel in sorted(directories):
            (staging / 'files' / rel).mkdir(parents=True, exist_ok=True)
        for rel in sorted(files):
            target = staging / 'files' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run / rel, target)
            if sha256_file(target) != hashes[rel] or sha256_file(run / rel) != hashes[rel]:
                raise ToolError('Live state changed during checkpoint creation.')
        saved = read_json(staging / 'files/audit.json')
        if saved != data:
            raise ToolError('Live audit.json changed during checkpoint creation.')
        issues, _ = validate_audit(saved, staging / 'files', project, final=True)
        if issues.errors:
            raise ToolError('Checkpoint requires validate --final PASS: ' + ', '.join(sorted(issues.codes())))
        write_json_new(staging / 'manifest.json', manifest)
        staging.rename(destination)
    finally:
        if staging.exists():
            remove_tree(staging)
    return verify_checkpoint(run)


def cmd_checkpoint(args: argparse.Namespace) -> int:
    run = Path(args.run_dir).resolve()
    try:
        result = (create_checkpoint(run, Path(args.project) if args.project else None)
                  if args.action == 'create' else verify_checkpoint(run))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ToolError(f'Checkpoint input unreadable: {exc}') from exc
    print_json(result)
    return 0 if result['state'] == 'VALID' else 1


def cmd_validate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    audit_path = run_dir / 'audit.json'
    try:
        data = read_json(audit_path)
    except OSError as exc:
        raise ToolError(f'audit.json unreadable: {exc}') from exc
    except ValueError as exc:
        raise ToolError(f'audit.json is not valid JSON: {exc}') from exc
    project = Path(args.project) if args.project else None
    issues, counts = validate_audit(data, run_dir, project, final=args.final)
    result = {'result': 'PASS' if not issues.errors else 'FAIL', 'audit_json': str(audit_path.resolve()),
              'final': args.final, 'errors': issues.errors, 'warnings': issues.warnings, 'counts': counts}
    if args.json:
        print_json(result)
    else:
        print(f'audit.json: {result["result"]} ({len(issues.errors)} errors, {len(issues.warnings)} warnings'
              f'{", final check" if args.final else ", interim state"})')
        for title, items in (('ERRORS', issues.errors), ('WARNINGS', issues.warnings)):
            if items:
                print(title)
                for item in items:
                    print(f'  {item["code"]:<30} {item["where"]}: {item["message"]}')
        print('COUNTS')
        print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0 if not issues.errors else 1


# --------------------------------------------------------------------------- example and selftest

EXAMPLE_SOURCE = (
    'class NoteStore:\n'
    '    def __init__(self):\n'
    '        self._notes = {}\n'
    '\n'
    '    def save(self, note_id, text):\n'
    '        self._notes[note_id] = text  # in memory only\n'
    '        return {"status": "saved"}\n'
)
EXAMPLE_README = '# Notes\n\n## Goal\n\nNotes survive a restart.\n'
EXAMPLE_TEST = 'def test_save_returns_status():\n    assert NoteStore().save(1, "x") == {"status": "saved"}\n'
EXAMPLE_FAIL_LOG = 'test_note_survives_restart FAILED\nAssertionError: Note not found after restart\n1 failed\n'
EXAMPLE_PASS_LOG = 'test_note_survives_restart PASSED\n1 passed\n'
TASK_TEXT = '# {id}\n\nCopyable implementation request: ... Report only `candidate_complete`.\n'


def example_audit(fingerprint: str = '0' * 64) -> dict[str, Any]:
    """A small, internally consistent NO_GO example; contents are fictional."""
    domains = []
    for domain in DOMAINS:
        if domain in ('A', 'F'):
            domains.append({'domain': domain, 'applicability': 'APPLICABLE',
                            'reason': 'Core functionality and persistence of notes', 'check_ids': ['CHK-002']})
        else:
            domains.append({'domain': domain, 'applicability': 'NOT_APPLICABLE',
                            'reason': 'Not present in the fictional mini-project (example).', 'check_ids': []})
    for row in domains:
        if row['domain'] == 'H':
            row.update(applicability='APPLICABLE', reason='Local note input.', check_ids=['CHK-001'])
    return {
        '_note': 'Example structure with fictional contents - use only as a format template.',
        'schema_version': SCHEMA_VERSION, 'prompt_version': PROMPT_VERSION,
        'run_id': '2026-01-01T0000Z_example', 'run_type': 'INITIAL', 'previous_run_ids': [],
        'baseline': {'fingerprint': fingerprint, 'captured_at_utc': '2026-01-01T00:00:00Z', 'head': None,
                     'evidence_id': None},
        'scope': {'in_scope': ['Note app (src/)'], 'out_of_scope': [], 'scope_gaps': [], 'size_profile': 'S'},
        'sources': [{'id': 'SRC-001', 'ref': 'README.md#goal', 'authority': 'Project product description',
                     'excerpt': 'Notes survive a restart.'}],
        'vision': {'status': 'EVIDENCED', 'open_questions': [],
                   'statement': 'The product must enable individual users to store notes permanently.'},
        'objectives': [{'id': 'OBJ-001', 'statement': 'Saved notes survive a restart.',
                        'source_ids': ['SRC-001'], 'fulfillment': 'VIOLATED', 'claim_ids': ['CLM-001']}],
        'invariants': [{'id': 'INV-001', 'statement': 'A confirmed save is readable after a restart.',
                        'source_ids': ['SRC-001']}],
        'environments': [{'id': 'ENV-001', 'kind': 'local isolated working copy', 'authorized': True,
                          'authorization_basis': 'Start request: local checks in an isolated copy', 'used': True}],
        'components': [{'id': 'CMP-001', 'ref': 'src/store.py', 'depth': 'EXECUTED'}],
        'requirements': [{'id': 'REQ-001', 'statement': 'Notes are stored persistently.',
                          'binding': 'BINDING', 'source_ids': ['SRC-001'], 'objective_ids': ['OBJ-001'],
                          'check_ids': ['CHK-002'], 'gap': None}],
        'journeys': [{'id': 'J-001', 'name': 'Create a note and read it after a restart', 'tier': 'T1',
                      'objective_ids': ['OBJ-001'], 'check_ids': ['CHK-002'], 'status': 'VIOLATED', 'gap': None}],
        'domain_matrix': domains,
        'security_review': {'status': 'DONE', 'reason': 'Defined local scope reviewed.',
                            'started_at_utc': '2026-01-01T00:00:00Z',
                            'completed_at_utc': '2026-01-01T00:06:00Z', 'executed_by': 'Synthetic reviewer'},
        'security_scope': [{'id': 'SEC-001', 'ref': 'src/store.py', 'component_ids': ['CMP-001'],
                            'journey_ids': ['J-001'], 'relevance': 'EXTERNAL_INPUT',
                            'reason': 'The note store receives user-supplied text.', 'check_ids': ['CHK-001']}],
        'claims': [{'id': 'CLM-001', 'statement': 'A saved note is readable after a restart.',
                    'basis': 'OBSERVED', 'assessment': 'VIOLATED', 'evidence_ids': ['EV-001'],
                    'counter_evidence_ids': []}],
        'checks': [
            {'id': 'CHK-001', 'title': 'Hypothesis: NoteStore stores in memory only (static)',
             'purpose': 'INVESTIGATION', 'method': 'STATIC', 'required_for_audit': True, 'required_for_release': True,
             'required_reason': 'Cause investigation for REQ-001', 'execution': 'PASS', 'exit_code': None,
             'claim_ids': [], 'evidence_ids': ['EV-002'], 'catch_up_plan': None},
            {'id': 'CHK-002', 'title': 'Save a note, restart the process, read the note',
             'purpose': 'ACCEPTANCE_REGRESSION', 'method': 'INTEGRATION', 'required_for_audit': True,
             'required_for_release': True, 'required_reason': 'Core objective OBJ-001', 'execution': 'FAIL',
             'exit_code': 1, 'claim_ids': ['CLM-001'], 'evidence_ids': ['EV-001'], 'catch_up_plan': None},
        ],
        'evidence': [
            {'id': 'EV-001', 'kind': 'LOG', 'path': 'evidence/logs/CHK-002.log',
             'sha256': sha256_bytes(EXAMPLE_FAIL_LOG.encode('utf-8')), 'captured_at_utc': '2026-01-01T00:05:00Z',
             'freshness': 'CURRENT', 'limits': 'Only a local restart in the working copy.'},
            {'id': 'EV-002', 'kind': 'STATIC_REF', 'ref': 'src/store.py:5-6', 'captured_at_utc': None,
             'freshness': 'CURRENT', 'limits': 'Static evidence; for runtime see EV-001.',
             'code_refs': [{'path': 'src/store.py', 'line_start': 5, 'line_end': 6,
                            'excerpt': 'def save(self, note_id, text):\n        self._notes[note_id] = text'}]},
        ],
        'findings': [{
            'id': 'F-001', 'title': 'Notes are held in memory only', 'type': 'DEFECT', 'category': 'F',
            'assessment': 'CONFIRMED', 'state': 'OPEN', 'severity': 'HIGH', 'priority': 'P1', 'confidence': 'HIGH',
            'release_blocker': True, 'cause_status': 'CONFIRMED', 'claim_ids': ['CLM-001'],
            'target': {'requirement_ids': ['REQ-001']},
            'review': {'reviewer': 'Synthetic independent reviewer', 'method': 'READ_CODE', 'outcome': 'UPHELD'},
            'evidence_ids': ['EV-001', 'EV-002'],
            'code_refs': [{'path': 'src/store.py', 'line_start': 6, 'line_end': 6,
                           'excerpt': 'self._notes[note_id] = text  # in memory only'}],
            'task_ids': ['T-001', 'T-002'], 'file': 'findings/F-001-notes-not-persistent.md'}],
        'tasks': [
            {'id': 'T-001', 'type': 'IMPLEMENT', 'title': 'Persistent storage for NoteStore',
             'finding_ids': ['F-001'], 'depends_on': [], 'wave': 1, 'file': 'tasks/T-001-persistence.md'},
            {'id': 'T-002', 'type': 'VERIFY', 'title': 'Recheck journey J-001 after implementation',
             'finding_ids': ['F-001'], 'depends_on': ['T-001'], 'wave': 2, 'file': 'tasks/T-002-verification.md'},
        ],
        'release_gates': [{'id': 'G-001', 'statement': 'Core journey J-001 fulfills OBJ-001', 'status': 'VIOLATED',
                           'claim_ids': ['CLM-001'], 'finding_ids': ['F-001']}],
        'test_assessments': [{'id': 'TA-001', 'ref': 'tests/test_store.py', 'class': 'WEAK',
                              'reason': 'Checks only the return value, not persistence.',
                              'objective_ids': ['OBJ-001']}],
        'doc_claims': [{'id': 'DC-001', 'source': 'README.md', 'source_ids': ['SRC-001'], 'statement': 'Notes survive a restart.',
                        'verdict': 'FALSE', 'reason': 'Disproven by CHK-002, see F-001.'}],
        'audit_status': 'PARTIAL', 'release_status': 'NO_GO',
        'residual_risks': ['The example models only one journey.'],
        'next_actions': ['Implement T-001, then execute T-002.'],
    }


def example_go_audit(fingerprint: str = '0' * 64) -> dict[str, Any]:
    """The same fictional project after a verified repair: GO for the defined scope."""
    data = example_audit(fingerprint)
    data['run_id'], data['run_type'], data['previous_run_ids'] = ('2026-02-01T0000Z_example', 'REAUDIT',
                                                                  ['2026-01-01T0000Z_example'])
    data['objectives'][0]['fulfillment'] = 'DEMONSTRATED'
    data['journeys'][0]['status'] = 'SUPPORTED'
    data['claims'][0].update(assessment='SUPPORTED', evidence_ids=['EV-003'])
    data['checks'][1].update(execution='PASS', exit_code=0, evidence_ids=['EV-003'])
    data['evidence'][0]['freshness'] = 'STALE'
    data['evidence'].append({'id': 'EV-003', 'kind': 'LOG', 'path': 'evidence/logs/CHK-002-2.log',
                             'sha256': sha256_bytes(EXAMPLE_PASS_LOG.encode('utf-8')),
                             'captured_at_utc': '2026-02-01T00:05:00Z', 'freshness': 'CURRENT',
                             'limits': 'Local restart in the working copy after implementation of T-001.'})
    data['findings'][0].update(state='VERIFIED_FIXED', evidence_ids=['EV-001', 'EV-002', 'EV-003'])
    data['release_gates'][0].update(status='MET', finding_ids=[])
    data['test_assessments'][0].update({'class': 'CARRYING', 'reason': 'Regression test checks restart.'})
    data['doc_claims'][0].update(verdict='TRUE', reason='Evidenced by CHK-002 (EV-003).')
    data['audit_status'], data['release_status'] = 'COMPLETE_FOR_DEFINED_SCOPE', 'GO_FOR_DEFINED_SCOPE'
    data['residual_risks'] = ['Only the local profile was checked.']
    data['next_actions'] = []
    return data


def example_interim_audit(fingerprint: str = '0' * 64) -> dict[str, Any]:
    data = example_go_audit(fingerprint)
    data['security_review'].update(status='PENDING', reason='Deferred review is next.', started_at_utc=None,
                                    completed_at_utc=None, executed_by=None)
    data['checks'][0].update(execution='NOT_RUN', evidence_ids=[], catch_up_plan='P8 security review')
    data['release_gates'][0]['status'] = 'NOT_DEMONSTRATED'
    data['audit_status'], data['release_status'] = 'PARTIAL', 'NOT_DEMONSTRATED'
    data['next_actions'] = ['Verify the interim checkpoint, then continue P8 and P9.']
    return data


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:  # Path.write_text(newline=) needs 3.10
        handle.write(text)


def write_example_run(root: Path, variant: str) -> tuple[Path, Path, dict[str, Any]]:
    """Project, start baseline, run directory and matching audit.json for a positive control."""
    project, run_dir = root / 'project', root / 'run'
    write_text_file(project / 'README.md', EXAMPLE_README)
    write_text_file(project / 'src' / 'store.py', EXAMPLE_SOURCE)
    write_text_file(project / 'tests' / 'test_store.py', EXAMPLE_TEST)
    snap, _ = take_snapshot(project, mode='filesystem', with_side_effects=False)
    stamp = snap['captured_at_utc']
    write_json_new(run_dir / 'evidence' / 'baseline' / 'baseline-start.json', snap)
    for stem, text, code in (('CHK-002', EXAMPLE_FAIL_LOG, 1), ('CHK-002-2', EXAMPLE_PASS_LOG, 0)):
        write_text_file(run_dir / 'evidence' / 'logs' / f'{stem}.log', text)
        write_json_new(run_dir / 'evidence' / 'logs' / f'{stem}.meta.json',
                       {'id': 'CHK-002', 'tool': 'audit_tools.py', 'command': ['synthetic-test'],
                        'started_at_utc': stamp, 'ended_at_utc': stamp,
                        'exit_code': code, 'log_sha256': sha256_bytes(text.encode('utf-8'))})
    data = {'nogo': example_audit, 'go': example_go_audit, 'interim': example_interim_audit}[variant](snap['content_fingerprint'])
    data['baseline']['captured_at_utc'] = stamp
    for item in data['evidence']:
        if item['captured_at_utc'] is not None:
            item['captured_at_utc'] = stamp
    if variant != 'interim':
        data['security_review'].update(started_at_utc=stamp, completed_at_utc=stamp)
    write_text_file(run_dir / 'findings' / 'F-001-notes-not-persistent.md', '# F-001\n\nCategory: F\n')
    write_text_file(run_dir / 'tasks' / 'T-001-persistence.md', TASK_TEXT.format(id='T-001'))
    write_text_file(run_dir / 'tasks' / 'T-002-verification.md', TASK_TEXT.format(id='T-002'))
    write_text_file(run_dir / 'REPORT.md', f'# Report\n\n## Executive summary\n\n{data["audit_status"]}, '
                                           f'{data["release_status"]}\n\n## Security review\n{data["security_review"]["status"]}\n')
    write_text_file(run_dir / 'VISION.md', '# Vision\n\nStatus: EVIDENCED\n')
    write_text_file(run_dir / 'RESUME.md', '# Resume\n')
    write_text_file(run_dir / 'RUN-STATE.json', json.dumps({'run_id': data['run_id'], 'schema_version': SCHEMA_VERSION,
                                                            'prompt_version': PROMPT_VERSION, 'phase': 'P7',
                                                            'updated_at_utc': '2026-01-01T01:00:00Z',
                                                            'authorization': ['Synthetic start request: execute the audit.'],
                                                            'next_steps': []}))
    write_json_new(run_dir / 'audit.json', data)
    return run_dir, project, data


def remove_tree(path: Path) -> None:
    def on_error(function: Callable, target: str, _info: Any) -> None:
        try:
            os.chmod(target, 0o700)
            function(target)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=on_error)
    else:
        shutil.rmtree(path, onerror=on_error)


def entry(data: dict[str, Any], register: str, entry_id: str) -> dict[str, Any]:
    return next(item for item in data[register] if item['id'] == entry_id)


Mutation = Callable[[dict[str, Any], Path, Path], None]


def set_fields(register: str, entry_id: str, **changes: Any) -> Mutation:
    return lambda data, run, project: entry(data, register, entry_id).update(changes)


def chain(*steps: Mutation) -> Mutation:
    def apply(data: dict[str, Any], run: Path, project: Path) -> None:
        for step in steps:
            step(data, run, project)
    return apply


def overwrite(rel: str, text: str, base: str = 'run') -> Mutation:
    return lambda data, run, project: write_text_file((run if base == 'run' else project) / rel, text)


def unlink(rel: str) -> Mutation:
    return lambda data, run, project: (run / rel).unlink()


# Credential-shaped test values, assembled at runtime so this file itself contains no such literal.
FAKE_LIVE_KEY = 'sk_' + 'live_' + 'AUDITSELFTEST' + '0' * 8
FAKE_PEM_HEADER = '-----BEGIN ' + 'PRIVATE KEY-----'
FAKE_PEM_BODY = 'MIIEvQIBADANBgkqhkiG9w0BAQEFAASC' + 'BKcwggSjAgEAAoIBAQ'  # generic PKCS#8 prefix, no key

def open_critical_suspicion(data: dict[str, Any], run: Path, project: Path) -> None:
    data['findings'].append({
        'id': 'F-002', 'title': "Suspicion: other users' notes readable", 'type': 'DEFECT', 'category': 'SECURITY', 'assessment': 'UNCONFIRMED',
        'state': 'OPEN', 'severity': 'CRITICAL', 'priority': 'P0', 'confidence': 'LOW', 'release_blocker': None,
        'cause_status': 'UNKNOWN', 'claim_ids': [], 'evidence_ids': [], 'code_refs': [], 'task_ids': ['T-003'],
        'file': None})
    data['tasks'].append({'id': 'T-003', 'type': 'INVESTIGATE', 'title': 'Investigate suspicion', 'finding_ids': ['F-002'],
                          'depends_on': [], 'wave': 1, 'file': 'tasks/T-003-suspicion.md'})
    write_text_file(run / 'tasks' / 'T-003-suspicion.md', TASK_TEXT.format(id='T-003'))


def log_from_earlier_run(data: dict[str, Any], run: Path, project: Path) -> None:
    """A log copied from a previous run: its metadata predate the start baseline of this run."""
    meta_path = run / 'evidence' / 'logs' / 'CHK-002.meta.json'
    meta = read_json(meta_path)
    meta.update(started_at_utc='2020-01-01T00:00:00Z', ended_at_utc='2020-01-01T00:01:00Z')
    meta_path.write_text(json.dumps(meta), encoding='utf-8')


def observation_only(kind: str = 'NOTE', *, origin: bool = False, stale: bool = False) -> Mutation:
    def mutate(data: dict[str, Any], run: Path, project: Path) -> None:
        text = 'Observed the saved note after reopening the notes view.\n'
        write_text_file(run / 'evidence' / 'observation.txt', text)
        item = {'id': 'EV-OBS', 'kind': kind, 'path': 'evidence/observation.txt',
                'sha256': sha256_bytes(text.encode()), 'captured_at_utc': utc_now(),
                'freshness': 'STALE' if stale else 'CURRENT', 'limits': 'One local observation.'}
        if origin:
            item['observation'] = {'observer': 'Synthetic reviewer', 'description': text.strip()}
        data['evidence'].append(item)
        data['findings'][0].update(evidence_ids=['EV-OBS'], code_refs=[])
        data['checks'][1].update(method='MANUAL', exit_code=None, evidence_ids=['EV-OBS'])
    return mutate


def valid_security_na(data: dict[str, Any], run: Path, project: Path) -> None:
    data['security_scope'] = []
    data['security_review'].update(status='NOT_APPLICABLE', reason='Synthetic fixture has no applicable areas.',
                                   started_at_utc=None, completed_at_utc=utc_now(), executed_by=None)
    next(row for row in data['domain_matrix'] if row['domain'] == 'H').update(
        applicability='NOT_APPLICABLE', reason='No applicable areas in this synthetic variation.', check_ids=[])


def valid_not_performed(data: dict[str, Any], run: Path, project: Path) -> None:
    data['sources'].append({'id': 'SRC-DECISION', 'ref': 'user:StopDecision', 'authority': 'User decision'})
    data['security_review'].update(status='NOT_PERFORMED', reason='User chose closure without the deferred review.',
                                   started_at_utc=None, completed_at_utc=utc_now(), executed_by=None,
                                   decision_source_ids=['SRC-DECISION'], task_ids=['T-002'])


def close_review_run(data: dict[str, Any], run: Path, project: Path) -> None:
    state = read_json(run / 'RUN-STATE.json')
    state.update(phase='P9', closed_at_utc=utc_now())
    write_text_file(run / 'RUN-STATE.json', json.dumps(state))


def superseded_review_run(data: dict[str, Any], run: Path, project: Path) -> None:
    close_review_run(data, run, project)
    successor_id = 'linked-successor'
    state = read_json(run / 'RUN-STATE.json')
    state['superseded_by_run'] = successor_id
    write_text_file(run / 'RUN-STATE.json', json.dumps(state))
    write_json_new(run.parent / successor_id / 'RUN-STATE.json', {
        'run_id': successor_id, 'schema_version': SCHEMA_VERSION, 'run_type': 'REAUDIT',
        'previous_run_ids': [data['run_id']]})


def initialized_supersession(data: dict[str, Any], run: Path, project: Path, link: str = 'audit') -> None:
    """Successor metadata from the real init CLI, with an optional persisted backlink."""
    code, output = run_cli('init', '--project', str(project), '--results-dir', str(run.parent), '--mode', 'filesystem')
    if code != 0:
        raise ToolError('Synthetic supersession init failed: ' + output)
    successor = Path(json.loads(output)['run_dir'])
    successor_state = read_json(successor / 'RUN-STATE.json')
    assert successor_state['previous_run_ids'] == []
    if link == 'audit':
        write_json_new(successor / 'audit.json', {'run_id': successor.name, 'schema_version': SCHEMA_VERSION,
                                                'previous_run_ids': [data['run_id']]})
    elif link == 'state':
        successor_state['previous_run_ids'] = [data['run_id']]
        write_text_file(successor / 'RUN-STATE.json', json.dumps(successor_state))
    state = read_json(run / 'RUN-STATE.json')
    state.update(closed_at_utc=utc_now(), superseded_by_run=successor.name)
    write_text_file(run / 'RUN-STATE.json', json.dumps(state))


def contradict_successor(field: str, value: str, file: str = 'RUN-STATE.json') -> Mutation:
    def mutate(data: dict[str, Any], run: Path, project: Path) -> None:
        successor = run.parent / read_json(run / 'RUN-STATE.json')['superseded_by_run']
        record = read_json(successor / file)
        record[field] = value
        write_text_file(successor / file, json.dumps(record))
    return mutate


def release_control_defect(data: dict[str, Any], run: Path, project: Path) -> None:
    data['findings'][0].update(type='TEST_QUALITY', release_control={
        'requirement_ids': ['REQ-001'], 'gate_ids': ['G-001'], 'evidence_ids': ['EV-001'],
        'malfunction': 'Synthetic delivered release gate certifies the demonstrated broken persistence path.'})


def pending_security(data: dict[str, Any], run: Path, project: Path) -> None:
    data['security_review'].update(status='PENDING', reason='Review stopped; resume required.',
                                   started_at_utc=None, completed_at_utc=None, executed_by=None)


def started_security(data: dict[str, Any], run: Path, project: Path) -> None:
    data['security_review'].update(started_at_utc=data['baseline']['captured_at_utc'], executed_by='Synthetic reviewer')


def optional_security_check(data: dict[str, Any], run: Path, project: Path) -> None:
    check = copy.deepcopy(EXTRA_REQUIRED_CHECK)
    check.update(purpose='INVESTIGATION', required_for_audit=False, required_for_release=False, security=True)
    data['checks'].append(check)
    next(row for row in data['domain_matrix'] if row['domain'] == 'H')['check_ids'].append(check['id'])


def cleanup_without_target(data: dict[str, Any], run: Path, project: Path) -> None:
    data['findings'][0].update(type='CLEANUP', target=None, release_blocker=False, severity='LOW', priority='P3')
    data['release_gates'][0]['status'] = 'NOT_DEMONSTRATED'
    data['release_status'] = 'NOT_DEMONSTRATED'
    write_text_file(run / 'REPORT.md', '# Executive summary\nPARTIAL NOT_DEMONSTRATED\n## Security review\nDONE\n')


def offset_observation(data: dict[str, Any], run: Path, project: Path) -> None:
    observation_only('UI', origin=True)(data, run, project)
    data['evidence'][-1]['captured_at_utc'] = utc_now().replace('Z', '+00:00')


def offset_log(data: dict[str, Any], run: Path, project: Path) -> None:
    from datetime import timedelta
    stamp = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-2))).isoformat()
    data['evidence'][0]['captured_at_utc'] = stamp
    path = run / 'evidence/logs/CHK-002.meta.json'
    meta = read_json(path)
    meta.update(started_at_utc=stamp, ended_at_utc=stamp)
    write_text_file(path, json.dumps(meta))
    data['findings'][0].update(code_refs=[], evidence_ids=['EV-001'])


WORKFLOW_POSITIVE_CONTROLS: list[tuple[str, str, Mutation | None]] = [
    ('VALID_SUPERSESSION_INIT_AUDIT_LINK', 'interim', initialized_supersession),
    ('VALID_SUPERSESSION_INIT_STATE_LINK', 'interim',
        lambda d, r, p: initialized_supersession(d, r, p, 'state')),
    ('VALID_SUPERSEDED_PENDING_RUN', 'interim', superseded_review_run),
    ('VALID_SUPERSEDED_IN_PROGRESS_RUN', 'interim', chain(superseded_review_run, started_security,
        lambda d, r, p: d['security_review'].update(status='IN_PROGRESS'))),
    ('PENDING_AFTER_STARTED_REVIEW', 'interim', started_security),
    ('NOT_PERFORMED_AFTER_REFUSAL', 'interim', chain(valid_not_performed, started_security,
        lambda d, r, p: write_text_file(r / 'RUN-STATE.json', json.dumps({
            **read_json(r / 'RUN-STATE.json'), 'closed_at_utc': utc_now()})))),
    ('DONE_WITH_OPTIONAL_DOMAIN_H_CHECK', 'go', optional_security_check),
    ('SECURITY_HEADING_NUMBERED', 'nogo', overwrite('REPORT.md', '# Executive summary\nPARTIAL NO_GO\n## 8. Security review\n')),
    ('SECURITY_HEADING_SUFFIX_CASE', 'nogo', overwrite('REPORT.md', '# Executive summary\nPARTIAL NO_GO\n## security REVIEW (interim)\n')),
    ('UTC_OFFSET_OBSERVATION', 'nogo', offset_observation),
    ('UTC_COMPACT_OFFSET_OBSERVATION', 'nogo', chain(offset_observation,
        lambda d, r, p: d['evidence'][-1].update(captured_at_utc=utc_now().replace('Z', '+0000')))),
    ('UTC_NONZERO_OFFSET_LOG', 'nogo', offset_log),
    ('EVIDENCE_DOT_PATH', 'nogo', set_fields('evidence', 'EV-001', path='./evidence/logs/CHK-002.log')),
    ('EVIDENCE_BACKSLASH_PATH', 'nogo', set_fields('evidence', 'EV-001', path='evidence\\logs\\CHK-002.log')),
    ('LOW_CLEANUP_WITHOUT_TARGET', 'nogo', cleanup_without_target),
]

WORKFLOW_NEGATIVE_CONTROLS: list[tuple[str, str, Mutation, str]] = [
    ('CLOSED_SECURITY_INIT_UNLINKED_SUCCESSOR', 'interim',
        lambda d, r, p: initialized_supersession(d, r, p, 'none'), 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_SUCCESSOR_STATE_ID', 'interim', chain(initialized_supersession,
        contradict_successor('run_id', 'different-successor')), 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_SUCCESSOR_STATE_SCHEMA', 'interim', chain(initialized_supersession,
        contradict_successor('schema_version', 'unsupported-schema')), 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_SUCCESSOR_AUDIT_ID', 'interim', chain(initialized_supersession,
        contradict_successor('run_id', 'different-successor', 'audit.json')), 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_SUCCESSOR_AUDIT_SCHEMA', 'interim', chain(initialized_supersession,
        contradict_successor('schema_version', 'unsupported-schema', 'audit.json')), 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_PENDING', 'interim', close_review_run, 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_IN_PROGRESS', 'interim', chain(close_review_run, started_security,
        lambda d, r, p: d['security_review'].update(status='IN_PROGRESS')), 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_FAKE_SUPERSESSION', 'interim', chain(close_review_run,
        lambda d, r, p: write_text_file(r / 'RUN-STATE.json', json.dumps({
            **read_json(r / 'RUN-STATE.json'), 'superseded_by_run': 'missing-successor'}))), 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_SELF_SUPERSESSION', 'interim', chain(close_review_run,
        lambda d, r, p: write_text_file(r / 'RUN-STATE.json', json.dumps({
            **read_json(r / 'RUN-STATE.json'), 'superseded_by_run': d['run_id']}))), 'E_RUN_SECURITY_CLOSURE'),
    ('CLOSED_SECURITY_UNLINKED_SUCCESSOR', 'interim', chain(superseded_review_run,
        lambda d, r, p: write_text_file(r.parent / 'linked-successor/RUN-STATE.json', json.dumps({
            'run_id': 'linked-successor', 'schema_version': SCHEMA_VERSION, 'previous_run_ids': []}))), 'E_RUN_SECURITY_CLOSURE'),
    ('NOT_PERFORMED_PROJECT_DECISION', 'interim', chain(valid_not_performed,
        lambda d, r, p: d['security_review'].update(decision_source_ids=['SRC-001'])), 'E_SECURITY_NOT_PERFORMED'),
    ('NOT_PERFORMED_MIXED_DECISIONS', 'interim', chain(valid_not_performed,
        lambda d, r, p: d['security_review'].update(decision_source_ids=['SRC-DECISION', 'SRC-001'])), 'E_SECURITY_NOT_PERFORMED'),
    ('FINDING_CATEGORY_MISSING', 'nogo', lambda d, r, p: d['findings'][0].pop('category'), 'E_FINDING_CATEGORY'),
    ('FINDING_CATEGORY_UNKNOWN', 'nogo', set_fields('findings', 'F-001', category='INVENTED'), 'E_FINDING_CATEGORY'),
    ('FINDING_CATEGORY_H', 'nogo', set_fields('findings', 'F-001', category='H'), 'E_FINDING_CATEGORY'),
    ('FINDING_CATEGORY_NULL', 'nogo', set_fields('findings', 'F-001', category=None), 'E_FINDING_CATEGORY'),
    ('FINDING_CATEGORY_NON_STRING', 'nogo', set_fields('findings', 'F-001', category=['SECURITY']), 'E_FINDING_CATEGORY'),
    ('PENDING_START_WITHOUT_EXECUTOR', 'interim', lambda d, r, p: d['security_review'].update(
        started_at_utc=d['baseline']['captured_at_utc']), 'E_SECURITY_REVIEW'),
    ('NOT_PERFORMED_EXECUTOR_WITHOUT_START', 'interim', chain(valid_not_performed,
        lambda d, r, p: d['security_review'].update(executed_by='Synthetic reviewer')), 'E_SECURITY_REVIEW'),
    ('SECURITY_NA_DOMAIN_H_APPLICABLE', 'interim', chain(valid_security_na, lambda d, r, p:
        next(row for row in d['domain_matrix'] if row['domain'] == 'H').update(applicability='APPLICABLE')), 'E_SECURITY_NA_REASON'),
    ('NOT_PERFORMED_DECISION_WITHOUT_TASK', 'interim', chain(valid_not_performed,
        lambda d, r, p: d['security_review'].update(task_ids=[])), 'E_SECURITY_NOT_PERFORMED'),
]


CORE2_CONTROLS: list[tuple[str, str, Mutation, str]] = [
    ('SECURITY_REVIEW_MISSING', 'go', lambda d, r, p: d.pop('security_review', None), 'E_SECURITY_REVIEW'),
    ('SECURITY_REVIEW_MALFORMED', 'go', lambda d, r, p: d.update(security_review=[]), 'E_SECURITY_REVIEW'),
    ('SECURITY_SCOPE_MALFORMED', 'go', lambda d, r, p: d.update(security_scope=None), 'E_SECURITY_SCOPE'),
    ('SECURITY_STATUS_UNKNOWN', 'go', lambda d, r, p: d['security_review'].update(status='UNKNOWN'), 'E_SECURITY_REVIEW'),
    ('SECURITY_TIMESTAMPS_INVALID', 'go', lambda d, r, p: d['security_review'].update(completed_at_utc=None), 'E_SECURITY_REVIEW'),
    ('SECURITY_TIMESTAMPS_MALFORMED_DATE', 'go', lambda d, r, p: d['security_review'].update(started_at_utc='2026-01-01Z'), 'E_SECURITY_REVIEW'),
    ('SECURITY_SCOPE_MISSING', 'go', lambda d, r, p: d.pop('security_scope', None), 'E_SECURITY_SCOPE'),
    ('SECURITY_SCOPE_INVALID', 'go', lambda d, r, p: d['security_scope'][0].update(relevance='GUESS'), 'E_SECURITY_SCOPE'),
    ('SECURITY_SCOPE_NO_MANDATORY_CHECK', 'go', lambda d, r, p: d['security_scope'][0].update(check_ids=[]), 'E_SECURITY_SCOPE'),
    ('SECURITY_DONE_UNEXECUTED', 'go', set_fields('checks', 'CHK-001', execution='NOT_RUN', catch_up_plan='Resume review'), 'E_SECURITY_DONE_CHECKS'),
    ('SECURITY_DONE_UNQUALIFIED', 'go', set_fields('checks', 'CHK-001', evidence_ids=[]), 'E_SECURITY_DONE_CHECKS'),
    ('SECURITY_DONE_DOMAIN_CHECK_OPEN', 'go', lambda d, r, p: (d['checks'].append(copy.deepcopy(EXTRA_REQUIRED_CHECK)),
        next(row for row in d['domain_matrix'] if row['domain'] == 'H')['check_ids'].append('CHK-004')), 'E_SECURITY_DONE_CHECKS'),
    ('SECURITY_NA_WITH_SCOPE', 'go', lambda d, r, p: d['security_review'].update(status='NOT_APPLICABLE'), 'E_SECURITY_NA_REASON'),
    ('SECURITY_NOT_PERFORMED_NO_DECISION', 'go', lambda d, r, p: d['security_review'].update(status='NOT_PERFORMED'), 'E_SECURITY_NOT_PERFORMED'),
    ('SECURITY_NOT_PERFORMED_REQUIRES_PARTIAL', 'interim', chain(valid_not_performed,
        lambda d, r, p: d.update(audit_status='BLOCKED')), 'E_SECURITY_NOT_PERFORMED'),
    ('GO_SECURITY_PENDING', 'go', pending_security, 'E_GO_SECURITY_PENDING'),
    ('COMPLETE_SECURITY_PENDING', 'go', pending_security, 'E_COMPLETE_SECURITY_PENDING'),
    ('GAP_AS_BLOCKER', 'nogo', set_fields('findings', 'F-001', type='MISSING_EVIDENCE'), 'E_GAP_AS_BLOCKER'),
    ('CLEANUP_AS_BLOCKER', 'nogo', set_fields('findings', 'F-001', type='CLEANUP'), 'E_GAP_AS_BLOCKER'),
    ('TEST_QUALITY_AS_BLOCKER', 'nogo', set_fields('findings', 'F-001', type='TEST_QUALITY'), 'E_GAP_AS_BLOCKER'),
    ('RELEASE_CONTROL_MALFUNCTION_NOT_TEXT', 'nogo', chain(release_control_defect,
        lambda d, r, p: d['findings'][0]['release_control'].update(malfunction=['claimed'])), 'E_GAP_AS_BLOCKER'),
    ('BLOCKER_CONFIDENCE_MEDIUM', 'nogo', set_fields('findings', 'F-001', confidence='MEDIUM'), 'E_BLOCKER_CONFIDENCE'),
    ('FINDING_REVIEW_MISSING', 'nogo', set_fields('findings', 'F-001', review=None), 'E_FINDING_REVIEW'),
    ('FINDING_REVIEW_UNAVAILABLE_NO_REASON', 'nogo', lambda d, r, p: d['findings'][0]['review'].update(
        outcome='UNAVAILABLE', method='COMMENT_ONLY'), 'E_FINDING_REVIEW'),
    ('FINDING_REVIEW_UNAVAILABLE_REASON_NOT_TEXT', 'nogo', lambda d, r, p: d['findings'][0]['review'].update(
        outcome='UNAVAILABLE', method='COMMENT_ONLY', reason=['unavailable']), 'E_FINDING_REVIEW'),
    ('FINDING_REVIEW_DISPUTED_NO_COUNTERCHECK', 'nogo', lambda d, r, p: d['findings'][0]['review'].update(outcome='DISPUTED'), 'E_FINDING_REVIEW'),
    ('FINDING_REVIEW_INVALID_METHOD', 'nogo', lambda d, r, p: d['findings'][0]['review'].update(method='INVENTED'), 'E_FINDING_REVIEW'),
    ('FINDING_REVIEW_INVALID_OUTCOME', 'nogo', lambda d, r, p: d['findings'][0]['review'].update(outcome='INVENTED'), 'E_FINDING_REVIEW'),
    ('UNSOURCED_REQUIREMENT', 'nogo', set_fields('requirements', 'REQ-001', source_ids=[]), 'E_UNSOURCED_REQUIREMENT'),
    ('UNSOURCED_OBJECTIVE', 'nogo', set_fields('objectives', 'OBJ-001', source_ids=[]), 'E_UNSOURCED_OBJECTIVE'),
    ('UNSOURCED_INVARIANT', 'nogo', set_fields('invariants', 'INV-001', source_ids=[]), 'E_UNSOURCED_INVARIANT'),
    ('FINDING_TARGET_MISSING', 'nogo', set_fields('findings', 'F-001', target=None), 'E_FINDING_TARGET'),
    ('FINDING_TARGET_UNSOURCED', 'nogo', chain(set_fields('requirements', 'REQ-001', source_ids=[])), 'E_FINDING_TARGET'),
    ('FINDING_TARGET_NECESSITY_INCOMPLETE', 'nogo', set_fields('findings', 'F-001', target={'necessity': {'actor': 'user'}}), 'E_FINDING_TARGET'),
    ('FINDING_TARGET_DANGLING_REF', 'nogo', set_fields('findings', 'F-001', target={'requirement_ids': ['REQ-NONE']}), 'E_REF'),
    ('CONFIRMED_ON_HANDWRITTEN_NOTE', 'nogo', observation_only(), 'E_FINDING_EVIDENCE'),
    ('CONFIRMED_FORGED_VERIFICATION_FLAG', 'nogo', set_fields('findings', 'F-001',
        code_refs=[], evidence_ids=[], _verified_refs=True), 'E_FINDING_EVIDENCE'),
    ('CONFIRMED_ON_STALE_OBSERVATION', 'nogo', observation_only('UI', origin=True, stale=True), 'E_FINDING_EVIDENCE'),
    ('CONFIRMED_ON_UNSUPPORTED_UI_LABEL', 'nogo', observation_only('UI'), 'E_FINDING_EVIDENCE'),
    ('CONFIRMED_ON_PREDATED_UI', 'nogo', chain(observation_only('UI', origin=True),
        set_fields('evidence', 'EV-OBS', captured_at_utc='2020-01-01T00:00:00Z')), 'E_FINDING_EVIDENCE'),
    ('STATIC_FINDING_WITHOUT_BASELINE', 'nogo', chain(set_fields('findings', 'F-001', evidence_ids=[]),
        unlink('evidence/baseline/baseline-start.json')), 'E_FINDING_EVIDENCE'),
    ('CONFIRMED_ON_LOG_WITHOUT_ORIGIN', 'nogo', chain(set_fields('findings', 'F-001', evidence_ids=['EV-001'], code_refs=[]),
        lambda d, r, p: write_text_file(r / 'evidence/logs/CHK-002.meta.json', json.dumps({
            'log_sha256': d['evidence'][0]['sha256'], 'exit_code': 1}))), 'E_FINDING_EVIDENCE'),
    ('COMPLETE_T1_ON_MANUAL_NOTE', 'go', observation_only(), 'E_COMPLETE_T1'),
    ('REPORT_SECURITY_SECTION_MISSING', 'nogo', overwrite('REPORT.md', '# Executive summary\nPARTIAL NO_GO\n'), 'E_FINAL_CONTENT'),
    ('REPORT_SECURITY_ONLY_IN_PROSE', 'nogo', overwrite('REPORT.md', '# Executive summary\nPARTIAL NO_GO\nSecurity review pending.\n'), 'E_FINAL_CONTENT'),
]


def run_state_authorization(value: Any, missing: bool = False) -> Mutation:
    def mutate(data: dict[str, Any], run: Path, project: Path) -> None:
        path = run / 'RUN-STATE.json'
        state = read_json(path)
        if missing:
            state.pop('authorization', None)
        else:
            state['authorization'] = value
        write_text_file(path, json.dumps(state))
    return mutate


def large_secret_result(data: dict[str, Any], run: Path, project: Path) -> None:
    with open(run / 'evidence' / 'large.txt', 'wb') as handle:
        # Beyond the old size limit, and straddling a scanner block boundary.
        handle.write(b'!' * (8192 + 80 * 65536 - 10))
        handle.write(FAKE_LIVE_KEY.encode())


EXTRA_REQUIRED_CHECK = {'id': 'CHK-004', 'title': 'Restore test', 'purpose': 'ACCEPTANCE_REGRESSION',
                        'method': 'INTEGRATION', 'required_for_audit': True, 'required_for_release': False,
                        'required_reason': 'Backup is promised', 'execution': 'NOT_RUN', 'exit_code': None,
                        'claim_ids': [], 'evidence_ids': [], 'catch_up_plan': 'Execute restore in the working copy.'}

VALIDATOR_CONTROLS: list[tuple[str, str, Mutation, str]] = [
    ('EVIDENCE_FILE_MISSING', 'nogo', unlink('evidence/logs/CHK-002.log'), 'E_EVIDENCE_MISSING'),
    ('EVIDENCE_HASH_MISMATCH', 'nogo', overwrite('evidence/logs/CHK-002.log', 'changed afterwards\n'),
     'E_EVIDENCE_HASH'),
    ('HAND_WRITTEN_LOG_WITHOUT_META', 'nogo', unlink('evidence/logs/CHK-002.meta.json'), 'E_LOG_META_MISSING'),
    ('EXIT_CODE_CONTRADICTS_LOG', 'nogo', set_fields('checks', 'CHK-002', exit_code=0), 'E_EXIT_MISMATCH'),
    ('EVIDENCE_OUTSIDE_EVIDENCE_DIR', 'nogo', set_fields('evidence', 'EV-001', path='REPORT.md'), 'E_EVIDENCE_PATH'),
    ('CODE_REF_FABRICATED', 'nogo',
     lambda d, r, p: d['findings'][0]['code_refs'][0].update(excerpt='self._db.commit(transaction=True)'), 'E_CODE_REF'),
    ('CODE_REF_TRIVIAL_EXCERPT', 'nogo',
     lambda d, r, p: d['findings'][0]['code_refs'][0].update(line_start=1, line_end=7, excerpt='return'),
     'E_CODE_REF_TRIVIAL'),
    ('CODE_REF_LOOSE_RANGE', 'nogo', lambda d, r, p: d['findings'][0]['code_refs'][0].update(line_start=5, line_end=7),
     'E_CODE_REF'),
    ('CODE_REF_OUTSIDE_BASELINE', 'nogo',
     chain(overwrite('evidence/notes.txt', 'self._notes[note_id] = text  # in memory only\n'),
           lambda d, r, p: d['findings'][0]['code_refs'][0].update(path='../run/evidence/notes.txt', line_start=1,
                                                                    line_end=1)), 'E_CODE_REF_NOT_IN_BASELINE'),
    ('CODE_REF_STALE_FILE', 'nogo', overwrite('src/store.py', EXAMPLE_SOURCE + '# changed\n', base='project'),
     'E_CODE_REF_STALE'),
    ('SECRET_IN_CODE_REF_EXCERPT', 'nogo',
     lambda d, r, p: d['findings'][0]['code_refs'][0].update(excerpt=f'STRIPE_SECRET_KEY = "{FAKE_LIVE_KEY}"'),
     'E_SECRET_IN_EXCERPT'),
    ('SECRET_IN_RESULT_FILE', 'nogo', overwrite('evidence/config-copy.txt', f'{FAKE_PEM_HEADER}\n{FAKE_PEM_BODY}\n'),
     'E_SECRET_IN_RESULTS'),
    ('SECRET_IN_UTF16_RESULT_FILE', 'nogo',
     lambda d, r, p: (r / 'evidence' / 'utf16.txt').write_bytes(FAKE_LIVE_KEY.encode('utf-16')),
     'E_SECRET_IN_RESULTS'),
    ('SECRET_IN_LARGE_RESULT_FILE', 'nogo', large_secret_result, 'E_SECRET_IN_RESULTS'),
    ('FINAL_AUTHORIZATION_MISSING', 'nogo', run_state_authorization(None, missing=True), 'E_RUN_AUTHORIZATION'),
    ('FINAL_AUTHORIZATION_NULL', 'nogo', run_state_authorization(None), 'E_RUN_AUTHORIZATION'),
    ('FINAL_AUTHORIZATION_BLANK_ENTRY', 'nogo', run_state_authorization(['']), 'E_RUN_AUTHORIZATION'),
    ('FINAL_AUTHORIZATION_NOT_LIST', 'nogo', run_state_authorization(True), 'E_RUN_AUTHORIZATION'),
    ('FINAL_AUTHORIZATION_BLANK', 'nogo', run_state_authorization(' \t\n'), 'E_RUN_AUTHORIZATION'),
    ('FABRICATED_SOURCE_EXCERPT', 'nogo',
     set_fields('sources', 'SRC-001', excerpt='Notes are synchronized with end-to-end encryption.'),
     'E_SOURCE_EXCERPT'),
    ('SOURCE_CHANGED_AFTER_BASELINE', 'nogo', overwrite('README.md', EXAMPLE_README + '\nLater addition.\n',
                                                        base='project'), 'E_SOURCE_STALE'),
    ('NONEXISTENT_PATH_REFERENCE', 'nogo', set_fields('components', 'CMP-001', ref='src/payment/service.py'),
     'E_PATH_REF'),
    ('BASELINE_FINGERPRINT_MISMATCH', 'nogo',
     lambda d, r, p: d['baseline'].update(fingerprint='f' * 64), 'E_BASELINE_MISMATCH'),
    ('DANGLING_REFERENCE', 'nogo', set_fields('findings', 'F-001', claim_ids=['CLM-999']), 'E_REF'),
    ('MALFORMED_NESTED_REFERENCE', 'nogo', set_fields('findings', 'F-001', task_ids=[['T-001']]), 'E_TYPE'),
    ('DUPLICATE_ID', 'nogo', lambda d, r, p: d['claims'].append(copy.deepcopy(d['claims'][0])), 'E_DUP_ID'),
    ('DOMAIN_MISSING', 'nogo',
     lambda d, r, p: d.update(domain_matrix=[x for x in d['domain_matrix'] if x['domain'] != 'S']), 'E_DOMAIN'),
    ('TASK_CYCLE', 'nogo', set_fields('tasks', 'T-001', depends_on=['T-002']), 'E_TASK_CYCLE'),
    ('STATIC_CHECK_WITH_EXIT_CODE', 'nogo', set_fields('checks', 'CHK-001', exit_code=0), 'E_STATIC_EXIT'),
    ('REQUIRED_NULL_WITHOUT_REASON', 'nogo',
     set_fields('checks', 'CHK-001', required_for_release=None, required_reason=''), 'E_REQUIRED_NULL'),
    ('REQUIRED_CHECK_DOWNGRADED', 'nogo', set_fields('checks', 'CHK-002', required_for_release=False),
     'E_REQUIRED_MISSING'),
    ('INVALID_ENUM', 'nogo', set_fields('findings', 'F-001', severity='SEVERE'), 'E_ENUM'),
    ('CONFIRMED_WITHOUT_EVIDENCE', 'nogo', set_fields('findings', 'F-001', evidence_ids=[], code_refs=[]),
     'E_FINDING_EVIDENCE'),
    ('CONFIRMED_ON_UNVERIFIABLE_REF', 'nogo',
     chain(lambda d, r, p: d['evidence'].append({'id': 'EV-009', 'kind': 'NOTE', 'ref': 'src/payment/x.py:999-1004',
                                                 'captured_at_utc': None, 'freshness': 'CURRENT', 'limits': ''}),
           set_fields('findings', 'F-001', evidence_ids=['EV-009'], code_refs=[])), 'E_FINDING_EVIDENCE'),
    ('CONFIRMED_OPEN_WITHOUT_TASK', 'nogo', set_fields('findings', 'F-001', task_ids=[]), 'E_FINDING_TASK'),
    ('EXECUTED_CHECK_WITHOUT_EVIDENCE', 'nogo', set_fields('checks', 'CHK-002', evidence_ids=[]), 'E_CHECK_EVIDENCE'),
    ('BLOCKED_WITHOUT_ATTEMPT', 'nogo',
     set_fields('checks', 'CHK-001', execution='BLOCKED', evidence_ids=[], catch_up_plan='Request access'),
     'E_BLOCKED_UNEVIDENCED'),
    ('T1_JOURNEY_UNTRACED', 'nogo', set_fields('journeys', 'J-001', check_ids=[], gap=None), 'E_JOURNEY_TRACE'),
    ('CORE_JOURNEY_DOWNGRADED', 'nogo', set_fields('journeys', 'J-001', tier='T3'), 'E_TIER_DOWNGRADE'),
    ('AUTHORIZED_ENVIRONMENT_UNUSED', 'nogo', set_fields('environments', 'ENV-001', used=False), 'E_ENV_UNUSED'),
    ('UNSOURCED_EXCLUSION', 'nogo',
     lambda d, r, p: d['requirements'].append({'id': 'REQ-002', 'statement': 'Export', 'binding': 'EXCLUDED',
                                               'source_ids': [], 'check_ids': [], 'gap': None}),
     'E_UNSOURCED_EXCLUSION'),
    ('FIXED_IN_INITIAL_RUN', 'nogo', set_fields('findings', 'F-001', state='CANDIDATE_FIXED'), 'E_FIXED_IN_INITIAL'),
    ('NO_GO_WITHOUT_BLOCKER', 'nogo',
     chain(set_fields('findings', 'F-001', release_blocker=False),
           set_fields('release_gates', 'G-001', status='NOT_DEMONSTRATED')), 'E_NO_GO_UNFOUNDED'),
    ('DISCOVERABLE_TEST_FILE', 'nogo', overwrite('evidence/audit-tests/test_persistence.py', 'def test_x(): pass\n'),
     'E_DISCOVERABLE_FILE'),
    ('PLACEHOLDER_REPORT', 'nogo', overwrite('REPORT.md', '# Report\n'), 'E_FINAL_CONTENT'),
    ('EMPTY_REPORT', 'nogo', overwrite('REPORT.md', ''), 'E_FINAL_CONTENT'),
    ('EMPTY_RESUME', 'nogo', overwrite('RESUME.md', ''), 'E_FINAL_CONTENT'),
    ('BLANK_RESUME', 'nogo', overwrite('RESUME.md', ' \t\n'), 'E_FINAL_CONTENT'),
    ('EMPTY_TASK_FILE', 'nogo', overwrite('tasks/T-001-persistence.md', ''), 'E_FINAL_CONTENT'),
    ('GO_WITH_VIOLATED_T1_JOURNEY', 'go', set_fields('journeys', 'J-001', status='VIOLATED'), 'E_GO_JOURNEY'),
    ('GO_WITH_OPEN_CRITICAL_SUSPICION', 'go', open_critical_suspicion, 'E_GO_OPEN_SUSPICION'),
    ('LOG_PREDATES_BASELINE', 'nogo', log_from_earlier_run, 'E_EVIDENCE_PREDATES_BASELINE'),
    ('GO_WITH_OPEN_BLOCKER', 'go', set_fields('findings', 'F-001', state='OPEN'), 'E_NO_GO_REQUIRED'),
    ('GO_WITH_STALE_EVIDENCE', 'go', set_fields('evidence', 'EV-003', freshness='STALE'), 'E_GO_STALE'),
    ('GO_ON_BUG_REPRODUCTION_ONLY', 'go', set_fields('checks', 'CHK-002', purpose='BUG_REPRODUCTION'), 'E_GO_SUPPORT'),
    ('GO_WITH_UNDEMONSTRATED_OBJECTIVE', 'go', set_fields('objectives', 'OBJ-001', fulfillment='NOT_DEMONSTRATED'),
     'E_GO_OBJECTIVE'),
    ('GO_WITHOUT_CRITICAL_OBJECTIVE', 'go', lambda d, r, p: d.update(objectives=[]), 'E_GO_NO_OBJECTIVE'),
    ('GO_WITH_PARTIAL_AUDIT', 'go', lambda d, r, p: d.update(audit_status='PARTIAL'), 'E_GO_INCOMPLETE'),
    ('GO_WITH_OPEN_SEVERE_NON_BLOCKER', 'go',
     set_fields('findings', 'F-001', state='OPEN', release_blocker=False, severity='CRITICAL', priority='P0',
                release_blocker_reason='noncritical from the agent viewpoint'), 'E_GO_OPEN_SEVERE'),
    ('GO_WITH_SELF_ACCEPTED_RISK', 'go',
     set_fields('findings', 'F-001', state='ACCEPTED_RISK', acceptance={'authorized_by': 'Auditor', 'scope': 'everything'}),
     'E_ACCEPTED_RISK'),
    ('COMPLETE_WITH_REQUIRED_GAP', 'go', lambda d, r, p: d['checks'].append(copy.deepcopy(EXTRA_REQUIRED_CHECK)),
     'E_COMPLETE_GAP'),
    ('COMPLETE_WITH_STATIC_ONLY_T1', 'go', set_fields('checks', 'CHK-002', method='STATIC', exit_code=None),
     'E_COMPLETE_T1'),
]


def legit_variations(data: dict[str, Any], run: Path, project: Path) -> None:
    """Valid conventions that must not be rejected (guards against false alarms of the validator)."""
    data['sources'].extend([
        {'id': 'SRC-002', 'ref': 'run:VISION.md#checkpoint', 'authority': 'User response at the vision checkpoint'},
        {'id': 'SRC-003', 'ref': 'user:StartRequest', 'authority': 'User start request'}])
    data['test_assessments'][0]['ref'] = 'tests/test_store.py::test_save_returns_status'
    for rel in ('evidence/audit-tests/test_persistence.py.txt', 'evidence/audit-tests/playwright.config.ts.txt',
                'evidence/audit-tests/persistence.patch'):
        write_text_file(run / rel, 'inert\n')
    # A secret location described without its value, and an obvious placeholder key, must not be rejected.
    write_text_file(run / 'evidence' / 'secret-location.txt',
                    'src/config.py:3 STRIPE_SECRET_KEY = "sk_live_[REDACTED]"\n'
                    'docker-compose.yml:12 postgres://app:[REDACTED]@localhost:5432/app\n'
                    'tests/test_redact.py:8 sample = "' + 'sk-' + 'proj-' + 'abcdefghijklmnopqrstuvwxyz0123456789"\n')
    log = 'Found 0 errors.\n'
    write_text_file(run / 'evidence' / 'logs' / 'CHK-003.log', log)
    write_json_new(run / 'evidence' / 'logs' / 'CHK-003.meta.json',
                   {'id': 'CHK-003', 'exit_code': 0, 'log_sha256': sha256_bytes(log.encode('utf-8'))})
    data['evidence'].append({'id': 'EV-004', 'kind': 'LOG', 'path': 'evidence/logs/CHK-003.log',
                             'sha256': sha256_bytes(log.encode('utf-8')), 'captured_at_utc': '2026-01-01T00:06:00Z',
                             'freshness': 'CURRENT', 'limits': 'Type check in the working copy'})
    data['checks'].append({'id': 'CHK-003', 'title': 'Type check', 'purpose': 'CHARACTERIZATION', 'method': 'STATIC',
                           'required_for_audit': False, 'required_for_release': False, 'required_reason': 'Additional',
                           'execution': 'PASS', 'exit_code': 0, 'claim_ids': [], 'evidence_ids': ['EV-004'],
                           'catch_up_plan': None})


def refuted_required_investigation(data: dict[str, Any], run: Path, project: Path) -> None:
    data['checks'].append({'id': 'CHK-005', 'title': 'Hypothesis: notes of other users readable (disproven)',
                           'purpose': 'INVESTIGATION', 'method': 'STATIC', 'required_for_audit': True,
                           'required_for_release': True, 'required_reason': 'Data privacy', 'execution': 'FAIL',
                           'exit_code': None, 'claim_ids': [], 'evidence_ids': ['EV-002'], 'catch_up_plan': None})


POSITIVE_CONTROLS: list[tuple[str, str, Mutation | None]] = [
    ('VALID_SECURITY_FINDING_CATEGORY', 'nogo', set_fields('findings', 'F-001', category='SECURITY')),
    ('VALID_NO_GO_EXAMPLE', 'nogo', None),
    ('VALID_GO_EXAMPLE', 'go', None),
    ('LEGIT_CONVENTIONS_ACCEPTED', 'nogo', legit_variations),
    ('GO_WITH_REFUTED_REQUIRED_INVESTIGATION', 'go', refuted_required_investigation),
    ('VALID_INTERIM_PENDING_EXAMPLE', 'interim', None),
    ('STATIC_FINDING_VERIFIED_CODE_REFS', 'nogo', set_fields('findings', 'F-001', evidence_ids=[])),
    ('GENUINE_MANUAL_UI_OBSERVATION', 'nogo', observation_only('UI', origin=True)),
    ('RUN_BACKED_OBSERVATION', 'nogo', set_fields('findings', 'F-001', code_refs=[], evidence_ids=['EV-001'])),
    ('RELEASE_CONTROL_MALFUNCTION_BLOCKER', 'nogo', release_control_defect),
    ('VALID_SECURITY_NOT_APPLICABLE', 'go', valid_security_na),
    ('VALID_SECURITY_NOT_PERFORMED', 'interim', valid_not_performed),
    ('VALID_REVIEW_UNAVAILABLE', 'nogo', lambda d, r, p: d['findings'][0]['review'].update(
        method='COMMENT_ONLY', outcome='UNAVAILABLE', reason='No independent reviewer available.')),
    ('VALID_REVIEW_DISPUTED_COUNTERCHECK', 'nogo', lambda d, r, p: d['findings'][0]['review'].update(
        outcome='DISPUTED', countercheck_evidence_ids=['EV-001'])),
    ('VALID_NECESSITY_TARGET', 'nogo', set_fields('findings', 'F-001', target={
        'necessity': {'actor': 'Local user', 'reachable_path': 'save then restart', 'harm': 'Persisted note lost'}})),
    ('VALID_DOC_CLAIM_TARGET', 'nogo', set_fields('findings', 'F-001', target={'doc_claim_ids': ['DC-001']})),
]


def run_validator_controls(root: Path) -> list[dict[str, Any]]:
    results = []
    for control_id, variant, mutation in POSITIVE_CONTROLS + WORKFLOW_POSITIVE_CONTROLS:
        run_dir, project, data = write_example_run(root / control_id.lower(), variant)
        if mutation is not None:
            mutation(data, run_dir, project)
        issues, _ = validate_audit(data, run_dir, project, final=True)
        results.append({'id': control_id, 'kind': 'POSITIVE_CONTROL', 'result': 'PASS' if not issues.errors else 'FAIL',
                        'observed_errors': sorted(issues.codes()), 'warnings': sorted({w['code'] for w in issues.warnings})})
    for control_id, variant, mutation, expected in VALIDATOR_CONTROLS + CORE2_CONTROLS + WORKFLOW_NEGATIVE_CONTROLS:
        run_dir, project, data = write_example_run(root / control_id.lower(), variant)
        mutation(data, run_dir, project)
        try:
            issues, _ = validate_audit(data, run_dir, project, final=True)
            observed = sorted(issues.codes())
        except Exception as exc:  # a crash is a failed control, not a pass
            observed = [f'CRASH:{exc.__class__.__name__}']
        results.append({'id': control_id, 'kind': 'NEGATIVE_CONTROL', 'expected_rejection': expected,
                        'result': 'PASS' if expected in observed else 'FAIL', 'observed_errors': observed})
    run_dir, project, data = write_example_run(root / 'bom', 'nogo')
    (run_dir / 'audit.json').write_bytes(b'\xef\xbb\xbf' + json.dumps(data).encode('utf-8'))
    code, _ = run_cli('validate', str(run_dir), '--project', str(project), '--final')
    results.append({'id': 'VALIDATE_ACCEPTS_UTF8_BOM', 'kind': 'TOOL_CONTROL', 'result': 'PASS' if code == 0 else 'FAIL'})
    workspace = root / 'ws-as-project'
    write_example_run(workspace, 'nogo')
    write_text_file(workspace / 'project' / WORKSPACE_MARKER, '{}')
    run_dir, project, data = workspace / 'run', workspace / 'project', example_audit()
    issues, _ = validate_audit(data, run_dir, project, final=True)
    results.append({'id': 'PROJECT_IS_WORKSPACE', 'kind': 'NEGATIVE_CONTROL', 'expected_rejection': 'E_PROJECT_IS_WORKSPACE',
                    'result': 'PASS' if 'E_PROJECT_IS_WORKSPACE' in issues.codes() else 'FAIL',
                    'observed_errors': sorted(issues.codes())})
    return results


def run_cli(*arguments: str, env: dict[str, str] | None = None, timeout: float = 180) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *arguments], env=env, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode('utf-8', 'replace')


def run_core2_controls(root: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for link in ('audit', 'state', 'none'):
        run, project, data = write_example_run(root / ('init-supersession-' + link), 'interim')
        initialized_supersession(data, run, project, link)
        write_text_file(run / 'audit.json', json.dumps(data))
        info = describe_run(run, inspect_live=False)
        expected = 'OPEN' if link == 'none' else 'CLOSED'
        control(results, 'INIT_SUPERSESSION_' + link.upper() + '_DISCOVERY',
                info is not None and info['status'] == expected)
    for status in ('PENDING', 'IN_PROGRESS', 'SUPERSEDED', 'DONE'):
        closed, original, closed_data = write_example_run(root / ('closed-' + status.lower()),
                                                        'go' if status == 'DONE' else 'interim')
        (superseded_review_run if status == 'SUPERSEDED' else close_review_run)(closed_data, closed, original)
        if status == 'IN_PROGRESS':
            started_security(closed_data, closed, original)
            closed_data['security_review']['status'] = status
        write_text_file(closed / 'audit.json', json.dumps(closed_data))
        expected = 'OPEN' if status in ('PENDING', 'IN_PROGRESS') else 'CLOSED'
        for inspect in (False, True):
            info = describe_run(closed, inspect_live=inspect)
            control(results, 'CLOSED_REVIEW_' + status + ('_INSPECTED' if inspect else '_LISTED'),
                    info is not None and info['status'] == expected)
    run, project, data = write_example_run(root / 'checkpoint', 'interim')
    write_text_file(run / 'findings/F-001-notes-not-persistent.md', '# F-001\n[Details](../evidence/detail.txt)\n')
    write_text_file(run / 'evidence/detail.txt', 'Referenced detail.\n')
    created = create_checkpoint(run, project)
    snapshot = run / 'checkpoints/interim/files'
    control(results, 'CHECKPOINT_ROUNDTRIP_REFERENCES', created['state'] == 'VALID'
            and (snapshot / 'evidence/detail.txt').is_file()
            and (snapshot / 'evidence/logs/CHK-002-2.meta.json').is_file())
    try:
        create_checkpoint(run, project)
        refused = False
    except ToolError:
        refused = True
    control(results, 'CHECKPOINT_REFUSES_OVERWRITE', refused)
    write_text_file(run / 'audit.json', '{interrupted')
    info = describe_run(run)
    control(results, 'INTERRUPTED_LIVE_STATE_CHECKPOINT_GUIDANCE', info is not None
            and info['checkpoint']['state'] == 'VALID' and info['live_state'] == 'INVALID_OR_INCOMPLETE'
            and 'Offer the verified interim checkpoint' in info['resume_guidance'])
    write_text_file(run / 'audit.json', json.dumps({'schema_version': SCHEMA_VERSION}))
    info = describe_run(run)
    control(results, 'INVALID_LIVE_STATE_CHECKPOINT_GUIDANCE', info is not None
            and 'Offer the verified interim checkpoint' in info['resume_guidance'])
    write_text_file(snapshot / 'REPORT.md', 'tampered\n')
    control(results, 'CHECKPOINT_TAMPER_REJECTED', verify_checkpoint(run)['state'] == 'INVALID')
    run2, project2, data2 = write_example_run(root / 'checkpoint-inventory', 'interim')
    create_checkpoint(run2, project2)
    manifest_path = run2 / 'checkpoints/interim/manifest.json'
    manifest = read_json(manifest_path)
    manifest['sha256'].pop('tasks/T-001-persistence.md')
    write_text_file(manifest_path, json.dumps(manifest))
    control(results, 'CHECKPOINT_INVENTORY_ENFORCED', verify_checkpoint(run2)['state'] == 'INVALID')
    run3, project3, _ = write_example_run(root / 'checkpoint-invalid', 'go')
    try:
        create_checkpoint(run3, project3)
        refused = False
    except ToolError:
        refused = True
    control(results, 'CHECKPOINT_REQUIRES_INTERIM_PENDING', refused)
    invalid, invalid_project, _ = write_example_run(root / 'checkpoint-final-invalid', 'interim')
    write_text_file(invalid / 'REPORT.md', '')
    try:
        create_checkpoint(invalid, invalid_project)
        refused = False
    except ToolError:
        refused = True
    control(results, 'CHECKPOINT_REQUIRES_FINAL_VALIDATION', refused)
    incompatible = {'schema_version': 'audit-core/1'}
    issues, counts = validate_audit(incompatible, root / 'absent', None, final=True)
    control(results, 'SCHEMA_MISMATCH_CHECKED_FIRST', [e['code'] for e in issues.errors] == ['E_SCHEMA_VERSION']
            and not issues.warnings and counts == {})
    legacy = root / 'discover' / 'old-open'
    write_json_new(legacy / 'audit.json', incompatible)
    write_json_new(legacy / 'RUN-STATE.json', {'prompt_version': PROMPT_VERSION, 'closed_at_utc': None})
    code, output = run_cli('runs', '--results-dir', str(legacy.parent))
    listing = json_head(output)
    old = next((r for r in listing.get('runs', []) if r['dir'] == 'old-open'), {})
    control(results, 'INCOMPATIBLE_OPEN_RUN_DISCOVERABLE', code == 0 and old.get('status') == 'OPEN'
            and old.get('resumable_with_package') is False and 'new linked' in old.get('resume_guidance', '')
            and listing.get('latest_resumable') is None)
    data2['prompt_version'] = 'different-prompt'
    issues, _ = validate_audit(data2, run2, project2, final=True)
    control(results, 'COMPATIBLE_PROMPT_DRIFT_WARNING_ONLY', not issues.errors
            and 'W_PROMPT_VERSION' in {w['code'] for w in issues.warnings})
    run4, project4, data4 = write_example_run(root / 'counters', 'nogo')
    open_critical_suspicion(data4, run4, project4)
    _, counts = validate_audit(data4, run4, project4, final=True)
    control(results, 'CONFIRMED_AND_UNCONFIRMED_OPEN_COUNTS', counts['findings']['confirmed_open'] == {'HIGH': 1}
            and counts['findings']['unconfirmed_open'] == 1)
    data4['findings'][0].update(target=None, review=None)
    issues, _ = validate_audit(data4, run4, project4, final=False)
    control(results, 'FINDING_TARGET_REVIEW_SOFT_UNTIL_FINAL', not issues.errors
            and {'W_FINDING_TARGET', 'W_FINDING_REVIEW'} <= {w['code'] for w in issues.warnings})
    return results


def report_reference(text: str) -> Mutation:
    return lambda data, run, project: write_text_file(run / 'REPORT.md',
        (run / 'REPORT.md').read_text(encoding='utf-8') + '\n' + text + '\n')


def run_checkpoint_workflow_controls(root: Path) -> list[dict[str, Any]]:
    import contextlib
    import io
    from unittest.mock import patch
    results: list[dict[str, Any]] = []
    cases = [
        ('CHECKPOINT_IGNORES_EXTENSION_PATH', lambda d, r, p: d['components'][0].update(path='src/store.py'),
         None, None, None),
        ('CHECKPOINT_NORMALIZES_QUALIFIED_TRAVERSAL', chain(
            overwrite('evidence/only.txt', 'Current-run observation.\n'),
            lambda d, r, p: d['previous_run_ids'].append('prior-run'),
            lambda d, r, p: d['sources'].append({'id': 'SRC-TRAVERSAL',
                'ref': 'run:prior-run/../evidence/only.txt', 'authority': 'Current run'})),
         None, None, 'evidence/only.txt'),
        ('CHECKPOINT_NORMALIZES_ENCODED_TRAVERSAL', chain(
            overwrite('evidence/only.txt', 'Current-run observation.\n'),
            lambda d, r, p: d['previous_run_ids'].append('prior-run'),
            lambda d, r, p: d['sources'].append({'id': 'SRC-TRAVERSAL',
                'ref': 'run:prior-run%5C..%5Cevidence%2Fonly.txt', 'authority': 'Current run'})),
         None, None, 'evidence/only.txt'),
        ('CHECKPOINT_PREVIOUS_RUN_REFERENCE', chain(
            lambda d, r, p: d['previous_run_ids'].append('prior-run'),
            lambda d, r, p: d['sources'].append({'id': 'SRC-PRIOR', 'ref': 'run:prior-run/VISION.md', 'authority': 'Prior run'})),
         'run:prior-run/VISION.md', 'previous-run reference', None),
        ('CHECKPOINT_PROJECT_SOURCE_LINK', report_reference('[Source](../project/src/store.py)'),
         '../project/src/store.py', 'outside current run', None),
        ('CHECKPOINT_DIRECTORY_LINK', chain(lambda d, r, p: (r / 'empty-directory').mkdir(),
            report_reference('[Directory](empty-directory)')), 'empty-directory', 'directory', None),
        ('CHECKPOINT_URL_ENCODED_LINK', chain(overwrite('evidence/capture one.txt', 'Captured observation.\n'),
            report_reference('[Capture](evidence/capture%20one.txt)')), None, None, 'evidence/capture one.txt'),
        ('CHECKPOINT_FENCED_PLACEHOLDER', report_reference(
            '```text\n[Template](missing-template.txt)\n```\n~~~text\n[Template](missing-template.txt)\n~~~'), None, None, None),
        ('CHECKPOINT_INLINE_PLACEHOLDER', report_reference('Example: `[Template](missing-template.txt)`.'), None, None, None),
        ('CHECKPOINT_ABSOLUTE_PATH', lambda d, r, p: report_reference(f'[Absolute]({(r / "VISION.md").resolve().as_posix()})')(d, r, p),
         None, 'absolute path', None),
        ('CHECKPOINT_ENCODED_ABSOLUTE_PATH', lambda d, r, p: report_reference('[Absolute](' +
            (r / 'VISION.md').resolve().as_posix().replace(':', '%3A').replace('/', '%2F') + ')')(d, r, p),
         None, 'absolute path', None),
        ('CHECKPOINT_URL_REFERENCE', report_reference('[Reference](https://example.invalid/reference)'),
         'https://example.invalid/reference', 'URL or external scheme', None),
        ('CHECKPOINT_CURRENT_QUALIFIED_REFERENCE', lambda d, r, p: d['sources'].append(
            {'id': 'SRC-CURRENT', 'ref': f'run:{d["run_id"]}/VISION.md', 'authority': 'Current run'}), None, None, 'VISION.md'),
        ('CHECKPOINT_DOT_EVIDENCE_PATH', set_fields('evidence', 'EV-003', path='./evidence/logs/CHK-002-2.log'),
         None, None, 'evidence/logs/CHK-002-2.log'),
        ('CHECKPOINT_BACKSLASH_EVIDENCE_PATH', set_fields('evidence', 'EV-003', path='evidence\\logs\\CHK-002-2.log'),
         None, None, 'evidence/logs/CHK-002-2.log'),
    ]
    for control_id, mutation, reference, reason, stored in cases:
        run, project, data = write_example_run(root / control_id.lower(), 'interim')
        mutation(data, run, project)
        write_text_file(run / 'audit.json', json.dumps(data))
        try:
            result = create_checkpoint(run, project)
            manifest = read_json(run / 'checkpoints/interim/manifest.json')
            omitted = manifest['not_snapshotted']
            ok = result['state'] == 'VALID' and verify_checkpoint(run)['state'] == 'VALID'
            if reason is not None:
                ok = ok and any(item['reason'] == reason and (reference is None or item['reference'] == reference) for item in omitted)
            if stored is not None:
                ok = ok and stored in manifest['sha256']
            if control_id == 'CHECKPOINT_DIRECTORY_LINK':
                ok = ok and (run / 'checkpoints/interim/files/empty-directory').is_dir()
            if 'PLACEHOLDER' in control_id:
                ok = ok and not any('missing-template' in item['reference'] for item in omitted)
            control(results, control_id, ok, result)
        except Exception as exc:
            control(results, control_id, False, str(exc))

    run, project, _ = write_example_run(root / 'missing-local', 'interim')
    report_reference('[Missing](evidence/missing-file.txt)')({}, run, project)
    try:
        create_checkpoint(run, project)
        missing_rejected = False
    except ToolError as exc:
        missing_rejected = 'missing inside current run' in str(exc)
    control(results, 'CHECKPOINT_MISSING_IN_RUN_REJECTED', missing_rejected)

    for failure in ('validation', 'copy'):
        run, project, _ = write_example_run(root / ('failed-' + failure), 'interim')
        if failure == 'validation':
            write_text_file(run / 'REPORT.md', '')
        failed = False
        try:
            if failure == 'copy':
                with patch.object(shutil, 'copyfile', side_effect=OSError('Synthetic copy failure')):
                    create_checkpoint(run, project)
            else:
                create_checkpoint(run, project)
        except (ToolError, OSError):
            failed = True
        control(results, 'CHECKPOINT_CLEANS_FAILED_' + failure.upper(), failed
                and not list((run / 'checkpoints').glob('.interim-*')) and not (run / 'checkpoints/interim').exists())

    run, project, data = write_example_run(root / 'live-progress', 'interim')
    create_checkpoint(run, project)
    data['findings'][0].update(state='OPEN', review=None)
    data['release_status'] = 'NO_GO'
    data['release_gates'][0].update(status='VIOLATED', finding_ids=['F-001'])
    state = read_json(run / 'RUN-STATE.json')
    state['phase'] = 'P8'
    write_text_file(run / 'RUN-STATE.json', json.dumps(state))
    write_text_file(run / 'audit.json', json.dumps(data))
    info = describe_run(run)
    control(results, 'MID_P8_UNREVIEWED_FINDING_CONTINUES', info is not None and info['live_state'] == 'VALID'
            and 'Continue' in info['resume_guidance'] and 'Offer' not in info['resume_guidance'])
    write_text_file(project / 'src/store.py', EXAMPLE_SOURCE + '# changed since checkpoint\n')
    info = describe_run(run)
    control(results, 'PROJECT_DRIFT_NEVER_RESTORES_CHECKPOINT', info is not None
            and 'project changed since checkpoint: follow re-audit rules' in info['resume_guidance']
            and 'Offer' not in info['resume_guidance'])

    run, project, data = write_example_run(root / 'pre-interim', 'interim')
    create_checkpoint(run, project)
    state = read_json(run / 'RUN-STATE.json')
    state['phase'] = 'P2'
    write_text_file(run / 'RUN-STATE.json', json.dumps(state))
    write_text_file(run / 'audit.json', json.dumps({'schema_version': SCHEMA_VERSION}))
    info = describe_run(run)
    control(results, 'PRE_P7_CONTINUES_WITHOUT_RESTORE', info is not None
            and 'Continue' in info['resume_guidance'] and 'Offer' not in info['resume_guidance'])
    state['phase'] = 'P8'
    write_text_file(run / 'RUN-STATE.json', json.dumps(state))
    data['schema_version'] = 'broken-schema'
    write_text_file(run / 'audit.json', json.dumps(data))
    info = describe_run(run)
    control(results, 'SCHEMA_INVALID_LIVE_CHECKPOINT_RESTORE', info is not None
            and 'Offer the verified interim checkpoint' in info['resume_guidance'])

    run, project, data = write_example_run(root / 'archive-scan', 'interim')
    issues_before, counts_before = validate_audit(data, run, project, final=True)
    write_text_file(run / 'checkpoints/interim/files/copied.py', FAKE_LIVE_KEY)
    write_text_file(run / 'checkpoints/.interim-interrupted/files/staging.py', FAKE_LIVE_KEY)
    issues_after, counts_after = validate_audit(data, run, project, final=True)
    control(results, 'VALIDATE_IGNORES_CHECKPOINT_COPIES_AND_STAGING', not issues_before.errors
            and not issues_after.errors and counts_before == counts_after)

    run, project, data = write_example_run(root / 'original-checkpoint', 'interim')
    create_checkpoint(run, project)
    other, _, other_data = write_example_run(root / 'other-run', 'interim')
    other_data['run_id'] = 'different-run'
    state = read_json(other / 'RUN-STATE.json')
    state['run_id'] = other_data['run_id']
    write_text_file(other / 'RUN-STATE.json', json.dumps(state))
    write_text_file(other / 'audit.json', json.dumps(other_data))
    shutil.copytree(run / 'checkpoints', other / 'checkpoints')
    control(results, 'CHECKPOINT_FROM_OTHER_RUN_REJECTED', verify_checkpoint(other)['state'] == 'INVALID')
    for name in ('RUN-STATE.json', 'audit.json'):
        live = read_json(run / name)
        changed = dict(live, run_id='different-run')
        write_text_file(run / name, json.dumps(changed))
        control(results, 'CHECKPOINT_BINDS_' + ('RUN_STATE' if name == 'RUN-STATE.json' else 'AUDIT_ID'),
                verify_checkpoint(run)['state'] == 'INVALID')
        write_text_file(run / name, json.dumps(live))

    run, project, data = write_example_run(root / 'null-security-duty', 'go')
    extra = copy.deepcopy(EXTRA_REQUIRED_CHECK)
    extra.update(required_for_audit=None, required_for_release=None, security=True)
    data['checks'].append(extra)
    issues, _ = validate_audit(data, run, project, final=True)
    control(results, 'NULL_SECURITY_OBLIGATIONS_USE_EXISTING_ERRORS', issues.codes() == {
        'E_COMPLETE_REQUIRED_NULL', 'E_GO_REQUIRED_NULL'})
    stamp = utc_stamp('2026-01-01T03:00:00+03:00')
    control(results, 'UTC_STAMP_NORMALIZES_OFFSET', stamp is not None and stamp.isoformat() == '2026-01-01T00:00:00+00:00')
    control(results, 'CANONICAL_EVIDENCE_PATH_RESOLUTION', canonical_run_path('./evidence\\capture.txt') == 'evidence/capture.txt')

    run, project, data = write_example_run(root / 'pending-only', 'go')
    pending_security(data, run, project)
    issues, _ = validate_audit(data, run, project, final=True)
    control(results, 'GO_PENDING_ONLY_SECURITY_VERDICT_ERRORS', issues.codes() == {
        'E_GO_SECURITY_PENDING', 'E_COMPLETE_SECURITY_PENDING'})

    results_dir = root / 'performance-runs'
    for index in range(4):
        run, _, _ = write_example_run(root / f'performance-fixture-{index}', 'interim')
        state = read_json(run / 'RUN-STATE.json')
        state.update(created_at_utc=f'2026-01-0{index + 1}T00:00:00Z', closed_at_utc='2026-01-05T00:00:00Z' if index < 2 else None)
        write_text_file(run / 'RUN-STATE.json', json.dumps(state))
        shutil.copytree(run, results_dir / f'run-{index}')
    for inspect_all in (False, True):
        with patch(__name__ + '.validate_audit', wraps=validate_audit) as validation, patch(
                __name__ + '.verify_checkpoint', wraps=verify_checkpoint) as verification, contextlib.redirect_stdout(io.StringIO()):
            cmd_runs(argparse.Namespace(project=str(root), results_dir=str(results_dir), inspect_all=inspect_all))
        expected_count = 4 if inspect_all else 1
        control(results, 'RUNS_INSPECTS_ALL_ON_REQUEST' if inspect_all else 'RUNS_INSPECTS_ONLY_LATEST_OPEN',
                validation.call_count == expected_count and verification.call_count == expected_count
                and all(call.kwargs.get('final') is False for call in validation.call_args_list))
    return results


def run_recovery_integrity_controls(root: Path) -> list[dict[str, Any]]:
    from unittest.mock import patch
    results: list[dict[str, Any]] = []
    for kind in ('dangling-ref', 'record-field', 'record-type', 'malformed', 'non-object', 'top-field-type', 'both-unreadable'):
        run, project, data = write_example_run(root / kind, 'interim')
        create_checkpoint(run, project)
        state = read_json(run / 'RUN-STATE.json')
        state['phase'] = 'P8'
        write_text_file(run / 'RUN-STATE.json', json.dumps(state))
        if kind == 'dangling-ref':
            data['findings'][0]['task_ids'].append('T-MISSING')
        elif kind == 'record-field':
            data['findings'][0].pop('title')
        elif kind == 'record-type':
            data['findings'][0]['task_ids'] = [['T-001']]
        elif kind == 'top-field-type':
            data['residual_risks'] = 'interrupted list'
        write_text_file(run / 'audit.json', '{interrupted' if kind in ('malformed', 'both-unreadable')
                        else '[]' if kind == 'non-object' else json.dumps(data))
        if kind == 'both-unreadable':
            write_text_file(run / 'RUN-STATE.json', '{interrupted')
        info = describe_run(run) or {}
        guidance = info.get('resume_guidance', '')
        if kind in ('dangling-ref', 'record-field', 'record-type'):
            expected = {'dangling-ref': 'E_REF', 'record-field': 'E_MISSING_FIELD', 'record-type': 'E_TYPE'}[kind]
            control(results, 'MID_P8_' + kind.replace('-', '_').upper() + '_REPAIR',
                    info.get('live_validation_errors') == [expected] and 'repair live state, then revalidate' in guidance.lower()
                    and 'Offer' not in guidance, info)
        else:
            restore = ('Offer the verified interim checkpoint' in guidance
                       and 'restoring discards all progress since the interim checkpoint' in guidance.lower())
            control_id = {'malformed': 'MALFORMED_RESTORE_DISCARD_WARNING', 'non-object': 'TOP_LEVEL_TYPE_RESTORE_DISCARD_WARNING',
                          'top-field-type': 'TOP_LEVEL_FIELD_TYPE_RESTORE_DISCARD_WARNING',
                          'both-unreadable': 'CORRUPT_RUN_RECOVERY_CONSISTENT'}[kind]
            control(results, control_id,
                    restore and info.get('checkpoint', {}).get('state') == 'VALID'
                    and (kind != 'both-unreadable' or info.get('status') == 'CORRUPT'
                         and info.get('live_state') == 'CORRUPT' and info.get('resumable_with_package') is True), info)
        if kind == 'both-unreadable':
            results_dir = root / 'listing'
            shutil.copytree(run, results_dir / 'corrupt')
            code, output = run_cli('runs', '--results-dir', str(results_dir))
            listing = json_head(output)
            sole = next(iter(listing.get('runs', [])), {})
            control(results, 'RUNS_RECOVERS_SOLE_CORRUPT_RUN', code == 0 and sole.get('status') == 'CORRUPT'
                    and sole.get('resumable_with_package') is True and listing.get('latest_resumable') == 'corrupt'
                    and 'restoring discards all progress since the interim checkpoint' in sole.get('resume_guidance', '').lower(), sole)
            other, _, _ = write_example_run(root / 'other-open', 'interim')
            shutil.copytree(other, results_dir / 'open')
            code, output = run_cli('runs', '--results-dir', str(results_dir))
            listing = json_head(output)
            corrupt = next((r for r in listing.get('runs', []) if r['dir'] == 'corrupt'), {})
            control(results, 'RUNS_LISTS_CORRUPT_WITH_OTHER_OPEN', code == 0 and len(listing.get('runs', [])) == 2
                    and corrupt.get('status') == 'CORRUPT' and corrupt.get('resumable_with_package') is not False
                    and 'Cannot resume' not in corrupt.get('resume_guidance', ''), corrupt)

    for reason in ('directory', 'previous-run reference'):
        run, project, _ = write_example_run(root / reason.replace(' ', '-'), 'interim')
        write_text_file(run / 'evidence/only.txt', 'Current-run observation.\n')
        report_reference('[Observation](evidence/only.txt)')({}, run, project)
        create_checkpoint(run, project)
        directory = run / 'checkpoints/interim'
        manifest = read_json(directory / 'manifest.json')
        # Reference-collection mutants can omit this file before the relabelling control.
        (directory / 'files/evidence/only.txt').unlink(missing_ok=True)
        manifest['sha256'].pop('evidence/only.txt', None)
        manifest['not_snapshotted'].append({'source': 'REPORT.md', 'reference': 'evidence/only.txt', 'reason': reason})
        manifest['not_snapshotted'].sort(key=lambda item: (item['source'], item['reference'], item['reason']))
        write_text_file(directory / 'manifest.json', json.dumps(manifest))
        control(results, 'CHECKPOINT_RELABELLED_' + ('DIRECTORY' if reason == 'directory' else 'PREVIOUS_RUN') + '_INVALID',
                verify_checkpoint(run)['state'] == 'INVALID')

    for location, suffix in (('files/extra.txt', 'SNAPSHOT_FILE'), ('extra.txt', 'ENVELOPE_FILE'),
                             ('../extra.txt', 'ROOT_FILE'), ('empty-directory', 'DIRECTORY')):
        run, project, _ = write_example_run(root / location.replace('/', '-'), 'interim')
        create_checkpoint(run, project)
        if suffix == 'DIRECTORY':
            (run / 'checkpoints/interim' / location).mkdir()
        else:
            write_text_file(run / 'checkpoints/interim' / location, 'Added after creation.\n')
        control(results, 'CHECKPOINT_EXTRA_' + suffix + '_INVALID',
                verify_checkpoint(run)['state'] == 'INVALID')

    native_datetime = datetime

    class Python310Datetime(datetime):
        @classmethod
        def fromisoformat(cls, value: str) -> datetime:
            if re.search(r'[+-]\d{4}$', value):
                raise ValueError('Python 3.10 requires a colon in UTC offsets.')
            return native_datetime.fromisoformat(value)

    # Exercise the 3.10 parser boundary even when selftest runs on a newer interpreter.
    for portable in (False, True):
        def stamps() -> bool:
            return all(utc_stamp(value) == datetime(2026, 1, 1, tzinfo=timezone.utc) for value in (
                '2026-01-01T00:00:00+0000', '2026-01-01T03:00:00+0300', '2025-12-31T21:30:00-0230'))
        if portable:
            with patch(__name__ + '.datetime', Python310Datetime):
                ok = stamps()
        else:
            ok = stamps()
        control(results, 'UTC_COMPACT_OFFSET_' + ('PY310_BOUNDARY' if portable else 'NATIVE'), ok)
    return results


def run_checkpoint_input_controls(root: Path) -> list[dict[str, Any]]:
    import contextlib
    import io
    from unittest.mock import patch
    results: list[dict[str, Any]] = []
    cases = [
        ('CHECKPOINT_NULL_TEST_ASSESSMENTS', lambda d: d.update(test_assessments=None)),
        ('CHECKPOINT_NULL_DOC_CLAIMS', lambda d: d.update(doc_claims=None)),
        ('CHECKPOINT_NULL_EVIDENCE_CODE_REFS', lambda d: d['evidence'][0].update(code_refs=None)),
        ('CHECKPOINT_NULL_FINDING_CODE_REFS', lambda d: d['findings'][0].update(code_refs=None)),
    ]
    for control_id, mutate in cases:
        run, project, data = write_example_run(root / control_id.lower(), 'interim')
        mutate(data)
        write_text_file(run / 'audit.json', json.dumps(data))
        issues, _ = validate_audit(data, run, project, final=True)
        try:
            result = create_checkpoint(run, project)
            ok = not issues.errors and result['state'] == 'VALID'
            detail = {'validation_errors': sorted(issues.codes()), 'checkpoint': result}
        except (ToolError, TypeError, KeyError) as exc:
            ok, detail = False, str(exc)
        control(results, control_id, ok, detail)

    run, _, _ = write_example_run(root / 'cli-errors', 'interim')
    for error_type in (TypeError, KeyError):
        stderr = io.StringIO()
        try:
            with patch(__name__ + '.create_checkpoint', side_effect=error_type('Synthetic malformed checkpoint input')), contextlib.redirect_stderr(stderr):
                code = main(['checkpoint', 'create', str(run)])
            ok = code == 2 and 'Checkpoint input unreadable' in stderr.getvalue() and 'Traceback' not in stderr.getvalue()
        except (TypeError, KeyError):
            ok = False
        control(results, 'CHECKPOINT_CLI_' + error_type.__name__.upper() + '_CLEAN_ERROR', ok, stderr.getvalue())

    for checkpoint in ('ABSENT', 'INVALID'):
        run, _, _ = write_example_run(root / ('malformed-' + checkpoint.lower()), 'interim')
        state = read_json(run / 'RUN-STATE.json')
        state['phase'] = 'P8' if checkpoint == 'ABSENT' else 'P9'
        write_text_file(run / 'RUN-STATE.json', json.dumps(state))
        write_text_file(run / 'audit.json', '{interrupted')
        if checkpoint == 'INVALID':
            write_text_file(run / 'checkpoints/interim/manifest.json', '{}')
        info = describe_run(run) or {}
        control(results, 'MALFORMED_LIVE_' + checkpoint + '_CHECKPOINT_REPAIR', info.get('checkpoint', {}).get('state') == checkpoint
                and 'Repair live state, then revalidate' in info.get('resume_guidance', '')
                and 'Offer' not in info.get('resume_guidance', ''), info)

    for saved in (False, True):
        run, project, _ = write_example_run(root / ('staging-saved' if saved else 'staging-first'), 'interim')
        if saved:
            create_checkpoint(run, project)
        staging = run / 'checkpoints/.interim-interrupted'
        write_text_file(staging / 'files/extra.txt', 'Interrupted copy.\n')
        if saved:
            control(results, 'CHECKPOINT_IGNORES_LEFTOVER_STAGING', verify_checkpoint(run)['state'] == 'VALID')
        try:
            create_checkpoint(run, project)
            refused, detail = False, 'Creation was not refused.'
        except ToolError as exc:
            detail = str(exc)
            refused = str(staging) in detail and 'remove' in detail.lower()
        control(results, 'CHECKPOINT_CREATE_REFUSES_LEFTOVER_STAGING' if saved else 'CHECKPOINT_CREATE_REFUSES_STAGING_BEFORE_FIRST_SNAPSHOT',
                refused and (staging / 'files/extra.txt').read_text() == 'Interrupted copy.\n'
                and (run / 'checkpoints/interim').exists() is saved, detail)
        if saved:
            write_text_file(run / 'checkpoints/interim/files/REPORT.md', 'Tampered checkpoint.\n')
            control(results, 'CHECKPOINT_STAGING_DOES_NOT_MASK_TAMPER', verify_checkpoint(run)['state'] == 'INVALID')
    return results


def json_head(output: str) -> dict[str, Any]:
    """The JSON summary printed before the log tail."""
    text = output.split('\n--- last', 1)[0]
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def wait_running_info(path: Path, timeout: float = 20) -> dict[str, Any]:
    """Selftest: a marker can exist before its writer has flushed the JSON."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            info = read_json(path)
            if isinstance(info, dict) and info:
                return info
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    return {}


def control(results: list[dict[str, Any]], control_id: str, ok: bool, detail: Any = None) -> None:
    results.append({'id': control_id, 'kind': 'TOOL_CONTROL', 'result': 'PASS' if ok else 'FAIL', 'detail': detail})


def same_file(found: Any, expected: Any) -> bool:
    try:
        return bool(found) and os.path.samefile(found, expected)
    except OSError:
        return False


def cleanup_link_control_ok(linked: bool, cleanup_ok: bool) -> bool:
    return linked and cleanup_ok


def run_tool_controls(root: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    workspace, evidence = root / 'ws', root / 'run' / 'evidence' / 'logs'
    write_text_file(workspace / WORKSPACE_MARKER, '{}')
    write_text_file(root / 'run' / 'RUN-STATE.json', '{}')
    python = sys.executable

    code, output = run_cli('run', '--id', 'R-REFUSE', '--evidence-dir', str(evidence), '--cwd', str(root), '--',
                           python, '-c', 'open("written.txt", "w").write("x")')
    control(results, 'RUN_REFUSES_OUTSIDE_WORKSPACE', code == 2 and not (root / 'written.txt').exists(), code)

    secret = 'AuditSelftestSecret-9f2c'
    env = {**os.environ, 'AUDIT_SELFTEST_API_KEY': 'ambient-key-7781'}
    code, output = run_cli('run', '--id', 'R-1', '--evidence-dir', str(evidence), '--cwd', str(workspace),
                           '--env', f'AUDIT_SELFTEST_TOKEN={secret}', '--', python, '-c',
                           'import os,sys; print("hello"); print(os.environ["AUDIT_SELFTEST_TOKEN"]); '
                           'print(os.environ.get("AUDIT_SELFTEST_API_KEY")); '
                           'print("postgres://admin:secret123@db.example:5432/app"); print("ghp_" + "a" * 36); '
                           'print("-----BEGIN PRIVATE KEY-----"); print("MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"); '
                           'print("-----END PRIVATE KEY-----"); sys.exit(3)', env=env)
    log = (evidence / 'R-1.log').read_text(encoding='utf-8') if (evidence / 'R-1.log').is_file() else ''
    meta = read_json(evidence / 'R-1.meta.json') if (evidence / 'R-1.meta.json').is_file() else {}
    summary = json_head(output)
    ok = (code == 3 and meta.get('exit_code') == 3 and 'hello' in log and secret not in log and secret not in output
          and '[REDACTED:AUDIT_SELFTEST_TOKEN]' in log and 'ambient-key-7781' not in log
          and 'AUDIT_SELFTEST_API_KEY' in meta.get('stripped_env', []) and 'secret123' not in log
          and 'ghp_aaaa' not in log and 'MIIEvQ' not in log and meta.get('log_sha256') == sha256_file(evidence / 'R-1.log')
          and summary.get('evidence_entry', {}).get('path') == 'evidence/logs/R-1.log')
    control(results, 'RUN_RECORDS_EXIT_STRIPS_ENV_AND_MASKS_SECRETS', ok, {'exit': code})

    code, output = run_cli('run', '--id', 'R-1', '--evidence-dir', str(evidence), '--cwd', str(workspace),
                           '--inherit', 'AUDIT_SELFTEST_API_KEY', '--', python, '-c',
                           'import os; print(os.environ.get("AUDIT_SELFTEST_API_KEY"))', env=env)
    log = (evidence / 'R-1-2.log').read_text(encoding='utf-8') if (evidence / 'R-1-2.log').is_file() else ''
    control(results, 'RUN_INHERIT_PASSES_VARIABLE_BUT_MASKS_IT', code == 0 and '[REDACTED:AUDIT_SELFTEST_API_KEY]' in log
            and 'ambient-key-7781' not in log and (evidence / 'R-1.log').is_file(), {'exit': code})

    code, output = run_cli('run', '--id', 'R-SHELL', '--evidence-dir', str(evidence), '--cwd', str(workspace),
                           '--shell', '--', python, '-c', 'print("a b")')
    log = (evidence / 'R-SHELL.log').read_text(encoding='utf-8') if (evidence / 'R-SHELL.log').is_file() else ''
    control(results, 'RUN_SHELL_KEEPS_ARGUMENT_QUOTING', code == 0 and 'a b' in log, {'exit': code})

    started = time.monotonic()
    code, _ = run_cli('run', '--id', 'R-TIMEOUT', '--evidence-dir', str(evidence), '--cwd', str(workspace),
                      '--timeout', '2', '--', python, '-c', 'import time; print("start", flush=True); time.sleep(60)')
    meta = read_json(evidence / 'R-TIMEOUT.meta.json')
    control(results, 'RUN_TIMEOUT_STOPS_PROCESS', code == 124 and meta.get('timed_out') is True
            and time.monotonic() - started < 40, {'exit': code})

    pid_file = root / 'grandchild.pid'
    code, _ = run_cli('run', '--id', 'R-LEFTOVER', '--evidence-dir', str(evidence), '--cwd', str(workspace), '--',
                      python, '-c',
                      'import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(90)"], '
                      'stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); '
                      f'open(r"{pid_file}","w").write(str(p.pid)); print("ok")')
    grandchild = int(pid_file.read_text()) if pid_file.is_file() else None
    time.sleep(0.5)
    meta = read_json(evidence / 'R-LEFTOVER.meta.json') if (evidence / 'R-LEFTOVER.meta.json').is_file() else {}
    control(results, 'RUN_TERMINATES_LEFTOVER_PROCESSES', code == 0 and grandchild is not None
            and not pid_alive(grandchild) and bool(meta.get('leftover_processes_terminated')),
            {'exit': code, 'leftovers': meta.get('leftover_processes_terminated')})

    service = subprocess.Popen([python, str(Path(__file__).resolve()), 'run', '--id', 'SVC-1', '--evidence-dir',
                                str(evidence), '--cwd', str(workspace), '--', python, '-c',
                                'import time; print("ready", flush=True); time.sleep(120)'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    running = wait_running_info(evidence / 'SVC-1.running.json')
    code, output = run_cli('stop', '--id', 'SVC-1', '--evidence-dir', str(evidence), '--wait', '30')
    try:
        service.wait(timeout=30)
    except subprocess.TimeoutExpired:
        service.kill()
    meta = read_json(evidence / 'SVC-1.meta.json') if (evidence / 'SVC-1.meta.json').is_file() else {}
    control(results, 'STOP_ENDS_SERVICE_AND_RECORDS_EVIDENCE', code == 0 and meta.get('stopped') is True
            and same_file(running.get('resolved_executable'), python)
            and not (evidence / 'SVC-1.running.json').exists(), {'exit': code})

    # run killed hard, as a harness timeout would do: stop must not report a live service or keep stale markers
    pid_file = root / 'aborted-child.pid'
    aborted = subprocess.Popen([python, str(Path(__file__).resolve()), 'run', '--id', 'SVC-ABORT', '--evidence-dir',
                                str(evidence), '--cwd', str(workspace), '--timeout', '0', '--', python, '-c',
                                f'import os,time; open(r"{pid_file}","w").write(str(os.getpid())); time.sleep(120)'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not (pid_file.is_file() and pid_file.read_text()
                                               and (evidence / 'SVC-ABORT.running.json').exists()):
        time.sleep(0.2)
    wait_running_info(evidence / 'SVC-ABORT.running.json')
    aborted.kill()
    aborted.wait(timeout=30)
    child = int(pid_file.read_text()) if pid_file.is_file() and pid_file.read_text() else None
    if os.name == 'nt':
        deadline = time.monotonic() + 3
        while child is not None and pid_alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
    command_was_alive = child is not None and pid_alive(child)
    code, output = run_cli('stop', '--id', 'SVC-ABORT', '--evidence-dir', str(evidence), '--wait', '2')
    control(results, 'STOP_CLEARS_MARKERS_OF_ABORTED_RUN', code == 0 and 'without completion' in output
            and child is not None and command_was_alive == (os.name != 'nt') and not pid_alive(child)
            and not (evidence / 'SVC-ABORT.running.json').exists() and not (evidence / 'SVC-ABORT.stop').exists()
            and not (evidence / 'SVC-ABORT.meta.json').exists(),
            {'exit': code, 'child_found': child is not None, 'alive_before_stop': command_was_alive})
    # Emergency cleanup only after the observed result; it cannot make the control pass.
    if os.name != 'nt' and child is not None and pid_alive(child):
        os.killpg(child, signal.SIGKILL)

    envfile = root / 'sample.env'
    write_text_file(envfile, 'EMPTY=\nPLACEHOLDER=changeme\nLIVE=sk_live_abcdefghijklmnop1234\n'
                             'DB=postgres://u:pw12345@h/db\nexport FLAG=true\n')
    code, output = run_cli('envkeys', str(envfile))
    shapes = {row['key']: row['shape'] for row in json_head(output).get('keys', [])}
    control(results, 'ENVKEYS_SHOWS_SHAPES_NEVER_VALUES', code == 0 and 'sk_live_abc' not in output
            and 'pw12345' not in output and shapes.get('EMPTY') == 'empty' and shapes.get('PLACEHOLDER') == 'Placeholder'
            and shapes.get('DB') == 'URL with credentials' and shapes.get('LIVE', '').startswith('looks real')
            and shapes.get('FLAG') == 'Switch or number', shapes)

    path_project = root / 'pathproject'
    write_text_file(path_project / 'config.json',
                    json.dumps({'data_dir': str(path_project.resolve()).replace('\\', '/') + '/data'}) + '\n')
    write_text_file(path_project / 'plain.txt', 'relative path: ./data\n')
    code_path, output = run_cli('workspace', '--project', str(path_project), '--dest', str(root / 'ws-paths'),
                                '--no-git-snapshot')
    refs = json_head(output).get('original_path_refs')
    control(results, 'WORKSPACE_REPORTS_ABSOLUTE_PATHS_TO_ORIGINAL', code_path == 0 and refs == ['config.json'], refs)

    runs_project = root / 'runsproject'
    for rel, value in (('r-closed/RUN-STATE.json', {'run_id': 'r-closed', 'prompt_version': PROMPT_VERSION,
                                                    'created_at_utc': '2026-01-01T00:00:00Z',
                                                    'closed_at_utc': '2026-01-01T05:00:00Z'}),
                       ('r-open/RUN-STATE.json', {'run_id': 'r-open', 'prompt_version': PROMPT_VERSION,
                                                  'created_at_utc': '2026-01-02T00:00:00Z', 'closed_at_utc': None}),
                       ('r-legacy/RUN-STATE.json', {'run_id': 'r-legacy', 'prompt_version': '3.0',
                                                    'created_at_utc': '2025-12-01T00:00:00Z'}),
                       ('r-legacy/audit.json', {'prompt_version': '3.0', 'audit_status': {'value': 'PARTIAL'}}),
                       ('r-legacy-open/audit.json', {'prompt_version': '3.0', 'audit_status': 'PARTIAL',
                                                     'baseline': {'captured_at_utc': '2025-11-01T00:00:00Z'}}),
                       ('r-vstyle/RUN-STATE.json', {'run_id': 'r-vstyle', 'prompt_version': 'V3.1',
                                                    'created_at_utc': '2025-10-01T00:00:00Z'})):
        write_json_new(runs_project / DEFAULT_RESULTS_DIRNAME / rel, value)
    write_text_file(runs_project / DEFAULT_RESULTS_DIRNAME / 'r-legacy' / 'REPORT.md', '# Report\n')
    write_text_file(runs_project / DEFAULT_RESULTS_DIRNAME / 'impl-notes' / 'PROGRESS.md', 'Notes\n')
    code_runs, output = run_cli('runs', '--project', str(runs_project))
    listing = json_head(output)
    statuses = {item['dir']: item['status'] for item in listing.get('runs', [])}
    control(results, 'RUNS_CLASSIFIES_OPEN_CLOSED_AND_LEGACY', code_runs == 0
            and statuses == {'r-closed': 'CLOSED', 'r-open': 'OPEN', 'r-legacy': 'LEGACY_CLOSED_INFERRED',
                             'r-legacy-open': 'LEGACY_OPEN_INFERRED', 'r-vstyle': 'OPEN'}
            and listing.get('latest_open') == 'r-open' and listing.get('latest_closed') == 'r-closed'
            and listing.get('other_dirs') == ['impl-notes'], statuses)

    # Real-looking values must stay visible; placeholders seen in real runs must not flood the warnings.
    placeholder_key = 'sk-' + 'proj-' + 'abcdefghijklmnopqrstuvwxyz0123456789'
    samples = {
        'live_key': (FAKE_LIVE_KEY, (['STRIPE_LIVE_KEY'], [])),
        'alphabet_key': (placeholder_key, ([], ['OPENAI_STYLE_KEY (Placeholder?)'])),
        'url_real_host': ('postgres://app:' + 'Q7m2R9x4kL' + '@db.internal.corp:5432/app', ([], ['URL_CREDENTIALS'])),
        'url_real_secret_on_localhost': ('postgres://app:' + 'Q7m2R9x4kL' + '@localhost/app', ([], ['URL_CREDENTIALS'])),
        'url_example_host': ('https://user:' + 'pa55word1' + '@media.example/x', ([], [])),
        'url_template': ('https://bot:${' + 'TOKEN}@github.com/o/r.git', ([], [])),
        'url_masked': ('https://user:***@cdn.example.com', ([], [])),
        'url_compose_default': ('postgres://postgres:' + 'postgres' + '@db:5432/app', ([], [])),
        'url_redis_without_user': ('redis://:' + 'Q7m2R9x4kL' + '@cache.internal:6379', ([], ['URL_CREDENTIALS'])),
        'jwt': ('eyJ' + 'hbGciOiJIUzI1NiJ9.' + 'eyJzdWIiOiIxMjM0In0.' + 'c2lnbmF0dXJlMTIzNDU', ([], ['JWT'])),
        'aws_documentation_key': ('AKIA' + 'IOSFODNN7' + 'EXAMPLE', ([], ['AWS_ACCESS_KEY (Placeholder?)'])),
        'identical_run_key': ('sk_' + 'live_' + 'x' * 24, ([], ['STRIPE_LIVE_KEY (Placeholder?)'])),
        'pem_header_only': (FAKE_PEM_HEADER, ([], ['PRIVATE_KEY_HEADER'])),
        'pem_with_key_material': (FAKE_PEM_HEADER + '\n' + FAKE_PEM_BODY, (['PRIVATE_KEY'], [])),
    }
    observed = {key: secret_pattern_hits(text) for key, (text, _) in samples.items()}
    control(results, 'SECRET_PATTERNS_SEPARATE_PLACEHOLDERS',
            all(observed[key] == expected for key, (_, expected) in samples.items()),
            {key: [list(part) for part in value] for key, value in observed.items()})

    if not git_available():
        control(results, 'BASELINE_WORKSPACE_GIT', True, 'SKIPPED: git unavailable')
        results[-1]['result'] = 'SKIPPED'
        return results
    project = root / 'gitproject'
    identity = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
                'GIT_COMMITTER_EMAIL': 't@t'}
    for rel, text in (('a.txt', 'a\n'), ('b.txt', 'b\n'), ('.gitignore', 'ignored.txt\nlocal.db\n'),
                      ('.npmrc', 'legacy-peer-deps=true\n'),
                      ('config/app.json', '{"db": "postgres://admin:pw@prod-db:5432/app"}\n')):
        write_text_file(project / rel, text)
    run_git(['init', '-q'], project)
    run_git(['add', '-A'], project)
    tree = run_git(['write-tree'], project).decode().strip()
    commit = run_git(['commit-tree', tree, '--no-gpg-sign', '-m', 'init'], project, env=identity).decode().strip()
    run_git(['update-ref', 'HEAD', commit], project)
    write_text_file(project / 'b.txt', 'b changed\n')
    write_text_file(project / 'c.txt', 'untracked\n')
    write_text_file(project / 'ignored.txt', 'ignored\n')
    write_text_file(project / 'local.db', 'dev data\n')
    write_text_file(project / '.env', 'TOKEN=not-real\n')
    write_text_file(project / 'audit-results' / 'old.txt', 'old run\n')
    nested = project / 'nested'
    write_text_file(nested / 'code.txt', 'nested code\n')
    write_text_file(nested / '.gitignore', 'secret.key\n')
    write_text_file(nested / 'secret.key', 'nested secret\n')
    write_text_file(nested / '.env', 'NESTED=1\n')
    run_git(['init', '-q'], nested)
    snap, _ = take_snapshot(project)
    expected = {'a.txt', 'b.txt', 'c.txt', '.gitignore', '.npmrc', 'config/app.json', '.env', 'nested/code.txt',
                'nested/.gitignore', 'nested/.env'}
    control(results, 'BASELINE_INCLUDES_WORKING_STATE_AND_NESTED_REPOS', set(snap['files']) == expected
            and snap['mode'] == 'git', sorted(snap['files']))

    baseline_file = root / 'baseline-start.json'
    code_base, _ = run_cli('baseline', '--project', str(project), '--out', str(baseline_file))
    dest = root / 'workspace'
    code_ws, output = run_cli('workspace', '--project', str(project), '--dest', str(dest), '--expect-baseline',
                              str(baseline_file))
    summary = json_head(output)
    head_ok = True
    try:
        run_git(['rev-parse', '--verify', 'HEAD'], dest)
    except ToolError:
        head_ok = False
    ok = (code_base == 0 and code_ws == 0 and summary.get('matches_baseline_except_withheld') is True
          and (dest / 'b.txt').read_text(encoding='utf-8') == 'b changed\n' and (dest / 'c.txt').is_file()
          and (dest / '.npmrc').is_file() and not (dest / '.env').exists() and not (dest / 'nested' / '.env').exists()
          and not (dest / 'nested' / 'secret.key').exists() and (dest / 'nested' / 'code.txt').is_file()
          and not (dest / 'ignored.txt').exists() and not (dest / 'audit-results').exists()
          and sorted(summary.get('withheld_credential_files', [])) == ['.env', 'nested/.env']
          and any(hit['path'] == 'config/app.json' for hit in summary.get('secret_content_hits', []))
          and '.npmrc' in summary.get('secret_like_files_copied', []) and head_ok and (dest / WORKSPACE_MARKER).is_file()
          and (summary.get('expected_baseline') or {}).get('matches') is True)
    control(results, 'WORKSPACE_COPIES_STATE_WITHHOLDS_UNTRACKED_CREDENTIALS_AND_COMMITS', ok,
            {'exit': code_ws, 'withheld': summary.get('withheld_credential_files'), 'head': head_ok})
    code_inside, _ = run_cli('workspace', '--project', str(project), '--dest', str(project / 'copy'))
    control(results, 'WORKSPACE_REFUSES_DEST_INSIDE_PROJECT', code_inside == 2 and not (project / 'copy').exists())

    link_project = root / 'linkproject'
    write_text_file(link_project / 'inner.txt', 'inside\n')
    write_text_file(root / 'outside.txt', 'outside\n')
    try:
        os.symlink(str(root / 'outside.txt'), link_project / 'abs_link.txt')
        os.symlink('inner.txt', link_project / 'rel_link.txt')
        links_possible = True
    except (OSError, NotImplementedError):
        links_possible = False
    if links_possible:
        code_links, output = run_cli('workspace', '--project', str(link_project), '--dest', str(root / 'ws-links'),
                                     '--no-git-snapshot')
        summary = json_head(output)
        unsafe = [item['path'] for item in summary.get('unsafe_symlinks_not_created', [])]
        control(results, 'WORKSPACE_SKIPS_SYMLINKS_LEAVING_PROJECT', unsafe == ['abs_link.txt']
                and not (root / 'ws-links' / 'abs_link.txt').exists(), unsafe)
    else:
        results.append({'id': 'WORKSPACE_SKIPS_SYMLINKS_LEAVING_PROJECT', 'kind': 'TOOL_CONTROL', 'result': 'SKIPPED',
                        'detail': 'Symlinks cannot be created on this system'})

    code_same, output_same = run_cli('baseline', '--project', str(project), '--compare', str(baseline_file))
    write_text_file(project / 'local.db', 'dev data overwritten by tests\n')
    run_git(['branch', 'audit-side-effect'], project)
    run_git(['config', '--local', 'core.hooksPath', '.husky'], project)
    code_side, output_side = run_cli('baseline', '--project', str(project), '--compare', str(baseline_file))
    side = json_head(output_side).get('compare', {})
    ok = (code_same == 0 and code_side == 1 and side.get('identical') is True
          and 'local.db' in side.get('side_effects', {}).get('ignored_files', [])
          and {'refs', 'config_local'} <= set(side.get('side_effects', {}).get('git_metadata', [])))
    control(results, 'BASELINE_DETECTS_SIDE_EFFECTS_ON_IGNORED_FILES_AND_GIT', ok,
            {'unchanged_exit': code_same, 'side_exit': code_side, 'side': side.get('side_effects')})
    write_text_file(project / 'a.txt', 'a changed\n')
    code_diff, output = run_cli('baseline', '--project', str(project), '--compare', str(baseline_file))
    changed = json_head(output).get('compare', {}).get('changed', [])
    control(results, 'BASELINE_COMPARE_DETECTS_PRODUCT_CHANGE', code_diff == 1 and changed == ['a.txt'], changed)
    code_late, output = run_cli('workspace', '--project', str(project), '--dest', str(root / 'workspace-late'),
                                '--expect-baseline', str(baseline_file), '--no-git-snapshot')
    expected = json_head(output).get('expected_baseline') or {}
    control(results, 'WORKSPACE_REPORTS_CHANGE_SINCE_START_BASELINE', code_late == 1 and expected.get('matches') is False
            and 'a.txt' in expected.get('changed', []), expected)
    return results


def run_workspace_safeguard_controls(root: Path, results: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Controls for baseline, workspace, masking, environment and cleanup safeguards."""
    results = [] if results is None else results
    # Check before dependent fixtures: TEMP can live in an ignored parent repository.
    if git_available():
        parent = root / 'parent-repo'
        inner = parent / 'tools-project'
        write_text_file(inner / 'README.md', '# Project\n')
        write_text_file(inner / 'src' / 'main.py', 'print("hello")\n')
        write_text_file(parent / '.gitignore', 'tools-project/\n')
        subprocess.run(['git', 'init', '-q'], cwd=parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        snap, _ = take_snapshot(inner, results_dir=root / 'parent-results')
        control(results, 'BASELINE_PROJECT_IGNORED_BY_PARENT_REPO', sorted(snap['files']) == ['README.md', 'src/main.py']
                and snap['mode'] == 'filesystem' and bool(snap['notes']), {'mode': snap['mode'], 'files': len(snap['files'])})

    # run masks values from the copy's files (printed alone, known from workspace), --env values and assignments
    project, dest, logs = root / 'mask-project', root / 'mask-ws', root / 'mask-run' / 'evidence' / 'logs'
    file_value, yaml_value = 'Synth' + 'FileValue' + '7731xq', 'Synth' + 'YamlValue' + '4410kp'
    write_text_file(project / 'settings.py', f'API_KEY = "{file_value}"\nDEBUG = True\n')
    write_text_file(project / 'config.yaml', f'database:\n  db_password: {yaml_value}\n')
    write_text_file(project / 'show.py', 'import settings\nprint(settings.API_KEY)\nprint(open("config.yaml").read())\n'
                                         'import os\nprint("override", os.environ["X_PASSWORD"])\n'
                                         'print("".join(["SESSION_TOK", "EN=Synth", "Inline", "9921zz"]))\n'
                                         'print("VISIBLE_MARKER_42")\n')
    code_ws, output = run_cli('workspace', '--project', str(project), '--dest', str(dest), '--results-dir',
                              str(root / 'mask-run'))
    value_files = json_head(output).get('secret_value_files')
    code_run, output = run_cli('run', '--id', 'CHK-MASK', '--evidence-dir', str(logs), '--cwd', str(dest),
                               '--env', 'X_PASSWORD=Zq7!', '--', sys.executable, '-B', 'show.py')
    log_text = (logs / 'CHK-MASK.log').read_text(encoding='utf-8', errors='replace') if (logs / 'CHK-MASK.log').is_file() else ''
    meta_text = (logs / 'CHK-MASK.meta.json').read_text(encoding='utf-8') if (logs / 'CHK-MASK.meta.json').is_file() else ''
    # each value is caught by one path only: copy files, --env, assignment pattern at runtime
    leaked = [v for v in (file_value, yaml_value, 'Zq7!', 'SynthInline' + '9921zz')
              if v in log_text + meta_text + output]
    control(results, 'RUN_MASKS_SECRET_VALUES_FROM_COPY', code_ws == 0 and code_run == 0 and not leaked
            and sorted(value_files or []) == ['config.yaml', 'settings.py'] and 'VISIBLE_MARKER_42' in log_text,
            {'secret_value_files': value_files, 'leaked_count': len(leaked), 'run_exit': code_run})

    # masking: passwords with $ ( ) # * are masked whole, also after dotted names and `=>`, in webhook URLs, dotted
    # tokens, generated paths and Authorization headers; test IDs, file:line, status words, code, connection targets
    # and endpoints stay readable; ordinary config values are not recorded, passphrases are; long lines stay fast
    secrets = ['Kx9(' + 'mP2)zQw', 'Pa$$' + 'w0rd!2024', 'abcd#' + 'efgh77', '*Rt5' + 'yU8iO', 'Zq9!' + 'kL2m',
               'q8Zr2LmN' + '5vXc7TbW', 'sk.eyJ1Ijoi' + 'YWNtZSJ9.Q7wErTy8', 'hunter2' + '.Backup', 'Lm4!' + 'nB7v',
               'dXNlcjpL' + 'eDltUDJ6', 'k9x2' + 'm7q4', '$Kx9' + 'mP2zQ', 'Def' + 'ault', '4829' + '1375',
               'Wv3#' + 'Tn8q', 'qwertzuiop' + 'asdfghjk', 'Summer!' + 'Breeze', 'hunter' + '22x', 'kx9mP2' + '(zQw)!',
               'summer' + 'breeze', 'autumn' + 'leaves']
    leaky = '\n'.join([f'POSTGRES_PASSWORD: {secrets[0]}', f'spring.datasource.password={secrets[1]}',
                       f'mail.password={secrets[2]}', f'api.secret={secrets[3]}', f'SMTP_PASS={secrets[4]}',
                       f'X-Api-Key: {secrets[4]}x', f'ALERT_WEBHOOK_URL=https://hooks.example.test/T0AB/{secrets[5]}',
                       f'MAP_TOKEN={secrets[6]}', f'DB_PASSWORD={secrets[7]}', f'db.password:{secrets[8]}',
                       f"'password' => '{secrets[8]}x',", f'2fa_secret={secrets[8]}y', f'encryptionKey: {secrets[8]}z',
                       f'curl -H "Authorization: Basic {secrets[9]}"', f'DB_PASSWORD=/srv/{secrets[10]}',
                       f'DB_PASS={secrets[11]}', f'password = "{secrets[12]}"', f'ADMIN_PIN={secrets[13]}',
                       f'level=debug msg="connecting with password={secrets[14]}"', f'opts=token={secrets[14]}x',
                       f'password: !!str {secrets[14]}y', f'client_secret: |\n  {secrets[14]}z',
                       f"password = '''{secrets[14]}w'''", f'TEAM_WEBHOOK=https://hooks.example.test/{secrets[15]}',
                       f'  password.txt: {secrets[16]}', f'config: {{Host:db Port:5432 Password:{secrets[16]}x}}',
                       f'DATABASE_URL=sqlserver://db:1433;database=app;pwd={secrets[16]}y',
                       f'token=login(password={secrets[17]})', f'DB_PASSWORD={secrets[18]}',
                       f'DB_PASSWORD=/opt/{secrets[19]}', f'clientSecretSource: {secrets[18]}x',
                       f'password:{secrets[20]}', f'settings: map[password:{secrets[20]}x user:admin]',
                       f'AZURE_STORAGE_KEY={secrets[20]}/{secrets[19]}',
                       'body="{\\"user\\":\\"admin\\",\\"password\\":\\"' + secrets[20] + 'q\\"}"',
                       f'curl -H "X-Api-Key:{secrets[20]}r" https://api.example.test',
                       f'x}} {{Password:{secrets[20]}p}}', f']] {{user:alice password:{secrets[19]}z}}',
                       'body="{\\"password\\" : \\"' + secrets[20] + 's\\"}"',
                       'body="{\\"password\\":\\"' + secrets[20] + '\\\\\\"t\\\\u0026\\"}"'])
    masked = mask_sensitive_assignments(leaky)[0]
    keep = ['tests/test_token.py::test_refresh PASSED', 'FAILED tests/test_secret_rotation.py::test_rotate',
            'test_token.py::test_refresh', 'app/token_store.py:123: error', 'src\\auth\\api_key.ts:12:5: error',
            'token_test.go:142: expected 1', 'src/token_store.py: Unexpected token', 'Refreshing token: done',
            'DATABASE_URL=postgres://db:5432/app', 'REDIRECT_URI=https://app.example.test/callback',
            'password = get_password(user)', 'token: string;', 'TOKEN_ENDPOINT = "/oauth/token"',
            'if token == expected_token:', 'token = jwt.encode(payload, key)', 'token = client.FetchToken(scope)',
            'password=user.password,', 'SECRET_KEY_FILE=/run/secrets/app_key', 'password_reset_url=/accounts/reset/',
            'Authorization: Bearer ${API_TOKEN}', 'token_type=Bearer', 'secrets.yaml:3: error',
            'token.example.test:8443', 'password_ttl=3600', 'INFO auth: token_source=cookie expires_in=3600',
            'WARN password_policy=strict', 'secret_scanning: enabled=true', 'script: |\n  echo deploy',
            'INFO     app.services.token_service:token_service.py:42 Refreshing token',
            'WARNING:app.auth.token:Token expired for user 42',
            '2026-10-11 INFO 1 --- [main] c.a.auth.TokenService : Issuing token',
            'routing_key=orders.created', 'api_key = APIKey.objects.create(user=user)',
            'WARNING:token:Token expired', 'API_SECRET=vault[', '\x1b[33mWARN\x1b[0m refresh token:stale-ish']
    altered = [line for line in keep if mask_sensitive_assignments(line)[0] != line]
    # brackets the value did not open stay outside the mask
    go_dump = mask_sensitive_assignments('cfg: {Host:db Password:' + secrets[14] + '} f(token=' + secrets[14] + ')')[0]
    altered += [] if go_dump.endswith('} f(token=[REDACTED:token])') else ['brackets after a masked value']
    ending = mask_sensitive_assignments('DB_PASSWORD=' + secrets[14] + ']')[0]
    altered += [] if ending == 'DB_PASSWORD=[REDACTED:DB_PASSWORD]' else ['bracket at the end of a secret']
    recorded = assignment_secrets('SESSION_COOKIE_NAME = "session"\ntoken_type = "Bearer"\napi_key_header: X-API-Key\n'
                                  'password_field = "password"\nTOKEN_ENDPOINT = "/oauth/token"\n'
                                  'TOKEN_URL = "https://auth.example.test/oauth/token"\n'
                                  'API_KEY_HEADER: X-API-Key\nSECRET_KEY = "changeme"\n')
    passphrase, header_value = 'correct-horse-' + 'battery-staple', 'Kx9mP2' + 'zQw7abcd'
    wanted = assignment_secrets(f'admin_password: {passphrase}\ncurl -H "Authorization: Bearer {header_value}"\n')
    started = time.monotonic()
    mask_sensitive_assignments('9f3a' * 25_000)
    assignment_secrets('key' + 'a1' * 50_000)
    mask_sensitive_assignments('password=abc(' + ')' * 40_000 + 'x')
    mask_sensitive_assignments('a.' * 40_000 + '=x ' + 'webhook=https://' + 'a/' * 40_000 + '@')
    mask_sensitive_assignments('token=' + 'a,token=' * 12_000 + '[REDACTED:x]')
    elapsed = time.monotonic() - started
    leaked = [s for s in secrets if s in masked]
    missing = [v for v in (passphrase, header_value) if v not in [value for _, value in wanted]]
    control(results, 'MASKING_CATCHES_SYMBOL_PASSWORDS_AND_KEEPS_EVIDENCE', not leaked and not altered and not recorded
            and not missing and elapsed < 5, {'leaked': len(leaked), 'altered': altered, 'recorded': recorded,
                                              'not_recorded': len(missing), 'seconds': round(elapsed, 2)})

    # workspace --expect-baseline applies the exclusions recorded by init.
    excl_project = root / 'excl-project'
    write_text_file(excl_project / 'app.py', 'print(1)\n')
    write_text_file(excl_project / 'data' / 'big.bin', 'x' * 100)
    code_init, output = run_cli('init', '--project', str(excl_project), '--exclude', 'data')
    start_baseline = json_head(output).get('baseline')
    code_excl, output = run_cli('workspace', '--project', str(excl_project), '--dest', str(root / 'excl-copy'),
                                '--expect-baseline', str(start_baseline), '--no-git-snapshot')
    expected = json_head(output).get('expected_baseline') or {}
    control(results, 'WORKSPACE_KEEPS_INIT_EXCLUDES', code_init == 0 and code_excl == 0
            and expected.get('matches') is True and not (root / 'excl-copy' / 'data').exists(),
            {'init': code_init, 'workspace': code_excl, 'matches': expected.get('matches')})

    # cleanup refuses while the cwd is inside the copy, and keeps its marker until everything else is gone, so an
    # interrupted cleanup can be repeated (a held file blocks deletion on Windows only)
    lock_project, lock_copy = root / 'lock-project', root / 'lock-copy'
    write_text_file(lock_project / 'z' / 'server.log', 'x\n')
    code_lws, _ = run_cli('workspace', '--project', str(lock_project), '--dest', str(lock_copy), '--no-git-snapshot')
    inside = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'cleanup', '--dest', '.', '--project',
                             str(lock_project)], cwd=lock_copy, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=120).returncode
    detail: dict[str, Any] = {'workspace': code_lws, 'cwd_inside': inside}
    lock_ok = True
    held = lock_copy / 'z' / 'server.log'
    if os.name == 'nt' and held.is_file():
        handle = open(held, 'a', encoding='utf-8')
        try:
            code_first, _ = run_cli('cleanup', '--dest', str(lock_copy), '--project', str(lock_project))
            marker_kept = (lock_copy / WORKSPACE_MARKER).is_file()
            code_dry, _ = run_cli('cleanup', '--dest', str(lock_copy), '--project', str(lock_project), '--dry-run')
        finally:
            handle.close()
        lock_ok = code_first == 1 and marker_kept and code_dry == 0
        detail.update(first=code_first, marker_kept=marker_kept, dry_run=code_dry)
    elif os.name == 'nt':
        lock_ok = False
        detail['held_file'] = 'missing in the copy'
    else:
        detail['held_file'] = 'checked only on Windows'
    code_retry, _ = run_cli('cleanup', '--dest', str(lock_copy), '--project', str(lock_project))
    detail['final'] = code_retry
    control(results, 'CLEANUP_KEEPS_MARKER_UNTIL_DONE_AND_REFUSES_CWD_INSIDE', code_lws == 0 and inside == 2 and lock_ok
            and code_retry == 0 and not lock_copy.exists() and (lock_project / 'z' / 'server.log').is_file(), detail)

    # envkeys: continuation lines of a multi-line value are never shown as keys
    envfile = root / 'multi' / '.env'
    tail = 'Synth' + 'KeyTail0042xyz=='
    write_text_file(envfile, f'SAFE=1\nPRIVATE_KEY="{FAKE_PEM_HEADER}\n{FAKE_PEM_BODY}\n{tail}\n-----END PRIVATE KEY-----"\n'
                             'AFTER=2\n')
    code_env, output = run_cli('envkeys', str(envfile))
    keys = [row.get('key') for row in json_head(output).get('keys', [])]
    control(results, 'ENVKEYS_MULTILINE_VALUE_HIDDEN', code_env == 0 and keys == ['SAFE', 'PRIVATE_KEY', 'AFTER']
            and tail[:-2] not in output and FAKE_PEM_BODY not in output, keys)

    # baseline --compare keeps the excludes given to init (continuation prompt compares without options)
    excl = root / 'excl-project'
    write_text_file(excl / 'app.py', 'print(1)\n')
    write_text_file(excl / 'data' / 'blob.txt', 'large\n')
    code_init, output = run_cli('init', '--project', str(excl), '--exclude', 'data')
    baseline_path = json_head(output).get('baseline', '')
    write_text_file(excl / 'data' / 'other.txt', 'excluded\n')
    code_cmp, output = run_cli('baseline', '--project', str(excl), '--compare', str(baseline_path))
    control(results, 'BASELINE_COMPARE_KEEPS_INIT_EXCLUDES', code_init == 0 and code_cmp == 0,
            json_head(output).get('compare', {}).get('counts'))

    # original_path_refs: non-ASCII capitals and siblings of the project path
    # Non-ASCII path fixture: intentionally tests Unicode paths.
    umlaut = root / 'ÜberApp'
    write_text_file(umlaut / 'cfg.json', json.dumps({'dir': str(umlaut.resolve())}, ensure_ascii=False) + '\n')
    write_text_file(umlaut / 'sibling.txt', str(umlaut.resolve()) + '-other' + os.sep + 'data\n')
    code_u, output = run_cli('workspace', '--project', str(umlaut), '--dest', str(root / 'ws-umlaut'))
    refs = json_head(output).get('original_path_refs')
    control(results, 'ORIGINAL_PATH_REFS_UNICODE_AND_SIBLINGS', code_u == 0 and refs == ['cfg.json'], refs)

    # validate: a strong hit in a run log and weak values in results only warn; never block the run
    run_dir, example_project, data = write_example_run(root / 'warn', 'nogo')
    secret_log = f'token printed by a test: {FAKE_LIVE_KEY}\n'
    write_text_file(run_dir / 'evidence' / 'logs' / 'CHK-009.log', secret_log)
    write_json_new(run_dir / 'evidence' / 'logs' / 'CHK-009.meta.json',
                   {'id': 'CHK-009', 'exit_code': 0, 'log_sha256': sha256_bytes(secret_log.encode('utf-8'))})
    jwt = 'eyJ' + 'hbGciOiJIUzI1NiJ9' + '.' + 'eyJzdWIiOiIxMjM0In0' + '.' + 'c2lnbmF0dXJlMTIzNDU'
    write_text_file(run_dir / 'evidence' / 'session-note.txt', f'Cookie value in test run: {jwt}\n'
                                                               f'{FAKE_PEM_HEADER} (header only)\n')
    issues, _ = validate_audit(data, run_dir, example_project, final=True)
    warned = sorted({w['where'] for w in issues.warnings if w['code'] == 'W_SECRET_LIKE_IN_RESULTS'})
    control(results, 'SECRET_IN_RUN_LOG_AND_WEAK_VALUES_ONLY_WARN', not issues.errors
            and warned == ['evidence/logs/CHK-009.log', 'evidence/session-note.txt'],
            {'errors': sorted(issues.codes()), 'warned': warned})

    # code citations count lines like grep -n: a form feed or U+2028 does not start a new line
    run_dir, ff_project, data = write_example_run(root / 'formfeed', 'nogo')
    content = 'a = 1\n\x0c\ns = "a\u2028b"\ntarget_line_value = 4242\n'
    with open(ff_project / 'src' / 'mod.py', 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(content)
    (run_dir / 'evidence' / 'baseline' / 'baseline-start.json').unlink()
    snap, _ = take_snapshot(ff_project, mode='filesystem', with_side_effects=False)
    write_json_new(run_dir / 'evidence' / 'baseline' / 'baseline-start.json', snap)
    data['baseline']['fingerprint'] = snap['content_fingerprint']
    data['findings'][0]['code_refs'].append({'path': 'src/mod.py', 'line_start': 4, 'line_end': 4,
                                             'excerpt': 'target_line_value = 4242'})
    issues, _ = validate_audit(data, run_dir, ff_project, final=True)
    control(results, 'CODE_REF_LINES_COUNT_LIKE_GREP', not issues.errors, sorted(issues.codes()))

    # cleanup removes only a marked work copy and removes links inside it without following them
    keep = root / 'keep'
    write_text_file(keep / 'keep.txt', 'remains\n')
    unmarked = root / 'unmarked'
    write_text_file(unmarked / 'file.txt', 'remains\n')
    code_refused, _ = run_cli('cleanup', '--dest', str(unmarked), '--project', str(project))
    overlap = project / 'inner-copy'
    write_json_new(overlap / WORKSPACE_MARKER, {'project_root': str(project.resolve()), 'baseline_fingerprint': 'x'})
    code_overlap, _ = run_cli('cleanup', '--dest', str(overlap), '--project', str(project))
    link = dest / 'linked-cache'
    linked = False
    try:
        if os.name == 'nt':
            linked = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(keep)], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL).returncode == 0
        else:
            os.symlink(keep, link, target_is_directory=True)
            linked = True
    except OSError:
        linked = False
    code_clean, output = run_cli('cleanup', '--dest', str(dest), '--project', str(project))
    cleanup_ok = (code_refused == 2 and (unmarked / 'file.txt').is_file() and code_overlap == 2 and overlap.is_dir()
                  and code_clean == 0 and not dest.exists() and (keep / 'keep.txt').is_file())
    control(results, 'CLEANUP_REMOVES_ONLY_MARKED_COPY_WITHOUT_FOLLOWING_LINKS',
            cleanup_link_control_ok(linked, cleanup_ok),
            {'link_created': linked, 'refused': code_refused, 'overlap': code_overlap, 'cleanup': code_clean})
    control(results, 'LINK_CONTROL_REJECTS_UNCREATED_LINK', not cleanup_link_control_ok(False, True))

    return results


def run_environment_process_controls(root: Path, results: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    results = [] if results is None else results
    workspace, evidence = root / 'copy', root / 'logs'
    write_json_new(workspace / WORKSPACE_MARKER, {})
    python = sys.executable
    common = ('--evidence-dir', str(evidence), '--cwd', str(workspace))
    credential_url = 'https://synth:UrlSecret7791@registry.invalid/simple'
    public_url = 'https://registry.invalid/simple'
    values = {'PIP_INDEX_URL': credential_url, 'UV_INDEX_URL': public_url, 'HTTPS_PROXY': credential_url,
              'HTTP_PROXY': public_url, 'AUDIT_OPAQUE': 'ghp_' + 'a' * 36,
              'AUDIT_KEY_BLOCK': '-----BEGIN PRIVATE KEY-----', 'DB_PIN': '4711',
              'COREPACK_ENABLE_AUTO_PIN': '0', 'AUDIT_PGP': '-----BEGIN PGP PRIVATE KEY BLOCK-----',
              'AUDIT_DOCKERCFG': '{"auths":{"r.invalid":{"auth":"c3ludGg6U3ludGhQdzk5"}}}'}
    child_code = ('import os,json; expected=' + repr(values) + '; '
                  'print(json.dumps({k:os.environ.get(k)==v for k,v in expected.items()}))')
    code, _ = run_cli('run', '--id', 'ENV-VALUES', *common, '--', python, '-c', child_code,
                      env={**os.environ, **values})
    observed = read_json(evidence / 'ENV-VALUES.log') if code == 0 else {}
    meta = read_json(evidence / 'ENV-VALUES.meta.json')
    expected = {k: k in ('UV_INDEX_URL', 'HTTP_PROXY', 'COREPACK_ENABLE_AUTO_PIN') for k in values}
    control(results, 'ENV_STRIPS_VALUES_AND_KEEPS_SAFE_COUNTEREXAMPLES', code == 0 and observed == expected
            and set(values) - {k for k, kept in expected.items() if kept} <= set(meta.get('stripped_env', [])),
            {'exit': code, 'kept': sorted(k for k, kept in observed.items() if kept)})
    safe = dict.fromkeys(('PIP_INDEX_URL', 'UV_INDEX_URL', 'HTTPS_PROXY', 'HTTP_PROXY'), public_url)
    code, _ = run_cli('run', '--id', 'ENV-PUBLIC-URLS', *common, '--', python, '-c',
                      'import os; expected=' + repr(safe) + '; assert all(os.environ.get(k)==v for k,v in expected.items())',
                      env={**os.environ, **safe})
    meta = read_json(evidence / 'ENV-PUBLIC-URLS.meta.json')
    control(results, 'ENV_CREDENTIAL_URLS_STRIPPED_PUBLIC_URLS_KEPT', code == 0
            and not set(safe) & set(meta.get('stripped_env', []))
            and all(should_strip(k, credential_url) and not should_strip(k, public_url) for k in safe), {'exit': code})

    names = ['AUTH', 'NPM_CONFIG__AUTH', 'YARN_REGISTRY_AUTH', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT',
             'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_12', 'NPM_CONFIG_//registry.invalid/:_authToken',
             'DOCKER_AUTH_CONFIG', 'BUNDLE_GEMS__REGISTRY__INVALID']
    auth_value = 'SynthAuth7731zzz'
    code, _ = run_cli('run', '--id', 'ENV-AUTH', *common, '--', python, '-c',
                      'import os,json; print(json.dumps({k:k in os.environ for k in ' + repr(names) + '}))',
                      env={**os.environ, **dict.fromkeys(names, auth_value)})
    observed = read_json(evidence / 'ENV-AUTH.log') if code == 0 else {}
    control(results, 'ENV_ONLY_AUTH_AND_GIT_NAMES_STRIPPED', code == 0 and observed == dict.fromkeys(names, False)
            and set(sensitive_values(dict.fromkeys(names, auth_value), []))
            == set(names) - {'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0'}, {'exit': code})

    code, output = run_cli('run', '--id', 'ENV-INHERIT-AUTH', *common, '--inherit', 'NPM_CONFIG__AUTH',
                           '--inherit', 'PIP_INDEX_URL', '--', python, '-c',
                           'import os; assert os.environ["NPM_CONFIG__AUTH"]==' + repr(auth_value) + '; '
                           'assert os.environ["PIP_INDEX_URL"]==' + repr(credential_url) + '; '
                           'print(os.environ["NPM_CONFIG__AUTH"]); print(os.environ["PIP_INDEX_URL"])',
                           env={**os.environ, 'NPM_CONFIG__AUTH': auth_value, 'PIP_INDEX_URL': credential_url})
    log = (evidence / 'ENV-INHERIT-AUTH.log').read_text(encoding='utf-8')
    meta_text = (evidence / 'ENV-INHERIT-AUTH.meta.json').read_text(encoding='utf-8')
    control(results, 'INHERIT_ENV_ONLY_AUTH_AND_VALUE_MASKED', code == 0
            and '[REDACTED:NPM_CONFIG__AUTH]' in log and auth_value not in log + meta_text + output
            and 'UrlSecret7791' not in log + meta_text + output, {'exit': code})

    switches = ['0', '1', 'true', 'FALSE', 'yes', 'NO', 'on', 'OFF']
    control(results, 'ENV_HARMLESS_SWITCHES_KEPT', all(
        not should_strip('AUDIT_TOKEN_ENABLED', v) and not sensitive_values({'AUDIT_TOKEN_ENABLED': v}, [], 1)
        and should_strip('AWS_SYNTH_FLAG', v) for v in switches) and should_strip('DB_PIN', '4711'))
    prefix_only = {'PGHOST': 'synthhost.invalid', 'AWS_REGION': 'synth-region-1', 'GIT_CONFIG_KEY_0': 'credential.helper'}
    control(results, 'MASKING_SKIPS_PREFIX_ONLY_AND_GIT_STRUCTURE_VALUES', all(should_strip(k, v) for k, v in prefix_only.items())
            and not sensitive_values(prefix_only, [], 1) and set(sensitive_values(
                {'PGPASSWORD': 'SynthPg8812', 'GIT_CONFIG_VALUE_0': 'SynthGitVal77'}, [])) == {'PGPASSWORD', 'GIT_CONFIG_VALUE_0'})
    code, output = run_cli('run', '--id', 'ENV-REDACT', *common, '--env', 'AUDIT_LABEL=SynthLabel8831xyz',
                           '--redact', 'AUDIT_LABEL', '--', python, '-c',
                           'import os; assert os.environ["AUDIT_LABEL"]=="SynthLabel8831xyz"; '
                           'print(os.environ["AUDIT_LABEL"])')
    log = (evidence / 'ENV-REDACT.log').read_text(encoding='utf-8')
    meta_text = (evidence / 'ENV-REDACT.meta.json').read_text(encoding='utf-8')
    control(results, 'REDACT_NON_SENSITIVE_ENV_VALUE_IN_LOG_AND_META', code == 0
            and '[REDACTED:AUDIT_LABEL]' in log and 'SynthLabel8831xyz' not in log + meta_text + output, {'exit': code})

    pointers = dict.fromkeys(PROFILE_POINTER_ENV, str(root / 'synthetic-profile'))
    home_code = ('import os,json; from pathlib import Path; '
                 'print(json.dumps({"home":str(Path.home()), "vars":{k:os.environ.get(k) '
                 'for k in ["HOME","USERPROFILE","APPDATA"]}, "pointers":'
                 '{k:k in os.environ for k in ' + repr(sorted(pointers)) + '}}))')
    code, _ = run_cli('run', '--id', 'ISOLATED-HOME', *common, '--isolated-home', '--', python, '-c', home_code,
                      env={**os.environ, **pointers})
    observed = read_json(evidence / 'ISOLATED-HOME.log') if code == 0 else {}
    meta = read_json(evidence / 'ISOLATED-HOME.meta.json')
    control(results, 'ISOLATED_HOME_PATHS_AND_POINTERS', code == 0 and bool(observed.get('vars'))
            and all(is_within(Path(v), workspace) for v in observed['vars'].values())
            and is_within(Path(observed['home']), workspace)
            and observed.get('pointers') == dict.fromkeys(pointers, False)
            and set(pointers) <= set(meta.get('stripped_env', [])), {'exit': code})
    code, _ = run_cli('run', '--id', 'ISOLATED-OVERRIDES', *common, '--isolated-home', '--inherit', 'DOCKER_CONFIG',
                      '--env', 'GCM_INTERACTIVE=synthetic-override', '--', python, '-c',
                      'import os; assert os.environ["DOCKER_CONFIG"]==' + repr(pointers['DOCKER_CONFIG']) + '; '
                      'assert os.environ["GCM_INTERACTIVE"]=="synthetic-override"', env={**os.environ, **pointers})
    meta = read_json(evidence / 'ISOLATED-OVERRIDES.meta.json')
    control(results, 'ISOLATED_HOME_INHERIT_AND_ENV_WIN', code == 0
            and 'DOCKER_CONFIG' not in meta.get('stripped_env', []), {'exit': code})

    git_detail: dict[str, Any] = {'git': 'not installed; only Git part skipped'}
    git_ok = True
    if git_available():
        git_check = (
            'import os,subprocess,json; '
            'p=subprocess.run(["git","config","--show-origin","--get-all","credential.helper"],'
            'stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10); '
            'lines=p.stdout.decode("utf-8","replace").splitlines(); '
            'reset=p.returncode==0 and bool(lines) and lines[-1].split("\\t")[-1]==""; '
            'assert reset; '
            'p=subprocess.run(["git","credential","fill"],input=b"protocol=https\\nhost=credential.invalid\\n\\n",'
            'stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10); '
            'assert p.returncode!=0 and os.environ.get("GIT_TERMINAL_PROMPT")=="0" '
            'and os.environ.get("GCM_INTERACTIVE")=="never"; print("reset-effective")')
        code, _ = run_cli('run', '--id', 'GIT-REAL-RESET', *common, '--isolated-home', '--', python, '-c', git_check)
        git_ok = code == 0
        # A system helper trap proves Git skips the helper, while other system settings remain effective.
        system_config, helper_called = root / 'system.gitconfig', root / 'helper-called'
        trap = '!' + shlex.quote(python.replace('\\', '/')) + ' -c ' + shlex.quote(
            'from pathlib import Path; Path(' + repr(str(helper_called)) + ').write_text("called")')
        configured = subprocess.run(['git', 'config', '--file', str(system_config), 'credential.helper', trap],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
        configured = configured and subprocess.run(['git', 'config', '--file', str(system_config), 'audit.syntheticsetting',
                                                   'preserved'],
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                   timeout=10).returncode == 0
        code_trap, _ = run_cli('run', '--id', 'GIT-TRAP-RESET', *common, '--isolated-home', '--env',
                               'GIT_CONFIG_SYSTEM=' + str(system_config), '--', python, '-c', git_check + '; '
                               'assert subprocess.check_output(["git","config","--get","audit.syntheticsetting"]).strip()==b"preserved"')
        git_ok = git_ok and configured and code_trap == 0 and not helper_called.exists()
        git_detail = {'git': 'installed', 'real_exit': code, 'trap_exit': code_trap,
                      'system_helper_skipped': not helper_called.exists(), 'nosystem_fallback': False}
    control(results, 'ISOLATED_HOME_GIT_HELPER_RESET_EFFECTIVE', git_ok, git_detail)
    if git_detail['git'].startswith('not installed'):
        results[-1]['result'] = 'SKIPPED'

    project = root / 'include-project'
    write_text_file(project / '.env', 'SYNTH_CONFIG=sample\n')
    write_text_file(project / 'synthetic.pem', 'synthetic certificate fixture\n')
    write_text_file(project / 'app.txt', 'normal\n')
    glob_copy, exact_copy = root / 'glob-copy', root / 'exact-copy'
    code_glob, _ = run_cli('workspace', '--project', str(project), '--dest', str(glob_copy), '--include', '*',
                           '--no-git-snapshot')
    code_exact, _ = run_cli('workspace', '--project', str(project), '--dest', str(exact_copy), '--include', '.env',
                            '--include', 'synthetic.pem', '--no-git-snapshot')
    control(results, 'WORKSPACE_INCLUDE_GLOB_WITHHOLDS_EXACT_COPIES_CREDENTIALS', code_glob == code_exact == 0
            and all(not (glob_copy / rel).exists() and (exact_copy / rel).is_file() for rel in ('.env', 'synthetic.pem')),
            {'glob_exit': code_glob, 'exact_exit': code_exact})

    secret = 'SynthWideSecret-8829xyz'
    write_text_file(workspace / 'config.txt', 'DB_PASSWORD=' + secret + '\n')
    (workspace / WORKSPACE_MARKER).write_text(json.dumps({'secret_value_files': ['config.txt']}), encoding='utf-8')
    code, _ = run_cli('run', '--id', 'UTF16-OUTPUT', *common, '--', python, '-c',
                      'import sys; from pathlib import Path; value=Path("config.txt").read_text().strip().split("=",1)[1]; '
                      'sys.stdout.buffer.write(value.encode("utf-16-le")+b"\\n"+value.encode("utf-16-be"))')
    raw = (evidence / 'UTF16-OUTPUT.log').read_bytes()
    control(results, 'RUN_MASKS_UTF16_WORKSPACE_SECRET', code == 0
            and all(secret.encode(enc) not in raw and '[REDACTED:DB_PASSWORD#1]'.encode(enc) in raw
                    for enc in ('utf-16-le', 'utf-16-be')), {'exit': code})
    utf16_ok = True
    for encoding in ('utf-16', 'utf-16-le', 'utf-16-be'):
        target = root / (encoding + '.txt')
        target.write_bytes(('fixture: ' + FAKE_LIVE_KEY + '\n').encode(encoding))
        utf16_ok = utf16_ok and 'STRIPE_LIVE_KEY' in result_secret_hits(target)[0]
    control(results, 'VALIDATOR_SCANS_UTF16_WITH_AND_WITHOUT_BOM', utf16_ok)
    border = root / 'chunk-border.txt'  # a 24-char word whose first 20 chars end the first window
    border.write_bytes(b'!' * (8192 - 20) + b'AKIAZQXJ7KD2MPLR4TNWHB8C\n' + b'.' * 70000 + b'\n')
    control(results, 'VALIDATOR_CHUNK_BORDER_NO_FALSE_HIT', result_secret_hits(border) == ([], []))

    original, copy = root / 'original', root / 'exec-copy'
    write_json_new(copy / WORKSPACE_MARKER, {'project_root': str(original.resolve())})
    name = 'synthetic-tool.exe' if os.name == 'nt' else 'synthetic-tool'
    for directory in (original, copy, copy / 'bin'):
        write_text_file(directory / name, 'synthetic executable fixture\n')
        (directory / name).chmod(0o700)
    before = Path.cwd()
    try:
        os.chdir(original)
        lookup_env = {'PATH': '.', 'PATHEXT': '.EXE;.CMD'}
        resolved = resolve_executable(name, copy, lookup_env)
        directory_resolved = resolve_executable('bin/' + (name[:-4] if os.name == 'nt' else name), copy, lookup_env)
        (copy / name).unlink()  # Windows searches --cwd first; only bin/ may answer now
        relative_path = resolve_executable(name, copy, {'PATH': 'bin', 'PATHEXT': '.EXE;.CMD'})
    finally:
        os.chdir(before)
    symlink_kept = True
    if os.name != 'nt':  # a venv interpreter is a symlink; starting its target would lose the venv
        (copy / 'venv-bin').mkdir()
        (copy / 'venv-bin' / name).symlink_to(copy / 'bin' / name)
        linked = resolve_executable(name, copy, {'PATH': str(copy / 'venv-bin')})
        symlink_kept = linked == os.path.normpath(copy / 'venv-bin' / name)
    control(results, 'EXECUTABLE_RESOLVES_ABSOLUTE_IN_COPY', bool(resolved) and is_within(Path(resolved), copy)
            and Path(resolved).parent.resolve() == copy.resolve()
            and same_file(directory_resolved, copy / 'bin' / name) and symlink_kept
            and same_file(relative_path, copy / 'bin' / name)
            and all(Path(p).is_absolute() for p in (resolved, directory_resolved, relative_path)),
            {'absolute': bool(resolved and Path(resolved).is_absolute())})
    if os.name == 'nt':
        drive_refused = False
        try:
            resolve_executable(copy.drive + name, copy, {'PATH': '', 'PATHEXT': '.EXE'})
        except ToolError as exc:
            drive_refused = 'Drive-relative' in str(exc)
        control(results, 'EXECUTABLE_REFUSES_DRIVE_RELATIVE', drive_refused)
    else:
        results.append({'id': 'EXECUTABLE_REFUSES_DRIVE_RELATIVE', 'kind': 'TOOL_CONTROL', 'result': 'SKIPPED',
                        'detail': 'Windows only: drive-relative paths do not exist on POSIX'})
    refused = False
    try:
        resolve_executable(name, copy, {'PATH': str(original), 'PATHEXT': '.EXE;.CMD'})
    except ToolError as exc:
        refused = str((original / name).resolve()) in str(exc) and '--env' in str(exc)
    control(results, 'EXECUTABLE_REFUSES_ORIGINAL_PROJECT', refused)
    code, _ = run_cli('run', '--id', 'NO-COMMAND', *common, '--', 'audit-synthetic-not-found-998877')
    meta = read_json(evidence / 'NO-COMMAND.meta.json')
    control(results, 'RUN_META_RESOLVED_EXECUTABLE_INCLUDING_NOT_FOUND', code == 127
            and 'resolved_executable' in meta and meta['resolved_executable'] is None
            and same_file(read_json(evidence / 'ENV-VALUES.meta.json').get('resolved_executable'), python))

    live_marker = evidence / 'LIVE-NO-META.running.json'
    write_json_new(live_marker, {'id': 'LIVE-NO-META', 'stem': 'LIVE-NO-META', 'pid': 2 ** 30,
                                 'runner_pid': os.getpid(), 'started_at_utc': utc_now(),
                                 'resolved_executable': None})
    code, output = run_cli('stop', '--id', 'LIVE-NO-META', '--evidence-dir', str(evidence), '--wait', '0.1')
    control(results, 'STOP_KEEPS_MARKERS_WHILE_RUNNER_IS_ALIVE', code == 1 and 'no response' in output
            and live_marker.is_file() and (evidence / 'LIVE-NO-META.stop').is_file())

    unrelated = subprocess.Popen([python, '-c', 'import time; time.sleep(20)'], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=os.name != 'nt')
    try:
        marker = evidence / 'WRONG-PID.running.json'
        write_json_new(marker, {'id': 'WRONG-PID', 'stem': 'WRONG-PID', 'pid': unrelated.pid,
                                'runner_pid': 2 ** 30, 'started_at_utc': '2020-01-01T00:00:00Z'})
        if os.name == 'nt' or Path('/proc').is_dir() or shutil.which('ps'):
            code, output = run_cli('stop', '--id', 'WRONG-PID', '--evidence-dir', str(evidence), '--wait', '0.1')
            control(results, 'STOP_SPARES_REUSED_PID_AND_CLEARS_MARKERS', code == 0 and unrelated.poll() is None
                    and not marker.exists() and 'reassigned' in output)
        else:
            results.append({'id': 'STOP_SPARES_REUSED_PID_AND_CLEARS_MARKERS', 'kind': 'TOOL_CONTROL', 'result': 'SKIPPED',
                            'detail': 'Without /proc, only the group leader check is available on POSIX; '
                                      'time check skipped.'})
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=10)

    if os.name != 'nt':
        child_file, started = root / 'orphan-child.pid', utc_now()
        leader = subprocess.Popen([python, '-c', 'import subprocess,sys; from pathlib import Path; '
                                   'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                                   'Path(sys.argv[1]).write_text(str(p.pid))', str(child_file)],
                                  start_new_session=True)
        leader.wait(timeout=20)
        orphan = int(child_file.read_text()) if child_file.is_file() else None
        marker = evidence / 'ORPHAN-GROUP.running.json'
        write_json_new(marker, {'id': 'ORPHAN-GROUP', 'stem': 'ORPHAN-GROUP', 'pid': leader.pid, 'runner_pid': 2 ** 30,
                                'started_at_utc': started,
                                'command_started_before_utc': datetime.now(timezone.utc).isoformat()})
        code, output = run_cli('stop', '--id', 'ORPHAN-GROUP', '--evidence-dir', str(evidence), '--wait', '0.1')
        survived = orphan is not None and pid_alive(orphan)
        control(results, 'STOP_ENDS_ORPHANED_GROUP_MEMBERS', orphan is not None and code == 0 and not survived
                and not marker.exists(), {'exit': code, 'survived': survived})
        if survived:  # emergency cleanup after the observed result
            os.kill(orphan, signal.SIGKILL)
    else:
        results.append({'id': 'STOP_ENDS_ORPHANED_GROUP_MEMBERS', 'kind': 'TOOL_CONTROL', 'result': 'SKIPPED',
                        'detail': 'POSIX only: on Windows the job object terminates the command tree'})
    run, project, data = write_example_run(root / 'empty-authorization', 'nogo')
    state = read_json(run / 'RUN-STATE.json')
    state['authorization'] = []
    write_text_file(run / 'RUN-STATE.json', json.dumps(state))
    issues, _ = validate_audit(data, run, project, final=True)
    control(results, 'FINAL_EMPTY_AUTHORIZATION_ALLOWED', not any(e['code'] == 'E_RUN_AUTHORIZATION'
                                                                   for e in issues.errors))
    run, project, data = write_example_run(root / 'closing-order', 'nogo')
    issues, _ = validate_audit(data, run, project, final=True)
    control(results, 'FINAL_UNCLOSED_RUN_WARNS_AFTER_VALIDATION', not issues.errors and any(
        w['code'] == 'W_RUN_NOT_CLOSED' and 'before closure' in w['message'] for w in issues.warnings))
    return results


def cmd_selftest(args: argparse.Namespace) -> int:
    # Synthetic fixtures only; inherit TEMP's ACL also in Windows sandboxes (3.13 mkdir(mode=0o700) restricts it).
    root = Path(tempfile.gettempdir()) / ('audit-tools-selftest-' + os.urandom(8).hex())
    root.mkdir()
    try:
        results = (run_validator_controls(root / 'validator') + run_tool_controls(root / 'tools')
                   + run_core2_controls(root / 'core2') + run_checkpoint_workflow_controls(root / 'checkpoint-workflow')
                   + run_recovery_integrity_controls(root / 'recovery-integrity') + run_checkpoint_input_controls(root / 'checkpoint-input'))
        workspace_controls: list[dict[str, Any]] = []
        try:
            run_workspace_safeguard_controls(root / 'workspace-safeguards', workspace_controls)
        except Exception as exc:
            control(workspace_controls, 'WORKSPACE_SAFEGUARD_CONTROLS_COMPLETE', False, f'Control group aborted: {exc.__class__.__name__}')
        results += workspace_controls
        environment_controls: list[dict[str, Any]] = []
        try:
            run_environment_process_controls(root / 'environment-process', environment_controls)
        except Exception as exc:
            control(environment_controls, 'ENVIRONMENT_PROCESS_CONTROLS_COMPLETE', False, f'Control group aborted: {exc.__class__.__name__}')
        results += environment_controls
    finally:
        remove_tree(root)
    summary = {
        'check_type': 'AUDIT_TOOLS_SELFTEST', 'tool_version': TOOL_VERSION, 'checked_at_utc': utc_now(),
        'python': sys.version.split()[0], 'platform': sys.platform,
        'limitations': ['Checks the tools with constructed examples, not a software project.',
                        'A passed selftest proves neither truth nor completeness of an audit.',
                        'Deliberate falsification (such as invented metadata) remains possible; mistakes and '
                        'unsupported shortcuts are detected.'],
        'results': results,
        'summary': {kind: {'total': sum(r['kind'] == kind for r in results),
                           'passed': sum(r['kind'] == kind and r['result'] == 'PASS' for r in results)}
                    for kind in ('POSITIVE_CONTROL', 'NEGATIVE_CONTROL', 'TOOL_CONTROL')},
    }
    summary['summary']['failed'] = [r['id'] for r in results if r['result'] == 'FAIL']
    summary['summary']['skipped'] = [r['id'] for r in results if r['result'] == 'SKIPPED']
    if args.out:
        write_json_new(Path(args.out), summary)
    print_json(summary if not args.out else summary['summary'])
    return 0 if not summary['summary']['failed'] else 1


def cmd_example(args: argparse.Namespace) -> int:
    print_json({'nogo': example_audit, 'go': example_go_audit, 'interim': example_interim_audit}[args.variant]())
    return 0


# --------------------------------------------------------------------------- CLI

def add_snapshot_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--project', default='.', help='Project root (default: current directory)')
    parser.add_argument('--results-dir', help='Results directory, excluded (default: <project>/audit-results)')
    parser.add_argument('--exclude', action='append', default=[], metavar='PATH_OR_GLOB',
                        help='Additional exclusion, relative to the project (repeatable)')
    parser.add_argument('--mode', choices=['auto', 'git', 'filesystem'], default='auto',
                        help='auto: Git if available; otherwise filesystem')
    parser.add_argument('--no-default-excludes', action='store_true',
                        help='Filesystem mode without default exclusions (node_modules, dist, ...)')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='audit_tools.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init', help='Create run directory and capture start baseline')
    add_snapshot_options(p)
    p = sub.add_parser('cleanup', help='Remove working copy from workspace (checks marker and location)')
    p.add_argument('--dest', required=True, help='Working copy with .audit-workspace.json')
    p.add_argument('--project', required=True, help='Original project; must match the marker')
    p.add_argument('--dry-run', action='store_true', help='Only show scope, remove nothing')
    p = sub.add_parser('runs', help='List existing runs with version, phase and closure status')
    p.add_argument('--project', default='.', help='Project root (default: current directory)')
    p.add_argument('--results-dir', help='Results directory (default: <project>/audit-results)')
    p.add_argument('--inspect-all', action='store_true', help='Validate live state and rehash checkpoints for every run')
    p = sub.add_parser('baseline', help='Capture or compare project state')
    add_snapshot_options(p)
    p.add_argument('--out', help='Save state as a new JSON file (never overwrites)')
    p.add_argument('--compare', metavar='OLD.json',
                   help='Compare with earlier state; exit code 1 on deviation or side effect')
    p = sub.add_parser('workspace', help='Create isolated working copy')
    add_snapshot_options(p)
    p.add_argument('--dest', required=True, help='Empty destination directory outside the project')
    p.add_argument('--expect-baseline', metavar='BASELINE.json',
                   help='Start baseline of the run (evidence/baseline/baseline-start.json); reports changes since then')
    p.add_argument('--include', action='append', metavar='GLOB',
                   help='Additional paths to copy, e.g. ignored paths (credentials only with exact filename)')
    p.add_argument('--no-git-snapshot', action='store_true', help='No Git repository with snapshot commit in the copy')
    p.add_argument('--record', help='Also save summary as a new JSON file')
    p = sub.add_parser('run', help='Execute command in the working copy: run [options] -- command ...')
    p.add_argument('--id', required=True, help='Check ID, becomes filename (e.g. CHK-012)')
    p.add_argument('--evidence-dir', required=True, help='Destination directory for log and metadata (evidence/logs of the run)')
    p.add_argument('--cwd', help='Working directory in the working copy')
    p.add_argument('--outside-workspace', action='store_true',
                   help='Deliberately outside a working copy; only for read-only commands')
    p.add_argument('--timeout', type=float, default=1800, help='Seconds; 0 = no limit (default 1800)')
    p.add_argument('--tail', type=int, default=30, help='Number of final log lines to display')
    p.add_argument('--shell', action='store_true', help='Execute command through the system shell')
    p.add_argument('--ci', action='store_true', help='Set CI=true')
    p.add_argument('--no-color', action='store_true', help='Disable color output')
    p.add_argument('--env', action='append', metavar='NAME=VALUE', help='Set environment variable (repeatable)')
    p.add_argument('--inherit', action='append', metavar='NAME',
                   help='Deliberately pass through an otherwise stripped variable (credentials, cloud accounts)')
    p.add_argument('--isolated-home', action='store_true',
                   help='Redirect HOME, user profile and app data to the working copy (isolates profile credentials)')
    p.add_argument('--redact', action='append', metavar='NAME', help='Also mask the value of this variable')
    p = sub.add_parser('stop', help='Stop an active run command')
    p.add_argument('--id', required=True)
    p.add_argument('--evidence-dir', required=True)
    p.add_argument('--wait', type=float, default=30, help='Seconds to wait for metadata')
    p = sub.add_parser('envkeys', help='Show keys and value shapes of .env files without values')
    p.add_argument('files', nargs='+')
    p = sub.add_parser('validate', help='Check audit.json')
    p.add_argument('run_dir', help='Run directory with audit.json')
    p.add_argument('--project', help='Original project (default: from the start baseline)')
    p.add_argument('--final', action='store_true', help='Final check: verdict, artifacts and contents required')
    p.add_argument('--json', action='store_true', help='Print result as JSON')
    p = sub.add_parser('checkpoint', help='Create or verify the immutable interim checkpoint')
    p.add_argument('action', choices=['create', 'verify'])
    p.add_argument('run_dir')
    p.add_argument('--project', help='Original project (create; default from baseline)')
    p = sub.add_parser('selftest', help='Run positive and negative controls of the tools')
    p.add_argument('--out', help='Save result as a new JSON file')
    p = sub.add_parser('example', help='Print fictional audit.json (materialize referenced artifacts before validating)')
    p.add_argument('--variant', choices=['go', 'nogo', 'interim'], default='go')
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    arguments = list(sys.argv[1:] if argv is None else argv)
    command_tail: list[str] = []
    if arguments and arguments[0] == 'run' and '--' in arguments:
        index = arguments.index('--')
        arguments, command_tail = arguments[:index], arguments[index + 1:]
    args = build_parser().parse_args(arguments)
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        'init': cmd_init, 'runs': cmd_runs, 'cleanup': cmd_cleanup, 'baseline': cmd_baseline, 'workspace': cmd_workspace, 'stop': cmd_stop,
        'envkeys': cmd_envkeys, 'validate': cmd_validate, 'checkpoint': cmd_checkpoint, 'selftest': cmd_selftest, 'example': cmd_example,
    }
    try:
        if args.command == 'run':
            return cmd_run(args, command_tail)
        return handlers[args.command](args)
    except ToolError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
