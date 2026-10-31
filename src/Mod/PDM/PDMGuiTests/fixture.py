# SPDX-License-Identifier: LGPL-2.1-or-later
"""A throwaway vault for the PDM GUI tests and the I4 panel scenes: fixed users, times and messages.

``make_world`` builds the vault in ``tmp`` with two clients in one process (alice, bob; each Vault has its own
working copy, hence its own client id and lock identity) and returns a ``World``. Commit times come from
``GIT_AUTHOR_DATE``/``GIT_COMMITTER_DATE``, which ``PDM.vault`` passes through to git, so two builds produce the
same history text (hashes differ only through the lock-free tree contents and are never shown)."""

import os

import FreeCAD as App
import Part  # noqa: F401

from PDM import vault as V

ALICE, BOB = "alice", "bob"
_T0 = (2026, 3, 2, 9, 0, 0)


def set_time(step):
    """Pin git's clock to step * 47 minutes after 2026-03-02 09:00 UTC."""
    minutes = step * 47
    day = _T0[2] + minutes // (24 * 60)
    hh, mm = divmod(_T0[3] * 60 + minutes % (24 * 60), 60)
    stamp = f"2026-03-{day:02d}T{hh % 24:02d}:{mm:02d}:00+0000"
    os.environ["GIT_AUTHOR_DATE"] = os.environ["GIT_COMMITTER_DATE"] = stamp


def unset_time():
    os.environ.pop("GIT_AUTHOR_DATE", None)
    os.environ.pop("GIT_COMMITTER_DATE", None)


class World:
    def __init__(self, tmp, vault_path, alice, bob):
        self.tmp, self.vault_path, self.alice, self.bob = tmp, vault_path, alice, bob


def make_box(v, name, length):
    doc = App.newDocument(name)
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = length, 10, 5
    doc.recompute()
    doc.saveAsFolder(v.doc_path(name))
    App.closeDocument(doc.Name)


def make_link(v, name, targets):
    """A document with one App::Link per target document (targets are folders in ``v``'s working copy)."""
    tdocs = [App.openDocument(v.doc_path(t)) for t in targets]
    doc = App.newDocument(name)
    doc.saveAsFolder(v.doc_path(name))   # the stored link path is relative to the saved owner
    for i, t in enumerate(tdocs):
        lk = doc.addObject("App::Link", f"Link{i}")
        lk.LinkedObject = t.getObject("Box")
    doc.recompute()
    doc.save()
    for d in [doc] + tdocs:
        App.closeDocument(d.Name)


def edit_box(v, name, length):
    doc = App.openDocument(v.doc_path(name))
    doc.getObject("Box").Length = length
    doc.recompute()
    doc.save()
    App.closeDocument(doc.Name)


def revise(v, name, length, message):
    """Check out ``name``, change its length, save and check in (library calls only)."""
    v.checkout(name)
    edit_box(v, name, length)
    return v.checkin(name, message)


def make_world(tmp, scenario="basic", fixed_time=True):
    """scenario ``basic``: bracket (Released A.1), plate (WIP A.1), assembly (links bracket).
    ``full``: the history the panel scenes show (bracket A.1 to B.1 with a release, fixture, a plate edit
    in progress)."""
    tmp = os.path.realpath(tmp)
    step = [0]

    def tick():
        if fixed_time:
            step[0] += 1
            set_time(step[0])

    tick()
    vpath = V.Vault.create(os.path.join(tmp, "vault.git"))
    a = V.Vault.clone(vpath, os.path.join(tmp, "wc-alice"), ALICE)
    b = V.Vault.clone(vpath, os.path.join(tmp, "wc-bob"), BOB)
    try:
        make_box(a, "bracket", 20)
        a.add_document("bracket", message="Initial bracket")
        tick()
        make_box(a, "plate", 60)
        a.add_document("plate", message="Base plate for the bracket")
        tick()
        make_link(a, "assembly", ["bracket"])
        a.add_document("assembly", message="Assemble the bracket on the plate")
        tick()
        if scenario == "full":
            make_link(a, "fixture", ["bracket"])
            a.add_document("fixture", message="Drilling fixture for the bracket")
            tick()
            revise(a, "bracket", 30, "Lengthen the pad to 30 mm")
            tick()
            revise(a, "bracket", 32, "Add clearance for the M6 bolt")
            tick()
        b.transition("bracket", "InReview", "Sent for design review")
        tick()
        b.transition("bracket", "Released", "Approved by bob")
        tick()
        if scenario == "full":
            a.checkout("bracket", new_revision=True)
            edit_box(a, "bracket", 36)
            a.checkin("bracket", "Redesign for the 4 mm plate")
            tick()
            b.transition("bracket", "InReview", "Second review requested")
            tick()
            a.checkout("plate")
            edit_box(a, "plate", 64)   # saved, not yet checked in: the check-in panel scene
    finally:
        unset_time()
    return World(tmp, vpath, a, b)
