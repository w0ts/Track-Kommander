// tkw_py.cpp — TK's Python inside Traktor (Windows). python3XX.dll is loaded at run time from
// TK_PYTHONHOME (the interpreter TK's .venv was made from); sys.path gets the venv's
// site-packages and TK's src. Everything after "Python is up" is a script (pyexec), so changing
// what TK does inside Traktor needs no rebuild. The GIL is released after start-up and taken
// per call. Main thread only.
#include "tkw.h"

typedef int (*fn_ensure)(void);
typedef void (*fn_release)(int);
static fn_ensure p_ensure = nullptr;
static fn_release p_release = nullptr;
static int (*p_run)(const char*) = nullptr;
static bool g_ready = false;
static bool g_failed = false;  // a failed start is final: Python is half up, without the GIL

// Text for Python source as hex digits only (decoded there with bytes.fromhex): a path with
// quotes, backslashes or any letter cannot break or inject into the code.
static std::string hex(const std::string& s) {
  static const char* d = "0123456789abcdef";
  std::string out;
  for (unsigned char c : s) {
    out += d[c >> 4];
    out += d[c & 15];
  }
  return out;
}

bool tkw_py_init() {
  if (g_ready) return true;
  if (g_failed) return false;
  const std::string home = tkw_env("TK_PYTHONHOME");
  const std::string repo = tkw_env("TK_REPO");
  if (home.empty() || repo.empty()) {
    tklog("PY: TK_PYTHONHOME / TK_REPO not set — no Python");
    return false;
  }
  const std::string dll = home + "\\" + tkw_env("TK_PYTHON_DLL", "python313.dll");
  HMODULE h = LoadLibraryExA(dll.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
  if (!h) {
    tklog("PY: cannot load %s (err %lu)", dll.c_str(), GetLastError());
    return false;
  }
  auto init = (void (*)(int))GetProcAddress(h, "Py_InitializeEx");
  auto save = (void* (*)(void))GetProcAddress(h, "PyEval_SaveThread");
  p_run = (int (*)(const char*))GetProcAddress(h, "PyRun_SimpleString");
  p_ensure = (fn_ensure)GetProcAddress(h, "PyGILState_Ensure");
  p_release = (fn_release)GetProcAddress(h, "PyGILState_Release");
  if (!init || !save || !p_run || !p_ensure || !p_release) {
    tklog("PY: symbols missing in %s", dll.c_str());
    return false;
  }
  SetEnvironmentVariableA("PYTHONHOME", home.c_str());
  SetEnvironmentVariableA("PYTHONUTF8", "1");  // (the launcher sets these before Traktor starts,
                                               // with the bytecode cache folders)
  init(0);  // 0: leave Traktor's signal handlers alone
  // Paths reach Python through the environment, never pasted into source: a quote or a
  // non-ASCII letter in a path cannot break (or inject into) the code.
  const char* boot = "import os, sys\n"
                     "_r = os.environ['TK_REPO']\n"
                     "sys.path[:0] = [os.path.join(_r, '.venv', 'Lib', 'site-packages'), os.path.join(_r, 'src')]\n"
                     "sys.argv = ['traktor-tk']\n";
  if (p_run(boot) != 0) {
    tklog("PY: start-up code failed — Python not used for this Traktor session");
    save();
    g_failed = true;
    return false;
  }
  save();  // release the GIL: later calls (and ctypes callbacks) take it as needed
  g_ready = true;
  tklog("PY: Python up (%s)", dll.c_str());
  return true;
}

void tkw_pyexec(const std::string& path) {
  if (!tkw_py_init()) return;
  // os.environ is read once at Python's start, so the path goes in hex. A failing script's
  // traceback would go to Traktor's stderr (nowhere): it is written to our log.
  const std::string code = "import os, traceback as _tk_tb\n"
                     "_p = bytes.fromhex('" + hex(path) + "').decode('utf-8', 'replace')\n"
                     "try:\n"
                     "    exec(compile(open(_p, encoding='utf-8').read(), _p, 'exec'), {'__name__': '__tkembed__'})\n"
                     "except BaseException:\n"
                     "    _l = os.environ.get('TK_DYLIB_LOG')\n"
                     "    if _l:\n"
                     "        with open(_l, 'a', encoding='utf-8') as _f:\n"
                     "            _f.write('PY: traceback of ' + _p + '\\n' + _tk_tb.format_exc())\n"
                     "    else:\n"
                     "        _tk_tb.print_exc()\n";
  const int g = p_ensure();
  const int rc = p_run(code.c_str());
  p_release(g);
  tklog("PY: ran %s -> %d", path.c_str(), rc);
}
