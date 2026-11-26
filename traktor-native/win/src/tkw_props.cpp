// tkw_props.cpp — Traktor's app-property registry on Windows (port of ../../src/tk_props.mm).
//
// Every "app.traktor..." path D2's QML reads or writes through an AppProperty is a C++
// csi::application::TypedProperty<T> in one registry owned by csi::app(): an MSVC
// std::map<std::string, std::shared_ptr<Entity>>. This module looks properties up, reads and
// writes them with Traktor's own accessors and subscribes to their change signal (the
// connectValue QML's onValueChanged uses), so TK inside Traktor gets deck state as callbacks and
// drives the decks by direct calls.
//
// Thread rule (as on the Mac): lookups, reads and writes run on the MAIN thread only. A change
// callback may fire on any thread: it only flags the property and hops to the main thread,
// coalescing a burst into one call of TK's C callback with the property's id.
//
// Windows differences (win/map/traktor_4_5_1_21_win.json):
// - getTypedAppProperty<T> is inlined on Windows: the map is walked here (its offset in csi::app()
//   is found at run time; Tree::get_property 0xd6b070 reads it at +0x40), the Entity is a
//   Property when its int at +8 is 1, its type comes from Property::type_id() (vtable +0x20,
//   result through a hidden pointer: a type_info*, decorated name at +0x10), and the typed
//   pointer from __RTDynamicCast(Property -> TypedProperty<T>) exactly as Traktor's inlined code.
// - connectValue is Property vtable +0x40 (Mac +0x48): (this rcx, nod::connection* sret rdx,
//   std::function<void()> const& r8). std::function is MSVC's (64 bytes); Traktor copies it
//   through its own vtable, i.e. our code, so a real std::function from libtk is passed.
// - TypedProperty<std::string>::get returns an MSVC std::string through rdx.
// MSVC map node: {_Left, _Parent, _Right, char _Color, char _Isnil, key std::string @+0x20,
// shared_ptr<Entity> {ptr @+0x40, ctrl @+0x48}}.
#include <math.h>
#include <string.h>

#include <atomic>
#include <set>

#include "tkw.h"
#include "tkw_collection.h"

static_assert(sizeof(std::string) == 32, "MSVC release std::string layout expected");
static_assert(sizeof(std::function<void()>) == 64, "MSVC std::function layout expected");

namespace {

// RVAs not in the generated header yet (added to win/map/traktor_4_5_1_21_win.json, entries
// A_rtdynamiccast_thunk and A_ti_property / A_ti_tprop_*): checked against traktor.exe 4.5.1.21.
const uint32_t kRvaRtDynamicCast = 0x2a339ee;  // jmp [VCRUNTIME140!__RTDynamicCast]
const uint32_t kTdProperty = 0x21b42ab8;       // .?AVProperty@application@csi@@
const unsigned kVtTypeId = 0x20;               // Property::type_id() (Mac 0x28)
const unsigned kVtConnectValue = 0x40;         // VariableProperty<T>::connectValue (Mac 0x48)
// TypedProperty<T>::get / set: slots 10 / 11 of every property vtable (map entry "TypedProperty
// vtable slots"; e.g. VariableProperty<float> 0x1fefc200: 0x6bc160 / 0x6c4b50).
const unsigned kVtGet = 0x50, kVtSet = 0x58;
const unsigned kOffEntityKind = 0x08;          // int; 1 = Property (Tree::get_property checks it)
const char kStringTypeName[] = ".?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@";

enum TKPropType { PT_NONE = 0, PT_FLOAT = 1, PT_BOOL = 2, PT_INT = 3, PT_STRING = 4 };

struct Kind {
  const char* type_name;  // MSVC decorated name of T
  int type;
  uint32_t td;            // TypeDescriptor of TypedProperty<T>
};
const Kind kKinds[] = {
    {".M", PT_FLOAT, 0x21b42b20}, {"._N", PT_BOOL, 0x21b42b58},
    {".H", PT_INT, 0x21b42ae8},   {kStringTypeName, PT_STRING, 0x21b42ba0},
};

struct TKProp {
  int id;
  int type;
  std::string path;
  void* obj;  // TypedProperty<T>*; the registry keeps it alive for Traktor's lifetime
  bool watched;
  // GetTickCount() when a change was queued to the main thread, 0 = none queued. A job the main
  // queue dropped (it refuses jobs when full) is retried after 2 s instead of blocking forever.
  std::atomic<uint32_t> pending_since;
  alignas(8) unsigned char conn[32];  // nod::connection (0x18 bytes), kept alive, never dropped
};

std::vector<TKProp*> g_props;              // id = index; main thread only
void (*g_prop_cb)(int) = nullptr;          // TK's callback, called on the main thread
std::set<std::string> g_missing_logged;    // paths already reported as missing (main thread)

void* g_app = nullptr;
const char* g_head = nullptr;  // the map's head (sentinel) node
size_t g_off = 0;

bool prop_on_main(const char* what) {
  if (tkw_is_main_thread()) return true;
  tklog("PROP: %s refused off the main thread", what);
  return false;
}

TKProp* prop_at(int id) {
  if (id < 0 || (size_t)id >= g_props.size()) return nullptr;
  return g_props[(size_t)id];
}

int copy_out(const std::string& s, char* out, int cap) {
  if (out && cap > 0) strncpy_s(out, (size_t)cap, s.c_str(), _TRUNCATE);
  return (int)s.size();
}

bool in_image(const void* p) {
  return (uintptr_t)p >= tkw_base() && (uintptr_t)p < tkw_base() + TKW_IMAGE_SIZE;
}

// The virtual function at byte offset off of obj, or null unless it is Traktor's code.
void* vslot(const void* obj, unsigned off) {
  if (tkw_rtti_name(obj).empty()) return nullptr;
  const void* const* vt = *(const void* const* const*)obj;
  if (!tkw_readable(vt + off / 8, 8)) return nullptr;
  void* fn = (void*)vt[off / 8];
  return in_image(fn) ? fn : nullptr;
}

// --- the registry map (MSVC _Tree) ------------------------------------------------------------
bool node_ok(const char* n) { return tkw_readable(n, 0x50); }
const char* node_left(const char* n) { return *(const char* const*)n; }
const char* node_parent(const char* n) { return *(const char* const*)(n + 8); }
const char* node_right(const char* n) { return *(const char* const*)(n + 0x10); }
bool node_nil(const char* n) { return n[0x19] != 0; }
void* node_value(const char* n) { return *(void* const*)(n + 0x40); }
bool node_key(const char* n, std::string* k) { return tkw_read_string(n + 0x20, k) && k->size() <= 4096; }

bool looks_like_registry(const char* head, size_t size) {
  if (size < 16 || size > 1000000 || !tkw_readable(head, 0x60) || !node_nil(head)) return false;
  const char* root = node_parent(head);
  const char* first = node_left(head);
  std::string k;
  return tkw_readable(root, 0x60) && !node_nil(root) && tkw_readable(first, 0x60) && node_key(first, &k) &&
         k.rfind("app.", 0) == 0;
}

void* call_csi_app() {
  try {
    return ((void* (*)())WF(WF_A_csi_app))();
  } catch (...) {  // csi::app() throws before the application exists
    return nullptr;
  }
}

bool find_registry() {
  if (g_head) return true;
  g_app = call_csi_app();
  if (!tkw_readable(g_app, 0x400)) {
    tklog("PROP: csi::app() = %p, unreadable (not created yet?)", g_app);
    return false;
  }
  for (size_t off = 0; off + 16 <= 0x400; off += 8) {
    const char* head = *(const char* const*)((char*)g_app + off);
    const size_t size = *(const size_t*)((char*)g_app + off + 8);
    if (looks_like_registry(head, size)) {
      g_head = head;
      g_off = off;
      tklog("PROP: registry = std::map at csi::app()+0x%zx (%zu entities), app %p (%s)", off, size, g_app,
            tkw_rtti_name(g_app).c_str());
      return true;
    }
  }
  tklog("PROP: no std::map<string, Entity> in csi::app() %p (%s)", g_app, tkw_rtti_name(g_app).c_str());
  return false;
}

// The first node whose key is not less than key (the head when there is none).
const char* lower_bound(const std::string& key) {
  const char* best = g_head;
  const char* n = node_parent(g_head);  // root
  for (int depth = 0; depth < 64 && node_ok(n) && !node_nil(n); depth++) {
    std::string k;
    if (!node_key(n, &k)) return g_head;
    if (k.compare(key) < 0) {
      n = node_right(n);
    } else {
      best = n;
      n = node_left(n);
    }
  }
  return best;
}

// In-order successor; the head (nil) at the end or on an unreadable node.
const char* next_node(const char* n) {
  const char* r = node_right(n);
  if (!node_ok(r)) return g_head;
  if (!node_nil(r)) {
    n = r;
    while (node_ok(node_left(n)) && !node_nil(node_left(n))) n = node_left(n);
    return n;
  }
  const char* p = node_parent(n);
  while (node_ok(p) && !node_nil(p) && n == node_right(p)) {
    n = p;
    p = node_parent(p);
  }
  return node_ok(p) ? p : g_head;
}

void* lookup_entity(const std::string& path) {
  if (!find_registry()) return nullptr;
  const char* n = lower_bound(path);
  std::string k;
  if (n == g_head || !node_ok(n) || node_nil(n) || !node_key(n, &k) || k != path) return nullptr;
  return node_value(n);
}

bool is_property(const void* ent) {
  return tkw_readable(ent, 0x18) && *(const int*)((const char*)ent + kOffEntityKind) == 1 &&
         !tkw_rtti_name(ent).empty();
}

// Property::type_id(): the decorated name of T (".M" float …), "" when not readable. The result
// comes back through the hidden pointer rdx: VariableProperty<float>'s slot 4 (0x640880) is
// `lea rax,[TD .M]; mov [rdx],rax; mov rax,rdx; ret` — a type_info*, decorated name at +0x10.
std::string type_name_of(void* prop) {
  void* fn = vslot(prop, kVtTypeId);
  if (!fn) return "";
  void* ti = nullptr;
  try {
    tkw_call<void*>(fn, prop, (void*)&ti);
  } catch (...) {
    return "";
  }
  const char* name = (const char*)ti + 0x10;
  if (!tkw_readable(ti, 0x10) || !tkw_readable(name, 2)) return "";
  const size_t len = strnlen(name, tkw_readable(name, 256) ? 256 : 2);
  return std::string(name, len);
}

const char* friendly_type(const std::string& t) {
  for (const Kind& k : kKinds)
    if (t == k.type_name) {
      switch (k.type) {
        case PT_FLOAT: return "float";
        case PT_BOOL: return "bool";
        case PT_INT: return "int";
        default: return "string";
      }
    }
  return nullptr;
}

void* dyn_cast(void* prop, uint32_t td) {
  typedef void* (*RtDynCast)(void*, long, void*, void*, int);
  try {
    return ((RtDynCast)tkw_rva(kRvaRtDynamicCast))(prop, 0, tkw_rva(kTdProperty), tkw_rva(td), 0);
  } catch (...) {  // __non_rtti_object: not a polymorphic object after all
    return nullptr;
  }
}

// Calls f(path, entity) for every Property whose path starts with prefix, in key order (the map
// is sorted: start at the prefix, stop at the first key past it). Returns the count.
template <typename F>
size_t each_property(const std::string& prefix, F f) {
  if (!find_registry()) return 0;
  size_t hits = 0;
  const char* n = lower_bound(prefix);
  for (int guard = 0; guard < 2000000 && n != g_head && node_ok(n) && !node_nil(n); guard++) {
    std::string k;
    if (!node_key(n, &k)) { tklog("PROP: map walk stopped at %p", n); break; }
    if (k.compare(0, prefix.size(), prefix) != 0) break;
    void* ent = node_value(n);
    if (is_property(ent)) {
      f(k, ent);
      hits++;
    }
    n = next_node(n);
  }
  return hits;
}

const char* prop_type_name(int t) {
  switch (t) {
    case PT_FLOAT: return "float";
    case PT_BOOL: return "bool";
    case PT_INT: return "int";
    case PT_STRING: return "string";
    default: return "?";
  }
}

void note_missing(const char* path, const char* why) {
  if (g_missing_logged.insert(path).second) tklog("PROP: '%s' %s", path, why);
}

// Any thread. A burst of changes becomes one main-thread call; the reader then gets the latest value.
void prop_changed(TKProp* p) {
  const uint32_t now = GetTickCount() | 1u;
  uint32_t prev = p->pending_since.load();
  if (prev && now - prev < 2000) return;
  if (!p->pending_since.compare_exchange_strong(prev, now)) return;
  try {
    tkw_on_main([p] {
      p->pending_since.store(0);
      if (g_prop_cb) g_prop_cb(p->id);
    });
  } catch (...) {  // never throw into Traktor's signal
    p->pending_since.store(0);
  }
}

}  // namespace


// ── Exported C API (ctypes, the Mac signatures) ─────────────────────────────────────────────────
TKW_EXPORT int tk_prop_handle(const char* path) {
  if (!path || !*path || !prop_on_main("handle")) return -1;
  for (TKProp* p : g_props)
    if (p->path == path) return p->id;
  const ULONGLONG t0 = GetTickCount64();  // a burst of new handles stalled the UI (2026-11-25)
  struct Took {
    ULONGLONG t0;
    ~Took() {
      const ULONGLONG ms = GetTickCount64() - t0;
      if (ms >= 2) tklog("PROP: handle took %llu ms", ms);
    }
  } took{t0};
  void* ent = lookup_entity(path);
  if (!ent) { note_missing(path, "is not in the registry"); return -1; }
  if (!is_property(ent)) { note_missing(path, "is not a Property"); return -1; }
  const std::string t = type_name_of(ent);
  for (const Kind& k : kKinds) {
    if (t != k.type_name) continue;
    void* obj = dyn_cast(ent, k.td);
    if (!obj || tkw_rtti_name(obj).empty()) {
      tklog("PROP: '%s' (%s) did not cast to TypedProperty (%p)", path, t.c_str(), obj);
      return -1;
    }
    TKProp* p = new TKProp();
    p->id = (int)g_props.size();
    p->type = k.type;
    p->path = path;
    p->obj = obj;
    p->watched = false;
    p->pending_since.store(0);
    memset(p->conn, 0, sizeof(p->conn));
    g_props.push_back(p);
    tklog("PROP: #%d %s (%s)", p->id, path, prop_type_name(p->type));
    return p->id;
  }
  note_missing(path, ("has type '" + t + "', not float/bool/int/string").c_str());
  return -1;
}

TKW_EXPORT int tk_prop_type(int id) {
  TKProp* p = prop_at(id);
  return p ? p->type : PT_NONE;
}

// Numbers, and bools as 0/1. NaN for a string property, a bad id or an unreadable vtable.
// get() is the object's own vtable slot 10: property classes (Variable/Gateway/…) differ in
// layout behind it, so it is never called by a fixed address.
TKW_EXPORT double tk_prop_get_num(int id) {
  TKProp* p = prop_at(id);
  if (!p || !prop_on_main("get")) return NAN;
  void* get = vslot(p->obj, kVtGet);
  if (!get) { tklog("PROP: #%d %s has no readable get() slot", p->id, p->path.c_str()); return NAN; }
  switch (p->type) {
    case PT_FLOAT: return tkw_call<float>(get, p->obj);
    case PT_BOOL: return tkw_call<bool>(get, p->obj) ? 1.0 : 0.0;  // al only
    case PT_INT: return tkw_call<int>(get, p->obj);
    default: return NAN;
  }
}

// Copies the string (NUL-terminated, cut to cap-1). Returns its full length, -1 on a bad id/type.
TKW_EXPORT int tk_prop_get_str(int id, char* out, int cap) {
  TKProp* p = prop_at(id);
  if (!p || p->type != PT_STRING || !out || cap <= 0 || !prop_on_main("get_str")) return -1;
  // get() constructs an MSVC std::string in our buffer (hidden pointer in rdx); same STL and
  // heap as Traktor, so our std::string destructor frees it.
  void* get = vslot(p->obj, kVtGet);
  if (!get) { tklog("PROP: #%d %s has no readable get() slot", p->id, p->path.c_str()); return -1; }
  alignas(8) unsigned char buf[sizeof(std::string)];
  memset(buf, 0, sizeof(buf));
  tkw_call<void*>(get, p->obj, (void*)buf);
  std::string* s = reinterpret_cast<std::string*>(buf);
  const std::string v = *s;
  s->~basic_string();
  return copy_out(v, out, cap);
}

// Traktor's own set() (vtable slot 11): it checks write access and the value's domain, then
// notifies observers.
TKW_EXPORT int tk_prop_set_num(int id, double v) {
  TKProp* p = prop_at(id);
  if (!p || !prop_on_main("set")) return -1;
  void* set = vslot(p->obj, kVtSet);
  if (!set) { tklog("PROP: #%d %s has no readable set() slot", p->id, p->path.c_str()); return -1; }
  switch (p->type) {
    case PT_FLOAT: { const float f = (float)v; tkw_call<void>(set, p->obj, &f); return 0; }
    case PT_BOOL: { const bool b = v != 0.0; tkw_call<void>(set, p->obj, &b); return 0; }
    case PT_INT: { const int i = (int)lround(v); tkw_call<void>(set, p->obj, &i); return 0; }
    default: return -1;
  }
}

TKW_EXPORT int tk_prop_set_str(int id, const char* v) {
  TKProp* p = prop_at(id);
  if (!p || p->type != PT_STRING || !v || !prop_on_main("set_str")) return -1;
  void* set = vslot(p->obj, kVtSet);
  if (!set) { tklog("PROP: #%d %s has no readable set() slot", p->id, p->path.c_str()); return -1; }
  const std::string s(v);
  tkw_call<void>(set, p->obj, &s);
  return 0;
}

TKW_EXPORT int tk_prop_watch(int id) {
  TKProp* p = prop_at(id);
  if (!p || !prop_on_main("watch")) return -1;
  if (p->watched) return 0;
  void* connect = vslot(p->obj, kVtConnectValue);
  if (!connect) { tklog("PROP: #%d %s has no connectValue slot", p->id, p->path.c_str()); return -1; }
  // Traktor copies the function (through its own vtable, i.e. our code) into its signal.
  std::function<void()> fn = [p]() { prop_changed(p); };
  tkw_call<void*>(connect, p->obj, (void*)p->conn, (const void*)&fn);
  p->watched = true;
  tklog("PROP: watching #%d %s", p->id, p->path.c_str());
  return 0;
}

TKW_EXPORT void tk_prop_set_callback(void (*cb)(int)) {
  g_prop_cb = cb;
  tklog("PROP: change callback %s", cb ? "set" : "cleared");
}

// "path\ttype\n" lines into out (cut at cap). Returns the length the full listing needs.
TKW_EXPORT int tk_prop_list(const char* prefix, char* out, int cap) {
  if (!prop_on_main("list")) return -1;
  std::string all;
  each_property(prefix ? prefix : "", [&](const std::string& path, void* ent) {
    const std::string t = type_name_of(ent);
    const char* f = friendly_type(t);
    all += path;
    all += '\t';
    all += f ? f : (t.empty() ? "?" : t.c_str());
    all += '\n';
  });
  return copy_out(all, out, cap);
}

// Load the collection track at path into deck (0 = A). Main thread; 0 = started, -2 = not in
// the collection (in this spelling).
TKW_EXPORT int tk_load_path(int deck, const char* path) {
  if (!path || !*path || deck < 0 || deck > 3 || !prop_on_main("load_path")) return -1;
  // Mac contract: 0 = load started, -2 = not in Traktor's collection, -1 = error (logged).
  if (tkw_collection_key_of_path(path) == 0) return -2;
  return tkw_collection_load_path(deck, path) ? 0 : -1;
}

// ── "prop" line command (manual checks) ─────────────────────────────────────────────────────────
static std::string prop_value_text(int id) {
  TKProp* p = prop_at(id);
  if (!p) return "?";
  char buf[512];
  if (p->type == PT_STRING) {
    tk_prop_get_str(id, buf, sizeof(buf));
    return std::string("\"") + buf + "\"";
  }
  snprintf(buf, sizeof(buf), "%g", tk_prop_get_num(id));
  return buf;
}

static std::string prop_command(const std::string& args) {
  const std::string usage = "ERR usage: prop find | list [prefix] | get <path> | set <path> <value>";
  const size_t s1 = args.find(' ');
  const std::string sub = args.substr(0, s1);
  const std::string rest = s1 == std::string::npos ? "" : args.substr(s1 + 1);
  const size_t s2 = rest.find(' ');
  const std::string path = rest.substr(0, s2), value = s2 == std::string::npos ? "" : rest.substr(s2 + 1);
  if (sub == "find") return find_registry() ? "OK registry at csi::app()+" + std::to_string(g_off) : "ERR not found (see log)";
  if (sub == "list") {
    const size_t hits = each_property(path, [](const std::string& k, void* ent) {
      const std::string t = type_name_of(ent);
      const char* f = friendly_type(t);
      tklog("PROPLIST %s\t%s", k.c_str(), f ? f : t.c_str());
    });
    tklog("PROP: %zu propert%s under '%s'", hits, hits == 1 ? "y" : "ies", path.c_str());
    return "OK " + std::to_string(hits) + " (PROPLIST lines in the log)";
  }
  if (sub == "get" && !path.empty()) {
    const int id = tk_prop_handle(path.c_str());
    if (id < 0) return "ERR no float/bool/int/string property " + path;
    const std::string v = prop_value_text(id);
    tklog("PROP: %s = %s", path.c_str(), v.c_str());
    return "OK " + path + " = " + v;
  }
  if (sub == "set" && !path.empty() && !value.empty()) {
    const int id = tk_prop_handle(path.c_str());
    if (id < 0) return "ERR no float/bool/int/string property " + path;
    const int rc = tk_prop_type(id) == PT_STRING ? tk_prop_set_str(id, value.c_str())
                                                  : tk_prop_set_num(id, strtod(value.c_str(), nullptr));
    const std::string v = prop_value_text(id);
    tklog("PROP: %s set (rc %d), now %s", path.c_str(), rc, v.c_str());
    return (rc == 0 ? "OK " : "ERR set failed, ") + path + " = " + v;
  }
  return usage;
}

void tkw_mod_props_init() {
  tkw_command("prop", [](const std::string& a, const std::vector<std::string>&) { return prop_command(a); });
}
