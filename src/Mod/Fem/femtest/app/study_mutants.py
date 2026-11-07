# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mutation test for the G11 static study (brief check 6): 16 planted defects, a byte patch each, a named small case subset that must catch it.

  python3 -I study_mutants.py [--only M01,M05] [--keep-going]

Written in run 067 against the file names declared in femstudy/SPEC.md; the anchors (the `old` byte strings) were corrected against the real source
after the study code existed (a missing anchor prints ANCHOR MISSING and counts as neither caught nor survived). Patches are applied to the installed copy the driver imports, `$FC_BUILD_DIR/Mod/Fem/femstudy/`, never
to the source tree. Bytes are read, patched and restored byte for byte in a finally block, and a pristine copy is kept under SCRATCH/mutants/.

A mutant is `caught` when some case of its subset that passes on the clean code fails (any status other than PASS or MISS_ALLOWED) with the
patch in place; it `survived` otherwise. Every survivor must be killed or named in the run note. M15 (kappa = 1) patches the corpus, not the
study: expected to survive at L/h >= 5 (the shear term is 3% of the deflection at L/h = 5 and kappa = 1 changes it by 17%, 0.5% in all, under the
3% tolerance); killing it takes L/h = 2 cases.
"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
FC_SRC = os.environ.get("FC_SRC", os.path.join(HOME, "src/cad-fork/worktrees/G11"))
FC_BUILD = os.environ.get("FC_BUILD_DIR", os.path.join(FC_SRC, "build/release"))
SCRATCH = os.environ.get("SCRATCH", os.path.join(HOME, "src/cad-fork/scratch/r067"))
STUDY_DIR = os.path.join(FC_BUILD, "Mod/Fem/femstudy")

# (id, name, [(file, old, new), ...], case subset, remark)
MUTANTS = [
    ("M01", "modulus read in the wrong unit (GPa as MPa)", [("spec.py", b'getValueAs("MPa")', b'getValueAs("GPa")')], ["A01", "B02", "G01"], ""),
    ("M02", "load sign flipped", [("model.py", b"Reversed = False", b"Reversed = True")], ["A02", "B01", "C01"], "the sign shows in the signed reaction sum, not in magnitudes: the oracle's equilibrium check sees it"),
    ("M03", "load applied to the wrong face", [("model.py", b'ld["refs"]', b'spec["supports"][0]["refs"]')], ["B02", "D01"], "loads sent to the support face: reaction cancels, displacement 0"),
    ("M04", "Poisson ratio ignored", [("model.py", b'["nu"]', b'["nu"] * 0')], ["B03", "E01", "F01"], "nu = 0.3 cases only: A is nu = 0"),
    ("M05", "refinement ratio not applied (three identical meshes)", [("run.py", b"h = h0 / ratio ** k", b"h = h0")], ["B03", "C03"], "the study must report mesh_not_refined, never a zero estimate"),
    ("M06", "convergence estimate from the wrong pair of levels", [("convergence.py", b"levels[-3:]", b"levels[:2] + levels[:1]")], ["B01", "C01", "D01"], "needs a case whose drift is not the same between pairs"),
    ("M07", "equilibrium check removed (and the delivered load 1% low)", [("checks.py", b"EQUILIBRIUM_TOL = 1e-3", b"EQUILIBRIUM_TOL = 1e9"), ("run.py", b"load_scale = 1.0", b"load_scale = 0.99")], ["B03", "C02"], "two patches: the check is off and the load delivered to the mesh is 1% off the request"),
    ("M08", "stale flag never set", [("stale.py", b"return h != stored", b"return False")], ["STALE"], "caught by study_stale_scenarios.py, not by a corpus case"),
    ("M09", "solver exit code ignored", [("run.py", b"if rc != 0", b"if False")], ["I12"], "I12 is a ccx shim that writes valid results and exits 3"),
    ("M10", "nan result passed on", [("checks.py", b"isnan(v) or isinf(v)", b"False")], ["I13"], "I13 is a ccx shim that writes nan into the results"),
    ("M11", "singularity threshold disabled", [("convergence.py", b"SINGULAR_GROWTH = 1.30", b"SINGULAR_GROWTH = 1e9")], ["H02", "H04"], ""),
    ("M12", "element cap ignored", [("run.py", b'opts["max_elements"]', b"10**12")], ["I14"], "I14 runs with max_elements = 600"),
    ("M13", "displacement-size check disabled", [("checks.py", b"LARGE_DISPLACEMENT_FRACTION = 0.10", b"LARGE_DISPLACEMENT_FRACTION = 1e9")], ["I10"], ""),
    ("M14", "previous run's result reused after an edit", [("__init__.py", b"input_hash != stored_hash", b"False")], ["STALE"], "caught by study_stale_scenarios.py"),
    ("M15", "kappa taken as 1 (corpus formula)", [("@corpus", b"return 5.0 / 6.0", b"return 1.0")], ["B01", "B02", "C01", "C02", "B13", "B14"], "patches study_corpus.py, the truth of the rectangular cantilevers; the stubby cases B13 and B14 end unconverged, see the run note"),
    ("M16", "missing support not refused", [("checks.py", b'if not spec["supports"]', b"if False")], ["I01"], ""),
]


def read(p):
    with open(p, "rb") as f:
        return f.read()


def target(fname):
    return os.path.join(HERE, "study_corpus.py") if fname == "@corpus" else os.path.join(STUDY_DIR, fname)


def run_subset(cases, out):
    if os.path.exists(out):
        os.remove(out)
    ids = ",".join(c for c in cases if c != "STALE")
    if ids:
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "study_corpus_run.py"), "--mode", "study", "--cases", ids, "--out", out, "--redo"], check=False, stdout=subprocess.DEVNULL)
    sys.path.insert(0, HERE)
    import study_corpus
    import study_oracle

    by = {c.id: c for c in study_corpus.all_cases()}
    res = {}
    if os.path.exists(out):
        for ln in open(out):
            if ln.strip():
                r = json.loads(ln)
                res[r["case"]] = study_oracle.judge(by[r["case"]], r)["status"]
    if "STALE" in cases:
        p = subprocess.run([os.path.join(FC_BUILD, "bin/FreeCADCmd"), os.path.join(HERE, "study_stale_scenarios.py")], capture_output=True, text=True,
                           env=dict(os.environ, PATH=os.path.join(FC_SRC, ".pixi/envs/default/bin") + ":" + os.environ["PATH"], QT_QPA_PLATFORM="offscreen", CADFORK_STALE_RUN="1"))
        res["STALE"] = "PASS" if "STALE_ALL_PASS" in p.stdout else "FAIL"
    return res


def main(argv):
    only = None
    if "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(","))
    outdir = os.path.join(SCRATCH, "mutants")
    os.makedirs(outdir, exist_ok=True)
    caught, survived, missing = [], [], []
    for mid, name, patches, cases, remark in MUTANTS:
        if only and mid not in only:
            continue
        clean = run_subset(cases, os.path.join(outdir, f"{mid}-clean.jsonl"))
        originals = {}
        try:
            ok = True
            for fname, old, new in patches:
                p = target(fname)
                if not os.path.exists(p):
                    ok = False
                    print(f"{mid} ANCHOR MISSING: {fname} does not exist")
                    break
                b = read(p)
                if old not in b:
                    ok = False
                    print(f"{mid} ANCHOR MISSING: {old!r} not in {fname}")
                    break
                originals[p] = b
                shutil.copy(p, os.path.join(outdir, f"{mid}-{os.path.basename(p)}.orig"))
                with open(p, "wb") as f:
                    f.write(b.replace(old, new, 1))
            if not ok:
                missing.append(mid)
                continue
            mutated = run_subset(cases, os.path.join(outdir, f"{mid}-mutant.jsonl"))
        finally:
            for p, b in originals.items():
                with open(p, "wb") as f:
                    f.write(b)
                assert read(p) == b
        killers = [c for c in cases if clean.get(c) in ("PASS", "MISS_ALLOWED") and mutated.get(c) not in ("PASS", "MISS_ALLOWED")]
        if killers:
            caught.append(mid)
            print(f"{mid} caught   by {','.join(killers)}  ({name})")
        else:
            survived.append(mid)
            print(f"{mid} SURVIVED  clean={clean} mutated={mutated}  ({name}) {remark}")
    print(f"caught {len(caught)}, survived {len(survived)} {survived}, anchors missing {len(missing)} {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
