"""Minimal fake tkinter so the UI code paths can run headless (logic check only, not rendering)."""
import sys, types, itertools, os

BUTTONS, TREES, VARS = [], [], []
_after = itertools.count(1)

class TclError(Exception): pass

class W:
    def __init__(self, *a, **k):
        self.kw = dict(k); self.master = a[0] if a else None; self.binds = {}
    def __getattr__(self, name):
        if name.startswith("__"): raise AttributeError(name)
        return lambda *a, **k: None
    def winfo_children(self): return []
    def winfo_exists(self): return True
    def winfo_class(self): return type(self).__name__
    def winfo_width(self): return 900
    def winfo_fpixels(self, s): return float(os.environ.get('FPI', '144'))
    def winfo_screenwidth(self): return 1920
    def winfo_screenheight(self): return 1080
    def winfo_rootx(self): return 100
    def winfo_rooty(self): return 50
    def winfo_reqwidth(self): return 400
    def winfo_reqheight(self): return 300
    def winfo_height(self): return 600
    def bind(self, seq, fn, *a): self.binds[seq] = fn
    def config(self, **k): self.kw.update(k)
    configure = config

class Tk(W):
    def after(self, ms, fn=None, *a): return next(_after)
    def mainloop(self): pass
    def destroy(self): pass

class Button(W):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.command = k.get("command"); self.text = k.get("text"); BUTTONS.append(self)
    def config(self, **k):
        super().config(**k)
        if "command" in k: self.command = k["command"]
    configure = config
    def invoke(self): return self.command()

class Var:
    def __init__(self, master=None, value=None, **k): self._v = value; self._tr = []
    def get(self): return self._v
    def set(self, v):
        self._v = v
        for cb in self._tr: cb()
    def trace_add(self, mode, cb): self._tr.append(lambda: cb())
class StringVar(Var):
    def __init__(self, master=None, value="", **k): super().__init__(master, value)
class BooleanVar(Var):
    def __init__(self, master=None, value=False, **k): super().__init__(master, value)

class Treeview(W):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.cols = list(k.get("columns", [])); self.items = {}; self._sel = (); self._n = 0; self._yv = (0.0, 0.5); self.scrolled = []; TREES.append(self)
    def insert(self, parent, index, iid=None, **kw):
        if iid is None:
            self._n += 1; iid = f"I{self._n:03d}"
        self.items[iid] = dict(parent=parent, **kw); return iid
    def get_children(self, item=""): return tuple(i for i, d in self.items.items() if d["parent"] == item)
    def delete(self, *ids):
        for i in ids:
            for c in list(self.get_children(i)): self.delete(c)
            self.items.pop(i, None)
        self._sel = tuple(s for s in self._sel if s in self.items)
    def selection(self): return self._sel
    def selection_set(self, iid): self._sel = (iid,) if isinstance(iid, str) else tuple(iid)
    def exists(self, iid): return iid in self.items
    def item(self, iid, option=None, **kw):
        d = self.items[iid]
        if kw: d.update(kw); return None
        if option == "values": return tuple(str(v) for v in d.get("values", ()))
        if option == "tags": return tuple(d.get("tags", ()))
        return d
    def set(self, iid, col=None):
        vals = self.items[iid].get("values", ())
        return str(vals[self.cols.index(col)]) if col in self.cols else ""
    def move(self, iid, parent, index):
        keys = list(self.items); keys.remove(iid); keys.insert(index, iid)
        self.items = {k: self.items[k] for k in keys}
    def yview(self, *a): return self._yv
    def yview_scroll(self, n, what): self.scrolled.append((n, what))
    def heading(self, *a, **k): pass
    def column(self, *a, **k): pass

class Style(W):
    def map(self, *a, **k): return []
    def configure(self, *a, **k): pass
    def theme_use(self, *a, **k): pass

class Font:
    def __init__(self, *a, **k): pass
    def cget(self, k): return 10
    def actual(self, k=None): return 'Segoe UI'
    def copy(self): return Font()
    def configure(self, **k): pass

tk = types.ModuleType("tkinter")
ttk = types.ModuleType("tkinter.ttk")
tkfont = types.ModuleType("tkinter.font")
messagebox = types.ModuleType("tkinter.messagebox")
simpledialog = types.ModuleType("tkinter.simpledialog")
for n in ("Frame", "Label", "Toplevel", "Entry", "Canvas", "Scrollbar"): setattr(tk, n, type(n, (W,), {}))
tk.Tk, tk.StringVar, tk.BooleanVar, tk.TclError = Tk, StringVar, BooleanVar, TclError
tk.font, tk.messagebox, tk.simpledialog, tk.ttk = tkfont, messagebox, simpledialog, ttk
for n in ("Frame", "Label", "Entry", "Combobox", "Checkbutton", "Scrollbar", "Notebook"): setattr(ttk, n, type(n, (W,), {}))
ttk.Button, ttk.Treeview, ttk.Style = Button, Treeview, Style
tk.Button = Button
tkfont.nametofont = lambda n: Font()
tkfont.families = lambda *a: ['Segoe UI']
tkfont.Font = Font
ANSWERS = {"yes": True, "string": "too many credits", "int": 50}
messagebox.askyesno = lambda *a, **k: ANSWERS["yes"]
messagebox.showerror = lambda *a, **k: None
messagebox.showinfo = lambda *a, **k: None
simpledialog.askstring = lambda *a, **k: ANSWERS["string"]
simpledialog.askinteger = lambda *a, **k: ANSWERS["int"]
for name, mod in (("tkinter", tk), ("tkinter.ttk", ttk), ("tkinter.font", tkfont), ("tkinter.messagebox", messagebox), ("tkinter.simpledialog", simpledialog)):
    sys.modules[name] = mod
