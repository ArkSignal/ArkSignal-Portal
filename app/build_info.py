"""What build is running. BUILD_INFO is written by CI (see .github/workflows/build.yml) and baked into the image."""
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BUILD_INFO")


def read():
    info = {"version": "unreleased", "built": "unknown", "commit": "unknown", "branch": "unknown"}
    try:
        for line in open(_PATH, encoding="utf-8"):
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip()] = v.strip()
    except OSError:
        pass
    info["short_commit"] = info["commit"][:7] if info["commit"] != "unknown" else "unknown"
    return info
