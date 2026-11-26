// tk_collection.mm — Traktor's whole collection, read from memory (included by tk_inject.mm).
//
// Walks the MediaItemRepository behind the main collection (ContentCollectionMgr+0x140) with
// Traktor's own forEachEntity, and resolves each track's file path through the EntryKeyManager
// (getURLFromKey, shared-locked). Writes one TSV row per track:
// (columns: see collection_dump). Strings are the MediaItem's std::strings; tabs/newlines become
// spaces.
// Read-only. Main thread only (the repository is not ours to read from another thread).
//
// Exported: tk_collection_dump(path) -> rows written, or -1. Line command: "colldump <file>".

#include <fstream>

// SmartPtr<MediaItem> as forEachEntity hands it to the callback: one raw pointer, non-trivial
// (so the std::function's operator() receives it by reference, like Traktor's own type). We only
// borrow it during the call: no addRef, no release — the caller owns the reference.
struct TKBorrowedPtr {
  void* raw;
  TKBorrowedPtr(const TKBorrowedPtr& o) : raw(o.raw) {}
  ~TKBorrowedPtr() {}
};

static std::string tsv_clean(std::string s) {
  for (char& c : s)
    if (c == '\t' || c == '\n' || c == '\r') c = ' ';
  return s;
}

// The file path of collection key `key`, or "" (not a file URL / unknown key).
static std::string path_of_key(uint32_t key) {
  void* ekm = gum_memory_is_readable((const void*)R(A_ekm_instance), 8)
                  ? *(void* const*)R(A_ekm_instance) : nullptr;
  if (!ekm || !gum_memory_is_readable(ekm, 0xd0)) return std::string();
  alignas(16) unsigned char url[URL_OBJ_SIZE];
  memset(url, 0, sizeof(url));
  call_sret2(R(A_ekm_getURLFromKey), ekm, &key, url);
  // URL = std::variant<monostate, boost::filesystem::path, std::string> (index at +0x18): both
  // non-empty alternatives hold one std::string at +0.
  const uint32_t kind = *(const uint32_t*)(url + 0x18);
  std::string path = (kind == 1 || kind == 2) ? decode_std_string(url) : std::string();
  ((void (*)(void*))R(A_url_dtor))(url);
  return path;
}

static int32_t read_i32_field(const void* item, size_t off) { return (int32_t)read_u32_field(item, off); }

// Calls f(track, key, path) for every collection track that has a file path. Returns false when
// the collection is not there. Main thread only.
template <typename F>
static bool for_each_track(const char* what, long& not_track, long& no_path, F f) {
  if (!pthread_main_np()) { tklog("%s: refused off the main thread", what); return false; }
  void* mgr = get_collection_mgr();
  if (!mgr) return false;
  void* repo = *(void**)((char*)mgr + OFF_CCM_REPO);
  if (!repo || !gum_memory_is_readable(repo, 0x48)) {
    tklog("%s: no MediaItemRepository at mgr+0x%x", what, OFF_CCM_REPO);
    return false;
  }
  std::function<void(TKBorrowedPtr)> each = [&](TKBorrowedPtr sp) {
    // A repository entity is a track only if it narrows to ContentEntryTrack (as getTrack does).
    const char* item = sp.raw && looks_like_object(sp.raw)
        ? (const char*)__dynamic_cast(sp.raw, R(A_ti_entity), R(A_ti_contententrytrack), 0) : nullptr;
    if (!item || !gum_memory_is_readable(item, OFF_CET_CUES + 0x10)) { not_track++; return; }
    const uint32_t key = read_u32_field(item, OFF_ENTRY_KEY);
    const std::string path = path_of_key(key);
    if (path.empty()) { no_path++; return; }
    f(item, key, path);
  };
  ((void (*)(void*, const void*))R(A_mir_forEachEntity))(repo, &each);
  return true;
}

// One row per collection track:
//   key path title artist album genre label comment comment2 bpm musical_key rating play_count
//   import_date release_date bitrate playtime
// bpm 0 = none; musical_key -1 = none (Traktor's 0-23 key index otherwise, as the NML's
// MUSICAL_KEY VALUE); rating raw 0-255, -1 = none; dates packed (year<<16 | month<<8 | day).
static long collection_dump(const char* file) {
  if (!file || !*file) return -1;
  std::ofstream out(file, std::ios::trunc);
  if (!out) { tklog("COLL: cannot write %s", file); return -1; }
  long rows = 0, no_path = 0, not_track = 0;
  const double t0 = CFAbsoluteTimeGetCurrent();
  const bool ok = for_each_track("COLL", not_track, no_path,
                                 [&](const char* item, uint32_t key, const std::string& path) {
    out << key << '\t' << tsv_clean(path);
    for (size_t off : {OFF_MI_TITLE, OFF_MI_ARTIST, OFF_MI_ALBUM, OFF_MI_GENRE, OFF_MI_LABEL,
                       OFF_MI_COMMENT, OFF_MI_COMMENT2})
      out << '\t' << tsv_clean(read_str_field(item, off));
    out << '\t' << *(const float*)(item + OFF_MI_BPM)
        << '\t' << read_i32_field(item, OFF_CET_KEYINDEX)
        << '\t' << read_i32_field(item, OFF_MI_RATING)
        << '\t' << read_u32_field(item, OFF_MI_PLAYCOUNT)
        << '\t' << read_u32_field(item, OFF_MI_IMPORTDATE)
        << '\t' << read_u32_field(item, OFF_MI_RELEASEDATE)
        << '\t' << read_i32_field(item, OFF_CET_BITRATE)
        << '\t' << read_i32_field(item, OFF_CET_PLAYTIME) << '\n';
    rows++;
  });
  if (!ok) return -1;
  tklog("COLL: %ld track(s) to %s in %.0f ms (%ld without a file path, %ld not tracks)", rows, file,
        (CFAbsoluteTimeGetCurrent() - t0) * 1000.0, no_path, not_track);
  return rows;
}

// Every track's cue points:
//   T <path> <bpm> <bpm quality>                    a track starts (quality as stored: 0-1)
//   C <type> <hotcue> <start ms> <length ms> <repeats> <name>
static long cues_dump(const char* file) {
  if (!file || !*file) return -1;
  std::ofstream out(file, std::ios::trunc);
  if (!out) { tklog("CUES: cannot write %s", file); return -1; }
  out.precision(17);
  long tracks = 0, cues = 0, no_path = 0, not_track = 0;
  const double t0 = CFAbsoluteTimeGetCurrent();
  const bool ok = for_each_track("CUES", not_track, no_path,
                                 [&](const char* item, uint32_t, const std::string& path) {
    out << "T\t" << tsv_clean(path) << '\t' << *(const float*)(item + OFF_MI_BPM) << '\t'
        << *(const float*)(item + OFF_MI_BPMQUALITY) << '\n';
    tracks++;
    const void* coll = item + OFF_CET_CUES;
    const int n = ((int (*)(const void*))R(A_cpc_getCueCount))(coll);
    for (int i = 0; i < n && i < 256; i++) {
      if (!(((uint32_t (*)(const void*, int))R(A_cpc_hasCue))(coll, i) & 0xffu)) continue;
      const char* cue = (const char*)((void* (*)(const void*, int))R(A_cpc_getCuePoint))(coll, i);
      if (!cue || !gum_memory_is_readable(cue, OFF_CUE_REPEATS + 4)) continue;
      const int32_t type = *(const int32_t*)(cue + OFF_CUE_TYPE);
      const int32_t slot = type == CUE_TYPE_GRID ? -1 : *(const int32_t*)(cue + OFF_CUE_HOTCUE);
      out << "C\t" << type << '\t' << slot << '\t'
          << *(const double*)(cue + OFF_CUE_START) << '\t'
          << 0 << '\t'  // length: not located in the stored element
          << *(const int32_t*)(cue + OFF_CUE_REPEATS) << '\t'
          << tsv_clean(decode_std_string(cue)) << '\n';
      cues++;
    }
  });
  if (!ok) return -1;
  tklog("CUES: %ld track(s), %ld cue(s) to %s in %.0f ms", tracks, cues, file,
        (CFAbsoluteTimeGetCurrent() - t0) * 1000.0);
  return tracks;
}

TK_EXPORT long tk_cues_dump(const char* file) { return cues_dump(file); }

TK_EXPORT long tk_collection_dump(const char* file) { return collection_dump(file); }

// ── The playlist tree ───────────────────────────────────────────────────────────────────────────
// Lines, depth-first in Traktor's order (what core.playlists.parse_playlists reads from the NML):
//   L <type 2|3> <folders joined by \x1f> <name>   a playlist (2) or smartlist (3) starts
//   Q <query>                                      a smartlist's saved search (NML QUERY text)
//   E <path>                                       a playlist entry, in order

static void dump_list_node(const char* node, int type, const std::string& folders, std::ostream& out,
                           long& lists, long& entries) {
  const std::string name = decode_std_string(node + OFF_SN_NAME);
  if (name.empty() || (type == 2 && name[0] == '$')) return;  // as the NML writer skips them
  out << "L\t" << type << '\t' << folders << '\t' << tsv_clean(name) << '\n';
  lists++;
  void* list = *(void* const*)(node + OFF_SN_LIST);
  if (!list || !looks_like_object(list)) return;
  if (type == 3) {
    void* smart = __dynamic_cast(list, R(A_ti_list), R(A_ti_contentlistsmart), 0);
    if (!smart) return;
    std::string query;
    ((int (*)(const void*, std::string*))R(A_query_print))((const char*)smart + OFF_CLS_FILTER, &query);
    out << "Q\t" << tsv_clean(query) << '\n';
    return;
  }
  void* cl = __dynamic_cast(list, R(A_ti_list), R(A_ti_contentlist), 0);
  if (!cl || !gum_memory_is_readable((const char*)cl + OFF_CL_ENTRIES, 16)) return;
  const char* it = *(const char* const*)((const char*)cl + OFF_CL_ENTRIES);
  const char* end = *(const char* const*)((const char*)cl + OFF_CL_ENTRIES + 8);
  if (!it || end < it || (size_t)(end - it) > (8u << 20)) return;
  for (; it < end; it += 8) {
    const uint32_t key = *(const uint32_t*)it;
    if (!key) continue;
    const std::string path = path_of_key(key);
    if (!path.empty()) { out << "E\t" << tsv_clean(path) << '\n'; entries++; }
  }
}

static void dump_structure(const char* node, const std::string& folders, bool root, std::ostream& out,
                           long& lists, long& entries, int depth) {
  if (!node || depth > 64 || !gum_memory_is_readable(node, OFF_SN_KIDS + 16)) return;
  const int type = *(const int*)node;
  if (type == 2 || type == 3) { dump_list_node(node, type, folders, out, lists, entries); return; }
  if (type != 1) return;  // search results and anything unknown: not in the NML's tree
  std::string inner = folders;
  if (!root) {
    const std::string name = tsv_clean(decode_std_string(node + OFF_SN_NAME));
    inner = folders.empty() ? name : folders + '\x1f' + name;
  }
  const char* sentinel = node + OFF_SN_KIDS;
  const char* e = *(const char* const*)(node + OFF_SN_KIDS + 8);
  for (int guard = 0; e && e != sentinel && guard < 100000; guard++) {
    if (!gum_memory_is_readable(e, 0x18)) break;
    dump_structure(*(const char* const*)(e + 0x10), inner, false, out, lists, entries, depth + 1);
    e = *(const char* const*)(e + 8);
  }
}

static long playlists_dump(const char* file) {
  if (!file || !*file) return -1;
  if (!pthread_main_np()) { tklog("PLAYLISTS: dump refused off the main thread"); return -1; }
  void* mgr = get_collection_mgr();
  if (!mgr) return -1;
  std::ofstream out(file, std::ios::trunc);
  if (!out) { tklog("PLAYLISTS: cannot write %s", file); return -1; }
  long lists = 0, entries = 0;
  const double t0 = CFAbsoluteTimeGetCurrent();
  dump_structure((const char*)mgr + OFF_CCM_STRUCTURE, std::string(), true, out, lists, entries, 0);
  tklog("PLAYLISTS: %ld list(s), %ld entr%s to %s in %.0f ms", lists, entries,
        entries == 1 ? "y" : "ies", file, (CFAbsoluteTimeGetCurrent() - t0) * 1000.0);
  return lists;
}

TK_EXPORT long tk_playlists_dump(const char* file) { return playlists_dump(file); }

static void handle_pldump_cmd(int fd, const char* line) {
  char file[512] = {0};
  if (sscanf(line, "%*s %511[^\n]", file) != 1) { cmd_reply(fd, "ERR usage: pldump <file>\n"); return; }
  std::string f(file);
  cmd_reply(fd, "OK\n");
  dispatch_async(dispatch_get_main_queue(), ^{ playlists_dump(f.c_str()); });
}

static void handle_cuedump_cmd(int fd, const char* line) {
  char file[512] = {0};
  if (sscanf(line, "%*s %511[^\n]", file) != 1) { cmd_reply(fd, "ERR usage: cuedump <file>\n"); return; }
  std::string f(file);
  cmd_reply(fd, "OK\n");
  dispatch_async(dispatch_get_main_queue(), ^{ cues_dump(f.c_str()); });
}

static void handle_colldump_cmd(int fd, const char* line) {
  char file[512] = {0};
  if (sscanf(line, "%*s %511[^\n]", file) != 1) { cmd_reply(fd, "ERR usage: colldump <file>\n"); return; }
  std::string f(file);
  cmd_reply(fd, "OK\n");
  dispatch_async(dispatch_get_main_queue(), ^{ collection_dump(f.c_str()); });
}
