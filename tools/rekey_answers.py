"""One-off migration: re-key answer files written with the old key (images + prompt text) to the new key
(images only), but only where the images are provably unchanged.

An old key is accepted when it equals legacy_request_key(images, prompt) for a prompt text found in any
committed version of streetaudit/llm.py. Files whose key matches no version are left alone (their images
changed, so the answer is out of date).

usage: python tools/rekey_answers.py -c configs/ward44.toml
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from streetaudit import llm  # noqa: E402
from streetaudit.config import Settings  # noqa: E402


def committed_prompts() -> list[str]:
    revs = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--", "streetaudit/llm.py"],
                          capture_output=True, text=True, check=True).stdout.split()
    prompts = []
    for rev in revs:
        src = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:streetaudit/llm.py"],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        namespace: dict = {}
        m = re.search(r'^PROMPT = """.*?"""', src, flags=re.S | re.M)
        if m:
            exec(m.group(0), namespace)           # the file's own string literal, from our own repository
            prompts.append(namespace["PROMPT"])
    return prompts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True)
    s = Settings.load(ap.parse_args().config)
    aimed = json.loads((s.work_dir / "aimed.json").read_text(encoding="utf-8"))
    prompts = committed_prompts()
    counts = {"already new": 0, "re-keyed": 0, "images changed, left alone": 0, "no images": 0}
    for path in sorted((s.work_dir / "llm").glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        info = aimed.get(path.stem)
        if not isinstance(saved, dict) or not info:
            counts["no images"] += 1
            continue
        new = llm.request_key(info)
        if saved.get("key") == new:
            counts["already new"] += 1
            continue
        if any(saved.get("key") == llm.legacy_request_key(info, p) for p in prompts):
            saved["key"] = new
            path.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
            counts["re-keyed"] += 1
        else:
            counts["images changed, left alone"] += 1
    print(f"prompt versions found in git: {len(prompts)} | {counts}")


if __name__ == "__main__":
    main()
