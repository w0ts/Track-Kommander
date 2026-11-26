// tkw_hooks.cpp — hook registry, module start-up and small shared helpers (strings, JSON, timers).
//
// All attach-hooks share one gum listener. gum attaches one listener to a function only once, so
// each TARGET gets one attachment whose function data is the index of its group; a group calls
// every hook registered on that target, in registration order (several modules hook e.g.
// Control::onEvent). No exception may leave a hook: it would unwind through Traktor's own frames.
// Modules register hooks only from their init (inside the transaction).
#include <chrono>
#include <condition_variable>
#include <map>
#include <mutex>
#include <thread>
#include <vector>

#include "frida-gum.h"
#include "tkw.h"
#include "tkw_modules.h"

namespace {

struct Hook {
  const char* name;
  TkwHookFn enter;
  TkwHookFn leave;
};

struct Group {
  void* target;
  std::vector<Hook> hooks;
};
std::vector<Group> g_groups;  // filled during init only, read afterwards: no lock needed
GumInterceptor* g_it = nullptr;
GumInvocationListener* g_listener = nullptr;

// The first four arguments as they were at entry: in on_leave the argument registers are
// clobbered (rcx is not `this` any more), so tkw_arg reads these there.
struct SavedArgs {
  void* a[4];
};

void dispatch(GumInvocationContext* ic, bool enter) {
  const size_t id = (size_t)gum_invocation_context_get_listener_function_data(ic);
  if (id >= g_groups.size()) return;
  if (enter) {
    SavedArgs* saved = GUM_IC_GET_INVOCATION_DATA(ic, SavedArgs);
    for (unsigned i = 0; i < 4; i++) saved->a[i] = gum_invocation_context_get_nth_argument(ic, i);
  }
  for (const Hook& h : g_groups[id].hooks) {
    TkwHookFn fn = enter ? h.enter : h.leave;
    if (!fn) continue;
    try {
      fn(ic);
    } catch (...) {
      tklog("HOOK %s: exception in %s — ignored", h.name, enter ? "on_enter" : "on_leave");
    }
  }
}

void on_enter(GumInvocationContext* ic, gpointer) { dispatch(ic, true); }
void on_leave(GumInvocationContext* ic, gpointer) { dispatch(ic, false); }

}  // namespace

bool tkw_hook(void* target, const char* name, TkwHookFn enter, TkwHookFn leave) {
  if (!g_it || !g_listener || !target) return false;
  for (Group& g : g_groups) {
    if (g.target == target) {  // already attached: one more hook in its group
      g.hooks.push_back({name, enter, leave});
      tklog("HOOK %s @%p -> joined (%zu on this function)", name, target, g.hooks.size());
      return true;
    }
  }
  GumAttachOptions o;
  memset(&o, 0, sizeof(o));
  o.listener_function_data = (gpointer)g_groups.size();
  const GumAttachReturn r = gum_interceptor_attach(g_it, target, g_listener, &o);
  tklog("HOOK %s @%p -> %d", name, target, (int)r);
  if (r != GUM_ATTACH_OK) return false;
  g_groups.push_back({target, {{name, enter, leave}}});
  return true;
}

bool tkw_replace(void* target, const char* name, void* replacement, void** original) {
  if (!g_it || !target) return false;
  const GumReplaceReturn r = gum_interceptor_replace(g_it, target, replacement, original, nullptr);
  tklog("REPLACE %s @%p -> %d", name, target, (int)r);
  return r == GUM_REPLACE_OK;
}

void* tkw_arg(GumInvocationContext* ic, unsigned n) {
  if (n < 4 && gum_invocation_context_get_point_cut(ic) == GUM_POINT_LEAVE)
    return GUM_IC_GET_INVOCATION_DATA(ic, SavedArgs)->a[n];  // the entry value, not a clobbered register
  return gum_invocation_context_get_nth_argument(ic, n);
}
void* tkw_retval(GumInvocationContext* ic) { return gum_invocation_context_get_return_value(ic); }
void tkw_set_retval(GumInvocationContext* ic, void* v) { gum_invocation_context_replace_return_value(ic, v); }

void tkw_modules_init(GumInterceptor* it) {
  g_it = it;
  g_listener = gum_make_call_listener(on_enter, on_leave, nullptr, nullptr);
  for (const TkwModule& m : kTkwModules) {
    try {
      m.init();
      tklog("MODULE %s: started", m.name);
    } catch (...) {
      tklog("MODULE %s: init threw — module off", m.name);
    }
  }
}

// --- small helpers --------------------------------------------------------------------------------
bool tkw_read_string(const void* s, std::string* out) {
  if (!tkw_readable(s, 32)) return false;
  const size_t size = *(const size_t*)((const char*)s + 0x10);
  const size_t cap = *(const size_t*)((const char*)s + 0x18);
  if (cap < size || size > (1u << 24)) return false;
  const char* data = cap >= 16 ? *(const char* const*)s : (const char*)s;
  if (size && !tkw_readable(data, size)) return false;
  out->assign(data, size);
  return true;
}

std::string tkw_json_escape(const std::string& in) {
  std::string out;
  out.reserve(in.size() + 8);
  for (unsigned char c : in) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char u[8];
          snprintf(u, sizeof(u), "\\u%04x", c);
          out += u;
        } else {
          out += (char)c;
        }
    }
  }
  return out;
}

namespace {
// One timer thread, one queue ordered by due time: a delay never races a timer handle, and the
// job always runs on the main thread (tkw_on_main) once due.
std::mutex g_timer_mu;
std::condition_variable g_timer_cv;
std::multimap<ULONGLONG, std::function<void()>> g_timers;
bool g_timer_thread = false;

void timer_loop() {
  std::unique_lock<std::mutex> lock(g_timer_mu);
  while (!g_tkw_quitting) {
    if (g_timers.empty()) {
      g_timer_cv.wait(lock);
      continue;
    }
    const ULONGLONG now = GetTickCount64(), due = g_timers.begin()->first;
    if (due > now) {
      g_timer_cv.wait_for(lock, std::chrono::milliseconds(due - now));
      continue;
    }
    std::function<void()> fn = std::move(g_timers.begin()->second);
    g_timers.erase(g_timers.begin());
    lock.unlock();
    tkw_on_main(std::move(fn));
    lock.lock();
  }
}
}  // namespace

void tkw_after_ms(unsigned ms, std::function<void()> fn) {
  if (ms == 0) {
    tkw_on_main(std::move(fn));
    return;
  }
  std::lock_guard<std::mutex> lock(g_timer_mu);
  g_timers.emplace(GetTickCount64() + ms, std::move(fn));
  if (!g_timer_thread) {
    g_timer_thread = true;
    std::thread(timer_loop).detach();
  }
  g_timer_cv.notify_one();
}
