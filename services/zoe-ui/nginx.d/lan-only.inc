# First line of every location that must NOT be reachable over the relay listener: local-trusted host services that carry no Zoe
# auth of their own (Music Assistant, the Multica board, Home Assistant's UI) and LiveKit signalling (no TURN: WebRTC cannot cross
# the relay). $zoe_ingress is empty on the LAN blocks, so this is a no-op there; on the relay listener it is "relay" -> 404.
# `return` inside `if` in a location is the one safe use of `if`.
if ($zoe_ingress != "") {
    return 404;
}
