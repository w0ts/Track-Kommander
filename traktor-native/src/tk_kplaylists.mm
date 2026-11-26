// tk_kplaylists.mm — TK's own playlists in Traktor's collection, by direct calls (User, 2026-11-24:
// inside Traktor TK never writes collection.nml; the Kommander date playlists used to be NML
// smartlists written before Traktor started). Included by tk_inject.mm. MAIN THREAD ONLY.
//
// A list is named by its StructurePath, Traktor's own "Kommander\Imported\imported-2026-07 to 09"
// (the form the NML's SORTING_INFO PATH uses; laid out as one std::string, as structure_path()
// and results_list() already rely on). ContentCollectionMgr does the work:
//   createFolder(StructurePath const&) -> bool, deleteList(StructurePath const&) -> bool (a
//   playlist or a smartlist), deleteFolder(StructurePath const&) -> bool; a playlist is filled the
//   way the Results list is (getList copy -> clear -> add -> commitList).
// Exports return 1 done, 0 refused by Traktor, -1 not possible here (no collection, off-thread).

static void* kpl_mgr(void) { return pthread_main_np() ? get_collection_mgr() : nullptr; }

TK_EXPORT int tk_pl_folder(const char* path) {
  void* mgr = path && *path ? kpl_mgr() : nullptr;
  if (!mgr) return -1;
  const std::string sp(path);
  const bool ok = ((bool (*)(void*, const std::string*))R(A_ccm_createFolder))(mgr, &sp);
  tklog("KPL: folder '%s' -> %d", path, (int)ok);
  return ok ? 1 : 0;
}

TK_EXPORT int tk_pl_delete(const char* path) {
  void* mgr = path && *path ? kpl_mgr() : nullptr;
  if (!mgr) return -1;
  const std::string sp(path);
  const bool ok = ((bool (*)(void*, const std::string*))R(A_ccm_deleteList))(mgr, &sp);
  tklog("KPL: delete '%s' -> %d", path, (int)ok);
  return ok ? 1 : 0;
}

TK_EXPORT int tk_pl_delete_folder(const char* path) {
  void* mgr = path && *path ? kpl_mgr() : nullptr;
  if (!mgr) return -1;
  const std::string sp(path);
  const bool ok = ((bool (*)(void*, const std::string*))R(A_ccm_deleteFolder))(mgr, &sp);
  tklog("KPL: delete folder '%s' -> %d", path, (int)ok);
  return ok ? 1 : 0;
}

// The playlist at <path> (created when missing) holds exactly <paths> (one file path per line),
// in that order. Returns how many were added (paths not in the collection are skipped), or -1.
TK_EXPORT int tk_pl_fill(const char* path, const char* paths) {
  void* mgr = path && *path && paths ? kpl_mgr() : nullptr;
  if (!mgr) return -1;
  std::string sp(path);
  void* lst = results_list(mgr, &sp);
  if (!lst) { tklog("KPL: could not get or create '%s'", path); return -1; }
  ((void (*)(void*))R(A_cl_clear))(lst);
  int added = 0, missing = 0;
  g_quiet_paths = true;
  for (const std::string& p : nl_lines(paths)) {
    void* track = p.empty() ? nullptr : get_track_by_path(mgr, p.c_str());
    if (!track && !p.empty()) {  // TK spells paths composed; Traktor may keep one decomposed
      const std::string d = nfd(p);
      if (d != p) track = get_track_by_path(mgr, d.c_str());
    }
    if (!track) { missing++; continue; }
    void* tsp = track;
    ((int (*)(void*, void* const*, int, bool, void*, int))R(A_cl_add))(lst, &tsp, -1, false, nullptr, 0);
    added++;
  }
  g_quiet_paths = false;
  void* lsp = lst;
  ((int (*)(void*, void* const*))R(A_ccm_commitList))(mgr, &lsp);
  ((void (*)(void*))R(A_refcount_release))(lst);
  tklog("KPL: '%s' <- %d track(s), %d not in the collection", path, added, missing);
  return added;
}
