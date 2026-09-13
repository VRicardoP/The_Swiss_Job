#!/bin/bash
# Requires an authenticated QNAP administrator. Never invoked by the app.
set -euo pipefail
umask 077
test "$(id -u)" = 0 || { echo 'Authenticated NAS administrator required'; exit 1; }
R=/share/CACHEDEV1_DATA/Public/unification-e10.XXkEjw88/e13-erasure
test -f "$R/retention.json"
test -x /usr/bin/sudo
test -x /sbin/log_tool
/usr/bin/sudo -u Ricardo /bin/bash -n "$R/nas_backup_daily.sh"
tag='# jobhunt-e13-private-backups'
line="20 2 * * * /usr/bin/sudo -u Ricardo /bin/bash $R/nas_backup_daily.sh >> $R/backup-cron.log 2>&1 || /sbin/log_tool -t 2 -a 'Jobhunt backup/retention failed; inspect private backup-cron.log' $tag"
backup="$R/crontab-before-$(date -u +%Y%m%dT%H%M%SZ)"
cp /etc/config/crontab "$backup"
temporary=$(mktemp "$R/crontab-new.XXXXXXXX")
trap 'rm -f "$temporary"' EXIT
# Replace only our tagged entry. Preserve every unrelated job byte-for-byte.
awk -v tag="$tag" 'index($0,tag)==0 {print}' /etc/config/crontab > "$temporary"
printf '%s\n' "$line" >> "$temporary"
/usr/bin/crontab "$temporary"
cp "$temporary" /etc/config/crontab
sync
/usr/bin/crontab -l | grep -F "$tag"
echo 'Daily 02:20 NAS-local-time job installed. Verify its first result and alarm path.'
