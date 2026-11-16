"""Stand-in container processes (run by runtime.py inside a container's namespace). Lab-only, synthetic.

  hub CONFIG [LINGER]   a Home-Assistant-like hub: reads CONFIG/configuration.yaml, EXECUTES every
                        CONFIG/custom_components/*.py (as HA loads custom components), writes CONFIG/.storage/core
  data DIR [LINGER]     a data service (auth-store-like): writes DIR/p02-state, then reads DIR/app.conf
  rtc FILE              a realtime-media-server-like reader: prints the first line of its read-only config file
  plant DIR             a COMPROMISED container root (any route): copies cp into DIR and makes it set-uid
Every mode exits 1 if a `crash` file appears in its directory while it lingers (a restart-policy trigger the
service user can pull without any Docker right)."""
import os
import shutil
import sys
import time


def linger(d, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if os.path.exists(os.path.join(d, "crash")):
            print("crashed on bad data")
            sys.exit(1)
        time.sleep(0.1)


def main(mode, path, wait="0"):
    if mode == "hub":
        if os.path.exists(os.path.join(path, "crash")):
            sys.exit(1)
        conf = open(os.path.join(path, "configuration.yaml")).read()
        cc = os.path.join(path, "custom_components")
        loaded = 0
        for name in sorted(os.listdir(cc)) if os.path.isdir(cc) else []:
            if name.endswith(".py"):
                exec(compile(open(os.path.join(cc, name)).read(), name, "exec"), {})
                loaded += 1
        os.makedirs(os.path.join(path, ".storage"), exist_ok=True)
        with open(os.path.join(path, ".storage", "core"), "w") as fh:
            fh.write("hub state\n")
        print(f"hub ok conf={len(conf)} plugins={loaded}")
        linger(path, float(wait))
    elif mode == "data":
        with open(os.path.join(path, "p02-state"), "w") as fh:
            fh.write("service state\n")
        conf = os.path.join(path, "app.conf")
        print(f"data ok conf={open(conf).read().strip() if os.path.exists(conf) else 'absent'}")
        linger(path, float(wait))
    elif mode == "rtc":
        print("rtc config:", open(path).readline().strip())
    elif mode == "plant":
        dst = os.path.join(path, "p02-cp")
        shutil.copy("/usr/bin/cp", dst)
        os.chmod(dst, 0o4755)
        st = os.stat(dst)
        print(f"planted uid={st.st_uid} mode={oct(st.st_mode & 0o7777)}")
    else:
        sys.exit(f"unknown mode {mode}")


if __name__ == "__main__":
    main(*sys.argv[1:])
