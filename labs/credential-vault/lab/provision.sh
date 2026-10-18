#!/bin/sh
# Builds the uid model of credential-vault.md section 5.1 inside the lab image (never on a host).
#   zoe-vault          the core: holds the vault client, keys, policy, audit
#   zoe-vault-browser  the login browser; cannot read zoe-vault's files
#   zoe-vault-phone    the phone pages (approve / code / unlock)
#   zoe-svc            stands in for zoe-data (the only "client" uid)
#   lab-grouped        in the socket's group but NOT on the allow-list (tests SO_PEERCRED, cell V8)
#   lab-intruder       in no group at all (tests the file mode)
#   lab-nginx          stands in for nginx in front of the phone socket
#   lab-member         runs the members' own bw clients (alice, bob, ...), each with a private data dir
#   lab-sites          runs the test sites
set -eu
groupadd -g 2101 zoe-vault-clients
groupadd -g 2102 zoe-vault-cdp
groupadd -g 2103 zoe-vault-web
useradd -r -u 2001 -U -M -d /var/lib/zoe-vault -s /usr/sbin/nologin -G zoe-vault-clients,zoe-vault-cdp zoe-vault
useradd -r -u 2002 -U -m -d /home/zoe-vault-browser -s /usr/sbin/nologin -G zoe-vault-cdp zoe-vault-browser
useradd -r -u 2003 -U -M -d /nonexistent -s /usr/sbin/nologin -G zoe-vault-clients zoe-svc
useradd -r -u 2004 -U -M -d /nonexistent -s /usr/sbin/nologin -G zoe-vault-clients,zoe-vault-web zoe-vault-phone
useradd -r -u 2005 -U -M -d /nonexistent -s /usr/sbin/nologin -G zoe-vault-clients lab-grouped
useradd -r -u 2006 -U -M -d /nonexistent -s /usr/sbin/nologin lab-intruder
useradd -r -u 2007 -U -M -d /nonexistent -s /usr/sbin/nologin -G zoe-vault-web lab-nginx
useradd -r -u 2008 -U -m -d /home/lab-member -s /usr/sbin/nologin lab-member
useradd -r -u 2100 -U -M -d /nonexistent -s /usr/sbin/nologin lab-sites
install -d -o zoe-vault -g zoe-vault -m 0700 /var/lib/zoe-vault
install -d -o zoe-vault -g zoe-vault -m 0700 /var/lib/zoe-vault/bw
install -d -o zoe-vault-browser -g zoe-vault-browser -m 0700 /home/zoe-vault-browser/.pki
install -d -o lab-member -g lab-member -m 0700 /home/lab-member/bw
chmod 0700 /home/zoe-vault-browser /home/lab-member
