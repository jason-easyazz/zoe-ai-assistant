# Included by EVERY proxying location in locations.inc. Sets the two ingress headers ABSOLUTELY from server-level variables
# (ingress-lan.inc: empty -> no header is sent, a client-sent copy is dropped; relay-listener.conf: "relay" + the verified client
# certificate's fingerprint). Without this a location that sets any proxy_set_header of its own still forwards a client's
# X-Zoe-Ingress untouched. Pinned by tests/unit/test_relay_nginx_listener.py.
proxy_set_header X-Zoe-Ingress $zoe_ingress;
proxy_set_header X-Zoe-Client-Cert-Fp $zoe_client_cert_fp;
