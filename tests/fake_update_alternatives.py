#!/usr/bin/env python3
"""Minimal stand-in for Debian's update-alternatives, for tests. State lives in $FAKE_ALT_DIR."""
import json
import os
import sys

state_dir = os.environ["FAKE_ALT_DIR"]
state_file = os.path.join(state_dir, "state.json")
state = json.load(open(state_file)) if os.path.exists(state_file) else {}


def save():
    json.dump(state, open(state_file, "w"))


def point(name):
    alt = state[name]
    target = alt["manual"] or max(alt["choices"], key=lambda p: alt["choices"][p])
    etc = os.path.join(state_dir, "etc-alternatives", name)
    os.makedirs(os.path.dirname(etc), exist_ok=True)
    for p, t in ((etc, target), (alt["link"], etc)):
        if os.path.lexists(p):
            os.unlink(p)
        os.symlink(t, p)


cmd, args = sys.argv[1], sys.argv[2:]
if cmd == "--install":
    link, name, path, prio = args
    alt = state.setdefault(name, {"link": link, "choices": {}, "manual": None})
    alt["choices"][path] = int(prio)
    point(name)
elif cmd == "--set":
    name, path = args
    if path not in state[name]["choices"]:
        sys.exit(2)
    state[name]["manual"] = path
    point(name)
elif cmd == "--auto":
    state[args[0]]["manual"] = None
    point(args[0])
elif cmd == "--remove":
    name, path = args
    alt = state[name]
    alt["choices"].pop(path, None)
    if alt["manual"] == path:
        alt["manual"] = None
    point(name)
elif cmd == "--list":
    print("\n".join(state.get(args[0], {}).get("choices", {})))
save()
