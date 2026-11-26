// tk_props.mm — Traktor's app-property registry, called natively (included by tk_inject.mm).
//
// Every "app.traktor..." path D2's QML reads or writes through an AppProperty is a C++
// csi::application::TypedProperty<T> in one registry (csi::app() + 0x38, a std::map keyed by the
// path). This module looks them up, reads and writes them with Traktor's own accessors and
// subscribes to their change signal (the connectValue QML's onValueChanged uses), so Track
// Kommander running inside Traktor gets deck state as callbacks and drives the decks by direct
// calls: no QML bridge, no HTTP.
//
// Thread rule: lookups, reads and writes run on the MAIN thread only (Traktor's C++ is not
// thread-safe; TK's Python runs there in integrated mode). A change callback may fire on any
// thread: it only flags the property and hops to the main queue, coalescing a burst into one
// call of the registered C callback (TK's ctypes function) with the property's id.
//
// Exported C API (ctypes): tk_prop_handle / tk_prop_type / tk_prop_get_num / tk_prop_get_str /
// tk_prop_set_num / tk_prop_set_str / tk_prop_watch / tk_prop_set_callback / tk_prop_list /
// tk_load_path. Line command for checks by hand: "prop list|get|set <...>" (results in the log).

#include <atomic>
#include <functional>

#define TK_EXPORT extern "C" __attribute__((visibility("default")))

enum TKPropType { PT_NONE = 0, PT_FLOAT = 1, PT_BOOL = 2, PT_INT = 3, PT_STRING = 4 };

struct TKProp {
  int id;
  int type;
  std::string path;
  void* obj;                       // TypedProperty<T>*; the shared_ptr ref we took is never dropped
  bool watched;
  std::atomic<bool> pending;       // a change is already queued to the main thread
  alignas(8) unsigned char conn[24];  // nod::connection, kept alive (never disconnected)
};

static std::vector<TKProp*> g_props;         // id = index; touched on the main thread only
static void (*g_prop_cb)(int) = nullptr;      // TK's callback, called on the main thread

static bool prop_on_main(const char* what) {
  if (pthread_main_np()) return true;
  tklog("PROP: %s refused off the main thread", what);
  return false;
}

static TKProp* prop_at(int id) {
  if (id < 0 || (size_t)id >= g_props.size()) return nullptr;
  return g_props[(size_t)id];
}

// getTypedAppProperty<T>(path) -> shared_ptr<TypedProperty<T>> via x8; null on a type mismatch.
static void* typed_lookup(uintptr_t getter, const std::string& path) {
  void* out[2] = {nullptr, nullptr};
  call_sret2(R(getter), (void*)&path, nullptr, out);
  return out[0];
}

static const char* prop_type_name(int t) {
  switch (t) {
    case PT_FLOAT: return "float";
    case PT_BOOL: return "bool";
    case PT_INT: return "int";
    case PT_STRING: return "string";
    default: return "?";
  }
}

TK_EXPORT int tk_prop_handle(const char* path) {
  if (!path || !*path || !prop_on_main("handle")) return -1;
  for (TKProp* p : g_props)
    if (p->path == path) return p->id;
  static const struct { uintptr_t fn; int type; } kinds[] = {
      {A_get_tprop_float, PT_FLOAT}, {A_get_tprop_bool, PT_BOOL},
      {A_get_tprop_int, PT_INT}, {A_get_tprop_string, PT_STRING}};
  const std::string key(path);
  for (const auto& k : kinds) {
    void* obj = typed_lookup(k.fn, key);
    if (!obj) continue;
    if (!looks_like_object(obj)) {
      tklog("PROP: '%s' resolved to an unreadable object %p", path, obj);
      return -1;
    }
    TKProp* p = new TKProp();
    p->id = (int)g_props.size();
    p->type = k.type;
    p->path = key;
    p->obj = obj;
    p->watched = false;
    p->pending.store(false);
    memset(p->conn, 0, sizeof(p->conn));
    g_props.push_back(p);
    tklog("PROP: #%d %s (%s)", p->id, path, prop_type_name(p->type));
    return p->id;
  }
  tklog("PROP: no float/bool/int/string property '%s'", path);
  return -1;
}

TK_EXPORT int tk_prop_type(int id) {
  TKProp* p = prop_at(id);
  return p ? p->type : PT_NONE;
}

// Numbers, and bools as 0/1. NaN for a string property or a bad id.
TK_EXPORT double tk_prop_get_num(int id) {
  TKProp* p = prop_at(id);
  if (!p || !prop_on_main("get")) return NAN;
  switch (p->type) {
    case PT_FLOAT: return ((float (*)(void*))R(A_tprop_float_get))(p->obj);
    // A bool comes back in w0 with only the low byte defined.
    case PT_BOOL: return (((uint32_t (*)(void*))R(A_tprop_bool_get))(p->obj) & 0xffu) ? 1.0 : 0.0;
    case PT_INT: return ((int (*)(void*))R(A_tprop_int_get))(p->obj);
    default: return NAN;
  }
}

// Copies the string (NUL-terminated, cut to cap-1). Returns its full length, -1 on a bad id/type.
TK_EXPORT int tk_prop_get_str(int id, char* out, int cap) {
  TKProp* p = prop_at(id);
  if (!p || p->type != PT_STRING || !out || cap <= 0 || !prop_on_main("get_str")) return -1;
  alignas(8) unsigned char buf[24];
  memset(buf, 0, sizeof(buf));
  call_sret2(R(A_tprop_string_get), p->obj, nullptr, buf);  // constructs a std::string in buf
  const std::string s = decode_std_string(buf);
  if (buf[23] & 0x80u) operator delete(*(void**)buf);  // a long string owns heap memory
  strlcpy(out, s.c_str(), (size_t)cap);
  return (int)s.size();
}

// Traktor's own set(): it checks write access and the value's domain, then notifies observers.
TK_EXPORT int tk_prop_set_num(int id, double v) {
  TKProp* p = prop_at(id);
  if (!p || !prop_on_main("set")) return -1;
  switch (p->type) {
    case PT_FLOAT: { const float f = (float)v; ((void (*)(void*, const float*))R(A_tprop_float_set))(p->obj, &f); return 0; }
    case PT_BOOL: { const bool b = v != 0.0; ((void (*)(void*, const bool*))R(A_tprop_bool_set))(p->obj, &b); return 0; }
    case PT_INT: { const int i = (int)lround(v); ((void (*)(void*, const int*))R(A_tprop_int_set))(p->obj, &i); return 0; }
    default: return -1;
  }
}

TK_EXPORT int tk_prop_set_str(int id, const char* v) {
  TKProp* p = prop_at(id);
  if (!p || p->type != PT_STRING || !v || !prop_on_main("set_str")) return -1;
  const std::string s(v);
  ((void (*)(void*, const std::string*))R(A_tprop_string_set))(p->obj, &s);
  return 0;
}

// Any thread. A burst of changes becomes one main-queue call; the reader then gets the latest value.
static void prop_changed(TKProp* p) {
  if (p->pending.exchange(true)) return;
  dispatch_async(dispatch_get_main_queue(), ^{
    p->pending.store(false);
    if (g_prop_cb) g_prop_cb(p->id);
  });
}

TK_EXPORT int tk_prop_watch(int id) {
  TKProp* p = prop_at(id);
  if (!p || !prop_on_main("watch")) return -1;
  if (p->watched) return 0;
  void* const* vt = *(void* const* const*)p->obj;
  void* connect = vt[VT_PROP_CONNECT_VALUE / 8];
  // Traktor copies the function (through its own vtable, i.e. our code) into its signal.
  std::function<void()> fn = [p]() { prop_changed(p); };
  call_sret2(connect, p->obj, &fn, p->conn);
  p->watched = true;
  tklog("PROP: watching #%d %s", p->id, p->path.c_str());
  return 0;
}

TK_EXPORT void tk_prop_set_callback(void (*cb)(int)) {
  g_prop_cb = cb;
  tklog("PROP: change callback %s", cb ? "set" : "cleared");
}

// ── Registry walk (discovery) ───────────────────────────────────────────────────────────────────
// libc++ std::map: {begin_node@0, end_node.left(root)@8, size@0x10}; a node is {left, right,
// parent, is_black, key std::string@0x20, shared_ptr<Entity>{ptr@0x38, ctrl@0x40}}.

static const char* entity_type_name(void* ent) {
  void* const* vt = *(void* const* const*)ent;
  const void* ti = ((const void* (*)(void*))vt[VT_PROP_TYPE_ID / 8])(ent);
  if (!ti || !gum_memory_is_readable(ti, 16)) return "?";
  const uintptr_t nm = *(const uintptr_t*)((const char*)ti + 8) & 0x7fffffffffffffffULL;
  if (!nm || !gum_memory_is_readable((const void*)nm, 1)) return "?";
  const char* n = (const char*)nm;
  if (!strcmp(n, "f")) return "float";
  if (!strcmp(n, "b")) return "bool";
  if (!strcmp(n, "i")) return "int";
  if (strstr(n, "basic_string")) return "string";
  return n;
}

// Calls f(path, type name) for every Property whose path starts with prefix. Returns the count.
template <typename F>
static size_t each_property(const char* prefix, F f) {
  void* app = ((void* (*)(void))R(A_csi_app))();
  if (!app) return 0;
  char* m = (char*)app + OFF_APP_ENTITY_MAP;
  if (!gum_memory_is_readable(m, 0x18)) return 0;
  void* end = m + 8;
  const size_t size = *(const size_t*)(m + 0x10);
  void* n = *(void**)m;
  size_t seen = 0, hits = 0;
  const size_t plen = prefix ? strlen(prefix) : 0;
  while (n && n != end && seen++ <= size) {
    if (!gum_memory_is_readable(n, 0x48)) { tklog("PROP: map walk stopped at %p", n); break; }
    void* ent = *(void**)((char*)n + 0x38);
    if (ent && looks_like_object(ent) && *(const int*)((char*)ent + OFF_ENTITY_KIND) == 1) {
      const std::string key = decode_std_string((char*)n + 0x20);
      if (!plen || key.compare(0, plen, prefix) == 0) { f(key, entity_type_name(ent)); hits++; }
    }
    void* r = *(void**)((char*)n + 8);
    if (r) {
      n = r;
      while (*(void**)n) n = *(void**)n;  // leftmost of the right subtree
    } else {
      void* par = *(void**)((char*)n + 0x10);
      while (par && *(void**)par != n) { n = par; par = *(void**)((char*)n + 0x10); }
      n = par;
    }
  }
  return hits;
}

// "path\ttype\n" lines into out (cut at cap). Returns the length the full listing needs.
TK_EXPORT int tk_prop_list(const char* prefix, char* out, int cap) {
  if (!prop_on_main("list")) return -1;
  std::string all;
  each_property(prefix, [&](const std::string& path, const char* type) {
    all += path; all += '\t'; all += type; all += '\n';
  });
  if (out && cap > 0) strlcpy(out, all.c_str(), (size_t)cap);
  return (int)all.size();
}

// Load the collection track at path into deck (0 = A). Main thread; 0 = started.
TK_EXPORT int tk_load_path(int deck, const char* path) {
  if (!path || !*path || deck < 0 || deck > 3 || !prop_on_main("load_path")) return -1;
  if (key_of_path(path) == 0) return -2;  // not in the collection (in this spelling)
  do_native_load_path(deck, path);
  return 0;
}

// ── "prop" line command (manual checks; results go to the log) ──────────────────────────────────
static void prop_log_value(int id) {
  TKProp* p = prop_at(id);
  if (!p) return;
  if (p->type == PT_STRING) {
    char buf[512];
    tk_prop_get_str(id, buf, sizeof(buf));
    tklog("PROP: %s = \"%s\"", p->path.c_str(), buf);
  } else {
    tklog("PROP: %s = %g", p->path.c_str(), tk_prop_get_num(id));
  }
}

static void handle_prop_cmd(int fd, const char* line) {
  char sub[16] = {0}, path[256] = {0}, value[256] = {0};
  const int n = sscanf(line, "%*s %15s %255s %255[^\n]", sub, path, value);
  if (n < 1) { cmd_reply(fd, "ERR usage: prop list [prefix] | get <path> | set <path> <value>\n"); return; }
  std::string s(sub), p(path), v(value);
  if (s == "list") {
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{
      const size_t hits = each_property(p.c_str(), [](const std::string& k, const char* t) {
        tklog("PROPLIST %s\t%s", k.c_str(), t);
      });
      tklog("PROP: %zu propert%s under '%s'", hits, hits == 1 ? "y" : "ies", p.c_str());
    });
  } else if (s == "get" && n >= 2) {
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ prop_log_value(tk_prop_handle(p.c_str())); });
  } else if (s == "set" && n == 3) {
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{
      const int id = tk_prop_handle(p.c_str());
      if (id < 0) return;
      if (tk_prop_type(id) == PT_STRING) tk_prop_set_str(id, v.c_str());
      else tk_prop_set_num(id, strtod(v.c_str(), nullptr));
      prop_log_value(id);
    });
  } else {
    cmd_reply(fd, "ERR usage: prop list [prefix] | get <path> | set <path> <value>\n");
  }
}
