// tkw_playlists.cpp — TK's playlists in Traktor's collection, by direct calls (port of the Mac
// tk_kplaylists.mm and the Results playlist of tk_inject.mm). MAIN THREAD ONLY. Memory only:
// inside Traktor TK never writes collection.nml (User, 2026-11-24).
//
// A list is named by its StructurePath, Traktor's own "Kommander\Imported\imported-2026-07 to 09"
// (the form the NML's SORTING_INFO PATH uses). On Windows too a StructurePath is one std::string
// (its ctor 0xcf27d0 copies the string and trims '\'), so a std::string is passed for it.
// ContentCollectionMgr does the work: createFolder / deleteList / deleteFolder(StructurePath
// const&) -> bool; a playlist is filled the way Traktor edits lists: getList (a copy) -> clear ->
// add -> commitList.
//
// TK's results: the `list` verb (paths to EOF) fills the real playlist "Results" inside the
// user's "Kommander" playlist folder; the band shows it when TK's tree node is clicked.
// Tree paths are "<PREFIX>\a<folder>\a<name>"; BrowserForm::getStructurePathFromTreePath maps them
// to the StructurePath.
//
// Exports (Mac signatures): tk_pl_folder, tk_pl_delete, tk_pl_delete_folder -> 1 done, 0 refused
// by Traktor, -1 not possible here; tk_pl_fill -> tracks added or -1.
#include <chrono>

#include "tkw_collection.h"

void* tkw_band_stdtree();                         // band module: the live BrowserStdTree, or null
extern "C" void tk_band_status(const char* text);  // band module (any thread)

namespace {

const char kPlFolder[] = "Kommander";
const char kPlLegacyFolder[] = "Track Kommander";
const size_t kMaxListPaths = 5000;  // as the Mac's list reader

std::string g_pl_treepath;              // tree path of the Results playlist, once known
std::vector<std::string> g_list_paths;  // what TK last sent
std::vector<std::string> g_list_rows;   // the paths that became the playlist's entries, in order
unsigned g_list_gen = 0;                // bumped whenever TK's list arrives

std::vector<std::string> nl_lines(const char* text) {
  std::vector<std::string> out;
  std::string cur;
  for (const char* p = text; p && *p; p++) {
    if (*p == '\n') { out.push_back(cur); cur.clear(); }
    else if (*p != '\r') cur += *p;
  }
  if (!cur.empty()) out.push_back(cur);
  return out;
}

std::string printable_path(const std::string& p) {
  std::string out(p);
  for (char& c : out)
    if (c == '\a') c = '|';
  return out;
}

// Tree path keys of BrowserStdTree's path -> TreeNodeInfo map (MSVC std::map at +0x6e0: head node
// pointer; nodes {left@0, parent@8, right@0x10, isnil@0x19, key std::string@0x20}).
std::vector<std::string> tree_path_keys() {
  std::vector<std::string> keys;
  char* tree = (char*)tkw_band_stdtree();
  if (!tree || !tkw_readable(tree + WOFF_BST_PATHMAP, 16)) return keys;
  const char* head = *(const char* const*)(tree + WOFF_BST_PATHMAP);
  if (!tkw_readable(head, 0x40) || !head[0x19]) return keys;
  auto nil = [](const char* n) { return !tkw_readable(n, 0x40) || n[0x19] != 0; };
  const char* it = *(const char* const*)head;  // leftmost
  for (int guard = 0; !nil(it) && guard < 20000; guard++) {
    std::string k;
    if (tkw_read_string(it + 0x20, &k)) keys.push_back(k);
    const char* right = *(const char* const*)(it + 0x10);
    if (!nil(right)) {
      it = right;
      while (!nil(*(const char* const*)it)) it = *(const char* const*)it;
      continue;
    }
    const char* parent = *(const char* const*)(it + 8);
    while (!nil(parent) && *(const char* const*)(parent + 0x10) == it) {
      it = parent;
      parent = *(const char* const*)(it + 8);
    }
    it = parent;
  }
  return keys;
}

// getStructurePathFromTreePath(this, sret StructurePath, std::string const&).
bool structure_path(const std::string& treepath, std::string* out) {
  void* bf = tkw_browserform();
  if (!bf) return false;
  alignas(16) unsigned char sret[0x40] = {};
  tkw_call<void*>(WF(WF_A_bf_getStructPathFromTreePath), bf, (void*)sret, &treepath);
  std::string* sp = reinterpret_cast<std::string*>(sret);
  *out = *sp;
  sp->~basic_string();  // same STL and heap as Traktor
  return !out->empty();
}

// Once: the playlist folder TK made as "Track Kommander" is now just "Kommander" (User,
// 2026-11-24). Rename it the way the tree's own rename does, unless "Kommander" already exists.
void migrate_pl_folder() {
  static bool done = false;
  void* bf = tkw_browserform();
  if (done || !bf) return;
  const std::vector<std::string> keys = tree_path_keys();
  if (keys.empty()) return;  // the tree's path map fills in after startup: try again later
  done = true;
  std::string legacy;
  for (const std::string& k : keys) {
    const size_t cut = k.rfind('\a');
    if (cut == std::string::npos || k.rfind("$PLAYLIST", 0) != 0) continue;
    const std::string last = k.substr(cut + 1);
    if (last == kPlFolder && k.find('\a') == cut) { tklog("PLAYLIST: '%s' folder already there", kPlFolder); return; }
    if (last == kPlLegacyFolder && k.find('\a') == cut) legacy = k;
  }
  if (legacy.empty()) return;
  std::string sp;
  if (!structure_path(legacy, &sp)) { tklog("PLAYLIST: no structure path for the old folder"); return; }
  const std::string to(kPlFolder);
  const int ok = tkw_call<int>(WF(WF_A_bf_renamePlaylist), bf, &sp, &to);
  g_pl_treepath.clear();
  tklog("PLAYLIST: renamed folder '%s' -> '%s': %s", kPlLegacyFolder, kPlFolder, (ok & 1) ? "ok" : "refused");
}

// The Results playlist's tree path: an existing one, else "<Kommander folder>\aResults". When the
// tree's index has no Kommander folder (first Windows run, 2026-11-25: TK had just created it,
// and the index never listed it in that session), the path is built from the prefix of the
// playlists' own keys ("$PLAYLIST...\a<top folder>").
std::string results_tree_path() {
  if (!g_pl_treepath.empty()) return g_pl_treepath;
  migrate_pl_folder();
  std::string folder, playlists_prefix;
  const std::vector<std::string> keys = tree_path_keys();
  tklog("PLAYLIST: %zu tree path(s) known", keys.size());
  for (const std::string& k : keys) {
    const size_t cut = k.rfind('\a');
    const std::string last = cut == std::string::npos ? k : k.substr(cut + 1);
    if (last == "Results" && k.find(std::string("\a") + kPlFolder + "\a") != std::string::npos) {
      g_pl_treepath = k;
      tklog("PLAYLIST: found '%s'", printable_path(k).c_str());
      return k;
    }
    if (last == kPlFolder && k.find('\a') != std::string::npos) folder = k;
    if (playlists_prefix.empty() && k.rfind("$PLAYLIST", 0) == 0 && cut != std::string::npos)
      playlists_prefix = k.substr(0, k.find('\a'));
  }
  if (!folder.empty()) return folder + "\aResults";
  if (playlists_prefix.empty()) {
    tklog("PLAYLIST: no '%s' playlist folder and no playlist key in the tree index", kPlFolder);
    return "";
  }
  const std::string built = playlists_prefix + "\a" + kPlFolder + "\aResults";
  tklog("PLAYLIST: no '%s' folder in the tree index: using '%s'", kPlFolder, printable_path(built).c_str());
  return built;
}

// The Results playlist's StructurePath for <tp>; when Traktor cannot map the tree path (a folder
// its index does not list yet), the collection path itself, with the folder created first.
std::string results_structure_path(void* mgr, const std::string& tp) {
  std::string sp;
  if (!tp.empty() && structure_path(tp, &sp) && !sp.empty()) return sp;
  const std::string folder(kPlFolder);
  const bool made = tkw_call<bool>(WF(WF_A_ccm_createFolder), mgr, &folder);
  sp = folder + "\\Results";
  tklog("PLAYLIST: Results by collection path '%s' (folder %s)", sp.c_str(), made ? "created" : "already there");
  return sp;
}

void release(void* refcounted) { tkw_call<void>(WF(WF_A_refcount_release), refcounted); }

// The playlist's ContentList (a copy we own one reference of), creating the playlist if missing.
// getList / createPlaylist return SmartPtrs through rdx.
void* results_list(void* mgr, const std::string& sp) {
  void* lst = nullptr;
  tkw_call<void*>(WF(WF_A_ccm_getList), mgr, (void*)&lst, &sp);
  if (lst) return lst;
  tklog("PLAYLIST: creating '%s'", sp.c_str());
  void* created = nullptr;
  tkw_call<void*>(WF(WF_A_ccm_createPlaylist), mgr, (void*)&created, &sp, false);
  tklog("PLAYLIST: createPlaylist -> %p", created);
  if (created) release(created);
  tkw_call<void*>(WF(WF_A_ccm_getList), mgr, (void*)&lst, &sp);
  return lst;
}

// The list holds exactly the collection tracks at <paths> (unknown paths skipped), committed.
// rows (optional) gets the paths that became entries. Returns how many were added.
int fill_list(void* mgr, void* lst, const std::vector<std::string>& paths, bool try_nfd,
              std::vector<std::string>* rows, int* missing) {
  tkw_call<int>(WF(WF_A_cl_clear), lst);
  int added = 0;
  *missing = 0;
  tkw_collection_quiet_paths(true);
  for (const std::string& p : paths) {
    void* track = p.empty() ? nullptr : tkw_collection_track_at(mgr, p.c_str());
    if (!track && try_nfd && !p.empty()) {  // TK spells paths composed; Traktor may keep one decomposed
      const std::string d = tkw_collection_nfd(p);
      if (d != p) track = tkw_collection_track_at(mgr, d.c_str());
    }
    if (!track) { (*missing)++; continue; }
    TkwRef ref(track);  // ours (getTrack); the list keeps entry keys, add() takes what it needs
    void* tsp = track;  // SmartPtr<Entity const> const& = the raw pointer
    tkw_call<int>(WF(WF_A_cl_add), lst, (void* const*)&tsp, -1, false, (void*)nullptr, 0);
    if (rows) rows->push_back(p);
    added++;
  }
  tkw_collection_quiet_paths(false);
  void* lsp = lst;
  tkw_call<int>(WF(WF_A_ccm_commitList), mgr, (void* const*)&lsp);
  release(lst);
  return added;
}

void do_playlist_set(const std::vector<std::string>& paths) {
  if (g_tkw_quitting) return;
  const auto t0 = std::chrono::steady_clock::now();
  const std::string tp = results_tree_path();
  void* mgr = tkw_collection_mgr();
  if (!mgr) return;
  const std::string sp = results_structure_path(mgr, tp);
  void* lst = results_list(mgr, sp);
  if (!lst) { tklog("PLAYLIST: could not get or create '%s'", sp.c_str()); return; }
  if (!tp.empty()) g_pl_treepath = tp;
  g_list_rows.clear();
  int missing = 0;
  const int added = fill_list(mgr, lst, paths, false, &g_list_rows, &missing);
  tklog("PLAYLIST: '%s' <- %d track(s), %d not in the collection (committed) in %.0f ms", sp.c_str(), added,
        missing, std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count());
}

void do_list_set(const std::vector<std::string>& paths) {
  g_list_gen++;
  tk_band_status("");
  if (paths == g_list_paths) { tklog("LIST: unchanged (%zu), kept", paths.size()); return; }
  g_list_paths = paths;
  do_playlist_set(paths);
}

// "list" + one path per line to EOF: "OK <count>" at once, the playlist right after.
std::string cmd_list(const std::string&, const std::vector<std::string>& body) {
  std::vector<std::string> paths(body.begin(), body.size() > kMaxListPaths ? body.begin() + kMaxListPaths : body.end());
  tklog("CMD list: %zu path(s)", paths.size());
  const std::string reply = "OK " + std::to_string(paths.size());
  tkw_on_main([paths] { do_list_set(paths); });
  return reply;
}

void* kpl_mgr(const char* path) {
  if (!path || !*path) return nullptr;
  if (!tkw_is_main_thread()) { tklog("KPL: refused off the main thread"); return nullptr; }
  return tkw_collection_mgr();
}

// createFolder / deleteList / deleteFolder (this, StructurePath const&) -> bool.
int structure_call(const char* what, int fn, const char* path) {
  void* mgr = kpl_mgr(path);
  if (!mgr) return -1;
  const std::string sp(path);
  const bool ok = tkw_call<bool>(WF(fn), mgr, &sp);
  tklog("KPL: %s '%s' -> %d", what, path, (int)ok);
  return ok ? 1 : 0;
}

}  // namespace

TKW_EXPORT int tk_pl_folder(const char* path) { return structure_call("folder", WF_A_ccm_createFolder, path); }

TKW_EXPORT int tk_pl_delete(const char* path) { return structure_call("delete", WF_A_ccm_deleteList, path); }

TKW_EXPORT int tk_pl_delete_folder(const char* path) {
  return structure_call("delete folder", WF_A_ccm_deleteFolder, path);
}

// The playlist at <path> (created when missing) holds exactly <paths> (one file path per line),
// in that order. Returns how many were added (paths not in the collection are skipped), or -1.
TKW_EXPORT int tk_pl_fill(const char* path, const char* paths) {
  void* mgr = paths ? kpl_mgr(path) : nullptr;
  if (!mgr) return -1;
  void* lst = results_list(mgr, std::string(path));
  if (!lst) { tklog("KPL: could not get or create '%s'", path); return -1; }
  int missing = 0;
  const int added = fill_list(mgr, lst, nl_lines(paths), true, nullptr, &missing);
  tklog("KPL: '%s' <- %d track(s), %d not in the collection", path, added, missing);
  return added;
}

// ---- for the band and columns modules (tkw_collection.h) ---------------------------------------
std::string tkw_playlists_results_tree_path() { return results_tree_path(); }
std::string tkw_playlists_results_known_path() { return g_pl_treepath; }
unsigned tkw_playlists_list_gen() { return g_list_gen; }
const std::vector<std::string>& tkw_playlists_rows() { return g_list_rows; }
bool tkw_playlists_structure_path(const std::string& treepath, std::string* out) {
  return structure_path(treepath, out);
}

// A click on TK's tree node: the playlist goes blank (not stale) until TK's fresh list lands, and
// the next list from TK must not be skipped as "unchanged".
void tkw_playlists_results_clear() {
  do_playlist_set(std::vector<std::string>());
  g_list_paths.clear();
}

// `treepaths [text]`: the tree index's keys containing <text> (all when empty), at most 40, with
// \a shown as " > " — to see how Traktor spells a folder before trusting a lookup.
static std::string cmd_treepaths(const std::string& a, const std::vector<std::string>&) {
  const std::vector<std::string> keys = tree_path_keys();
  std::string out = std::to_string(keys.size()) + " key(s):";
  int shown = 0;
  for (const std::string& k : keys) {
    if (!a.empty() && k.find(a) == std::string::npos) continue;
    if (++shown > 40) break;
    std::string p;
    for (char c : k) p += c == '\a' ? std::string(" > ") : std::string(1, c);
    out += " | " + p;
  }
  return out;
}

void tkw_mod_playlists_init() {
  tkw_command("list", cmd_list, true);
  tkw_command("treepaths", cmd_treepaths);
}
