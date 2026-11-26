// tkw_collection.h — the library side of libtk.dll (collection, loads, playlists, hotcues), for
// the modules that build on it (props: tk_load_path; band: the Results playlist; columns: the
// list's rows). Everything here runs on Traktor's main thread only.
#pragma once
#include <stdint.h>

#include <functional>
#include <string>
#include <vector>

#include "tkw.h"

// ---- collection (tkw_collection.cpp) ---------------------------------------------------------
void* tkw_collection_mgr();                              // ContentCollectionMgr*, or null
// Tracks: the caller owns one reference (getTrack's SmartPtr) — hold them in a TkwRef.
void* tkw_collection_track(void* mgr, uint32_t key);     // ContentEntryTrack* (MediaItem at +0)
void* tkw_collection_track_at(void* mgr, const char* path);
void tkw_collection_release(void* refcounted);           // RefCount::release (SmartPtr's dtor)
struct TkwRef {  // one owned reference, released at scope end
  explicit TkwRef(void* p = nullptr) : p_(p) {}
  ~TkwRef() { tkw_collection_release(p_); }
  TkwRef(const TkwRef&) = delete;
  TkwRef& operator=(const TkwRef&) = delete;
  void* get() const { return p_; }

 private:
  void* p_;
};
uint32_t tkw_collection_key_of_path(const char* path);   // 0: not in the collection
std::string tkw_collection_path_of_key(uint32_t key);    // UTF-8, "" when unknown / not a file
bool tkw_collection_load_path(int deck, const char* path);  // false: no mixer/track (logged)
void tkw_collection_quiet_paths(bool quiet);             // no per-path log while true
std::string tkw_collection_nfd(const std::string& utf8);  // Unicode NFD (as the Mac retries)
// __RTDynamicCast through Traktor's own import thunk; src/dst are WF_A_ti_* entries.
void* tkw_collection_cast(const void* obj, int src_ti, int dst_ti);
bool tkw_collection_is_object(const void* p);            // readable, with a readable vtable
// Each collection track with a file path: f(track, key, path). false: no collection here.
template <typename F>
bool tkw_collection_each_track(const char* what, long& not_track, long& no_path, F f);
std::wstring tkw_widen(const std::string& utf8);
std::string tkw_narrow(const std::wstring& wide);
void tkw_collection_dump_init();                          // tkw_collection_dump.cpp: verbs

// ---- playlists (tkw_playlists.cpp) -----------------------------------------------------------
std::string tkw_playlists_results_tree_path();           // finds/creates the path (may be "")
std::string tkw_playlists_results_known_path();          // the path once known, else ""
void tkw_playlists_results_clear();                      // a click on TK's node: empty, re-arm
unsigned tkw_playlists_list_gen();                       // bumped whenever TK's list arrives
const std::vector<std::string>& tkw_playlists_rows();    // the paths that became entries
bool tkw_playlists_structure_path(const std::string& treepath, std::string* out);

// ---- the walk behind tkw_collection_each_track (defined here: it is a template) ---------------

// SmartPtr<MediaItem> as forEachEntity hands it to the callback: one raw pointer. MSVC's
// std::function passes it to _Do_call by address and Traktor releases it after the call, so we
// only borrow it: no addRef, no release.
struct TkwBorrowedPtr {
  void* raw;
  TkwBorrowedPtr(const TkwBorrowedPtr& o) : raw(o.raw) {}
  ~TkwBorrowedPtr() {}
};
bool tkw_collection_for_each_entity(const char* what, const std::function<void(TkwBorrowedPtr)>& each);
void* tkw_collection_track_of_entity(void* raw);  // ContentEntryTrack* or null (not a track)

template <typename F>
bool tkw_collection_each_track(const char* what, long& not_track, long& no_path, F f) {
  std::function<void(TkwBorrowedPtr)> each = [&](TkwBorrowedPtr sp) {
    const char* item = (const char*)tkw_collection_track_of_entity(sp.raw);
    if (!item) { not_track++; return; }
    const uint32_t key = *(const uint32_t*)(item + WOFF_ENTRY_KEY);
    const std::string path = tkw_collection_path_of_key(key);
    if (path.empty()) { no_path++; return; }
    f(item, key, path);
  };
  return tkw_collection_for_each_entity(what, each);
}
