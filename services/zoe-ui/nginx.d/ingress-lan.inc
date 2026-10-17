# Ingress variables for the LAN server blocks (:80 and :443 in ../nginx.conf). Mounted at /etc/nginx/zoe/.
#
# locations.inc forwards these four variables instead of hard-coding them, so ONE file serves the LAN and the relay listener
# (relay-listener.conf) and each server says what its ingress is:
#
#   $zoe_ingress / $zoe_client_cert_fp  -> X-Zoe-Ingress / X-Zoe-Client-Cert-Fp. EMPTY here, and nginx does not send a header whose
#                                          value is empty - so a LAN client can NEVER claim the relay ingress by writing the header
#                                          itself (its copy is replaced by "nothing"). Only relay-listener.conf sets them, and only
#                                          after ssl_verify_client has accepted a certificate from the household CA.
#   $zoe_real_ip / $zoe_xff             -> X-Real-IP / X-Forwarded-For exactly as before (the TCP peer; the peer appended to any
#                                          client-sent chain). The relay listener sets them EMPTY: a relayed request has no address.
#
# A server block that includes locations.inc without including this file (or setting the four variables itself) fails `nginx -t`
# with "unknown variable", which is what the deploy pre-flight and validate.yml catch.
set $zoe_ingress "";
set $zoe_client_cert_fp "";
set $zoe_real_ip $remote_addr;
set $zoe_xff $proxy_add_x_forwarded_for;
