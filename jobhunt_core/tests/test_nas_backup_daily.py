"""Exercise the operational script without a NAS, credentials or real dumps."""
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/nas_backup_daily.sh'


@pytest.mark.parametrize('failure', ['', 'dump', 'verify'])
def test_daily_backup_fails_closed_and_releases_lock(tmp_path, failure):
    root = tmp_path / 'private'
    ops = root / 'e13-erasure'
    ops.mkdir(parents=True)
    root.chmod(0o700)
    ops.chmod(0o700)
    fake = tmp_path / 'docker'
    calls = tmp_path / 'calls'
    fake.write_text('''#!/bin/bash
set -eu
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *pg_dump*) test "$FAILURE" != dump; echo synthetic-archive ;;
  *pg_restore*) test "$FAILURE" != verify; cat >/dev/null ;;
  *"python -c"*) echo '{"synthetic":"inventory"}' ;;
  *) echo '{"retention_met":true}' ;;
esac
''')
    fake.chmod(0o700)
    script = tmp_path / 'daily.sh'
    text = SCRIPT.read_text().replace(
        '/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker', str(fake)
    ).replace('/share/CACHEDEV1_DATA/Public/unification-e10.XXkEjw88', str(root))
    # UID assertion is QNAP-specific, not a requirement on the test runner.
    text = text.replace('test "$(id -u)" = 1000', f'test "$(id -u)" = {os.getuid()}')
    # Do not flush the host's disks during this synthetic unit test.
    text = text.replace('\nsync\n', '\ntrue\n')
    script.write_text(text)
    result = subprocess.run(['bash',str(script)],env={**os.environ,'CALLS':str(calls),
                            'FAILURE':failure},capture_output=True,text=True)
    log = calls.read_text()
    assert not (ops/'backup-running').exists()
    if failure:
        assert result.returncode != 0
        assert '--kind temporary' in log
        assert '--apply' not in log
        assert not (ops/'last-backup-success').exists()
        assert not list(ops.glob('daily-*/*.dump'))
    else:
        assert result.returncode == 0, result.stderr
        assert log.count('--kind backup') == 3
        assert '--apply' in log
        assert len(list(ops.glob('daily-*/*.dump'))) == 3
        assert (ops/'last-backup-success').is_file()
