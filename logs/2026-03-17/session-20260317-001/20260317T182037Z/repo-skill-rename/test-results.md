# Test Results

- unit_tests_command: `python3 -m unittest discover -s tests -v`
- unit_tests_result: PASS
- unit_tests_summary: 11 tests passed
- integration_install_command: `MINDEX_SKIP_AUTO_CONFIGURE=1 <venv>/bin/pip install -e /home/andrew/mindex`
- integration_install_result: PASS
- integration_configure_command: `<venv>/bin/mindex configure --project-root /home/andrew/mindex --codex-home <tmp>/codex-home --dry-run`
- integration_configure_result: PASS
- overall: PASS
