// tkw_collection_dump.cpp — Traktor's whole collection and playlist tree, read from memory (port of
// the Mac tk_collection.mm; the cue dump is in tkw_cues.cpp). Read-only, main thread only.
//
// Exports (Mac signatures): tk_collection_dump(file), tk_playlists_dump(file) -> rows / lists
// written, or -1. Verbs: colldump <file>, pldump <file> (results go to the log).
// The files are UTF-8 TSV; <file> is a UTF-8 path.
#include <chrono>
#include <fstream>

#include "tkw_collection.h"

namespace {

std::string tsv_clean(std::string s) {
  for (char& c : s)
    if (c == '\t' || c == '\n' || c == '\r') c = ' ';
  return s;
}

std::string str_at(const void* p, size_t off) {
  std::string s;
  tkw_read_string((const char*)p + off, &s);
  return s;
}

int32_t i32_at(const void* p, size_t off) { return *(const int32_t*)((const char*)p + off); }

double ms_since(std::chrono::steady_clock::time_point t0) {
  return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
}

bool open_out(std::ofstream& out, const char* file, const char* what) {
  if (!file || !*file) return false;
  out.open(tkw_widen(file).c_str(), std::ios::out | std::ios::trunc | std::ios::binary);
  if (!out) tklog("%s: cannot write %s", what, file);
  return (bool)out;
}

// One row per collection track:
//   key path title artist album genre label comment comment2 bpm musical_key rating play_count
//   import_date release_date bitrate playtime
// bpm 0 = none; musical_key -1 = none (Traktor's 0-23 key index otherwise); rating raw 0-255,
// -1 = none; dates packed (year<<16 | month<<8 | day).
long collection_dump(const char* file) {
  std::ofstream out;
  if (!open_out(out, file, "COLL")) return -1;
  long rows = 0, no_path = 0, not_track = 0;
  const auto t0 = std::chrono::steady_clock::now();
  const size_t strs[] = {WOFF_MI_TITLE, WOFF_MI_ARTIST, WOFF_MI_ALBUM, WOFF_MI_GENRE,
                         WOFF_MI_LABEL, WOFF_MI_COMMENT, WOFF_MI_COMMENT2};
  const bool ok = tkw_collection_each_track("COLL", not_track, no_path,
                                            [&](const char* item, uint32_t key, const std::string& path) {
    out << key << '\t' << tsv_clean(path);
    for (size_t off : strs) out << '\t' << tsv_clean(str_at(item, off));
    out << '\t' << *(const float*)(item + WOFF_MI_BPM) << '\t' << i32_at(item, WOFF_CET_KEYINDEX) << '\t'
        << i32_at(item, WOFF_MI_RATING) << '\t' << (uint32_t)i32_at(item, WOFF_MI_PLAYCOUNT) << '\t'
        << (uint32_t)i32_at(item, WOFF_MI_IMPORTDATE) << '\t' << (uint32_t)i32_at(item, WOFF_MI_RELEASEDATE)
        << '\t' << i32_at(item, WOFF_CET_BITRATE) << '\t' << i32_at(item, WOFF_CET_PLAYTIME) << '\n';
    rows++;
  });
  if (!ok) return -1;
  tklog("COLL: %ld track(s) to %s in %.0f ms (%ld without a file path, %ld not tracks)", rows, file,
        ms_since(t0), no_path, not_track);
  return rows;
}

// ── The playlist tree ───────────────────────────────────────────────────────────────────────────
// Lines, depth-first in Traktor's order (what core.playlists.parse_playlists reads from the NML):
//   L <type 2|3> <folders joined by \x1f> <name>   a playlist (2) or smartlist (3) starts
//   Q <query>                                      a smartlist's saved search (NML QUERY text)
//   E <path>                                       a playlist entry, in order
// StructureNode (Windows): int type @0, std::string name @8, List SmartPtr @0x30, children =
// MSVC std::list whose head node pointer is @0x48 (node {next@0, prev@8, value@0x10}).
const size_t kSnName = 0x08, kSnList = WOFF_SN_LIST, kSnKids = 0x48, kClEntries = WOFF_CL_ENTRIES;

struct TreeDump {
  std::ostream& out;
  long lists = 0, entries = 0;
};

void dump_smart_query(const char* list, TreeDump& d) {
  void* smart = tkw_collection_cast(list, WF_A_ti_list, WF_A_ti_contentlistsmart);
  if (!smart) return;
  std::string query;  // MSVC std::string, as SearchExpressionPrinter::print takes it
  tkw_call<bool>(WF(WF_A_query_print), (const void*)((const char*)smart + WOFF_CLS_FILTER), &query);
  d.out << "Q\t" << tsv_clean(query) << '\n';
}

void dump_entries(const char* list, TreeDump& d) {
  const char* cl = (const char*)tkw_collection_cast(list, WF_A_ti_list, WF_A_ti_contentlist);
  if (!cl || !tkw_readable(cl + kClEntries, 16)) return;
  const char* it = *(const char* const*)(cl + kClEntries);
  const char* end = *(const char* const*)(cl + kClEntries + 8);
  if (!it || end < it || (size_t)(end - it) > (8u << 20) || !tkw_readable(it, (size_t)(end - it))) return;
  for (; it < end; it += 8) {  // 8-byte entries, the collection key first
    const uint32_t key = *(const uint32_t*)it;
    if (!key) continue;
    const std::string path = tkw_collection_path_of_key(key);
    if (path.empty()) continue;
    d.out << "E\t" << tsv_clean(path) << '\n';
    d.entries++;
  }
}

void dump_list_node(const char* node, int type, const std::string& folders, TreeDump& d) {
  const std::string name = str_at(node, kSnName);
  if (name.empty() || (type == 2 && name[0] == '$')) return;  // as the NML writer skips them
  d.out << "L\t" << type << '\t' << folders << '\t' << tsv_clean(name) << '\n';
  d.lists++;
  const char* list = *(const char* const*)(node + kSnList);
  if (!tkw_collection_is_object(list)) return;
  if (type == 3) dump_smart_query(list, d);
  else dump_entries(list, d);
}

void dump_structure(const char* node, const std::string& folders, bool root, TreeDump& d, int depth) {
  if (!node || depth > 64 || !tkw_readable(node, kSnKids + 16)) return;
  const int type = *(const int*)node;
  if (type == 2 || type == 3) { dump_list_node(node, type, folders, d); return; }
  if (type != 1) return;  // search results and anything unknown: not in the NML's tree
  std::string inner = folders;
  if (!root) {
    const std::string name = tsv_clean(str_at(node, kSnName));
    inner = folders.empty() ? name : folders + '\x1f' + name;
  }
  const char* head = *(const char* const*)(node + kSnKids);
  if (!tkw_readable(head, 0x18)) return;
  const char* e = *(const char* const*)head;
  for (int guard = 0; e && e != head && guard < 100000; guard++) {
    if (!tkw_readable(e, 0x18)) break;
    dump_structure(*(const char* const*)(e + 0x10), inner, false, d, depth + 1);
    e = *(const char* const*)e;
  }
}

long playlists_dump(const char* file) {
  if (!file || !*file) return -1;
  if (!tkw_is_main_thread()) { tklog("PLAYLISTS: dump refused off the main thread"); return -1; }
  void* mgr = tkw_collection_mgr();
  if (!mgr) return -1;
  std::ofstream out;
  if (!open_out(out, file, "PLAYLISTS")) return -1;
  TreeDump d{out};
  const auto t0 = std::chrono::steady_clock::now();
  dump_structure((const char*)mgr + 0x180, std::string(), true, d, 0);  // OFF_CCM_STRUCTURE (Win 0x180)
  tklog("PLAYLISTS: %ld list(s), %ld entr%s to %s in %.0f ms", d.lists, d.entries,
        d.entries == 1 ? "y" : "ies", file, ms_since(t0));
  return d.lists;
}

// "<verb> <file>": OK at once, the dump right after (results in the log), as on the Mac.
TkwCmd dump_verb(const char* verb, long (*dump)(const char*)) {
  const std::string usage = std::string("ERR usage: ") + verb + " <file>";
  return [usage, dump](const std::string& a, const std::vector<std::string>&) -> std::string {
    if (a.empty()) return usage;
    tkw_on_main([a, dump] { dump(a.c_str()); });
    return "OK";
  };
}

}  // namespace

TKW_EXPORT long tk_collection_dump(const char* file) { return collection_dump(file); }

TKW_EXPORT long tk_playlists_dump(const char* file) { return playlists_dump(file); }

void tkw_collection_dump_init() {
  tkw_command("colldump", dump_verb("colldump", collection_dump));
  tkw_command("pldump", dump_verb("pldump", playlists_dump));
}
