// tkw.h — libtk.dll, Track Kommander inside Traktor Pro 4 on Windows.
//
// The framework every module builds on. A module is one tkw_<name>.cpp with an init function
// listed in tkw_modules.h; init runs once, inside the hook transaction, after libtk has checked
// that this is Traktor 4.5.1.21 and that every mapped function starts with its expected bytes.
// From init a module registers its hooks (tkw_hook / tkw_replace) and its command verbs
// (tkw_command). Its C exports (TKW_EXPORT) are what TK's Python calls with ctypes: they keep
// the Mac libtk's names and signatures, so TK's Python is the same on both systems.
//
// Addresses: W(W_x) = stage-1 table (addrs_win_4_5_1_21.h); WF(WF_A_x) = full map
// (addrs_win_full_4_5_1_21.h, generated from win/map/traktor_4_5_1_21_win.json — read an
// entry's "evidence"/"replacement" there before using it). Struct offsets: WOFF_* in both headers.
// Windows rules (MSVC): see win/README.md, "Windows differences libtk must follow".
#pragma once
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdint.h>

#include <functional>
#include <string>
#include <vector>

#include "addrs_win_4_5_1_21.h"
#include "addrs_win_full_4_5_1_21.h"

#define TKW_EXPORT extern "C" __declspec(dllexport)

typedef struct _GumInvocationContext GumInvocationContext;

// ---- core (tkw_core.cpp) --------------------------------------------------------------------
void tklog(const char* fmt, ...);
std::string tkw_bench();  // the `bench` verb: pointer check and log line costs, measured
uintptr_t tkw_base();                       // Traktor's module base
void* W(int func);                          // stage-1 table entry, e.g. W(W_label_ctor)
void* WF(int entry);                        // full-map entry, e.g. WF(WF_A_bf_treeSelect)
void* tkw_rva(uint32_t rva);                // any other RVA (prefer WF: those are checked)
bool tkw_readable(const void* p, size_t n);
std::string tkw_rtti_name(const void* obj);  // ".?AVBrowserForm@@", "" when not a C++ object
std::string tkw_env(const char* name, const char* fallback = "");
void* tkw_new(size_t n);                    // Traktor's operator new: for objects Traktor deletes
extern volatile bool g_tkw_quitting;

// Main (UI) thread. Every call into Traktor's C++ happens there, as on the Mac. The UI thread
// is learned from the first UI hook that fires on a GUI thread: such hooks call
// tkw_adopt_main_thread() first (the band's BrowserForm hooks do).
void tkw_adopt_main_thread();
void tkw_set_message_observer(void (*fn)(const MSG*));  // every message the UI thread takes
void tkw_on_main(std::function<void()> fn);  // queued, run in order from the message loop
bool tkw_is_main_thread();
void tkw_after_ms(unsigned ms, std::function<void()> fn);  // on the main thread, ms later

// Calling Traktor: MSVC x64 (this/args in rcx, rdx, r8, r9; floats xmm0-3; class results via
// a hidden pointer: rdx for members, rcx for free functions).
template <typename R, typename... A>
inline R tkw_call(void* fn, A... a) {
  return ((R(*)(A...))fn)(a...);
}
inline void* tkw_vfn(const void* obj, unsigned byte_off) {  // virtual function at vtable+off
  return (*(void* const* const*)obj)[byte_off / sizeof(void*)];
}

// MSVC std::string (32 bytes: buf[16] | ptr, size @0x10, capacity @0x18). libtk is built with
// the same VS2022 STL and the same UCRT heap as Traktor, so a real std::string can be passed to
// Traktor by reference; reading one out of Traktor's memory goes through tkw_read_string.
bool tkw_read_string(const void* s, std::string* out);

// ---- hooks (call only from a module's init) -------------------------------------------------
typedef void (*TkwHookFn)(GumInvocationContext* ic);
bool tkw_hook(void* target, const char* name, TkwHookFn on_enter, TkwHookFn on_leave);
bool tkw_replace(void* target, const char* name, void* replacement, void** original);
void* tkw_arg(GumInvocationContext* ic, unsigned n);  // n-th integer/pointer argument; in on_leave
                                                      // the value it had at entry (n < 4)
void* tkw_retval(GumInvocationContext* ic);
void tkw_set_retval(GumInvocationContext* ic, void* v);

// ---- commands: one line "verb args" on 127.0.0.1:8767; reply "OK"/"ERR why"/a value -----------
// with_body: the verb's payload is the following lines up to EOF (Mac "list", "cards").
// Handlers run on the main thread.
typedef std::function<std::string(const std::string& args, const std::vector<std::string>& body)> TkwCmd;
void tkw_command(const char* verb, TkwCmd fn, bool with_body = false);
void tkw_cmd_start(int port);

// ---- to TK's Python (its local HTTP server, TK_SHOW_PORT) ----------------------------------
void tkw_post_json(const char* path, const char* body);
std::string tkw_json_escape(const std::string& s);

// ---- py (tkw_py.cpp) ---------------------------------------------------------------------------
bool tkw_py_init();
void tkw_pyexec(const std::string& path);  // main thread

// ---- shared UI state (owned by tkw_band.cpp; other modules read it) -----------------------------
void* tkw_browserform();             // the live BrowserForm, or null
bool tkw_band_visible();             // TK's view is selected in Traktor's tree
void tkw_band_set_visible(bool on);  // main thread
void tkw_layout_changed();           // tells TK's Python the list area moved
