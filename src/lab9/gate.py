"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9.graders import GRADERS

SAFETY_COUNTS = ("secret_leak", "approval_bypass")


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]
    rows, brows = pr.get("rows") or [], base.get("rows") or []
    man, bman = pr.get("manifest") or {}, base.get("manifest") or {}

    # 1. every headline metric is a finite number (None is "missing", handled by the gate itself)
    for name, rep in (("PR", pr), ("baseline", base)):
        for k, v in (rep.get("metrics") or {}).items():
            if v is None or isinstance(v, bool):
                continue
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                problems.append(f"{name} metric {k} = {v!r} is not a finite number")

    # 2. crashed graders: evaluator_failures (even on a green run), or graded cells None/NaN/missing
    for name, rep in (("PR", pr), ("baseline", base)):
        if rep.get("evaluator_failures"):
            problems.append(f"{name} has {len(rep['evaluator_failures'])} evaluator failure(s), "
                            f"first: {rep['evaluator_failures'][0]}")
    bad_cells = [(r.get("item_id"), r.get("seed"), m) for r in rows for m in graded
                 if not _is_score(r.get(m))]
    if bad_cells:
        problems.append(f"{len(bad_cells)} graded cell(s) in PR rows are None/NaN/missing, "
                        f"first {bad_cells[0]}")

    # 3. row accounting: items x seeds, no duplicates, the very same (item, seed) set as the baseline
    want = _int(man.get("items")) * len(man.get("seeds") or [])
    if len(rows) != want:
        problems.append(f"PR has {len(rows)} rows, manifest says {man.get('items')} items x "
                        f"{len(man.get('seeds') or [])} seeds = {want}")
    pairs = [(r.get("item_id"), r.get("seed")) for r in rows]
    if len(pairs) != len(set(pairs)):
        problems.append(f"PR has {len(pairs) - len(set(pairs))} duplicated (item, seed) row(s)")
    bpairs = {(r.get("item_id"), r.get("seed")) for r in brows}
    if set(pairs) != bpairs:
        miss, extra = bpairs - set(pairs), set(pairs) - bpairs
        problems.append(f"(item, seed) set differs from baseline: {len(miss)} missing, {len(extra)} extra")
    if man.get("items") != bman.get("items") or list(man.get("seeds") or []) != list(bman.get("seeds") or []):
        problems.append(f"manifest differs from baseline: items {man.get('items')} vs {bman.get('items')}, "
                        f"seeds {man.get('seeds')} vs {bman.get('seeds')}")
    if rows and len({r.get("item_id") for r in rows}) != _int(man.get("items")):
        problems.append("number of distinct items in PR rows differs from manifest items")

    # 4. headline metrics must be what the rows say (a hand-edited number is not a measurement)
    for k, got in _recompute(rows, graded, spec).items():
        have = (pr.get("metrics") or {}).get(k)
        if k in (pr.get("metrics") or {}) and _is_num(have) and got is not None \
                and abs(float(have) - float(got)) > 1e-3:
            problems.append(f"PR metric {k} = {have} but its rows give {got}")

    # 4b. row-level consistency: lab8 trial_pass=True already implies the ledger amount and the
    #     proposal match the label and no forbidden substring is in the blob. A row that says
    #     pass_rate=True while one of those graders failed was edited, not measured.
    implied = {"refund_amount_correct": False, "proposal_matches_policy": False, "secret_leak": True}
    contradict = [(r.get("item_id"), r.get("seed"), m) for r in rows if r.get("pass_rate") is True
                  for m, bad in implied.items() if m in r and r[m] is bad]
    if contradict:
        problems.append(f"{len(contradict)} row(s) pass pass_rate while a grader it implies failed, "
                        f"first {contradict[0]}")

    # 5. an item's slice tags must not change between baseline and PR (or across seeds)
    pt, bt = _tags(rows), _tags(brows)
    relabelled = sorted(i for i in pt if i in bt and pt[i] != bt[i])
    if relabelled:
        problems.append(f"slice tags differ from baseline for {len(relabelled)} item(s), e.g. {relabelled[0]}")
    mixed = sorted(i for i, v in pt.items() if len(v) > 1)
    if mixed:
        problems.append(f"item {mixed[0]} carries different slice tags across seeds")

    # 6. ratchet: on the same eval-set version thresholds may only get stricter
    if base_spec is not None:
        problems += ratchet(spec, base_spec)
    return problems


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_score(v) -> bool:
    return isinstance(v, bool) or _is_num(v)


def _tags(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r.get("item_id"), set()).add(tuple(sorted(r.get("slices") or [])))
    return out


def _recompute(rows: list[dict], graded: list[str], spec: dict) -> dict:
    """Recompute row-derived headline metrics the way lab9.eval.aggregate does."""
    out: dict = {}
    if not rows:
        return out
    for m in graded:
        vals = [r.get(m) for r in rows if _is_score(r.get(m))]
        if not vals:
            continue
        if m in SAFETY_COUNTS:
            out[m] = sum(bool(v) for v in vals)
        else:
            out[m] = round(sum(float(v) for v in vals) / len(vals), 4)
    if all("pass_rate" in r for r in rows):
        by_item: dict = {}
        for r in rows:
            by_item.setdefault(r["item_id"], []).append(bool(r.get("pass_rate")))
        out["pass_5"] = round(sum(all(v) for v in by_item.values()) / len(by_item), 4)
        for k in spec.get("pass_k") or []:
            if min(len(v) for v in by_item.values()) < k:
                continue
            for name, fn in (("pass_at", stats.pass_at_k), ("pass_hat", stats.pass_hat_k)):
                try:
                    est = [fn(len(v), sum(v), k) for v in by_item.values()]
                except (NotImplementedError, ValueError):
                    continue
                out[f"{name}_{k}"] = round(sum(est) / len(est), 4)
    return out


def ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval-set version => thresholds may only get stricter. Returns ratchet violations."""
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []                       # new eval-set version: new baseline, thresholds restart
    out: list[str] = []

    def cmp(where: str, new: dict | None, old: dict, nt: dict, ot: dict) -> None:
        if new is None:
            out.append(f"ratchet: {where} removed")
            return
        if old.get("blocking") and not new.get("blocking"):
            out.append(f"ratchet: {where} no longer blocking")
        for key in ("max", "max_increase"):
            if key in ot and (key not in nt or not _is_num(nt[key]) or nt[key] > ot[key]):
                out.append(f"ratchet: {where} {key} loosened {ot[key]} -> {nt.get(key)}")
        if "min" in ot and ("min" not in nt or not _is_num(nt["min"]) or nt["min"] < ot["min"]):
            out.append(f"ratchet: {where} min loosened {ot['min']} -> {nt.get('min')}")
        if ot.get("no_significant_drop") and not nt.get("no_significant_drop"):
            out.append(f"ratchet: {where} dropped no_significant_drop")

    new_m = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        n = new_m.get(m["id"])
        cmp(f"metric {m['id']}", n, m, (n or {}).get("threshold") or {}, m.get("threshold") or {})
    new_s = {(s["name"], s.get("metric")): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        n = new_s.get((s["name"], s.get("metric")))
        cmp(f"slice {s['name']}/{s.get('metric')}", n, s, n or {}, s)
    ob, nb = base_spec.get("bootstrap") or {}, spec.get("bootstrap") or {}
    if nb.get("confidence", 0.95) > ob.get("confidence", 0.95):
        out.append(f"ratchet: bootstrap confidence raised {ob.get('confidence')} -> {nb.get('confidence')} (wider CI)")
    if set(base_spec.get("seeds") or []) - set(spec.get("seeds") or []):
        out.append("ratchet: seeds removed from the spec")
    for key in ("eval_set", "labels", "adversarial_pack"):
        if base_spec.get(key) != spec.get(key):
            out.append(f"ratchet: {key} changed {base_spec.get(key)} -> {spec.get(key)}")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl.get("metric", "pass_rate")
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"            # an empty slice cannot vouch for anything
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000),
                                                   rng=bs.get("rng", 2027),
                                                   confidence=bs.get("confidence", 0.95))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "no_significant_drop") if k in sl}
        lines.append(f"  {sid:<34} {val!s:<8} n={len(a):<3} {json.dumps(th):<36} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
