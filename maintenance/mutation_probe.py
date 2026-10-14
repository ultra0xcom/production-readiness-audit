"""Mutation probe: revert single fixes in a copy of audit_tools.py and check that the matching selftest control fails.
Usage: mutation_probe.py <audit_tools.py> <work_dir> [mutant ...]"""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding='utf-8')
work = Path(sys.argv[2])
only = set(sys.argv[3:])
MASK = 'MASKING_CATCHES_SYMBOL_PASSWORDS_AND_KEEPS_EVIDENCE'
# mutant name -> (control that must fail, original text, mutated text)
MUTANTS = {
    'code_ref_splitlines': ('CODE_REF_LINES_COUNT_LIKE_GREP',
        "lines = target.read_text(encoding='utf-8', errors='replace').split('\\n')",
        "lines = target.read_text(encoding='utf-8', errors='replace').splitlines()"),
    'no_assignment_masking': ('RUN_MASKS_SECRET_VALUES_FROM_COPY',
        "    return ''.join(parts) + text[last:], count\n",
        "    return text, 0\n"),
    'no_copy_values': ('RUN_MASKS_SECRET_VALUES_FROM_COPY',
        "                values[f'{name}#{len(values) + 1}'] = value\n    return values\n",
        "                values[f'{name}#{len(values) + 1}'] = value\n    return {}\n"),
    'path_refs_no_boundary': ('ORIGINAL_PATH_REFS_UNICODE_AND_SIBLINGS',
        "re.escape(v) + r'(?![\\w.-])'", "re.escape(v)"),
    'envkeys_no_continuation': ('ENVKEYS_MULTILINE_VALUE_HIDDEN',
        "            if open_quote is not None:\n", "            if False:\n"),
    'compare_drops_excludes': ('BASELINE_COMPARE_KEEPS_INIT_EXCLUDES',
        "    if not args.exclude:\n        results = Path", "    if False:\n        results = Path"),
    'parent_ignore_unchecked': ('BASELINE_PROJECT_IGNORED_BY_PARENT_REPO',
        "            if ignored:\n                mode = 'filesystem'", "            if False:\n                mode = 'filesystem'"),
    'go_ignores_violated_journey': ('GO_WITH_VIOLATED_T1_JOURNEY',
        "journey.get('status') == 'VIOLATED':", "journey.get('status') == 'NEVER':"),
    'stop_ignores_aborted_run': ('STOP_CLEARS_MARKERS_OF_ABORTED_RUN',
        "        elif (isinstance(info.get('pid'), int)\n",
        "        elif (False\n"),
    'mask_old_value_class': (MASK,
        r"""|(?P<uvalue>[^\s"\'`]{4,})')""",
        r"""|(?P<uvalue>[^\s"\'`,;#(){}\[\]<>$]{4,})(?=[\s,;#]|$)')"""),
    'mask_no_lookbehind': (MASK,
        r"r'(?<![A-Za-z0-9_./\\])(?P<name>", r"r'(?P<name>"),
    'record_any_value': (MASK,
        "    return len(value) >= 8 and classes >= 2 and not re.fullmatch", "    return len(value) >= 6 or not re.fullmatch"),
    'workspace_ignores_init_excludes': ('WORKSPACE_KEEPS_INIT_EXCLUDES',
        "        inherit_reference_options(args, reference)  # init --exclude", "        pass  # init --exclude"),
    'cleanup_marker_first': ('CLEANUP_KEEPS_MARKER_UNTIL_DONE_AND_REFUSES_CWD_INSIDE',
        "    for child in list(dest.iterdir()):\n        if child.name != WORKSPACE_MARKER:\n            remove_entry(child)\n",
        "    remove_tree(dest)\n    dest.mkdir(exist_ok=True) if False else None\n"),
    'cleanup_allows_cwd_inside': ('CLEANUP_KEEPS_MARKER_UNTIL_DONE_AND_REFUSES_CWD_INSIDE',
        "    if not args.dry_run and is_within(Path.cwd(), dest):", "    if False:"),
    'empty_report_passes': ('EMPTY_REPORT',
        "            if report is not None and needle not in report:",
        "            if report and needle not in report:"),
    # third review of the masking
    'url_strong_name_ignored': (MASK,
        "    if PLAIN_URL_VALUE.fullmatch(bare):\n        return bool(STRONG_SECRET_NAME.search(name))\n",
        "    if PLAIN_URL_VALUE.fullmatch(bare):\n        return False\n"),
    'url_semicolon_exempt': (MASK, r"[^@\s?#;=]*')", r"[^@\s?#]*')"),
    'code_any_dotted': (MASK,
        "CODE_VALUE = re.compile(r'",
        r"CODE_VALUE = re.compile(r'[a-z_][a-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\)*|"),
    'tight_colon_line_start_off': (MASK,
        "        if match.group('dotted') or at_line_start:\n            return at_line_start\n",
        "        if match.group('dotted') or at_line_start:\n            return False\n"),
    'go_struct_tight_off': (MASK,
        "        return (depth > 0 and before in ('{', '[', ' ', '\\t', ',')\n",
        "        return (False\n"),
    'brackets_depth_ignored': (MASK, "        return (depth > 0 and before in", "        return (False and before in"),
    'header_quote_off': (MASK, "            return '-' in match.group('name')\n", "            return False\n"),
    'brackets_masked_too': (MASK,
        "                end = unclosed_end(text, value.start(group), end, opened)\n", "                pass\n"),
    'no_escaped_json': (MASK, r"""{4,})\\"'  # escaped JSON""", r"""{4,})\\"(?!)'  # escaped JSON"""),
    'escaped_json_no_escapes': (MASK, r"""(?P<edq>(?:[^"\\\r\n]|\\\\(?:\\\\|\\"|[^"\\\r\n])){4,})""",
                                r"""(?P<edq>[^"\\\r\n]{4,})"""),
    'context_ignored': (MASK,
        "    if match.group('sep') != ':':\n        return True\n", "    if True:\n        return True\n"),
    'no_auth_header': (MASK,
        "    text = AUTH_HEADER.sub(header, text)\n", "    pass\n"),
    'path_exempt_any_name': (MASK,
        "    if LOCAL_PATH_VALUE.fullmatch(bare):\n        return bool(PASSWORD_NAME.search(name))\n",
        "    if LOCAL_PATH_VALUE.fullmatch(bare):\n        return False\n"),
    'passphrase_not_recorded': (MASK,
        "    return worth_recording(value) or bool(PASSWORD_NAME.search(name)) and len(value) >= 12\n",
        "    return worth_recording(value)\n"),
    'digits_never_masked': (MASK,
        "    if bare.isdigit():\n        return bool(PASSWORD_NAME.search(name))\n",
        "    if bare.isdigit():\n        return False\n"),
    'no_fat_arrow': (MASK, r"(?P<sep>=>|=(?![=>])|", r"(?P<sep>=(?!=)|"),
    'name_no_digit_start': (MASK,
        r"(?P<name>[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", r"(?P<name>[A-Za-z_][A-Za-z0-9_-]{0,63}"),
    'other_values_consumed': (MASK,
        "        if (start < skip or not SENSITIVE_ENV_NAME.search(name)\n"
        "                or not assignment_in_context(text, match, line_start, depth)):\n            continue\n",
        "        if start < skip:\n            continue\n"
        "        if not SENSITIVE_ENV_NAME.search(name) or not assignment_in_context(text, match, line_start, depth):\n"
        "            consumed = ASSIGNED_VALUE.match(text, match.end())\n"
        "            skip = consumed.end() if consumed else skip\n            continue\n"),
    'exempt_values_skipped': (MASK,
        "            if is_sensitive_assignment(name, text[value.start(group):end]):\n                skip = end\n",
        "            skip = value.end()\n            if is_sensitive_assignment(name, text[value.start(group):end]):\n"),
    'no_block_scalar': (MASK, r"(?P<block>\S[^\r\n]{3,})", r"(?P<block>\S[^\r\n]{3,})(?!)"),
    'no_yaml_tag': (MASK, r"""[ \t]*(?:!!?[A-Za-z0-9_]+[ \t]+)?')""", r"""[ \t]*')"""),
    'descriptive_names_masked': (MASK,
        "    if DESCRIPTIVE_NAME.search(name):\n        return False\n", "    if False:\n        return False\n"),
    'generated_not_checked': (MASK,
        "    if looks_generated(bare):\n        return True\n", "    if False:\n        return True\n"),
    'no_triple_quotes': (MASK, r"""r'"{3}(?P<tdq>[^"\r\n]{4,})"{3}|\'{3}(?P<tsq>[^\'\r\n]{4,})\'{3}'""",
                         r"""r'"{3}(?P<tdq>[^"\r\n]{4,})"{3}(?!)|\'{3}(?P<tsq>[^\'\r\n]{4,})\'{3}(?!)'"""),
    # fifth review of the masking
    'depth_not_clamped': (MASK,
        "                opened[BRACKET_PAIRS[char]] = max(0, opened[BRACKET_PAIRS[char]] - 1)\n",
        "                opened[BRACKET_PAIRS[char]] -= 1\n"),
    'ansi_counted': (MASK, r"BRACKET = re.compile(r'\x1b\[|[()", r"BRACKET = re.compile(r'[()"),
    'closers_trimmed_freely': (MASK,
        "        if available[opener] <= 0 or counts[opener] >= counts[closer]:\n",
        "        if counts[opener] >= counts[closer]:\n"),
    'redacted_before_length': (MASK,
        "    if len(bare) > 200:\n        return True\n"
        "    if '[REDACTED' in bare or bare.lower().rstrip('.') in NON_SECRET_VALUES:\n        return False\n",
        "    if '[REDACTED' in bare or bare.lower().rstrip('.') in NON_SECRET_VALUES:\n        return False\n"
        "    if len(bare) > 200:\n        return True\n"),
    'gap_backslash_off': (MASK, """not in ('"', "'", '\\\\'):""", """not in ('"', "'"):"""),
    # tool fixes 1.3: environment, profile isolation, executables, encodings and interrupted runs
    'value_based_strip_off': ('ENV_STRIPS_VALUES_AND_KEEPS_SAFE_COUNTEREXAMPLES',
        "    if sensitive_env_value(value):\n", "    if False:\n"),
    'public_urls_stripped_by_name': ('ENV_CREDENTIAL_URLS_STRIPPED_PUBLIC_URLS_KEPT',
        "    if sensitive_env_value(value):\n",
        "    if name in {'PIP_INDEX_URL', 'UV_INDEX_URL', 'HTTPS_PROXY', 'HTTP_PROXY'} or sensitive_env_value(value):\n"),
    'env_only_auth_regex_off': ('ENV_ONLY_AUTH_AND_GIT_NAMES_STRIPPED',
        "or bool(SENSITIVE_ENV_ONLY_NAME.search(upper))", "or False"),
    'inherit_auth_masking_off': ('INHERIT_ENV_ONLY_AUTH_AND_VALUE_MASKED',
        "                SENSITIVE_ENV_ONLY_NAME.search(upper)\n                or SENSITIVE_ENV_NAME",
        "                False\n                or SENSITIVE_ENV_NAME"),
    'masking_by_prefix_list': ('MASKING_SKIPS_PREFIX_ONLY_AND_GIT_STRUCTURE_VALUES',
        "        elif len(value) >= min_length and (sensitive_env_value(value) or upper not in NEVER_STRIPPED_ENV\n",
        "        elif len(value) >= min_length and (should_strip(name, value) or upper not in NEVER_STRIPPED_ENV\n"),
    'git_structure_masked': ('MASKING_SKIPS_PREFIX_ONLY_AND_GIT_STRUCTURE_VALUES',
        "and not GIT_CONFIG_STRUCTURE_NAME.match(upper) and (", "and ("),
    'resolve_follows_symlinks': ('EXECUTABLE_RESOLVES_ABSOLUTE_IN_COPY',
        "            candidate = Path(os.path.normpath(cwd / directory / name))\n",
        "            candidate = (cwd / directory / name).resolve()\n"),
    'inherit_opt_in_off': ('INHERIT_ENV_ONLY_AUTH_AND_VALUE_MASKED',
        "if name.upper() not in inherit and (", "if ("),
    'helper_reset_off': ('ISOLATED_HOME_GIT_HELPER_RESET_EFFECTIVE',
        "overrides.update({'GIT_CONFIG_COUNT': '1'", "overrides.update({'GIT_CONFIG_COUNT': '0'"),
    'profile_pointer_vars_kept': ('ISOLATED_HOME_PATHS_AND_POINTERS',
        "        and name.upper() in PROFILE_POINTER_ENV))", "        and False))"),
    'isolated_home_paths_off': ('ISOLATED_HOME_PATHS_AND_POINTERS',
        "                overrides[name] = str(target)\n", "                overrides[name] = str(workspace_root.parent)\n"),
    'env_overrides_not_last': ('ISOLATED_HOME_INHERIT_AND_ENV_WIN',
        "        overrides[name] = value\n", "        overrides.setdefault(name, value)\n"),
    'in_original_refusal_off': ('EXECUTABLE_REFUSES_ORIGINAL_PROJECT',
        "            if original and is_within(candidate, Path(original)):\n", "            if False:\n"),
    'utf16_redactor_encodings_off': ('RUN_MASKS_UTF16_WORKSPACE_SECRET',
        "{'utf-8', 'utf-16-le', 'utf-16-be', locale.getpreferredencoding(False) or 'utf-8'}",
        "{'utf-8', locale.getpreferredencoding(False) or 'utf-8'}"),
    'validator_utf16_decode_off': ('SECRET_IN_UTF16_RESULT_FILE',
        "        encoding = 'utf-8'\n        if sample[:2]",
        "        encoding = 'utf-8'\n        if b'\\0' in sample:\n            return [], []\n        if sample[:2]"),
    'validator_bomless_utf16_off': ('VALIDATOR_SCANS_UTF16_WITH_AND_WITHOUT_BOM',
        "        elif len(sample) >= 16 and b'\\0' in sample:\n", "        elif False:\n"),
    'posix_killpg_off': ('STOP_CLEARS_MARKERS_OF_ABORTED_RUN',
        "            os.killpg(pid, sig)\n", "            return 'unverified'\n"),
    'stop_control_self_kill_reintroduced': ('STOP_CLEARS_MARKERS_OF_ABORTED_RUN',
        "    command_was_alive = child is not None and pid_alive(child)\n",
        "    if child is not None and pid_alive(child):\n"
        "        os.kill(child, signal.SIGTERM)\n        time.sleep(0.5)\n"
        "    command_was_alive = child is not None and pid_alive(child)\n"),
    'windows_process_identity_off': ('STOP_SPARES_REUSED_PID_AND_CLEARS_MARKERS',
        "            if not start <= created <= end + 1:", "            if False:"),
    'posix_process_identity_off': ('STOP_SPARES_REUSED_PID_AND_CLEARS_MARKERS',
        "        if not start - 2 <= created <= end + 2:", "        if False:"),
    'group_members_ignored': ('STOP_ENDS_ORPHANED_GROUP_MEMBERS',
        "    elif not posix_group_alive(pid):\n        return 'killed'\n", "    else:\n        return 'killed'\n"),
    'reused_pid_markers_kept': ('STOP_SPARES_REUSED_PID_AND_CLEARS_MARKERS',
        "            if outcome == 'unverified':\n", "            if outcome != 'killed':\n"),
    'chunk_border_unlimited': ('VALIDATOR_CHUNK_BORDER_NO_FALSE_HIT',
        "secret_pattern_hits(text, limit=max(0, len(text) - 1024))", "secret_pattern_hits(text)"),
    'pgp_key_missed': ('ENV_STRIPS_VALUES_AND_KEEPS_SAFE_COUNTEREXAMPLES',
        "PRIVATE KEY(?: BLOCK)?-----')  # PEM and PGP", "PRIVATE KEY-----')  # PEM and PGP"),
    'docker_bundler_names_off': ('ENV_ONLY_AUTH_AND_GIT_NAMES_STRIPPED',
        "r'|^DOCKER_AUTH_CONFIG$|^BUNDLE_\\w*__'", "r'|^NO_SUCH_AUDIT_NAME$'"),
    'json_auth_value_off': ('ENV_STRIPS_VALUES_AND_KEEPS_SAFE_COUNTEREXAMPLES',
        "                or JSON_AUTH_VALUE.search(value)\n", ""),
    'drive_relative_allowed': ('EXECUTABLE_REFUSES_DRIVE_RELATIVE',
        "    if os.name == 'nt' and re.match(r'[A-Za-z]:(?![\\\\/])', command):\n", "    if False:\n"),
    'relative_path_from_caller_cwd': ('EXECUTABLE_RESOLVES_ABSOLUTE_IN_COPY',
        "            candidate = Path(os.path.normpath(cwd / directory / name))\n",
        "            candidate = Path(os.path.normpath(Path(directory).absolute() / name))\n"),
    'empty_authorization_rejected': ('FINAL_EMPTY_AUTHORIZATION_ALLOWED',
        "if not isinstance(authorization, list) or not all(", "if not authorization or not isinstance(authorization, list) or not all("),
    'harmless_switch_values_stripped': ('ENV_HARMLESS_SWITCHES_KEPT',
        "value.lower() not in ENV_SWITCH_VALUES)\n", "True)\n"),
    'chunked_scan_off': ('SECRET_IN_LARGE_RESULT_FILE',
        "            raw = handle.read(65536)\n", "            raw = b''\n"),
    'authorization_check_off': ('FINAL_AUTHORIZATION_MISSING',
        "if not isinstance(authorization, list) or not all(", "if False and not all("),
    'resume_check_off': ('EMPTY_RESUME',
        "        if resume is not None and not resume.strip():\n", "        if False:\n"),
    'link_control_silent_pass': ('LINK_CONTROL_REJECTS_UNCREATED_LINK',
        "    return linked and cleanup_ok\n", "    return cleanup_ok\n"),
    'include_glob_copies_credentials': ('WORKSPACE_INCLUDE_GLOB_WITHHOLDS_EXACT_COPIES_CREDENTIALS',
        "not (explicit and path == src)", "not True"),
    'explicit_redact_off': ('REDACT_NON_SENSITIVE_ENV_VALUE_IN_LOG_AND_META',
        "    forced_names = {name.upper() for name in forced}\n", "    forced_names = set()\n"),
    'live_runner_markers_cleared': ('STOP_KEEPS_MARKERS_WHILE_RUNNER_IS_ALIVE',
        "and not (isinstance(info.get('runner_pid'), int) and pid_alive(info['runner_pid']))",
        "and True"),
    'missing_closed_at_warning_off': ('FINAL_UNCLOSED_RUN_WARNS_AFTER_VALIDATION',
        "                if not nonempty(run_state.get('closed_at_utc')):\n", "                if False:\n"),
    'meta_resolved_executable_off': ('RUN_META_RESOLVED_EXECUTABLE_INCLUDING_NOT_FOUND',
        "        'resolved_executable': redactor.redact_text(executable) if executable else None,\n"
        "        'outside_workspace': workspace_root is None, 'stripped_env': stripped,\n",
        "        'resolved_executable': None,\n"
        "        'outside_workspace': workspace_root is None, 'stripped_env': stripped,\n"),
    'running_resolved_executable_off': ('STOP_ENDS_SERVICE_AND_RECORDS_EVIDENCE',
        "                                          'resolved_executable': redactor.redact_text(executable) if executable else None,\n",
        "                                          'resolved_executable': None,\n"),
}
resolver_start = source.index('def resolve_executable(')
resolver_end = source.index('\n\ndef find_run_dir(', resolver_start)
MUTANTS['old_shutil_which_resolution'] = ('EXECUTABLE_RESOLVES_ABSOLUTE_IN_COPY',
    source[resolver_start:resolver_end],
    "def resolve_executable(command: str, cwd: Path, env: dict[str, str]) -> str | None:\n"
    "    if '/' in command or '\\\\' in command:\n"
    "        candidate = Path(command)\n"
    "        if not candidate.is_absolute():\n            candidate = cwd / candidate\n"
    "        return shutil.which(str(candidate))\n"
    "    return shutil.which(command, path=env.get('PATH'))\n")
PLATFORMS = {'posix_killpg_off': 'posix', 'resolve_follows_symlinks': 'posix', 'group_members_ignored': 'posix',
             'drive_relative_allowed': 'nt', 'stop_control_self_kill_reintroduced': 'posix',
             'posix_process_identity_off': 'linux', 'windows_process_identity_off': 'nt'}
unknown = only - MUTANTS.keys()
if unknown:
    raise SystemExit('Unknown mutants: ' + ', '.join(sorted(unknown)))
# Check every target before platform filtering, so a stale target can never be skipped.
invalid_targets = {name: source.count(old) for name, (_, old, _) in MUTANTS.items()
                   if source.count(old) != 1}
if invalid_targets:
    raise SystemExit('Mutation targets must occur exactly once: ' + json.dumps(invalid_targets, sort_keys=True))
if work.exists() and any(work.iterdir()):
    raise SystemExit(f'Work directory must be empty: {work}')
work.mkdir(parents=True, exist_ok=True)


def probe(name: str) -> tuple[str, str]:
    control_id, old, new = MUTANTS[name]
    platform = PLATFORMS.get(name)
    if platform and (sys.platform != 'linux' if platform == 'linux' else os.name != platform):
        return name, f'NOT_APPLICABLE: {platform} only ({control_id})'
    if source.count(old) != 1:
        return name, f'ERROR: anchor {source.count(old)}x'
    target_dir = work / name
    (target_dir / 'tools').mkdir(parents=True, exist_ok=True)
    (target_dir / 'tools' / 'audit_tools.py').write_text(source.replace(old, new), encoding='utf-8', newline='\n')
    out = target_dir / 'selftest.json'
    try:
        with open(target_dir / 'selftest-output.txt', 'wb') as output:
            subprocess.run([sys.executable, '-B', str(target_dir / 'tools' / 'audit_tools.py'), 'selftest', '--out', str(out)],
                           stdout=output, stderr=subprocess.STDOUT, timeout=600)
    except subprocess.TimeoutExpired:
        return name, f'ERROR: timeout ({control_id})'
    if not out.is_file():
        return name, f'ERROR: selftest aborted without report ({control_id} not reached)'
    data = json.loads(out.read_text(encoding='utf-8'))
    failed = data['summary']['failed']
    return name, f'KILLED by {control_id}' if control_id in failed else f'SURVIVED (failed: {failed})'


# MUTATION_PROBE_WORKERS lowers the parallel selftests when RAM is short (default 4)
with ThreadPoolExecutor(max_workers=max(1, int(os.environ.get('MUTATION_PROBE_WORKERS', '4')))) as executor:
    summary = dict(executor.map(probe, [name for name in MUTANTS if not only or name in only]))
print(json.dumps(summary, indent=2))
raise SystemExit(0 if all(value.startswith(('KILLED by ', 'NOT_APPLICABLE:')) for value in summary.values()) else 1)
