# SPDX-License-Identifier: LGPL-2.1-or-later
"""Edits the CalculiX deck that the stock writer produced, in the study's own working directory, and reads the `.dat` back.

The stock writers and file formats are not touched. What this module adds to a written deck:

* the nodal loads of every force constraint, and the pressure of every pressure constraint, are scaled so that the load the mesh delivers equals the
  load the spec requested (the stock force constraint delivers 1.14% less than requested on a round face); the factors are returned so the caller can
  warn when a correction exceeds 1e-3;
* `*EL PRINT, ELSET=Eall, TOTALS=ONLY` / `ELSE` for the total strain energy, and one `*NODE PRINT ... RF` totals request on the union of every
  constrained node (the stock per-constraint requests count a node twice when two constraints share it);
* the load that sits on constrained nodes, recomputed from the deck (nodal loads from `*CLOAD`, consistent face loads from `*DLOAD` on C3D10
  faces). ccx prints RF = K u at a node, so the RF total of the constrained nodes is the support reaction plus the whole external load on those
  nodes (free directions included); the reaction is the RF total minus that load (SPEC.md, item 7 of Checks that may not hold).
"""

import re

# six-point degree-4 rule on the reference triangle (Dunavant), weights sum to 1 (multiply by the reference area 1/2)
_B1, _B2 = 0.445948490915965, 0.091576213509771
_W1, _W2 = 0.223381589678011, 0.109951743655322
_GAUSS = [((_B1, _B1, 1 - 2 * _B1), _W1), ((_B1, 1 - 2 * _B1, _B1), _W1), ((1 - 2 * _B1, _B1, _B1), _W1),
          ((_B2, _B2, 1 - 2 * _B2), _W2), ((_B2, 1 - 2 * _B2, _B2), _W2), ((1 - 2 * _B2, _B2, _B2), _W2)]

# C3D10 faces: corner a, b, c then midside ab, bc, ca (one-based element node positions, ccx numbering)
FACES = {1: (1, 2, 3, 5, 6, 7), 2: (1, 4, 2, 8, 9, 5), 3: (2, 4, 3, 9, 10, 6), 4: (3, 4, 1, 10, 8, 7)}


def parse(text):
    """Nodes, C3D10 elements, node sets, boundary dofs and the *CLOAD / *DLOAD blocks of a deck."""
    lines = text.split("\n")
    nodes, elems, nsets, bounds, blocks = {}, {}, {}, [], []
    kw, cur, last_comment = None, None, ""
    for i, ln in enumerate(lines):
        if ln.startswith("**"):
            last_comment = ln[2:].strip()
            continue
        if ln.startswith("*"):
            parts = [p.strip() for p in ln.split(",")]
            kw = parts[0].upper()
            par = {}
            for p in parts[1:]:
                if "=" in p:
                    a, b = p.split("=", 1)
                    par[a.strip().upper()] = b.strip()
            cur = None
            if kw == "*NSET":
                cur = nsets.setdefault(par.get("NSET", ""), [])
            elif kw in ("*CLOAD", "*DLOAD"):
                cur = {"kind": kw[1:], "name": last_comment, "idx": []}
                blocks.append(cur)
            continue
        if not ln.strip():
            continue
        f = [x.strip() for x in ln.split(",") if x.strip() != ""]
        if kw == "*NODE" and len(f) >= 4:
            nodes[int(f[0])] = (float(f[1]), float(f[2]), float(f[3]))
        elif kw == "*ELEMENT" and len(f) == 11:
            elems[int(f[0])] = [int(x) for x in f[1:]]
        elif kw == "*NSET" and cur is not None:
            cur.extend(int(x) for x in f if x.lstrip("-").isdigit())
        elif kw == "*BOUNDARY" and len(f) >= 2:
            first = int(f[1])
            last = int(f[2]) if len(f) > 2 else first
            bounds.append((f[0], first, last))
        elif kw in ("*CLOAD", "*DLOAD") and cur is not None:
            cur["idx"].append(i)
    return {"lines": lines, "nodes": nodes, "elems": elems, "nsets": nsets, "bounds": bounds, "blocks": blocks}


def constrained_dofs(m):
    """{node id: set of dofs (1, 2, 3)} held by *BOUNDARY entries."""
    out = {}
    for name, a, b in m["bounds"]:
        ids = m["nsets"].get(name)
        if ids is None:
            ids = [int(name)] if name.isdigit() else []
        for n in ids:
            out.setdefault(n, set()).update(d for d in range(a, b + 1) if d <= 3)
    return out


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def face_loads(nodes, enodes, face, p):
    """Consistent nodal forces (N) of pressure `p` (MPa, positive into the element) on face `face` (1..4) of the C3D10 element `enodes`.
    Returns {node: (fx, fy, fz)}: f_i = -p * integral of N_i n dA over the quadratic triangle, n outward."""
    ids = [enodes[j - 1] for j in FACES[face]]
    x = [nodes[n] for n in ids]
    out = {n: [0.0, 0.0, 0.0] for n in ids}
    # outward sign: the reference area vector x_xi x x_eta follows the corner order a, b, c; it points outward when it points away from the
    # element's fourth corner
    cr = _cross(_sub(x[1], x[0]), _sub(x[2], x[0]))
    opp = next(n for n in enodes[:4] if n not in ids[:3])
    sign = -1.0 if _dot(cr, _sub(nodes[opp], x[0])) > 0 else 1.0
    for (l1, l2, l3), w in _GAUSS:
        # shape functions in barycentric form: corners L(2L-1), midsides 4 L L ; derivatives with respect to (xi = L2, eta = L3), L1 = 1 - xi - eta
        N = [l1 * (2 * l1 - 1), l2 * (2 * l2 - 1), l3 * (2 * l3 - 1), 4 * l1 * l2, 4 * l2 * l3, 4 * l3 * l1]
        d1 = [4 * l1 - 1, 0.0, 0.0, 4 * l2, 0.0, 4 * l3]  # dN/dL1
        d2 = [0.0, 4 * l2 - 1, 0.0, 4 * l1, 4 * l3, 0.0]
        d3 = [0.0, 0.0, 4 * l3 - 1, 0.0, 4 * l2, 4 * l1]
        dxi = [d2[j] - d1[j] for j in range(6)]
        deta = [d3[j] - d1[j] for j in range(6)]
        xxi = [sum(dxi[j] * x[j][c] for j in range(6)) for c in range(3)]
        xeta = [sum(deta[j] * x[j][c] for j in range(6)) for c in range(3)]
        a = _cross(xxi, xeta)
        for j in range(6):
            f = -p * sign * N[j] * w * 0.5
            o = out[ids[j]]
            o[0] += f * a[0]
            o[1] += f * a[1]
            o[2] += f * a[2]
    return {n: tuple(v) for n, v in out.items()}


def block_loads(m, blk):
    """{node: [fx, fy, fz]} delivered by one *CLOAD or *DLOAD block of the deck, from the lines as they stand now."""
    out = {}
    for i in blk["idx"]:
        f = [x.strip() for x in m["lines"][i].split(",")]
        if blk["kind"] == "CLOAD":
            n, d, v = int(f[0]), int(f[1]), float(f[2])
            out.setdefault(n, [0.0, 0.0, 0.0])[d - 1] += v
        else:
            if not (f[0].isdigit() and len(f) >= 3 and f[1].upper().startswith("P") and f[1][1:].isdigit()):
                continue  # gravity (`Eall,GRAV,...`) or anything that is not an element face pressure
            e = int(f[0])
            for n, v in face_loads(m["nodes"], m["elems"][e], int(f[1][1:]), float(f[2])).items():
                o = out.setdefault(n, [0.0, 0.0, 0.0])
                o[0] += v[0]
                o[1] += v[1]
                o[2] += v[2]
    return out


def _scale_block(m, blk, s):
    for i in blk["idx"]:
        f = [x.strip() for x in m["lines"][i].split(",")]
        f[2] = f"{float(f[2]) * s:.14g}"
        m["lines"][i] = ",".join(f)


def prepare(path, requested):
    """Edit the deck at `path` in place. `requested` maps a constraint name to the resultant the spec asked for, as a 3-vector in N.

    Returns {"scale": {name: factor applied}, "delivered": {name: resultant before scaling}, "unscaled": {name: relative mismatch} for blocks whose
    factor was outside 0.5 to 2 and therefore left alone, "applied_nodal": {node: [fx, fy, fz]} after scaling, "constrained": {node: dofs},
    "load_on_constrained": [fx, fy, fz] (all dofs of constrained nodes)}."""
    text = open(path).read()
    m = parse(text)
    info = {"scale": {}, "delivered": {}, "unscaled": {}}
    nodal = {}
    for blk in m["blocks"]:
        loads = block_loads(m, blk)
        d = [sum(v[c] for v in loads.values()) for c in range(3)]
        name = blk["name"]
        info["delivered"][name] = d
        if name in requested:
            a = requested[name]
            dd = _dot(d, d)
            s = _dot(a, d) / dd if dd > 0 else 1.0
            if 0.5 < s < 2.0:
                if abs(s - 1.0) > 1e-12:
                    _scale_block(m, blk, s)
                info["scale"][name] = s
            else:
                na = _dot(a, a) ** 0.5
                info["unscaled"][name] = (_dot(_sub(d, a), _sub(d, a)) ** 0.5 / na) if na > 0 else 1.0
        for n, v in block_loads(m, blk).items():
            o = nodal.setdefault(n, [0.0, 0.0, 0.0])
            for c in range(3):
                o[c] += v[c]
    cd = constrained_dofs(m)
    lc = [0.0, 0.0, 0.0]
    for n, v in nodal.items():
        if n in cd:  # every dof of a constrained node: ccx prints RF = K u there, which holds the node's whole external load, free dofs included
            for c in range(3):
                lc[c] += v[c]
    info["applied_nodal"] = nodal
    info["constrained"] = cd
    info["load_on_constrained"] = lc
    # additions: union node set before *STEP, prints before *END STEP
    lines = m["lines"]
    ids = sorted(cd)
    nset = ["*NSET,NSET=NStudySupport"] + [", ".join(str(n) for n in ids[j:j + 8]) + "," for j in range(0, len(ids), 8)]
    out, done_nset, done_prn = [], False, False
    for ln in lines:
        u = ln.strip().upper()
        if not done_nset and u.startswith("*STEP"):
            out += nset
            done_nset = True
        if not done_prn and u.startswith("*END STEP"):
            out += ["*NODE PRINT, NSET=NStudySupport, TOTALS=ONLY", "RF", "*EL PRINT, ELSET=Eall, TOTALS=ONLY", "ELSE"]
            done_prn = True
        out.append(ln)
    if not (done_nset and done_prn):
        raise ValueError("deck has no *STEP / *END STEP: cannot add the study's output requests")
    with open(path, "w") as f:
        f.write("\n".join(out))
    return info


_NUM = r"([-+0-9.eEnaNifINF]+)"


def parse_dat(text):
    """{"rf": {set name: [fx, fy, fz]}, "energy": float or None} from a CalculiX `.dat` text (values may be nan or inf: the caller checks)."""
    rf = {}
    for mt in re.finditer(r"total force \(fx,fy,fz\) for set (\S+) and time\s+\S+\s*\n\s*\n\s*" + _NUM + r"\s+" + _NUM + r"\s+" + _NUM, text):
        rf[mt.group(1).upper()] = [float(mt.group(2)), float(mt.group(3)), float(mt.group(4))]
    en = None
    mt = re.search(r"total internal energy for set (\S+) and time\s+\S+\s*\n\s*\n\s*" + _NUM, text)
    if mt:
        en = float(mt.group(2))
    return {"rf": rf, "energy": en}
