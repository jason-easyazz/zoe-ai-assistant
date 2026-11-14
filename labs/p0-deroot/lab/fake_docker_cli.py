#!/usr/bin/env python3
"""Lab stand-in for the docker CLI at /usr/bin/docker (the lab image has no Docker). It records every argv as one
JSON line in /run/p0lab-docker-cli.log and answers just enough for zoe-compose-deploy and compose_deploy.sh:
`compose ... config --format json [svc]` prints a canned rendering whose bind sources follow the scenario in
/run/p0lab-scenario (ready | home | symlink | rwtree); `compose ... ps --services --status running` prints
music-assistant; everything else exits 0. It runs nothing. Lab-only."""
import json
import os
import sys

LOG = "/run/p0lab-docker-cli.log"
DIGEST = "nginx@sha256:" + "1" * 64


def scenario():
    try:
        return open("/run/p0lab-scenario").read().strip()
    except OSError:
        return "ready"


def rendering(tree, sc):
    auth_src = {"ready": "/srv/zoe/zoe-auth/data", "home": "/home/zoe/assistant/data",
                "symlink": "/srv/zoe/link-data", "rwtree": "/srv/zoe/zoe-auth/data"}[sc]
    ui_conf = {"type": "bind", "source": f"{tree}/services/zoe-ui/nginx.conf", "target": "/etc/nginx/conf.d/default.conf",
               "read_only": sc != "rwtree"}
    return {"services": {
        "zoe-auth": {"build": {"context": f"{tree}/services/zoe-auth"},
                     "volumes": [{"type": "bind", "source": auth_src, "target": "/app/data"}]},
        "zoe-ui": {"image": DIGEST, "volumes": [ui_conf, {"type": "bind", "source": "/srv/zoe/zoe-ui/ssl",
                                                           "target": "/etc/nginx/ssl", "read_only": True}]},
        "music-assistant": {"image": "lab/ma:1", "volumes": [{"type": "bind", "source": "/srv/zoe/music-assistant",
                                                              "target": "/data"}]},
    }}


def main(argv):
    with open(LOG, "a") as fh:
        fh.write(json.dumps({"uid": os.getuid(), "argv": argv}) + "\n")
    if argv[:1] == ["compose"]:
        tree = argv[argv.index("--project-directory") + 1] if "--project-directory" in argv else os.getcwd()
        if "config" in argv and "--format" in argv:
            r = rendering(tree, scenario())
            files = [argv[i + 1] for i, a in enumerate(argv) if a == "-f"]
            mods = any(f.endswith("docker-compose.modules.yml") for f in files)
            keep = {"music-assistant"} if mods else {"zoe-auth", "zoe-ui"}
            r["services"] = {k: v for k, v in r["services"].items() if k in keep}
            print(json.dumps(r))
        elif "ps" in argv and "--services" in argv:
            print("music-assistant")
        elif "config" in argv and "--services" in argv:
            print("music-assistant")
    elif argv[:1] == ["logs"]:
        print("lab log line")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
