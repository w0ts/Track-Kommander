// tkw_cues.cpp — hotcues in Traktor's collection, live (port of the Mac tk_cuewrite.mm and the cue
// dump of tk_collection.mm). MAIN THREAD ONLY.
//
// Inside Traktor, TK cannot write collection.nml (Traktor overwrites it on quit, and libtk never
// writes Traktor's files): cues go in the way Traktor's own "store hotcue" puts them in
// (TrackDeck::storeHotCue): ContentCollectionMgr::executeOnTrack runs a function on the stored
// track, which adds the CuePoint and assigns it a hotcue slot. A track loaded on a deck at that
// moment shows the change when it is loaded again (as on the Mac).
//
// Windows: CuePoint is 0x68 bytes (type = signed byte @0x38, slot @0x20, start @0x40, repeats
// @0x4c, name = MSVC std::string @0); the cues of a ContentEntryTrack are at +0x518;
// executeOnTrack takes an MSVC std::function<bool(ContentEntryTrack&)>.
//
// Exports (Mac signatures): tk_cue_write_path, tk_cue_remove_path, tk_cues_dump.
// Verbs: cuewrite <slot 0-7|-1> <start_ms> <type> <name|-> <path>, cuedump <file>.
#include <stdio.h>
#include <string.h>

#include <chrono>
#include <fstream>

#include "tkw_collection.h"

namespace {

const int kCueTypeGrid = 4;      // CUE_TYPE_GRID (ContentEntryTrack::addCue's grid test)
const int kAuthorDeck = 3;       // TK_UPDATE_AUTHOR_DECK: what storeHotCue passes
const size_t kCueBuf = 0x100;    // CuePoint is 0x68 on Windows
const size_t kMaxName = 22;      // the Mac's limit (the name string is freed by us below)

struct TKEntryTrack;  // ContentEntryTrack, only ever passed by reference
struct TypedKey {
  uint32_t key;
  uint32_t pad;
};

std::string tsv_clean(std::string s) {
  for (char& c : s)
    if (c == '\t' || c == '\n' || c == '\r') c = ' ';
  return s;
}

const void* cues_of(const void* track) { return (const char*)track + WOFF_CET_CUES; }

int cue_count(const void* coll) { return tkw_call<int>(WF(WF_A_cpc_getCueCount), coll); }

// The CuePoint at index i, or null (no cue there / unreadable).
const char* cue_at(const void* coll, int i) {
  if (!(tkw_call<uint32_t>(WF(WF_A_cpc_hasCue), coll, i) & 0xffu)) return nullptr;
  const char* cue = tkw_call<const char*>(WF(WF_A_cpc_getCuePoint), coll, i);
  return tkw_readable(cue, WOFF_CUE_REPEATS + 4) ? cue : nullptr;
}

int cue_type(const char* cue) { return *(const int8_t*)(cue + 0x38); }  // OFF_CUE_TYPE (byte)

bool execute_on_track(void* mgr, uint32_t key, const std::function<bool(TKEntryTrack&)>& fn) {
  const TypedKey tkey = {key, 0};
  return tkw_call<bool>(WF(WF_A_ccm_executeOnTrack), mgr, &tkey, &fn);
}

// Add a cue of `type` at `start_ms` named `name` to collection track `key`, as hotcue `slot`
// (0..7, -1 = no slot). Returns the cue's index in the track, or -1.
int cue_write(uint32_t key, int slot, double start_ms, int type, const char* name) {
  if (!tkw_is_main_thread()) { tklog("CUEWRITE: refused off the main thread"); return -1; }
  if (key == 0 || slot < -1 || slot > 7 || !(start_ms >= 0)) { tklog("CUEWRITE: bad arguments"); return -1; }
  if (name && strlen(name) > kMaxName) { tklog("CUEWRITE: name over %zu chars refused", kMaxName); return -1; }
  void* mgr = tkw_collection_mgr();
  if (!mgr) return -1;

  alignas(16) unsigned char cue[kCueBuf] = {};
  tkw_call<void*>(WF(WF_A_cue_ctor), (void*)cue);
  const std::string cue_name(name ? name : "");
  tkw_call<void>(WF(WF_A_cue_setName), (void*)cue, &cue_name);
  tkw_call<void>(WF(WF_A_cue_setType), (void*)cue, type);
  tkw_call<void>(WF(WF_A_cue_setStart), (void*)cue, start_ms);
  tkw_call<void>(WF(WF_A_cue_setLength), (void*)cue, 0.0);

  int idx = -1;
  std::function<bool(TKEntryTrack&)> fn = [&](TKEntryTrack& track) -> bool {
    idx = tkw_call<int>(WF(WF_A_cet_addCue), &track, (const void*)cue, false);
    if (idx < 0) return false;
    if (slot >= 0) tkw_call<void>(WF(WF_A_cet_assignHotCue), &track, idx, slot);
    tkw_call<void>(WF(WF_A_cet_setUpdateAuthor), &track, kAuthorDeck, true);
    return true;
  };
  const bool ok = execute_on_track(mgr, key, fn);
  // addCue copied the cue; its name may be on the heap (MSVC: over 15 chars). Same STL and heap
  // as Traktor, so our std::string destructor frees it.
  reinterpret_cast<std::string*>(cue)->~basic_string();
  tklog("CUEWRITE: key=%u slot=%d start=%.3f ms type=%d name='%s' -> ok=%d idx=%d", key, slot, start_ms, type,
        cue_name.c_str(), (int)ok, idx);
  return ok ? idx : -1;
}

// Remove from collection track `key` every hotcue (slot >= 1, not a grid marker) whose name starts
// with `prefix` ("tk: " = the ones TK detected). Returns how many went, or -1.
int cue_remove(uint32_t key, const char* prefix) {
  if (!tkw_is_main_thread()) { tklog("CUEREMOVE: refused off the main thread"); return -1; }
  if (key == 0 || !prefix || !*prefix) return -1;
  void* mgr = tkw_collection_mgr();
  if (!mgr) return -1;
  const size_t plen = strlen(prefix);
  int removed = 0;
  std::function<bool(TKEntryTrack&)> fn = [&](TKEntryTrack& track) -> bool {
    const void* coll = cues_of(&track);
    const int n = cue_count(coll);
    for (int i = (n < 256 ? n : 256) - 1; i >= 0; i--) {  // from the end: indices stay valid
      const char* cue = cue_at(coll, i);
      if (!cue || cue_type(cue) == kCueTypeGrid || *(const int32_t*)(cue + WOFF_CUE_HOTCUE) < 1) continue;
      std::string nm;
      if (!tkw_read_string(cue, &nm) || nm.compare(0, plen, prefix) != 0) continue;
      tkw_call<void>(WF(WF_A_cet_removeCue), &track, i);
      removed++;
    }
    if (removed) tkw_call<void>(WF(WF_A_cet_setUpdateAuthor), &track, kAuthorDeck, true);
    return removed > 0;
  };
  execute_on_track(mgr, key, fn);
  tklog("CUEREMOVE: key=%u prefix='%s' -> %d removed", key, prefix, removed);
  return removed;
}

// Every track's cue points:
//   T <path> <bpm> <bpm quality>                    a track starts (quality as stored: 0-1)
//   C <type> <hotcue> <start ms> <length ms> <repeats> <name>
long cues_dump(const char* file) {
  if (!file || !*file) return -1;
  std::ofstream out(tkw_widen(file).c_str(), std::ios::out | std::ios::trunc | std::ios::binary);
  if (!out) { tklog("CUES: cannot write %s", file); return -1; }
  out.precision(17);
  long tracks = 0, cues = 0, no_path = 0, not_track = 0;
  const auto t0 = std::chrono::steady_clock::now();
  const bool ok = tkw_collection_each_track("CUES", not_track, no_path,
                                            [&](const char* item, uint32_t, const std::string& path) {
    out << "T\t" << tsv_clean(path) << '\t' << *(const float*)(item + WOFF_MI_BPM) << '\t'
        << *(const float*)(item + WOFF_MI_BPMQUALITY) << '\n';
    tracks++;
    const void* coll = cues_of(item);
    const int n = cue_count(coll);
    for (int i = 0; i < n && i < 256; i++) {
      const char* cue = cue_at(coll, i);
      if (!cue) continue;
      const int type = cue_type(cue);
      const int32_t slot = type == kCueTypeGrid ? -1 : *(const int32_t*)(cue + WOFF_CUE_HOTCUE);
      std::string nm;
      tkw_read_string(cue, &nm);
      out << "C\t" << type << '\t' << slot << '\t' << *(const double*)(cue + WOFF_CUE_START) << '\t'
          << 0 << '\t'  // length: not located in the stored element (as on the Mac)
          << *(const int32_t*)(cue + WOFF_CUE_REPEATS) << '\t' << tsv_clean(nm) << '\n';
      cues++;
    }
  });
  if (!ok) return -1;
  tklog("CUES: %ld track(s), %ld cue(s) to %s in %.0f ms", tracks, cues, file,
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count());
  return tracks;
}

// "cuewrite <slot> <start_ms> <type> <name|-> <path>": one cue by hand (results in the log);
// '_' in the name stands for a space.
std::string cmd_cuewrite(const std::string& a, const std::vector<std::string>&) {
  int slot = -1, type = 0, used = 0;
  double start = -1;
  char name[32] = {0};
  if (sscanf_s(a.c_str(), "%d %lf %d %31s %n", &slot, &start, &type, name, (unsigned)sizeof(name), &used) < 4 || used == 0 ||
      (size_t)used >= a.size())
    return "ERR usage: cuewrite <slot 0-7|-1> <start_ms> <type> <name|-> <path>";
  const std::string path = a.substr((size_t)used);
  std::string nm(strcmp(name, "-") == 0 ? "" : name);
  for (char& c : nm)
    if (c == '_') c = ' ';
  tkw_on_main([slot, start, type, nm, path] {
    const uint32_t key = tkw_collection_key_of_path(path.c_str());
    if (key == 0) { tklog("CUEWRITE: not in the collection: %s", path.c_str()); return; }
    cue_write(key, slot, start, type, nm.c_str());
  });
  return "OK";
}

std::string cmd_cuedump(const std::string& a, const std::vector<std::string>&) {
  if (a.empty()) return "ERR usage: cuedump <file>";
  tkw_on_main([a] { cues_dump(a.c_str()); });
  return "OK";
}

}  // namespace

// For TK (ctypes, on Traktor's main thread): by file path, as TK knows tracks.
// Main thread first: the path lookup is Traktor's own EntryKeyManager/URL code.
TKW_EXPORT int tk_cue_write_path(const char* path, int slot, double start_ms, int type, const char* name) {
  if (!tkw_is_main_thread()) { tklog("CUEWRITE: refused off the main thread"); return -1; }
  if (!(start_ms >= 0)) { tklog("CUEWRITE: bad start %f refused", start_ms); return -1; }  // NaN too
  const uint32_t key = tkw_collection_key_of_path(path);
  if (key == 0) { tklog("CUEWRITE: not in the collection: %s", path ? path : "(null)"); return -1; }
  return cue_write(key, slot, start_ms, type, name);
}

TKW_EXPORT int tk_cue_remove_path(const char* path, const char* prefix) {
  if (!tkw_is_main_thread()) { tklog("CUEREMOVE: refused off the main thread"); return -1; }
  const uint32_t key = tkw_collection_key_of_path(path);
  if (key == 0) return -1;
  return cue_remove(key, prefix);
}

TKW_EXPORT long tk_cues_dump(const char* file) { return cues_dump(file); }

void tkw_mod_cues_init() {
  tkw_command("cuewrite", cmd_cuewrite);
  tkw_command("cuedump", cmd_cuedump);
}
