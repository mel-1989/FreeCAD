# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panel for skeleton publish and subscribe (cad-fork G06 slice 3b). Imported only with the GUI up.

The panel is opened on one part (a Body or an ``App::Part``). It shows, in two lists:

- the items the part publishes, each with its kind, and under each item the subscribers that follow it, with their
  state (following, stale, frozen, broken, unloaded);
- the subscriptions the part holds, with their state and, for the selected one, the reason.

Actions: Publish (the selected face, edge, vertex, sketch or datum), Rename, Unpublish, Subscribe (to an item of any open
document), Unsubscribe, Freeze, Unfreeze, and Go to (selects the item's source in the 3D view; for a broken item whose
object is gone, the skeleton; for a skeleton whose document is not open, it says so in the panel).

Transaction model. The panel stages every action: nothing in any document changes while it is open. The lists show the
document as it will be after OK (a staged publish, rename or withdrawal is marked in the "Change" column). OK applies the
staged actions in order, each as its own call to ``AssemblySkeleton`` (so each is one undo transaction); an action that
the document no longer allows (an unrelated edit made while the panel was open removed its target) is reported and the
rest still go through. Cancel, closing the panel and closing the document discard the staged list and touch no document.
Go to only changes the selection and, for another document, which document is active.

Identity. A staged action names its objects by document ``Uid`` and object ``ID`` (never by a name, which another document or
object can take over), and a staged publish also by the element's mapped name and geometric fingerprint (never by an index
name, which an undo renumbers). OK re-checks each one; an action whose object is gone or is now another one, or whose element
no longer exists or has changed, is reported and skipped. One panel is open at a time: a module-level registry covers the
time a Go to another document hides it (task dialogs belong to a document, so ``Gui.Control.activeDialog()`` is then false).

Names are checked when they are staged (``AssemblySkeleton._check_name``, plus the names already staged), so a refused
name is never staged. The questions the panel asks (a name, a confirmation, an item to subscribe to, the list of
problems after OK) are module-level functions (``ask_name``, ``confirm``, ``ask_item``, ``report_problems``) that tests
replace, so no modal dialog opens on the test path.

Selection. ``resolve_selection`` reads the selection with the sub-names unresolved and resolves each one with FreeCAD's own
``DocumentObject.resolve`` (which walks ``App::Link`` and sub-assembly paths), never by splitting the sub-name here.

Behaviour sources: the slice 3a API; Qt widget documentation; FreeCAD's task dialog, selection and document observer
interfaces. Wording and layout are this project's own.
"""

import itertools

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import AssemblySkeleton as AS

translate = App.Qt.translate

ELEMENT_PREFIXES = ("Face", "Edge", "Vertex")


# ----------------------------------------------------------------- questions

def ask_name(parent, title, text, default=""):
    """Ask for a name; None when cancelled. Replaceable for tests."""
    value, ok = QtWidgets.QInputDialog.getText(parent, title, text, QtWidgets.QLineEdit.Normal, default)
    return value if ok else None


def confirm(parent, title, text):
    """Ask a yes/no question whose default is No; True when confirmed. Replaceable for tests."""
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(translate("Assembly", "Continue"), QtWidgets.QMessageBox.AcceptRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
    box.exec_()
    return box.clickedButton() is yes


def ask_item(parent, candidates):
    """Choose one of ``candidates`` (published item objects of the open documents); None when cancelled. Replaceable for tests."""
    labels = ["%s  (%s)" % (item_text(i), i.Document.Name) for i in candidates]
    value, ok = QtWidgets.QInputDialog.getItem(parent, translate("Assembly", "Subscribe"),
                                               translate("Assembly", "Published item"), labels, 0, False)
    return candidates[labels.index(value)] if ok and value in labels else None


def report_problems(parent, problems):
    """Tell the user what OK could not do. Replaceable for tests."""
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle(translate("Assembly", "Some changes were not made"))
    box.setText(translate("Assembly", "These changes made in the Skeleton panel could not be applied:"))
    box.setInformativeText("\n".join(problems))
    box.exec_()


def item_text(item):
    return "%s.%s" % (item.SkeletonName, item.ItemName) if AS.is_item(item) else item.Label


# ----------------------------------------------------------------- selection

class Candidate:
    """One piece of skeleton geometry the selection names: the object that owns it, the element ('' for a whole sketch or datum)
    and the skeleton part that carries it."""

    def __init__(self, target, sub, skeleton):
        self.target, self.sub, self.skeleton = target, sub, skeleton

    def __repr__(self):
        return "Candidate(%s, %r, %s)" % (self.target.Name, self.sub, self.skeleton.Name if self.skeleton else None)


def is_part(obj):
    return obj is not None and (obj.TypeId == "PartDesign::Body" or obj.isDerivedFrom("App::Part"))


def owning_part(obj):
    """The Body or Part that carries ``obj``: the object itself when it is one, else the nearest GeoFeatureGroup."""
    if is_part(obj):
        return obj
    return obj.getParentGeoFeatureGroup()


def resolve_one(obj, sub):
    """Candidate or an explanation (str) for one selected (object, sub-name), both as the selection reports them (unresolved)."""
    try:
        subobj, _parent, _name, element = obj.resolve(sub or "")
    except Exception as exc:  # noqa: BLE001
        return translate("Assembly", "The selection cannot be resolved: %s") % exc
    if subobj is None:
        return translate("Assembly", "The selection cannot be resolved")
    target = subobj
    if element and target.TypeId in ("App::Link", "App::LinkElement"):
        target = target.getLinkedObject()
    if element:
        # the selection names an element by its stable (mapped) name; the API wants the index name (Face3) of the same element
        try:
            index_name = obj.resolveSubElement(sub or "")[2]
        except Exception:  # noqa: BLE001
            index_name = ""
        if not index_name or index_name.startswith("?"):
            return translate("Assembly", "The selected element of %s cannot be resolved") % target.Label
        element = index_name
    if element and not element.startswith(ELEMENT_PREFIXES):
        return translate("Assembly", "Select a face, edge or vertex (not %s)") % element
    if not element and is_part(target):
        return translate("Assembly", "Select a face, edge, vertex, sketch or datum of the part, not the part itself")
    if not element and not hasattr(target, "Shape"):
        return translate("Assembly", "%s has no geometry to publish") % target.Label
    skeleton = owning_part(target)
    if skeleton is None:
        return translate("Assembly", "%s is not inside a part or body") % target.Label
    try:
        AS._kind_of(target, element)
    except AS.SkeletonError as exc:
        return str(exc)
    return Candidate(target, element, skeleton)


def resolve_selection():
    """(candidates, explanations) for the current selection; ``explanations`` says why a selected thing was left out."""
    cands, why = [], []
    for sel in Gui.Selection.getSelectionEx("", 0):
        for sub in list(sel.SubElementNames) or [""]:
            r = resolve_one(sel.Object, sub)
            (why if isinstance(r, str) else cands).append(r)
    return cands, why


def selected_items():
    """Published item objects in the selection (resolved), in selection order."""
    out = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        try:
            subobj = sel.Object.resolve(sel.SubElementNames[0] if sel.SubElementNames else "")[0]
        except Exception:  # noqa: BLE001
            continue
        if subobj is not None and AS.is_item(subobj):
            out.append(subobj)
    return out


def selected_part():
    """The one Body or Part named by the selection (not counting published items), else the active part, else None."""
    parts = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        try:
            subobj = sel.Object.resolve(sel.SubElementNames[0] if sel.SubElementNames else "")[0]
        except Exception:  # noqa: BLE001
            continue
        if is_part(subobj) and subobj not in parts:
            parts.append(subobj)
    if len(parts) == 1:
        return parts[0]
    if not parts:
        import UtilsAssembly
        return UtilsAssembly.activePart()
    return None


def any_published_item():
    """True as soon as one open document holds a published item (cheap enough for a command's IsActive)."""
    return any(AS.is_item(o) and o.Published for doc in App.listDocuments().values() for o in doc.Objects)


def published_items():
    """Every published item (not withdrawn) of every open document."""
    out = []
    for doc in App.listDocuments().values():
        for o in doc.Objects:
            if AS.is_item(o) and o.Published:
                AS._ensure_proxy(o, AS.PublishedItem)
                out.append(o)
    return out


# ------------------------------------------------------------------ observer

class _Observer:
    """Tells the panel that a document changed, so the lists can be refreshed (deferred)."""

    PROPS = ("Label", "ItemName", "Published", "Status", "BindMode", "Support", "Source", "Subscription", "Group", "Shape")

    def __init__(self, panel):
        self.panel = panel

    def _hit(self, doc_or_obj):
        self.panel.schedule_refresh()

    def slotCreatedObject(self, obj):
        self._hit(obj)

    def slotDeletedObject(self, obj):
        self._hit(obj)

    def slotChangedObject(self, obj, prop):
        if prop in self.PROPS:
            self._hit(obj)

    def slotUndoDocument(self, doc):
        self._hit(doc)

    def slotRedoDocument(self, doc):
        self._hit(doc)

    def slotDeletedDocument(self, doc):
        self._hit(doc)


_current = None                 # the open panel, until it is accepted, rejected or its document is closed
_op_ids = itertools.count(1)


def panel_open():
    """True while a Skeleton panel is open, also while a Go to another document has hidden it."""
    return _current is not None and not _current.done


_IDENT_KEYS = ("uid", "id", "name", "doc")


def _ident(obj):
    """The identity of an object that survives renames of the document and reuse of object names. It also keeps the live
    object: a copied file shares its document Uid and every object ID with the original, so the Uid alone cannot say which
    of the two open documents the object came from."""
    doc = obj.Document
    return {"uid": doc.Uid, "id": obj.ID, "name": obj.Name, "doc": doc.Name, "file": doc.FileName, "obj": obj}


def _same(a, b):
    """True when two identities name the same object (the live reference is not compared, it may be dead)."""
    return a is not None and b is not None and all(a[k] == b[k] for k in _IDENT_KEYS)


def _resolve(ident):
    """The very object ``ident`` was made from, or None when it, or its document, is gone, or the document is no longer
    the open one it was (closed, then another file with the same Uid opened). Never looks an object up by Uid alone."""
    try:
        obj = ident["obj"]
        doc = obj.Document
        if doc is None or App.listDocuments().get(doc.Name) is None or App.listDocuments()[doc.Name] != doc:
            return None
        if doc.Uid != ident["uid"] or obj.ID != ident["id"] or obj.Name != ident["name"]:
            return None
        if ident["file"] and doc.FileName != ident["file"]:
            return None
        if doc.getObject(obj.Name) != obj:
            return None
        return obj
    except Exception:  # noqa: BLE001  (the wrapper of a deleted object or of a closed document raises)
        return None


def _element_name_and_key(target, sub):
    """(mapped name or None, geometric fingerprint) of element ``sub`` (an index name) of ``target``'s shape."""
    shape = target.Shape
    mapped = ""
    try:
        mapped = shape.getElementMappedName(sub)
    except Exception:  # noqa: BLE001
        pass
    return (mapped if mapped and mapped != sub else None), AS.shape_digest(shape.getElement(sub))


# --------------------------------------------------------------------- panel

ROLE_KEY = QtCore.Qt.UserRole
ROLE_ROW = QtCore.Qt.UserRole + 1


class TaskSkeleton:
    """The skeleton panel of ``part``."""

    def __init__(self, part):
        if Gui.Control.activeDialog() or panel_open():   # before anything touches a document
            raise RuntimeError("another task dialog is already open")
        self.part = part
        self.doc = part.Document
        self.document_name = self.doc.Name
        self.part_name = part.Name
        self.part_id = part.ID
        self.ops = []               # staged actions, oldest first: dicts with an "op" key
        self.problems = []          # what the last OK could not do
        self.done = False
        self._pending = False
        self._busy = False
        self._observing = False
        try:
            self._build()
        except BaseException:
            self._stop_observing()          # a failed construction must not leave the registry or an observer behind
            self._release()
            raise
        global _current
        _current = self                     # registered last: only a panel that was built can block the next one

    def _build(self):
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Assembly_TaskSkeleton")
        self.form.setWindowTitle(translate("Assembly", "Skeleton"))
        lay = QtWidgets.QVBoxLayout(self.form)
        self.header = QtWidgets.QLabel()
        self.header.setObjectName("header")
        self.header.setWordWrap(True)
        lay.addWidget(self.header)

        self.items_tree = self._tree("items", translate("Assembly", "Published items and their subscribers"),
                                     [translate("Assembly", "Item / subscriber"), translate("Assembly", "Kind / state"),
                                      translate("Assembly", "Change")], 120)
        lay.addWidget(self.items_tree, 1)
        self.subs_tree = self._tree("subscriptions", translate("Assembly", "Subscriptions of this part"),
                                    [translate("Assembly", "Subscription"), translate("Assembly", "State"),
                                     translate("Assembly", "Change")], 90)
        lay.addWidget(self.subs_tree, 1)

        rows = (
            (("publish", "Publish", "Publish the selected face, edge, vertex, sketch or datum of this part"),
             ("rename", "Rename...", "Rename the selected item"),
             ("unpublish", "Unpublish", "Withdraw the selected item; its subscribers become broken")),
            (("subscribe", "Subscribe...", "Subscribe this part to a published item"),
             ("unsubscribe", "Unsubscribe", "Remove the selected subscription"),
             ("freeze", "Freeze", "Keep the geometry the selected subscription has now"),
             ("unfreeze", "Unfreeze", "Follow the item again")),
            (("goto", "Go to", "Select the item's source (the skeleton when the item is gone)"),),
        )
        self.buttons = {}
        for specs in rows:
            row = QtWidgets.QHBoxLayout()
            for key, text, tip in specs:
                b = QtWidgets.QPushButton(translate("Assembly", text))
                b.setObjectName("btn_" + key)
                b.setToolTip(translate("Assembly", tip))
                self.buttons[key] = b
                row.addWidget(b)
            if len(specs) < 3:
                row.addStretch(1)
            lay.addLayout(row)
        self.buttons["publish"].clicked.connect(lambda *_: self.publish())
        self.buttons["rename"].clicked.connect(lambda *_: self.rename())
        self.buttons["unpublish"].clicked.connect(lambda *_: self.unpublish())
        self.buttons["subscribe"].clicked.connect(lambda *_: self.subscribe())
        self.buttons["unsubscribe"].clicked.connect(lambda *_: self.unsubscribe())
        self.buttons["freeze"].clicked.connect(lambda *_: self.freeze())
        self.buttons["unfreeze"].clicked.connect(lambda *_: self.unfreeze())
        self.buttons["goto"].clicked.connect(lambda *_: self.goto())

        self.detail = QtWidgets.QLabel()
        self.detail.setObjectName("detail")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)
        self.error = QtWidgets.QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("background-color: #b00020; color: white; padding: 2px 6px;")
        self.error.hide()
        lay.addWidget(self.error)

        self.items_tree.currentItemChanged.connect(lambda cur, *_: self._current_changed(self.items_tree, cur))
        self.subs_tree.currentItemChanged.connect(lambda cur, *_: self._current_changed(self.subs_tree, cur))
        self.items_tree.itemDoubleClicked.connect(lambda *_: self.goto())
        self.subs_tree.itemDoubleClicked.connect(lambda *_: self.goto())
        self.observer = _Observer(self)
        App.addDocumentObserver(self.observer)
        self._observing = True
        self.form.destroyed.connect(self._form_gone)
        self.refresh()

    def _tree(self, name, accessible, headers, min_height):
        tree = QtWidgets.QTreeWidget()
        tree.setObjectName(name)
        tree.setColumnCount(len(headers))
        tree.setHeaderLabels(headers)
        tree.setRootIsDecorated(True)
        tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        tree.setMinimumHeight(min_height)
        tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        tree.header().setStretchLastSection(True)
        tree.setAccessibleName(accessible)
        return tree

    # ---- the document as it will be after OK

    def _alive(self):
        if self.document_name not in App.listDocuments():
            return False
        part = App.getDocument(self.document_name).getObject(self.part_name)
        return part is not None and part.ID == self.part_id

    def _sub_row(self, binder, pending=""):
        d = AS.describe_subscription(binder)
        state = d["state"]
        if state == AS.FROZEN and d["skeleton_changed"]:
            text = translate("Assembly", "frozen, skeleton changed")
        else:
            text = state
        return {"kind": "sub", "object": binder.Name, "document": binder.Document.Name, "label": binder.Label,
                "ident": _ident(binder), "op_id": None,
                "item": d["item"], "skeleton": d["skeleton"], "state": state, "state_text": text,
                "reason": d["reason"], "frozen": d["frozen"], "skeleton_changed": d["skeleton_changed"],
                "pending": pending, "item_problem": d.get("item_problem")}

    def _holds(self, binder):
        p = binder.getParentGeoFeatureGroup()
        while p is not None:
            if p == self.part:
                return True
            p = p.getParentGeoFeatureGroup()
        return False

    def model(self):
        """The two lists as they will be after OK: {"items": [item rows with "subs"], "subs": [subscription rows]}."""
        part = self.doc.getObject(self.part_name)
        items = []
        for it in AS.items(part):
            items.append({"kind": "item", "object": it.Name, "name": it.ItemName, "itemkind": it.Kind, "ident": _ident(it),
                          "status": it.Status, "pending": "", "old_name": None, "withdrawn": False,
                          "subs": [self._sub_row(b) for b in AS.subscribers_of(it)]})
        subs = [self._sub_row(b) for b in AS.subscriptions(self.doc) if self._holds(b)]
        for op in self.ops:
            kind = op["op"]
            if kind == "publish":
                items.append({"kind": "item", "object": None, "name": op["name"], "itemkind": op["itemkind"], "ident": None,
                              "status": "", "pending": translate("Assembly", "publish"), "old_name": None,
                              "withdrawn": False, "subs": []})
            elif kind == "rename":
                row = _by_ident(items, op["item"])
                if row is not None:
                    row["old_name"] = row["old_name"] or row["name"]
                    row["name"] = op["new"]
                    row["pending"] = translate("Assembly", "rename")
            elif kind == "unpublish":
                row = _by_ident(items, op["item"])
                if row is not None:
                    row["withdrawn"] = True
                    row["pending"] = translate("Assembly", "withdraw")
                    for s in row["subs"]:
                        s["pending"] = translate("Assembly", "will break")
            elif kind == "subscribe":
                item = _resolve(op["item"])
                subs.append({"kind": "sub", "object": None, "document": self.document_name, "op_id": op["op_id"],
                             "ident": None,
                             "label": op["label"], "item": op["label"], "skeleton": op["skeleton"], "state": "new",
                             "state_text": translate("Assembly", "new"), "reason": None, "frozen": False,
                             "skeleton_changed": None, "pending": translate("Assembly", "subscribe"),
                             "item_problem": None, "staged": True, "item_object": item})
            else:
                row = next((s for s in subs + [c for r in items for c in r["subs"]] if _same(s["ident"], op["binder"])), None)
                if row is None:
                    continue
                if kind == "unsubscribe":
                    row["pending"] = translate("Assembly", "unsubscribe")
                elif kind == "freeze":
                    row["pending"] = translate("Assembly", "freeze")
                    row["frozen"] = True
                    row["state_text"] = translate("Assembly", "frozen")
                elif kind == "unfreeze":
                    row["pending"] = translate("Assembly", "unfreeze")
                    row["frozen"] = False
                    row["state_text"] = translate("Assembly", "following")
        return {"items": items, "subs": subs}

    # ---- refresh

    def schedule_refresh(self):
        if self._pending or self._busy or self.done:
            return
        self._pending = True
        QtCore.QTimer.singleShot(0, self._deferred_refresh)

    def _deferred_refresh(self):
        self._pending = False
        if not self.done and self._alive():
            try:
                self.refresh()
            except Exception:  # noqa: BLE001  (the document is going away)
                pass
        elif not self.done:
            self._stop_observing()
            self._release()                      # the document is closed: the panel is gone with it

    def refresh(self):
        keep_item, keep_sub = self.selected_item_name(), self.selected_subscription_key()
        m = self.model()
        part = self.doc.getObject(self.part_name)
        self.header.setText(translate("Assembly", "Part: %s. It publishes %d item(s) and holds %d subscription(s).")
                            % (part.Label, len(m["items"]), len(m["subs"])))
        self.items_tree.blockSignals(True)
        self.subs_tree.blockSignals(True)
        self.items_tree.clear()
        self.subs_tree.clear()
        if not m["items"]:
            self.items_tree.addTopLevelItem(QtWidgets.QTreeWidgetItem(
                [translate("Assembly", "This part publishes nothing"), "", ""]))
        for row in m["items"]:
            change = row["pending"] + (" (was %s)" % row["old_name"] if row["old_name"] else "")
            top = QtWidgets.QTreeWidgetItem([row["name"], row["itemkind"], change])
            top.setData(0, ROLE_KEY, ("item", row["name"]))
            top.setData(0, ROLE_ROW, row)
            self.items_tree.addTopLevelItem(top)
            for s in row["subs"]:
                child = QtWidgets.QTreeWidgetItem([s["label"], s["state_text"], s["pending"]])
                child.setData(0, ROLE_KEY, ("sub", s["document"], s["object"]))
                child.setData(0, ROLE_ROW, s)
                if s["reason"]:
                    child.setToolTip(1, s["reason"])
                top.addChild(child)
            top.setExpanded(True)
        if not m["subs"]:
            self.subs_tree.addTopLevelItem(QtWidgets.QTreeWidgetItem(
                [translate("Assembly", "This part holds no subscriptions"), "", ""]))
        for s in m["subs"]:
            name = s["label"] + ("" if s["item"] in (None, s["label"]) else " (%s)" % s["item"])
            it = QtWidgets.QTreeWidgetItem([name, s["state_text"], s["pending"]])
            it.setData(0, ROLE_KEY, ("sub", s["document"], s["object"]) if s["object"] else ("staged", s["op_id"]))
            it.setData(0, ROLE_ROW, s)
            if s["reason"]:
                it.setToolTip(1, s["reason"])
            self.subs_tree.addTopLevelItem(it)
        self.items_tree.blockSignals(False)
        self.subs_tree.blockSignals(False)
        if keep_item is not None:
            self.select_item(keep_item)
        elif keep_sub is not None:
            self.select_subscription(keep_sub[1], keep_sub[0])
        self._update_state()

    def _current_changed(self, tree, cur):
        other = self.subs_tree if tree is self.items_tree else self.items_tree
        if cur is not None and other.currentItem() is not None:
            other.blockSignals(True)
            other.setCurrentItem(None)
            other.blockSignals(False)
        self._update_state()

    # ---- what is selected

    def _row(self, tree):
        it = tree.currentItem()
        return None if it is None else it.data(0, ROLE_ROW)

    def current_row(self):
        return self._row(self.items_tree) or self._row(self.subs_tree)

    def selected_item_name(self):
        row = self._row(self.items_tree)
        return row["name"] if row and row["kind"] == "item" else None

    def selected_subscription_key(self):
        row = self._row(self.subs_tree) or self._row(self.items_tree)
        if row and row["kind"] == "sub":
            return (row["document"], row["object"] if row["object"] else row["op_id"])
        return None

    def select_item(self, name):
        """Select the item row called ``name`` (as projected, so a staged rename is found under its new name)."""
        for i in range(self.items_tree.topLevelItemCount()):
            it = self.items_tree.topLevelItem(i)
            row = it.data(0, ROLE_ROW)
            if row and row["kind"] == "item" and row["name"] == name:
                self.items_tree.setCurrentItem(it)
                return True
        return False

    def select_subscription(self, key, document=None):
        """Select a subscription by object name (or label for a staged one), in the subscriptions list."""
        for i in range(self.subs_tree.topLevelItemCount()):
            it = self.subs_tree.topLevelItem(i)
            row = it.data(0, ROLE_ROW)
            if row and row["kind"] == "sub" and (key in (row["object"], row["label"]) or (
                    row["op_id"] is not None and key == row["op_id"])) and (document is None or document == row["document"]):
                self.subs_tree.setCurrentItem(it)
                return True
        return False

    def select_subscriber(self, label):
        """Select a subscriber row under a published item by its label."""
        for i in range(self.items_tree.topLevelItemCount()):
            top = self.items_tree.topLevelItem(i)
            for j in range(top.childCount()):
                if top.child(j).data(0, ROLE_ROW)["label"] == label:
                    self.items_tree.setCurrentItem(top.child(j))
                    return True
        return False

    def rows(self):
        """Plain-data view of what the lists show, for tests: (items, subscriptions)."""
        m = self.model()
        return m["items"], m["subs"]

    def _update_state(self):
        if not self._alive():
            return
        row = self.current_row()
        b = self.buttons
        has_item = row is not None and row["kind"] == "item"
        has_sub = row is not None and row["kind"] == "sub"
        real_sub = has_sub and row["object"] is not None
        staged_sub = has_sub and row["object"] is None
        pending_gone = row is not None and row.get("pending") in (translate("Assembly", "withdraw"),
                                                                  translate("Assembly", "unsubscribe"))
        b["publish"].setEnabled(True)
        b["rename"].setEnabled(has_item and not row["withdrawn"])
        b["unpublish"].setEnabled(has_item and not row["withdrawn"])
        b["subscribe"].setEnabled(True)
        b["unsubscribe"].setEnabled(has_sub and not pending_gone)
        b["freeze"].setEnabled(real_sub and not row["frozen"] and not pending_gone)
        b["unfreeze"].setEnabled(real_sub and row["frozen"] and not pending_gone)
        b["goto"].setEnabled((has_item and row["object"] is not None) or real_sub)
        if staged_sub:
            b["freeze"].setEnabled(False)
        self.detail.setText(self._detail(row))

    def _detail(self, row):
        if row is None:
            return translate("Assembly", "Nothing changes until you press OK. Cancel discards the changes listed here.")
        if row["kind"] == "item":
            text = translate("Assembly", "Item %s (%s): %s.") % (row["name"], row["itemkind"],
                                                                 row["status"] or translate("Assembly", "not made yet"))
            if row["subs"]:
                text += " " + translate("Assembly", "%d subscriber(s).") % len(row["subs"])
            return text
        text = translate("Assembly", "%s: %s.") % (row["label"], row["state_text"])
        if row["reason"]:
            text += " " + row["reason"]
        elif row["state"] == AS.FROZEN and row["skeleton_changed"]:
            text += " " + translate("Assembly", "The skeleton has changed since it was frozen.")
        if row.get("item_problem"):
            text += " " + row["item_problem"]
        return text

    def _message(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # ---- staging

    def _stage(self, op):
        self.ops.append(op)
        self._message("")
        self.refresh()
        return True

    def _names_taken(self, skip=None):
        return {r["name"] for r in self.model()["items"] if r["name"] != skip and not r["withdrawn"]}

    def check_name(self, name, own=None, skip=None):
        """The stripped name, or SkeletonError: the API's checks against the document, then the names already staged.
        ``own`` is the item object a rename is for, ``skip`` the projected name it currently has."""
        part = self.doc.getObject(self.part_name)
        try:
            name = AS._check_name(part, name, own=own)
        except AS.SkeletonError:
            # a name freed by a staged rename or withdrawal is free again once OK has applied it (ops apply in order)
            freed = {op["name"] for op in self.ops if op["op"] in ("rename", "unpublish")}
            holder = next((i for i in AS.items(part) if i.ItemName == str(name).strip() and i.ItemName in freed), None)
            if holder is None or holder == own:
                raise
            name = AS._check_name(part, name, own=holder)
        if name in self._names_taken(skip):
            raise AS.SkeletonError(translate("Assembly", "%s already has a staged item named %r") % (part.Label, name))
        return name

    def publish(self, name=None, candidates=None):
        """Stage a publish of each selected piece of geometry (or of ``candidates``), asking for a name (default proposed
        from the element). Returns the number staged."""
        part = self.doc.getObject(self.part_name)
        if candidates is None:
            candidates, why = resolve_selection()
            if not candidates:
                self._message(why[0] if why else translate(
                    "Assembly", "Select a face, edge, vertex, sketch or datum of this part first"))
                return 0
        staged = 0
        for c in candidates:
            if c.skeleton != part:
                self._message(translate("Assembly", "%s belongs to %s, not to %s") % (
                    c.target.Label, c.skeleton.Label, part.Label))
                continue
            if c.target.Document != self.doc:
                self._message(translate("Assembly", "%s is not in this document") % c.target.Label)
                continue
            proposed = self._proposal(part, c)
            chosen = name if (name is not None and len(candidates) == 1) else ask_name(
                self.form, translate("Assembly", "Publish"), translate("Assembly", "Name of the published item"), proposed)
            if chosen is None:
                continue
            try:
                chosen = self.check_name(chosen)
                kind = AS._kind_of(c.target, c.sub)
            except AS.SkeletonError as exc:
                self._message(str(exc))
                continue
            mapped, key = _element_name_and_key(c.target, c.sub) if c.sub else (None, None)
            self._stage({"op": "publish", "op_id": next(_op_ids), "name": chosen, "target": _ident(c.target),
                         "target_label": c.target.Label, "sub": c.sub, "mapped": mapped, "fingerprint": key, "itemkind": kind})
            staged += 1
        return staged

    def _proposal(self, part, c):
        base = AS.default_name(part, c.target, c.sub)
        taken, name, n = self._names_taken(), base, 1
        while name in taken:
            n += 1
            name = "%s_%d" % (base, n)
        return name

    def _item_row(self, name):
        name = self.selected_item_name() if name is None else name
        if name is None:
            return None
        return _by_name(self.model()["items"], name)

    def rename(self, new_name=None, name=None):
        """Stage a rename of the selected item (or ``name``). Returns True when staged."""
        row = self._item_row(name)
        if row is None or row["withdrawn"]:
            self._message(translate("Assembly", "Select a published item first"))
            return False
        if new_name is None:
            new_name = ask_name(self.form, translate("Assembly", "Rename item"), translate("Assembly", "New name"), row["name"])
            if new_name is None:
                return False
        part = self.doc.getObject(self.part_name)
        own = self.doc.getObject(row["object"]) if row["object"] else None
        try:
            new_name = self.check_name(new_name, own=own, skip=row["name"])
        except AS.SkeletonError as exc:
            self._message(str(exc))
            return False
        if new_name == row["name"]:
            return False
        for op in self.ops:                      # a staged publish or rename of this very item is edited, not stacked
            if op["op"] == "publish" and op["name"] == row["name"]:
                op["name"] = new_name
                return self._stage_done()
            if op["op"] == "rename" and _same(op["item"], row["ident"]) and op["new"] == row["name"]:
                if any(i.ItemName == new_name and i != own for i in AS.items(part)):
                    break                        # that name is held until an earlier action frees it: a step of its own
                op["new"] = new_name
                if op["new"] == op["name"]:
                    self.ops.remove(op)
                return self._stage_done()
        return self._stage({"op": "rename", "op_id": next(_op_ids), "name": row["name"], "new": new_name,
                            "item": row["ident"], "skeleton_id": own.SkeletonId})

    def _stage_done(self):
        self._message("")
        self.refresh()
        return True

    def unpublish(self, name=None, assume_yes=False):
        """Stage the withdrawal of the selected item. Asks first when it has subscribers. Returns True when staged."""
        row = self._item_row(name)
        if row is None or row["withdrawn"]:
            self._message(translate("Assembly", "Select a published item first"))
            return False
        if row["subs"] and not assume_yes and not confirm(
                self.form, translate("Assembly", "Unpublish"),
                translate("Assembly", "%d subscriber(s) follow %s. They will keep their last geometry and report that the "
                                      "item is gone.") % (len(row["subs"]), row["name"])):
            return False
        for op in self.ops:
            if op["op"] == "publish" and op["name"] == row["name"]:   # never made: just forget it
                self.ops.remove(op)
                return self._stage_done()
        item = self.doc.getObject(row["object"])
        new = {"op": "unpublish", "op_id": next(_op_ids), "name": row["name"], "item": row["ident"],
               "skeleton_id": item.SkeletonId, "confirmed": [s["ident"] for s in row["subs"]]}
        pending_rename = next((op for op in self.ops if op["op"] == "rename" and _same(op["item"], row["ident"])
                               and op["new"] == row["name"]), None)
        if pending_rename is not None:           # the withdrawal takes the place of the rename, so the order stays as shown
            new["name"] = pending_rename["name"]
            self.ops[self.ops.index(pending_rename)] = new
            return self._stage_done()
        return self._stage(new)

    def _sub_row_for(self, key):
        row = self._row(self.subs_tree) or self._row(self.items_tree)
        if key is not None:
            subs = self.model()["subs"]
            row = next((s for s in subs if key in (s["object"], s["label"])), None)
        return row if row is not None and row["kind"] == "sub" else None

    def subscribe(self, item=None):
        """Stage a subscription of this part to ``item`` (a published item object; default: ask which one). Returns True when staged."""
        part = self.doc.getObject(self.part_name)
        if item is None:
            own = {o.Name for o in AS.items(part)}
            cands = [i for i in published_items() if not (i.Document == self.doc and i.Name in own)]
            if not cands:
                self._message(translate("Assembly", "No other part publishes an item yet"))
                return False
            item = ask_item(self.form, cands)
            if item is None:
                return False
        if not AS.is_item(item) or not item.Published:
            self._message(translate("Assembly", "%s is not a published item") % item.Label)
            return False
        sk = AS.skeleton_of(item)
        if item.Document != self.doc and not (self.doc.FileName and item.Document.FileName):
            self._message(translate("Assembly", "Save both documents before subscribing across documents"))
            return False
        try:
            AS._refuse_cycle(part, item)
        except AS.SkeletonError as exc:
            self._message(str(exc))
            return False
        if any(op["op"] == "subscribe" and _same(op["item"], _ident(item)) for op in self.ops):
            self._message(translate("Assembly", "A subscription to %s is already staged") % item.ItemName)
            return False
        return self._stage({"op": "subscribe", "op_id": next(_op_ids), "item": _ident(item), "label": item.ItemName,
                            "item_name": item.ItemName, "skeleton_id": item.SkeletonId,
                            "skeleton": sk.Label if sk else item.SkeletonName})

    def unsubscribe(self, key=None, assume_yes=False):
        """Stage the removal of the selected subscription. Asks first. Returns True when staged."""
        row = self._sub_row_for(key)
        if row is None:
            self._message(translate("Assembly", "Select a subscription first"))
            return False
        if not assume_yes and not confirm(
                self.form, translate("Assembly", "Unsubscribe"),
                translate("Assembly", "Remove %s? Features built on it are left without their geometry.") % row["label"]):
            return False
        if row["object"] is None:                 # staged here and never made: forget it
            for op in self.ops:
                if op["op"] == "subscribe" and op["op_id"] == row["op_id"]:
                    self.ops.remove(op)
                    return self._stage_done()
        return self._stage({"op": "unsubscribe", "op_id": next(_op_ids), "binder": row["ident"], "label": row["label"]})

    def freeze(self, key=None):
        return self._freeze(key, "freeze", "unfreeze")

    def unfreeze(self, key=None):
        return self._freeze(key, "unfreeze", "freeze")

    def _freeze(self, key, op, opposite):
        row = self._sub_row_for(key)
        if row is None or row["object"] is None:
            self._message(translate("Assembly", "Select a subscription first"))
            return False
        if (op == "freeze") == bool(row["frozen"]):
            return False
        for staged in self.ops:                   # freeze then unfreeze (or the reverse) cancels out
            if staged["op"] == opposite and _same(staged["binder"], row["ident"]):
                self.ops.remove(staged)
                return self._stage_done()
        return self._stage({"op": op, "op_id": next(_op_ids), "binder": row["ident"], "label": row["label"]})

    # ---- go to

    def goto(self, key=None):
        """Select the source of the selected item or subscription in the 3D view. Returns a dict saying what happened:
        {"went": "source" | "item" | "skeleton" | None, "message": text}. Activates the document the object is in when it
        is not the active one; never raises a window."""
        row = self._sub_row_for(key) if key is not None else self.current_row()
        if row is None:
            self._message(translate("Assembly", "Select an item or a subscription first"))
            return {"went": None, "message": self.error.text()}
        if row["kind"] == "item":
            if row["object"] is None:
                self._message(translate("Assembly", "%s is not made yet") % row["name"])
                return {"went": None, "message": self.error.text()}
            result = self._goto_item(self.doc.getObject(row["object"]))
        else:
            result = self._goto_subscription(row)
        self._message(result["message"] if result["went"] is None else "")
        if result["went"] is not None:
            self.detail.setText(result["message"])
        return result

    def _goto_item(self, item):
        src = item.Source
        if src and src[0] is not None:
            target, sub = src[0], (src[1][0] if src[1] else "")
            _select(target, sub)
            return {"went": "source", "message": translate("Assembly", "Selected %s%s in %s.") % (
                target.Label, "." + sub if sub else "", target.Document.Name)}
        _select(item, "")
        return {"went": "item", "message": translate("Assembly", "The source of %s is gone; selected the item.") % item.ItemName}

    def _goto_subscription(self, row):
        binder = App.getDocument(row["document"]).getObject(row["object"]) if row["object"] else None
        if binder is None:
            return {"went": None, "message": translate("Assembly", "%s is not made yet") % row["label"]}
        item = AS._support_item(binder)
        if item is not None:
            return self._goto_item(item)
        rec = AS._info(binder)
        doc = next((d for d in App.listDocuments().values() if rec.get("uid") and d.Uid == rec["uid"]), None)
        if doc is None and rec.get("same"):
            doc = binder.Document
        if doc is None or not AS._document_open(binder, rec):
            return {"went": None, "message": translate(
                "Assembly", "The document %s is not open. Open it to go to %s.") % (
                    rec.get("file") or rec.get("doc") or "?", rec.get("item") or row["label"])}
        skeleton = next((o for o in doc.Objects if o.Label == rec.get("skeleton") and is_part(o)), None)
        if skeleton is None:
            return {"went": None, "message": translate(
                "Assembly", "%s is gone from %s; nothing to go to.") % (rec.get("item") or row["label"], doc.Name)}
        _select(skeleton, "")
        return {"went": "skeleton", "message": translate(
            "Assembly", "The item %s is gone; selected its skeleton %s in %s.") % (rec.get("item"), skeleton.Label, doc.Name)}

    # ---- OK and Cancel

    def apply(self):
        """Apply the staged actions in order, each as its own API call. Returns the problems (empty when all went through)."""
        problems = []
        part = self.doc.getObject(self.part_name)
        self._busy = True
        try:
            for op in self.ops:
                try:
                    self._apply_one(part, op)
                except Exception as exc:  # noqa: BLE001  (SkeletonError, or the document changed under the panel)
                    problems.append("%s: %s" % (_op_text(op), exc))
        finally:
            self._busy = False
            self.ops = []
        return problems

    def _apply_one(self, part, op):
        kind = op["op"]
        if kind == "publish":
            target = _resolve(op["target"])
            if target is None:
                raise AS.SkeletonError(translate("Assembly", "%s no longer exists") % op["target_label"])
            AS.publish(part, op["name"], target, self._current_element(target, op))
        elif kind in ("rename", "unpublish"):
            item = self._current_item(part, op)
            if kind == "rename":
                AS.rename(part, op["name"], op["new"])
            else:
                strangers = [b for b in AS.subscribers_of(item)
                             if not any(_same(c, _ident(b)) for c in op["confirmed"])]
                if strangers:
                    raise AS.SkeletonError(translate("Assembly", "%s now has %d subscriber(s) that were not confirmed: %s") % (
                        op["name"], len(strangers), ", ".join(b.Label for b in strangers)))
                AS.unpublish(part, op["name"])
        elif kind == "subscribe":
            item = _resolve(op["item"])
            if item is None or not AS.is_item(item) or item.ItemName != op["item_name"] \
                    or item.SkeletonId != op["skeleton_id"] or not item.Published:
                raise AS.SkeletonError(translate("Assembly", "the item %s no longer exists") % op["label"])
            AS.subscribe(part, item)
        else:
            binder = _resolve(op["binder"])
            if binder is None:
                raise AS.SkeletonError(translate("Assembly", "the subscription no longer exists"))
            {"unsubscribe": AS.unsubscribe, "freeze": AS.freeze, "unfreeze": AS.unfreeze}[kind](binder)

    @staticmethod
    def _current_item(part, op):
        """The item a staged rename or withdrawal meant, if it is still that very object, published under the name it was
        staged for; otherwise SkeletonError (an action is never applied to another item)."""
        item = _resolve(op["item"])
        if item is None or not AS.is_item(item) or not item.Published or item.ItemName != op["name"] \
                or item.SkeletonId != op["skeleton_id"] or item.SkeletonId != part.ID:
            raise AS.SkeletonError(translate("Assembly", "the item %s is gone or has become another one") % op["name"])
        return item

    @staticmethod
    def _current_element(target, op):
        """The index name, now, of the element a staged publish meant, or SkeletonError. The element is followed by its mapped
        name when that still resolves; failing that, by its geometric fingerprint, accepted only when exactly one element of
        the same kind has it. An element that is gone, changed or ambiguous is never replaced by another one."""
        sub = op["sub"]
        if not sub:
            return sub
        shape = target.Shape

        def same(name):
            try:
                return AS.shape_digest(shape.getElement(name)) == op["fingerprint"]
            except Exception:  # noqa: BLE001
                return False

        current = sub
        if op["mapped"]:
            current = target.resolveSubElement(op["mapped"])[2]
        if current and not current.startswith("?") and same(current):
            return current
        kind = sub.rstrip("0123456789")
        found = [n for n in ("%s%d" % (kind, i + 1) for i in range(len(getattr(shape, kind + "s" if kind != "Vertex" else "Vertexes"))))
                 if same(n)]
        if len(found) == 1:
            return found[0]
        raise AS.SkeletonError(translate("Assembly", "the selected element of %s no longer exists or has changed")
                               % op["target_label"])

    def _release(self):
        global _current
        if _current is self:
            _current = None
        self.done = True

    def _stop_observing(self):
        if getattr(self, "_observing", False):
            self._observing = False
            try:
                App.removeDocumentObserver(self.observer)
            except Exception:  # noqa: BLE001
                pass

    def _form_gone(self, *_):
        """Gui.Control.closeDialog() calls neither accept nor reject, nor does closing the document: nothing was changed, so
        there is nothing to undo."""
        self._stop_observing()
        self._release()
        self.ops = []

    # ---- task dialog protocol

    def getStandardButtons(self):
        std = QtWidgets.QDialogButtonBox.StandardButton
        ok, cancel = std.Ok, std.Cancel
        return int(getattr(ok, "value", ok)) | int(getattr(cancel, "value", cancel))

    def accept(self):
        self._stop_observing()
        try:
            if not self.done and self._alive():
                self.problems = self.apply()
                self.done = True
                if self.problems:
                    report_problems(self.form, self.problems)
        finally:
            self._release()
        return True

    def reject(self):
        self._stop_observing()
        self.ops = []
        self._release()
        return True

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True


# ------------------------------------------------------------------- helpers

def _by_name(rows, name):
    return next((r for r in rows if r["name"] == name), None)


def _by_ident(rows, ident):
    """The made item row that is the object ``ident`` names (None when it is not in the list any more)."""
    return next((r for r in rows if _same(r.get("ident"), ident)), None)


def _op_text(op):
    kind = op["op"]
    if kind == "publish":
        return translate("Assembly", "publish %s") % op["name"]
    if kind == "rename":
        return translate("Assembly", "rename %s to %s") % (op["name"], op["new"])
    if kind == "unpublish":
        return translate("Assembly", "unpublish %s") % op["name"]
    if kind == "subscribe":
        return translate("Assembly", "subscribe to %s") % op["label"]
    return "%s %s" % (kind, op["label"])


def _select(obj, sub=""):
    """Select ``obj`` (and ``sub``) in its own document, making that document the active one first. Neither raises a window
    nor changes any document."""
    doc = obj.Document
    gdoc = Gui.getDocument(doc.Name)
    if Gui.ActiveDocument is None or Gui.ActiveDocument.Document.Name != doc.Name:
        App.setActiveDocument(doc.Name)
        Gui.ActiveDocument = gdoc
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, obj.Name, sub)
