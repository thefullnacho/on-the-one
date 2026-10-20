"""Build the app's workflow templates from composed graphs: strip API nodes, prove it, record named slots.

Open Weights Only means no partner/API node may appear in any graph we run. Some upstream templates
(LTX-2.5) carry API nodes on a branch that only fires when an API key is present. This removes them for
good: every API node is deleted, every switch that pointed at one collapses onto its local branch, and
anything no longer feeding an output node is pruned. The result is checked again before it is written.

Named slots replace node ids: a template is composed with marker values ("{{prompt}}", 777001, 7.777)
and this script records where each marker landed, so the runner sets slots by name.

usage: python tools/build_workflows.py <object_info.json> <name>=<compiled.json> ...
writes workflows/<name>.json  {"graph": {...}, "slots": {name: [node_id, field]}, "removed": [...]}
"""
import json, sys
from pathlib import Path

MARKERS = {"{{prompt}}": "prompt", "{{negative}}": "negative", 777001: "seed", 777002: "seed_2",
           7.777: "duration"}
LOADERS = {"LoadImage": ("image", "image"), "LoadAudio": ("audio", "audio")}
OUTPUTS = {"SaveImage", "SaveVideo", "SaveAudio", "PreviewImage"}


def is_api(cls, info):
    meta = info.get(cls, {})
    return bool(meta.get("api_node")) or str(meta.get("category", "")).lower().startswith("api node")


def refs(node):
    return [(f, v[0]) for f, v in node["inputs"].items()
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)]


def strip_api(graph, info):
    removed = sorted({n["class_type"] for n in graph.values() if is_api(n["class_type"], info)})
    dead = {k for k, n in graph.items() if is_api(n["class_type"], info)}
    changed = True
    while changed:
        changed = False
        for k, n in list(graph.items()):
            if k in dead:
                continue
            bad = [(f, src) for f, src in refs(n) if src in dead]
            if not bad:
                continue
            if n["class_type"] == "ComfySwitchNode":
                branch = {"on_true", "on_false"} - {f for f, _ in bad}
                keep = n["inputs"].get(branch.pop()) if len(branch) == 1 else None
                if not (isinstance(keep, list) and keep[0] not in dead):
                    raise SystemExit(f"switch {k} has no surviving branch")
                for m in graph.values():          # rewire every consumer of the switch to the kept branch
                    for f, v in m["inputs"].items():
                        if isinstance(v, list) and len(v) == 2 and v[0] == k:
                            m["inputs"][f] = list(keep)
                dead.add(k); changed = True
            else:
                raise SystemExit(f"node {k} ({n['class_type']}) needs API node {bad}: cannot remove cleanly")
    for k in dead:
        graph.pop(k, None)
    # prune: keep only what feeds an output node
    live, stack = set(), [k for k, n in graph.items() if n["class_type"] in OUTPUTS]
    while stack:
        k = stack.pop()
        if k in live or k not in graph:
            continue
        live.add(k); stack += [src for _, src in refs(graph[k])]
    for k in set(graph) - live:
        graph.pop(k)
    return removed


def check(graph, info):
    api = [n["class_type"] for n in graph.values() if is_api(n["class_type"], info)]
    missing = [(k, f, src) for k, n in graph.items() for f, src in refs(n) if src not in graph]
    if api or missing:
        raise SystemExit(f"compliance FAILED: api nodes {api}, dangling {missing}")


def slots(graph):
    out = {}
    ref_names = {"images.image0": "image", "images.image1": "image2", "images.image2": "image3", "images.image3": "image4"}
    for k, n in graph.items():
        if n["class_type"] in LOADERS:
            name, field = LOADERS[n["class_type"]]
            if n["class_type"] == "LoadImage":     # several references: name each by the slot it feeds
                fed = [f for m in graph.values() for f, v in m["inputs"].items()
                       if isinstance(v, list) and len(v) == 2 and v[0] == k]
                name = next((ref_names[f] for f in fed if f in ref_names), name)
            if name in out:
                raise SystemExit(f"two loaders map to slot {name!r}")
            out[name] = [k, field]
        for f, v in n["inputs"].items():
            if not isinstance(v, (list, dict, bool)) and v in MARKERS:
                if MARKERS[v] in out:
                    raise SystemExit(f"marker {v!r} appears twice")
                out[MARKERS[v]] = [k, f]
    return out


def main():
    info = json.load(open(sys.argv[1]))
    root = Path(__file__).resolve().parents[1] / "workflows"
    root.mkdir(exist_ok=True)
    for arg in sys.argv[2:]:
        name, path = arg.split("=", 1)
        graph = json.load(open(path)); graph.pop("_meta", None)
        before = len(graph)
        removed = strip_api(graph, info)
        check(graph, info)
        s = slots(graph)
        (root / f"{name}.json").write_text(json.dumps({"graph": graph, "slots": s, "removed": removed}, indent=1))
        print(f"{name}: {before} -> {len(graph)} nodes, removed API classes {removed or 'none'}, slots {sorted(s)}")


if __name__ == "__main__":
    main()
