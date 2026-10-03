#!/usr/bin/env bash
# Read-only diagnostics: why is our public key rejected?

echo "== effective sshd config"
sshd -T 2>/dev/null | grep -iE "authorizedkeysfile|pubkeyauthentication|strictmodes|permitrootlogin"

echo
echo "== config file lines"
grep -rniE "authorizedkeysfile|pubkeyauthentication|permitrootlogin" /etc/ssh/sshd_config /etc/ssh/sshd_config.d 2>/dev/null | head -20

echo
echo "== authorized_keys visible line ends"
sed -n "1,4p" /root/.ssh/authorized_keys | cat -A | cut -c1-170
wc -c -l /root/.ssh/authorized_keys

echo
echo "== ownership and modes"
stat -c "%a %U:%G %n" /root /root/.ssh /root/.ssh/authorized_keys

echo
echo "== recent auth log"
if [ -f /var/log/auth.log ]; then
  tail -120 /var/log/auth.log | grep -iE "publickey|Failed|Accepted|AuthorizedKeys|Bad owner|Could not open" | tail -14
else
  journalctl -u ssh -u sshd -n 80 --no-pager 2>/dev/null | grep -iE "publickey|Failed|Accepted" | tail -14
fi

echo
echo "== sshd processes"
ps -eo pid,etime,args | grep -i "[s]shd" | head -5
