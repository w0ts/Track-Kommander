// tkw_columns_store.cpp — the list's column layout string and TK's own store of it (part of the
// columns module; port of the layout half of the Mac tk_columns.mm).
//
// The layout string ("count type width ..." in display order, a hidden column's width negative)
// goes to Traktor's settings file through get/setSettingsNormal and History. The unpatched Traktor
// reads the same file, so TK's types never reach it: they are taken out on the way to the file and
// kept in TK's own store (list-columns.tsv, keyed by the Traktor part), and put back on the way
// in. A Traktor part TK has not seen (changed by the unpatched app) gets TK's columns hidden at
// the end. TK's store is TK's own file, never one of Traktor's.
//
// Windows: get/setSettings take an MSVC std::string& (same STL and heap as libtk: assigned in
// place); getSettings* returns that reference in rax (`mov rax, rdx` at its start), so ours does too.
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <utility>
#include <vector>

#include "tkw.h"
#include "tkw_columns.h"

namespace {

struct ColLayout {
  std::vector<std::pair<int, int>> pairs;
  bool ok = false;
};

ColLayout layout_parse(const std::string& s) {
  ColLayout l;
  std::istringstream in(s);
  long count = 0;
  if (!(in >> count) || count < 0 || count > 256) return l;
  for (long i = 0; i < count; i++) {
    int type = 0, width = 0;
    if (!(in >> type >> width)) return l;
    l.pairs.emplace_back(type, width);
  }
  l.ok = true;
  return l;
}

// Traktor's own format: "<count> " then "<type> <width> " per column.
std::string layout_text(const std::vector<std::pair<int, int>>& pairs) {
  std::string s = std::to_string(pairs.size()) + " ";
  for (const auto& p : pairs) s += std::to_string(p.first) + " " + std::to_string(p.second) + " ";
  return s;
}

bool layout_has_tk(const ColLayout& l) {
  for (const auto& p : l.pairs)
    if (col_is_tk(p.first)) return true;
  return false;
}

// SCORE, BPM MATCH and KEY MATCH are never stored visible: TK shows them only while its own list
// is up.
ColLayout layout_score_hidden(ColLayout l) {
  for (auto& p : l.pairs)
    if (col_is_tk_list(p.first) && p.second > 0) p.second = -p.second;
  return l;
}

std::string layout_traktor_part(const ColLayout& l) {
  std::vector<std::pair<int, int>> kept;
  for (const auto& p : l.pairs)
    if (!col_is_tk(p.first)) kept.push_back(p);
  return layout_text(kept);
}

// Traktor part -> the full layout. The replaced get/setSettings* can run on any Traktor thread
// (e.g. the settings save at exit): g_store_mu guards the map, its loading and the .tsv write.
std::mutex g_store_mu;
std::map<std::string, std::string> g_store;
bool g_store_loaded = false;

// %LOCALAPPDATA%\Track Kommander\traktor-native\ (libtk.log's neighbour), made when missing.
std::wstring store_dir() {
  wchar_t base[MAX_PATH];
  const DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", base, MAX_PATH);
  if (!n || n >= MAX_PATH) return std::wstring();
  std::wstring dir = std::wstring(base) + L"\\Track Kommander";
  CreateDirectoryW(dir.c_str(), nullptr);
  dir += L"\\traktor-native";
  CreateDirectoryW(dir.c_str(), nullptr);
  return dir;
}

std::wstring store_file() {
  const std::wstring dir = store_dir();
  return dir.empty() ? dir : dir + L"\\list-columns.tsv";
}

void store_load() {
  if (g_store_loaded) return;
  g_store_loaded = true;
  const std::wstring file = store_file();
  if (file.empty()) { tklog("COLS: no LOCALAPPDATA, layouts not stored"); return; }
  std::ifstream in(file.c_str());
  std::string line;
  while (std::getline(in, line)) {
    const size_t tab = line.find('\t');
    if (tab != std::string::npos) g_store[line.substr(0, tab)] = line.substr(tab + 1);
  }
  tklog("COLS: %zu stored layout(s)", g_store.size());
}

// Written to a .tmp then swapped in (MoveFileEx: rename() refuses an existing target on Windows).
void store_save() {
  const std::wstring file = store_file();
  if (file.empty()) return;
  const std::wstring tmp = file + L".tmp";
  {
    std::ofstream out(tmp.c_str(), std::ios::trunc);
    if (!out) { tklog("COLS: cannot write list-columns.tsv.tmp"); return; }
    for (const auto& kv : g_store) out << kv.first << '\t' << kv.second << '\n';
    if (!out) { tklog("COLS: write error on list-columns.tsv.tmp"); return; }
  }
  if (!MoveFileExW(tmp.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING))
    tklog("COLS: cannot replace list-columns.tsv (%lu)", GetLastError());
}

// Keep the store small: Traktor's layouts are few (Normal, History); stale ones go first.
void store_put(const std::string& traktor_part, const std::string& full) {
  store_load();
  auto it = g_store.find(traktor_part);
  if (it != g_store.end() && it->second == full) return;
  if (g_store.size() >= 32 && it == g_store.end()) g_store.erase(g_store.begin());
  g_store[traktor_part] = full;
  store_save();
}

typedef std::string* (*FnGetLayout)(const void*, std::string*);
typedef void (*FnSetLayout)(void*, const std::string*);
FnGetLayout g_orig_get_normal = nullptr, g_orig_get_history = nullptr;
FnSetLayout g_orig_set_normal = nullptr, g_orig_set_history = nullptr;

// Called by Traktor directly (replaced functions): no exception may leave them.
std::string* get_layout(FnGetLayout orig, const void* self, std::string* out) {
  std::string* r = orig(self, out);
  try {
    cols_layout_out(out);
  } catch (...) {
    tklog("COLS: layout out failed (kept Traktor's)");
  }
  return r;
}

void set_layout(FnSetLayout orig, void* self, const std::string* in) {
  std::string mine;
  try {
    mine = cols_layout_in(*in);
  } catch (...) {
    tklog("COLS: layout in failed (Traktor's as it is)");
    orig(self, in);
    return;
  }
  orig(self, &mine);
}

std::string* tk_get_normal(const void* self, std::string* out) { return get_layout(g_orig_get_normal, self, out); }
std::string* tk_get_history(const void* self, std::string* out) { return get_layout(g_orig_get_history, self, out); }
void tk_set_normal(void* self, const std::string* in) { set_layout(g_orig_set_normal, self, in); }
void tk_set_history(void* self, const std::string* in) { set_layout(g_orig_set_history, self, in); }

}  // namespace

// Outgoing (to Traktor's settings file): TK's types taken out, the full layout kept.
void cols_layout_out(std::string* s) {
  const ColLayout l = layout_parse(*s);
  if (!l.ok || !layout_has_tk(l)) return;
  const std::string traktor_part = layout_traktor_part(l);
  {
    std::lock_guard<std::mutex> lock(g_store_mu);
    store_put(traktor_part, layout_text(layout_score_hidden(l).pairs));
  }
  *s = traktor_part;
}

// Incoming (from Traktor's settings file): TK's types put back where the user left them.
std::string cols_layout_in(const std::string& s) {
  const ColLayout l = layout_parse(s);
  if (!l.ok || layout_has_tk(l)) return s;
  std::string stored_text;
  bool known = false;
  {
    std::lock_guard<std::mutex> lock(g_store_mu);
    store_load();
    auto it = g_store.find(layout_text(l.pairs));
    if (it != g_store.end()) {
      stored_text = it->second;
      known = true;
    }
  }
  if (known) {
    const ColLayout stored = layout_parse(stored_text);
    return stored.ok ? layout_text(layout_score_hidden(stored).pairs) : s;
  }
  std::vector<std::pair<int, int>> pairs = l.pairs;  // unseen: TK's columns hidden at the end
  pairs.emplace_back(kColScore, -70);
  pairs.emplace_back(kColBpm, -kBpmMinW);
  pairs.emplace_back(kColKey, -kKeyMinW);
  pairs.emplace_back(kColDirShort, -160);
  pairs.emplace_back(kColDir, -260);
  return layout_text(pairs);
}

// The four settings functions, from the module's init (inside the hook transaction).
void cols_store_hook() {
  tkw_replace(WF(WF_A_tlc_getSettingsNormal), "TLC::getSettingsNormal", (void*)tk_get_normal,
              (void**)&g_orig_get_normal);
  tkw_replace(WF(WF_A_tlc_getSettingsHistory), "TLC::getSettingsHistory", (void*)tk_get_history,
              (void**)&g_orig_get_history);
  tkw_replace(WF(WF_A_tlc_setSettingsNormal), "TLC::setSettingsNormal", (void*)tk_set_normal,
              (void**)&g_orig_set_normal);
  tkw_replace(WF(WF_A_tlc_setSettingsHistory), "TLC::setSettingsHistory", (void*)tk_set_history,
              (void**)&g_orig_set_history);
}
