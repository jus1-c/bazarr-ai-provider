from __future__ import annotations

from pathlib import Path
import shutil
import time


PROVIDER_SOURCE = Path("/tmp/aiproxy.py")
AIPROXY_UI_ENTRY = (
    '{key:"aiproxy",name:"AI Proxy",'
    'description:"Runs Bazarr providers first, then AI-assisted fallback for matching subtitles."},'
)


def main() -> None:
    provider_dir = find_provider_dir()
    shutil.copyfile(PROVIDER_SOURCE, provider_dir / "aiproxy.py")
    patch_frontend_provider_list()
    PROVIDER_SOURCE.unlink(missing_ok=True)


def find_provider_dir() -> Path:
    for root in (Path("/app"), Path("/usr"), Path("/opt")):
        if not root.exists():
            continue
        for candidate in root.rglob("custom_libs/subliminal_patch/providers"):
            if candidate.is_dir():
                return candidate
    raise RuntimeError("Could not find Bazarr provider directory")


def patch_frontend_provider_list() -> None:
    frontend_dir = Path("/app/bazarr/bin/frontend/build/assets")
    if not frontend_dir.exists():
        return

    patched = False
    for js_file in frontend_dir.glob("*.js"):
        content = js_file.read_text(encoding="utf-8", errors="ignore")
        if 'key:"aiproxy"' in content:
            patched = True
            continue
        needle = '{key:"addic7ed"'
        if needle not in content:
            continue
        js_file.write_text(content.replace(needle, AIPROXY_UI_ENTRY + needle, 1), encoding="utf-8")
        patched = True

    if not patched:
        raise RuntimeError("Could not patch Bazarr frontend provider list")

    service_worker = Path("/app/bazarr/bin/frontend/build/sw.js")
    if service_worker.exists():
        content = service_worker.read_text(encoding="utf-8", errors="ignore")
        marker = "// aiproxy-provider-ui-patch"
        if marker not in content:
            service_worker.write_text(f"{content}\n{marker}-{int(time.time())}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
