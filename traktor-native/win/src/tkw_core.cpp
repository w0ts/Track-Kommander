// tkw_core.cpp — load checks, log, main-thread queue, crash-report block, gum hooks.
//
// Loaded into "Traktor Pro 4.exe" by tk_launch.exe (CreateRemoteThread + LoadLibraryW while
// Traktor's main thread is still suspended). DllMain only starts tkw_init on its own thread; the
// launcher waits for the "Local\TKReady-<pid>" event before it resumes Traktor, so the hooks
// (above all the crash-report block) are in place before Traktor's own code runs.
//
// Refuses to do anything when: the process is not Traktor Pro 4.exe, its version is not
// 4.5.1.21, or any function in addrs_win_4_5_1_21.h does not start with the expected bytes.
#include "tkw.h"
#include "tk_supported.h"

#include <share.h>
#include <stdarg.h>
#include <stdio.h>
#include <tlhelp32.h>

#include <atomic>
#include <deque>
#include <mutex>
#include <vector>

#include "frida-gum.h"

extern "C" IMAGE_DOS_HEADER __ImageBase;  // libtk.dll's own base (crash lines say libtk+0x...)

#pragma comment(lib, "version.lib")

volatile bool g_tkw_quitting = false;
void tkw_modules_init(GumInterceptor* it);  // tkw_hooks.cpp
static HMODULE g_self = nullptr;
static uintptr_t g_base = 0;
static bool g_active = false;
static std::mutex g_log_mu;
static std::string g_log_path;
static char g_crash_path[MAX_PATH];  // the log's path, copied once: the crash filter takes no locks

std::string tkw_env(const char* name, const char* fallback) {
  char buf[2048];
  DWORD n = GetEnvironmentVariableA(name, buf, sizeof(buf));
  return (n > 0 && n < sizeof(buf)) ? std::string(buf, n) : std::string(fallback);
}

void tklog(const char* fmt, ...) {
  char msg[2048];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(msg, sizeof(msg), fmt, ap);
  va_end(ap);
  SYSTEMTIME t;
  GetLocalTime(&t);
  std::lock_guard<std::mutex> lock(g_log_mu);
  // Opened once and kept: an open + close per line (with the virus scanner looking at every
  // close) cost the UI thread on every line, and libtk logs thousands (2026-11-25 lag hunt,
  // `bench` measures both ways). Flushed per line: readers see every line at once.
  static FILE* f = nullptr;
  if (!f) f = _fsopen(g_log_path.c_str(), "a", _SH_DENYNO);  // others may read the log meanwhile
  if (!f) return;
  if (fprintf(f, "%02d:%02d:%02d.%03d [%lu] %s\n", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds,
              GetCurrentThreadId(), msg) < 0 ||
      fflush(f) != 0) {
    fclose(f);  // a write failed: open again on the next line
    f = nullptr;
  }
}

// `bench`: what the UI thread pays for libtk's basics, measured on this PC (2026-11-25 lag hunt):
// 10,000 pointer checks, 200 log lines through the kept handle, and 200 lines written the old way
// (open + close per line) to a scratch file next to the log, which is removed after.
std::string tkw_bench() {
  LARGE_INTEGER fq, a, b;
  QueryPerformanceFrequency(&fq);
  auto us = [&](LARGE_INTEGER x, LARGE_INTEGER y) { return (double)(y.QuadPart - x.QuadPart) * 1e6 / fq.QuadPart; };
  int probe = 0;
  QueryPerformanceCounter(&a);
  int ok = 0;
  for (int i = 0; i < 10000; i++) ok += tkw_readable(&probe, 8);
  QueryPerformanceCounter(&b);
  const double readable_us = us(a, b) / 10000;
  QueryPerformanceCounter(&a);
  for (int i = 0; i < 200; i++) tklog("BENCH line %d", i);
  QueryPerformanceCounter(&b);
  const double kept_us = us(a, b) / 200;
  const std::string scratch = g_log_path + ".bench";
  QueryPerformanceCounter(&a);
  for (int i = 0; i < 200; i++) {
    FILE* g = _fsopen(scratch.c_str(), "a", _SH_DENYNO);
    if (!g) break;
    fprintf(g, "BENCH line %d\n", i);
    fclose(g);
  }
  QueryPerformanceCounter(&b);
  const double reopen_us = us(a, b) / 200;
  DeleteFileA(scratch.c_str());
  char out[200];
  _snprintf_s(out, sizeof(out), _TRUNCATE,
              "readable %.2f us (%d ok), log line kept %.1f us, log line open+close %.1f us", readable_us, ok,
              kept_us, reopen_us);
  tklog("BENCH: %s", out);
  return out;
}

void* W(int func) { return (void*)(g_base + kTkwFuncs[func].rva); }
void* WF(int entry) { return (void*)(g_base + kTkwFullFuncs[entry].rva); }
void* tkw_rva(uint32_t rva) { return (void*)(g_base + rva); }
uintptr_t tkw_base() { return g_base; }
void* tkw_new(size_t n) { return ((void* (*)(size_t))W(W_operator_new))(n); }

// First live run (2026-11-25): gum_memory_is_readable said yes to an address whose read then
// faulted, inside tkw_rtti_name on the UI thread, and Traktor died. The check is ours now:
// every page of the range committed, readable, not a guard page (VirtualQuery); then each page
// is touched under SEH, so a block freed between the check and the read answers false too.
static const DWORD kReadableProt = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ |
                                   PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;

static bool pages_committed_readable(uintptr_t a, uintptr_t last) {
  while (a <= last) {
    MEMORY_BASIC_INFORMATION mbi;
    if (VirtualQuery((LPCVOID)a, &mbi, sizeof(mbi)) != sizeof(mbi)) return false;
    if (mbi.State != MEM_COMMIT || (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)) || !(mbi.Protect & kReadableProt))
      return false;
    const uintptr_t next = (uintptr_t)mbi.BaseAddress + mbi.RegionSize;
    if (next <= a) return false;
    a = next;
  }
  return true;
}

// No C++ objects here: __try needs a frame without destructors.
static bool pages_touch(uintptr_t a, uintptr_t last) {
  __try {
    for (uintptr_t q = a; q <= last; q = (q & ~(uintptr_t)0xfff) + 0x1000) (void)*(const volatile char*)q;
    return true;
  } __except (GetExceptionCode() == EXCEPTION_ACCESS_VIOLATION ? EXCEPTION_EXECUTE_HANDLER
                                                                : EXCEPTION_CONTINUE_SEARCH) {
    return false;
  }
}

bool tkw_readable(const void* p, size_t n) {
  const uintptr_t a = (uintptr_t)p;
  if (a <= 0x10000 || n > ((size_t)1 << 30)) return false;
  const uintptr_t last = a + (n ? n : 1) - 1;
  if (last < a || last >= 0x00007FFFFFFF0000ull) return false;  // wraps, or past user space
  return pages_committed_readable(a, last) && pages_touch(a, last);
}

// MSVC RTTI: vtable[-1] -> CompleteObjectLocator {sig, off, cdOff, TypeDescriptor rva, ...};
// TypeDescriptor {vfptr, spare, char name[]}. All RVAs are relative to Traktor's base.
std::string tkw_rtti_name(const void* obj) {
  if (!tkw_readable(obj, 8)) return "";
  const uintptr_t vt = *(const uintptr_t*)obj;
  if (vt < g_base || vt >= g_base + TKW_IMAGE_SIZE || !tkw_readable((void*)(vt - 8), 8)) return "";
  const uint32_t* col = *(const uint32_t* const*)(vt - 8);
  if ((uintptr_t)col < g_base || (uintptr_t)col >= g_base + TKW_IMAGE_SIZE || !tkw_readable(col, 24)) return "";
  if (col[0] != 1) return "";
  const char* td = (const char*)(g_base + col[3]);
  // Names can be long (templates): read in steps, each step checked, at most 512 bytes.
  const char* name = td + 16;
  for (size_t span = 64; span <= 512; span *= 2) {
    if (!tkw_readable(name, span)) return "";
    const size_t len = strnlen(name, span);
    if (len < span) return std::string(name, len);
  }
  return "";
}

// --- main-thread queue: a WH_GETMESSAGE hook on Traktor's UI thread drains it -------------------
static std::mutex g_q_mu;
static std::deque<std::function<void()>> g_q;
static std::atomic<DWORD> g_main_tid{0};
static HHOOK g_msg_hook = nullptr;
static const size_t kMaxQueued = 256;  // before the UI thread is known, the queue only grows

bool tkw_is_main_thread() {
  const DWORD tid = g_main_tid.load();
  return tid && GetCurrentThreadId() == tid;
}

static void drain_queue() {
  for (int guard = 0; guard < 64; guard++) {
    std::function<void()> fn;
    {
      std::lock_guard<std::mutex> lock(g_q_mu);
      if (g_q.empty()) return;
      fn = std::move(g_q.front());
      g_q.pop_front();
    }
    try {
      fn();
    } catch (...) {  // never unwind through USER32's or Traktor's frames
      tklog("MAIN: a queued job threw — skipped");
    }
  }
}

static void (*g_msg_observer)(const MSG*) = nullptr;  // tkw_window.cpp: mouse-downs etc.
void tkw_set_message_observer(void (*fn)(const MSG*)) { g_msg_observer = fn; }

static LRESULT CALLBACK msg_hook(int code, WPARAM wp, LPARAM lp) {
  if (code >= 0 && !g_tkw_quitting) {
    // Each message is seen once: when it is taken off the queue (PM_REMOVE), not when peeked —
    // and queued jobs run only then too, never inside a PM_NOREMOVE peek of Traktor's.
    if (wp == PM_REMOVE) {
      if (g_msg_observer && lp) {
        try {
          g_msg_observer((const MSG*)lp);
        } catch (...) {
          tklog("MAIN: message observer threw — ignored");
        }
      }
      drain_queue();
    }
  }
  return CallNextHookEx(g_msg_hook, code, wp, lp);
}

void tkw_on_main(std::function<void()> fn) {
  {
    std::lock_guard<std::mutex> lock(g_q_mu);
    if (g_q.size() >= kMaxQueued) {
      tklog("MAIN: queue full (%zu), job dropped (UI thread not adopted yet?)", g_q.size());
      return;
    }
    g_q.push_back(std::move(fn));
  }
  const DWORD tid = g_main_tid.load();
  if (tid) PostThreadMessageW(tid, WM_NULL, 0, 0);  // wake its message loop
}

// Called by UI hooks (BrowserForm paint/resize). Only a thread with a message queue and windows
// (IsGUIThread) is adopted as the UI thread: a helper thread that paints or resizes is ignored.
void tkw_adopt_main_thread() {
  if (g_msg_hook) return;
  if (!IsGUIThread(FALSE)) {
    static int refused = 0;
    if (refused++ < 3) tklog("MAIN: thread %lu fired a UI hook but is not a GUI thread — not adopted", GetCurrentThreadId());
    return;
  }
  const DWORD tid = GetCurrentThreadId();
  g_msg_hook = SetWindowsHookExW(WH_GETMESSAGE, msg_hook, nullptr, tid);
  if (!g_msg_hook) g_msg_hook = SetWindowsHookExW(WH_GETMESSAGE, msg_hook, g_self, tid);
  g_main_tid.store(tid);
  tklog("MAIN: UI thread %lu, message hook %s (err %lu)", tid, g_msg_hook ? "on" : "FAILED",
        g_msg_hook ? 0ul : GetLastError());
  PostThreadMessageW(tid, WM_NULL, 0, 0);
}

// --- no crash reports from a patched Traktor: crashpad_handler.exe is never started ----------
typedef BOOL(WINAPI* CreateProcessInternalW_t)(HANDLE, LPCWSTR, LPWSTR, LPSECURITY_ATTRIBUTES,
                                               LPSECURITY_ATTRIBUTES, BOOL, DWORD, LPVOID, LPCWSTR,
                                               LPSTARTUPINFOW, LPPROCESS_INFORMATION, PHANDLE);
static CreateProcessInternalW_t g_orig_cpi = nullptr;

static bool mentions_crashpad(LPCWSTR s) { return s && wcsstr(s, L"crashpad") != nullptr; }

static BOOL WINAPI tk_CreateProcessInternalW(HANDLE tok, LPCWSTR app, LPWSTR cmd, LPSECURITY_ATTRIBUTES pa,
                                             LPSECURITY_ATTRIBUTES ta, BOOL inh, DWORD flags, LPVOID env,
                                             LPCWSTR cwd, LPSTARTUPINFOW si, LPPROCESS_INFORMATION pi,
                                             PHANDLE newtok) {
  if (mentions_crashpad(app) || mentions_crashpad(cmd)) {
    tklog("CRASHPAD: refused to start the crash reporter (no crash reports from a patched Traktor)");
    SetLastError(ERROR_FILE_NOT_FOUND);
    return FALSE;
  }
  return g_orig_cpi(tok, app, cmd, pa, ta, inh, flags, env, cwd, si, pi, newtok);
}

// --- a crash stays on this PC: logged here, then the process ends (no WER report, no dialog) ---
// Traktor's crash client would install its own filter after us; that call is kept from taking
// effect, so ours stays the last chance handler.
// No lock, no heap, no CRT stream: the crash may have happened inside tklog or the allocator.
// " Traktor+0x..." / " libtk+0x..." / " dll@base+0x...": RtlPcToFileHeader takes no loader lock
// (the crash may have happened while another thread held it). libtk offsets resolve with
// build\libtk.map.
static int crash_where(char* out, size_t cap, uintptr_t pc) {
  PVOID base = nullptr;
  RtlPcToFileHeader((PVOID)pc, &base);
  const uintptr_t b = (uintptr_t)base;
  if (!b) return _snprintf_s(out, cap, _TRUNCATE, " %p", (void*)pc);
  if (b == g_base) return _snprintf_s(out, cap, _TRUNCATE, " Traktor+0x%llx", (unsigned long long)(pc - b));
  if (base == &__ImageBase) return _snprintf_s(out, cap, _TRUNCATE, " libtk+0x%llx", (unsigned long long)(pc - b));
  return _snprintf_s(out, cap, _TRUNCATE, " dll@%p+0x%llx", base, (unsigned long long)(pc - b));
}

// Up to 16 frames by the x64 unwind tables; a frame without one (a hook trampoline) is read
// as a leaf, which may end the walk early. Never throws: a bad stack only shortens the line.
static size_t crash_stack(char* out, size_t cap, const CONTEXT* ctx) {
  size_t len = 0;
  __try {
    CONTEXT c = *ctx;
    for (int i = 0; i < 16 && c.Rip && len + 48 < cap; i++) {
      const int k = crash_where(out + len, cap - len, (uintptr_t)c.Rip);
      if (k <= 0) break;
      len += (size_t)k;
      DWORD64 ib = 0;
      PRUNTIME_FUNCTION fe = RtlLookupFunctionEntry(c.Rip, &ib, nullptr);
      if (!fe) {
        c.Rip = *(const DWORD64*)c.Rsp;
        c.Rsp += 8;
        continue;
      }
      PVOID hd = nullptr;
      DWORD64 ef = 0;
      RtlVirtualUnwind(UNW_FLAG_NHANDLER, ib, c.Rip, fe, &c, &hd, &ef, nullptr);
    }
  } __except (EXCEPTION_EXECUTE_HANDLER) {
  }
  return len;
}

static void crash_append(const char* s, int n) {
  if (n <= 0) return;
  HANDLE h = CreateFileA(g_crash_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                         OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (h == INVALID_HANDLE_VALUE) return;
  DWORD w = 0;
  WriteFile(h, s, (DWORD)n, &w, nullptr);
  CloseHandle(h);
}

static LONG WINAPI tk_crash_filter(EXCEPTION_POINTERS* ep) {
  const EXCEPTION_RECORD* r = ep ? ep->ExceptionRecord : nullptr;
  const uintptr_t at = r ? (uintptr_t)r->ExceptionAddress : 0;
  char where[96] = "";
  crash_where(where, sizeof(where), at);
  char line[1024];
  int n = _snprintf_s(line, sizeof(line), _TRUNCATE,
                      "CRASH: code 0x%08lx at %p (%s), thread %lu: Traktor ends here, no report is sent\r\n",
                      r ? r->ExceptionCode : 0ul, (void*)at, where + 1, GetCurrentThreadId());
  crash_append(line, n);
  if (r && r->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && r->NumberParameters >= 2) {
    const ULONG_PTR kind = r->ExceptionInformation[0];  // 0 read, 1 write, 8 execute
    n = _snprintf_s(line, sizeof(line), _TRUNCATE, "CRASH: %s of address %p\r\n",
                    kind == 0 ? "read" : kind == 1 ? "write" : "execute", (void*)r->ExceptionInformation[1]);
    crash_append(line, n);
  }
  if (ep && ep->ContextRecord) {
    const int head = _snprintf_s(line, sizeof(line), _TRUNCATE, "CRASH: stack");
    size_t len = head > 0 ? (size_t)head : 0;
    len += crash_stack(line + len, sizeof(line) - len - 3, ep->ContextRecord);
    line[len++] = '\r';
    line[len++] = '\n';
    crash_append(line, (int)len);
  }
  TerminateProcess(GetCurrentProcess(), 0xDEAD);
  return EXCEPTION_EXECUTE_HANDLER;
}

typedef LPTOP_LEVEL_EXCEPTION_FILTER(WINAPI* SetUEF_t)(LPTOP_LEVEL_EXCEPTION_FILTER);
static SetUEF_t g_orig_setuef = nullptr;
static LPTOP_LEVEL_EXCEPTION_FILTER WINAPI tk_SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER f) {
  static int logged = 0;
  if (logged++ < 3) tklog("CRASH: Traktor's own crash filter %p not installed (TK's stays)", (void*)f);
  return nullptr;
}

static void keep_crashes_local(GumInterceptor* it) {
  SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
  SetUnhandledExceptionFilter(tk_crash_filter);
  HMODULE kb = GetModuleHandleW(L"kernelbase.dll");
  void* fn = kb ? (void*)GetProcAddress(kb, "SetUnhandledExceptionFilter") : nullptr;
  if (fn) tklog("CRASH: SetUnhandledExceptionFilter hook -> %d",
                (int)gum_interceptor_replace(it, fn, (gpointer)tk_SetUnhandledExceptionFilter,
                                             (gpointer*)&g_orig_setuef, nullptr));
}

// --- checks ------------------------------------------------------------------------------------
static bool is_traktor_4_5_1_21() {
  wchar_t exe[MAX_PATH];
  GetModuleFileNameW(nullptr, exe, MAX_PATH);
  const wchar_t* name = wcsrchr(exe, L'\\');
  if (!name || _wcsicmp(name + 1, L"Traktor Pro 4.exe") != 0) return false;
  DWORD h = 0, sz = GetFileVersionInfoSizeW(exe, &h);
  std::vector<char> buf(sz ? sz : 1);
  VS_FIXEDFILEINFO* fi = nullptr;
  UINT len = 0;
  if (!sz || !GetFileVersionInfoW(exe, 0, sz, buf.data()) ||
      !VerQueryValueW(buf.data(), L"\\", (void**)&fi, &len) || !fi)
    return false;
  const bool ok = fi->dwFileVersionMS == ((4u << 16) | 5u) && fi->dwFileVersionLS == ((1u << 16) | 21u);
  if (!ok) tklog("CHECK: Traktor version %u.%u.%u.%u is not %s", HIWORD(fi->dwFileVersionMS),
                 LOWORD(fi->dwFileVersionMS), HIWORD(fi->dwFileVersionLS), LOWORD(fi->dwFileVersionLS),
                 TKW_TRAKTOR_VERSION);
  if (!ok) return false;
  // tk_launch.exe hashed the whole executable (SHA-256) before starting it and passes the hash:
  // a Traktor started any other way, or another build with the same version, is never hooked.
  const std::string sha = tkw_env("TK_TRAKTOR_SHA256");
  const TkSupportedBuild* b = tk_supported_build(sha.c_str());
  if (!b) {
    tklog("CHECK: no launcher-verified SHA-256 for this Traktor ('%s') — libtk stays inactive", sha.c_str());
    return false;
  }
  tklog("CHECK: %s, SHA-256 verified by the launcher", b->name);
  return true;
}

static bool head_ok(const char* name, const void* p, const uint8_t* head) {
  if (tkw_readable(p, 12) && memcmp(p, head, 12) == 0) return true;
  tklog("CHECK: %s @%p does not start with the expected bytes", name, p);
  return false;
}

// Every code address of both tables must start with the bytes of the mapped build.
static bool code_matches() {
  int bad = 0;
  for (int i = 0; i < TKW_NFUNCS; i++)
    if (!head_ok(kTkwFuncs[i].name, W(i), kTkwFuncs[i].head)) bad++;
  for (int i = 0; i < TKW_NFULLFUNCS; i++)
    if (kTkwFullFuncs[i].code && !head_ok(kTkwFullFuncs[i].name, WF(i), kTkwFullFuncs[i].head)) bad++;
  tklog("CHECK: %d + %d mapped functions, %d differ", TKW_NFUNCS, TKW_NFULLFUNCS, bad);
  return bad == 0;
}

static void block_crashpad(GumInterceptor* it) {
  HMODULE kb = GetModuleHandleW(L"kernelbase.dll");
  void* cpi = kb ? (void*)GetProcAddress(kb, "CreateProcessInternalW") : nullptr;
  if (!cpi) { tklog("CRASHPAD: CreateProcessInternalW not found — crash reporter NOT blocked"); return; }
  GumReplaceReturn r = gum_interceptor_replace(it, cpi, (gpointer)tk_CreateProcessInternalW,
                                               (gpointer*)&g_orig_cpi, nullptr);
  tklog("CRASHPAD: CreateProcessInternalW hook -> %d", (int)r);
}

static DWORD WINAPI tkw_init(LPVOID) {
  const DWORD pid = GetCurrentProcessId();
  wchar_t evname[64];
  swprintf(evname, 64, L"Local\\TKReady-%lu", pid);
  HANDLE ready = CreateEventW(nullptr, TRUE, FALSE, evname);
  g_base = (uintptr_t)GetModuleHandleW(nullptr);
  tklog("=== libtk.dll in pid %lu, Traktor base %p ===", pid, (void*)g_base);
  if (!is_traktor_4_5_1_21()) {
    tklog("CHECK: not Traktor Pro 4 %s — libtk stays inactive", TKW_TRAKTOR_VERSION);
  } else {
    gum_init_embedded();
    GumInterceptor* it = gum_interceptor_obtain();
    gum_interceptor_begin_transaction(it);
    block_crashpad(it);
    keep_crashes_local(it);
    if (code_matches()) {
      tkw_modules_init(it);  // each module registers its hooks and commands
      g_active = true;
    } else {
      tklog("CHECK: code differs from the mapped build — no Traktor hooks (crash block stays)");
    }
    gum_interceptor_end_transaction(it);
    if (g_active) {
      const std::string port = tkw_env("TK_CMD_PORT", "8767");
      tkw_cmd_start(atoi(port.c_str()) == 8766 ? 8767 : atoi(port.c_str()));  // 8766 is reserved
    }
  }
  if (ready) SetEvent(ready);  // the launcher resumes Traktor now (it keeps its own handle)
  return 0;
}

BOOL APIENTRY DllMain(HMODULE mod, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    g_self = mod;
    DisableThreadLibraryCalls(mod);
    g_log_path = tkw_env("TK_DYLIB_LOG");
    if (g_log_path.empty()) g_log_path = tkw_env("LOCALAPPDATA", "C:") + "\\Track Kommander\\logs\\libtk.log";
    strncpy_s(g_crash_path, g_log_path.c_str(), _TRUNCATE);
    HANDLE t = CreateThread(nullptr, 0, tkw_init, nullptr, 0, nullptr);
    if (t) CloseHandle(t);
  } else if (reason == DLL_PROCESS_DETACH) {
    g_tkw_quitting = true;
  }
  return TRUE;
}
