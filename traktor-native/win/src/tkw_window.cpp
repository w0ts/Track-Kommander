// tkw_window.cpp — Traktor's main window for TK's Python (Windows only; the Mac uses AppKit there):
//   tk_main_window()            Traktor's main window handle (HWND), for QWindow.fromWinId;
//   tk_set_quit_callback(cb)    cb() once when Traktor is closing (WM_CLOSE / WM_QUERYENDSESSION
//                               of its main window), before anything is torn down: TK closes the
//                               normal way (webhook, workers, database);
//   tk_set_mousedown_callback(cb)  cb(x, y) (screen coordinates) on every mouse-down taken by
//                               Traktor's UI thread: TK closes its popups on a click outside them.
// All callbacks run on the main thread.
#include "tkw.h"  // windows.h first: commctrl.h needs it

#include <commctrl.h>

#pragma comment(lib, "comctl32.lib")

namespace {

HWND g_main = nullptr;
bool g_subclassed = false, g_quit_sent = false;
void (*g_quit_cb)() = nullptr;
void (*g_down_cb)(int, int) = nullptr;
const UINT_PTR kSubclassId = 0x544b;  // "TK"

struct Find {
  DWORD pid;
  HWND best;
  long long area;
};

BOOL CALLBACK find_main(HWND w, LPARAM lp) {
  Find* f = (Find*)lp;
  DWORD pid = 0;
  GetWindowThreadProcessId(w, &pid);
  if (pid != f->pid || !IsWindowVisible(w) || GetWindow(w, GW_OWNER)) return TRUE;
  RECT r;
  if (!GetWindowRect(w, &r)) return TRUE;
  const long long area = (long long)(r.right - r.left) * (r.bottom - r.top);
  if (area > f->area) {
    f->area = area;
    f->best = w;
  }
  return TRUE;
}

// The largest visible, unowned top-level window of this process: Traktor's main window (TK's
// own Qt windows are children of it, or hidden).
HWND locate_main() {
  Find f{GetCurrentProcessId(), nullptr, 0};
  EnumWindows(find_main, (LPARAM)&f);
  return f.best;
}

void send_quit(const char* why) {
  if (g_quit_sent || !g_quit_cb) return;
  g_quit_sent = true;
  tklog("WINDOW: Traktor is closing (%s): telling Track Kommander", why);
  g_quit_cb();
}

LRESULT CALLBACK subclass_proc(HWND w, UINT msg, WPARAM wp, LPARAM lp, UINT_PTR, DWORD_PTR) {
  try {
    if (msg == WM_CLOSE) send_quit("WM_CLOSE");
    else if (msg == WM_QUERYENDSESSION) send_quit("end of session");
    else if (msg == WM_NCDESTROY) {
      RemoveWindowSubclass(w, subclass_proc, kSubclassId);
      g_subclassed = false;
      g_main = nullptr;
    }
  } catch (...) {
    tklog("WINDOW: exception in the main window hook — ignored");
  }
  return DefSubclassProc(w, msg, wp, lp);
}

// Main thread. Finds the main window (it appears after start-up) and watches it.
void attach() {
  if (g_subclassed && g_main && IsWindow(g_main)) return;
  g_main = locate_main();
  if (!g_main) return;
  if (GetWindowThreadProcessId(g_main, nullptr) != GetCurrentThreadId()) {
    tklog("WINDOW: main window %p belongs to another thread — not watched", (void*)g_main);
    return;
  }
  g_subclassed = SetWindowSubclass(g_main, subclass_proc, kSubclassId, 0) != FALSE;
  tklog("WINDOW: main window %p, quit watch %s", (void*)g_main, g_subclassed ? "on" : "FAILED");
}

void on_message(const MSG* m) {
  switch (m->message) {
    case WM_LBUTTONDOWN:
    case WM_RBUTTONDOWN:
    case WM_MBUTTONDOWN:
    case WM_NCLBUTTONDOWN:
    case WM_NCRBUTTONDOWN:
      if (g_down_cb) g_down_cb(m->pt.x, m->pt.y);
      break;
    default:
      break;
  }
  static ULONGLONG next_try = 0;  // until the main window exists: look twice a second at most
  if (!g_subclassed && GetTickCount64() >= next_try) {
    next_try = GetTickCount64() + 500;
    attach();
  }
}

}  // namespace

TKW_EXPORT void* tk_main_window(void) {
  if (tkw_is_main_thread() && (!g_main || !IsWindow(g_main))) attach();
  return g_main;
}

TKW_EXPORT void tk_set_quit_callback(void (*cb)(void)) { g_quit_cb = cb; }
TKW_EXPORT void tk_set_mousedown_callback(void (*cb)(int, int)) { g_down_cb = cb; }

void tkw_mod_window_init() { tkw_set_message_observer(on_message); }
