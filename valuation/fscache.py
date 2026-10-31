"""Per-process cache for file reads on the shared project folder (2026-10-21, the speedup list Kilian approved).

The shared folder is a network mount: every open, stat or directory listing is a round trip (~0.02-0.05s), and the
nightly scripts make thousands of them (allocate.py about 10,000, review.py about 8,000), most for files they already
read. Run on local disk the same steps are 10-50x faster. install() makes this process:
  - read each file under the project folder once (later opens get the bytes from memory),
  - remember os.path.exists / isfile / isdir / getmtime / getsize and os.listdir answers,
  - answer glob.glob() with the same list in the same order, listing directories 32 at a time and remembering them.
Any write from this process (open for writing, makedirs, rename, remove, utime) drops the remembered answers (and that
file's bytes), so a script still sees its own writes. What it cannot see is another process writing the same files while
it runs; the scripts that call install() do not start subprocesses, and the nightly run has the folder to itself.
Usage, first line of a script's __main__ block:  import fscache; fscache.install()
"""
import builtins, concurrent.futures as cf, fnmatch, glob as _glob, io, os, threading, weakref

ALIASES = ('/mnt/attach/project-files', '/mnt/project-files')  # /mnt/project-files is a symlink to the first
_ROOT = None
_lock = threading.RLock()
_data, _meta, _writing = {}, {}, {}
_real = {}
_pool = None
STATS = dict(hits=0, reads=0)


def _canon(p):
    try: p = os.path.abspath(os.fspath(p))
    except TypeError: return None
    if isinstance(p, bytes): return None
    for a in ALIASES[:-1]:
        if p == a or p.startswith(a + os.sep): p = ALIASES[-1] + p[len(a):]
    return p


def _mine(k):
    return k is not None and (k == _ROOT or k.startswith(_ROOT + os.sep))


def _drop(k=None):
    with _lock:
        if k is not None: _data.pop(k, None)
        _meta.clear()


class _Bytes(io.BytesIO):
    def __init__(self, b, name):
        super().__init__(b); self.name = name


def _done_writing(k):
    with _lock:
        n = _writing.get(k, 0) - 1
        if n > 0: _writing[k] = n
        else: _writing.pop(k, None)
    _drop(k)


def _open(file, mode='r', buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
    k = _canon(file) if isinstance(file, (str, os.PathLike)) else None
    if not _mine(k) or opener is not None or not closefd:
        return _real['open'](file, mode, buffering, encoding, errors, newline, closefd, opener)
    if any(c in mode for c in 'wax+'):
        with _lock: _writing[k] = _writing.get(k, 0) + 1
        _drop(k)
        try: f = _real['open'](file, mode, buffering, encoding, errors, newline, closefd, opener)
        except BaseException: _done_writing(k); raise
        weakref.finalize(f, _done_writing, k)  # forget the bytes again once this handle is gone
        return f
    with _lock:
        b = _data.get(k) if k not in _writing else None
    if b is None:
        with _real['open'](file, 'rb') as f: b = f.read()
        with _lock:
            if k not in _writing: _data[k] = b
        STATS['reads'] += 1
    else: STATS['hits'] += 1
    buf = _Bytes(b, file)
    if 'b' in mode: return buf
    return io.TextIOWrapper(buf, encoding=encoding, errors=errors, newline=newline)


def _memo(name, k, fn):
    key = (name, k)
    with _lock:
        if key in _meta: return _meta[key]
    v = fn()
    with _lock: _meta[key] = v
    return v


def _known_absent(k):
    """True when a directory listing already in memory shows that k cannot exist: some folder on its path is missing
    from its parent's listing, or k itself is missing from a full listing of its folder. Never lists anything itself."""
    with _lock:
        d, tail = os.path.split(k); first = True
        while d and d != k and _mine(d):
            full = _meta.get((('scan', False), d)); dirs = _meta.get((('scan', True), d))
            if full is not None and tail not in full: return True
            if not first and dirs is not None and tail not in dirs: return True  # a folder on the path, not a folder here
            k, (d, tail), first = d, os.path.split(d), False
    return False


def _wrap_bool(name):
    real = _real[name]
    def f(path):
        k = _canon(path) if isinstance(path, (str, os.PathLike)) else None
        if not _mine(k): return real(path)
        return _memo(name, k, lambda: False if _known_absent(k) else real(path))
    return f


def _wrap_val(name):
    real = _real[name]
    def f(path):
        k = _canon(path) if isinstance(path, (str, os.PathLike)) else None
        if not _mine(k): return real(path)
        key = (name, k)
        with _lock:
            if key in _meta: return _meta[key]
        v = real(path)  # errors (a missing file) are raised every time, not remembered
        with _lock: _meta[key] = v
        return v
    return f


def _listdir(path='.'):
    k = _canon(path) if isinstance(path, (str, os.PathLike)) else None
    if not _mine(k): return _real['listdir'](path)
    key = ('listdir', k)
    with _lock:
        if key in _meta: return list(_meta[key])
    v = _real['listdir'](path)
    with _lock: _meta[key] = list(v)
    return v


def _writer(name):
    real = _real[name]
    def f(*a, **kw):
        try: return real(*a, **kw)
        finally:
            for p in a[:2]:
                if isinstance(p, (str, os.PathLike)): _drop(_canon(p))
            _drop()
    return f


# ---- glob.glob with the standard library's order, directories listed in parallel and remembered ----
def _scan(dirname, dironly):
    """glob._listdir: names in os.scandir order (directories only when dironly); [] when unreadable."""
    path = dirname or os.curdir
    k = _canon(path)
    def go():
        out = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if not dironly or e.is_dir(): out.append(e.name)
                    except OSError: pass
        except OSError: pass
        return out
    return list(_memo(('scan', dironly), k, go)) if _mine(k) else go()


def _lexists(p):
    k = _canon(p)
    return _memo('lexists', k, lambda: False if _known_absent(k) else os.path.lexists(p)) if _mine(k) else os.path.lexists(p)


def _isdir(p):
    k = _canon(p)
    return _memo('isdir', k, lambda: False if _known_absent(k) else _real['isdir'](p)) if _mine(k) else _real['isdir'](p)


def _in_dir(dirname, basename, dironly, magic, bulk=False):
    if bulk and basename and not magic:  # many folders, one literal name: list each folder once (reused by later globs)
        return [basename] if basename in _scan(dirname, False) else []
    if magic:
        names = _scan(dirname, dironly)
        if not _glob._ishidden(basename): names = [x for x in names if not _glob._ishidden(x)]
        return fnmatch.filter(names, basename)
    if basename: return [basename] if _lexists(os.path.join(dirname, basename)) else []
    return [basename] if _isdir(dirname) else []


def _iglob(pathname, dironly):
    dirname, basename = os.path.split(pathname)
    if not _glob.has_magic(pathname):
        if basename: return [pathname] if _lexists(pathname) else []
        return [pathname] if _isdir(dirname) else []
    if not dirname: return _in_dir(dirname, basename, dironly, True)
    dirs = _iglob(dirname, True) if dirname != pathname and _glob.has_magic(dirname) else [dirname]
    magic = _glob.has_magic(basename)
    if len(dirs) > 1:
        global _pool
        if _pool is None: _pool = cf.ThreadPoolExecutor(32)
        found = list(_pool.map(lambda d: _in_dir(d, basename, dironly, magic, len(dirs) > 8), dirs))
    else: found = [_in_dir(d, basename, dironly, magic) for d in dirs]
    return [os.path.join(d, n) for d, names in zip(dirs, found) for n in names]


def _glob_glob(pathname, *, root_dir=None, dir_fd=None, recursive=False, include_hidden=False):
    p = pathname if isinstance(pathname, str) else None
    if p is None or root_dir is not None or dir_fd is not None or include_hidden or (recursive and '**' in p) \
            or not _mine(_canon(p.split('*')[0].split('?')[0].split('[')[0] or '.')):
        return _real['glob'](pathname, root_dir=root_dir, dir_fd=dir_fd, recursive=recursive, include_hidden=include_hidden)
    key = ('glob', p, os.getcwd() if not os.path.isabs(p) else '')
    with _lock:
        if key in _meta: return list(_meta[key])
    v = _iglob(p, False) if p else []
    with _lock: _meta[key] = list(v)
    _read_ahead(v)
    return v


def _read_ahead(paths, most=60):
    """A short glob of .json/.csv files is nearly always followed by reading them: fetch them 32 at a time now."""
    todo = [p for p in paths if p.endswith(('.json', '.csv'))]
    if not todo or len(todo) > most: return
    def get(p):
        k = _canon(p)
        with _lock:
            if k in _data or k in _writing: return
        try:
            with _real['open'](p, 'rb') as f: b = f.read()
        except OSError: return
        with _lock:
            if k not in _writing and k not in _data: _data[k] = b
    global _pool
    if _pool is None: _pool = cf.ThreadPoolExecutor(32)
    list(_pool.map(get, todo))


def install(root=None):
    """Turn the cache on for this process (idempotent). root: the folder to cache (default: the project folder)."""
    global _ROOT
    if _real: return
    _ROOT = _canon(root or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _real.update(open=builtins.open, listdir=os.listdir, glob=_glob.glob, exists=os.path.exists, isfile=os.path.isfile,
                 isdir=os.path.isdir, getmtime=os.path.getmtime, getsize=os.path.getsize)
    for n in ('makedirs', 'mkdir', 'rename', 'replace', 'remove', 'unlink', 'rmdir', 'utime'): _real[n] = getattr(os, n)
    builtins.open = io.open = _open
    os.path.exists, os.path.isfile, os.path.isdir = _wrap_bool('exists'), _wrap_bool('isfile'), _wrap_bool('isdir')
    os.path.getmtime, os.path.getsize = _wrap_val('getmtime'), _wrap_val('getsize')
    os.listdir = _listdir
    for n in ('makedirs', 'mkdir', 'rename', 'replace', 'remove', 'unlink', 'rmdir', 'utime'): setattr(os, n, _writer(n))
    _glob.glob = _glob_glob
