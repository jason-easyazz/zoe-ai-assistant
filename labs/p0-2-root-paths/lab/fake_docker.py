#!/usr/local/bin/python3
"""Lab stand-in for the Docker CLI at /usr/bin/docker: logs its argv (one JSON line) and runs nothing. The O cells
read the log to tell "the helper called Docker with exactly this" from "refused before Docker". Lab-only."""
import json
import sys

with open("/run/p02-docker.log", "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
