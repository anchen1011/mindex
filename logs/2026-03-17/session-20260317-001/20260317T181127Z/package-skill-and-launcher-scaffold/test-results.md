# Test Results

- unit_tests_command: `python3 -m unittest discover -s tests -v`
- unit_tests_result: PASS
- unit_tests_summary: 11 tests passed
- integration_test_1: FAIL
- integration_test_1_reason: `python3 -m venv` initially failed because `python3.11-venv` was not installed
- recovery_action: PASS
- recovery_action_detail: installed `python3.11-venv` with `apt`
- integration_test_2_command: `MINDEX_SKIP_AUTO_CONFIGURE=1 <venv>/bin/pip install -e /home/andrew/mindex` then `<venv>/bin/mindex configure --project-root /home/andrew/mindex --codex-home <tmp>/codex-home --dry-run`
- integration_test_2_result: PASS
- integration_test_3_command: `CODEX_HOME=<tmp>/codex-home <venv>/bin/pip install -e /home/andrew/mindex` with a fake `codex` binary earlier on `PATH`
- integration_test_3_result: PASS
- integration_test_3_summary: editable install hook completed, wrote a managed Codex profile, and recorded a configure log entry
- final_unit_test_rerun: PASS
- final_unit_test_rerun_summary: 11 tests passed after cleanup and `.gitignore` updates
- overall: PASS
