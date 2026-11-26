// tk_cuewrite.mm — write a hotcue into Traktor's collection, live (included by tk_inject.mm).
//
// Inside Traktor, TK's hotcue detection cannot write collection.nml (Traktor overwrites it when it
// quits), so cues go in the way Traktor's own "store hotcue" puts them in (TrackDeck::storeHotCue,
// 2026-11-24): ContentCollectionMgr::executeOnTrack runs a function on the stored track, which
// adds the CuePoint and assigns it a hotcue slot. MAIN THREAD ONLY.
//
// Stage 1 (2026-11-24, User-checked): one cue by hand ("cuewrite") shows on the deck once loaded and
// is in collection.nml after a real quit. Stage 2: TK's hotcue page writes through
// tk_cue_write_path / tk_cue_remove_path. Not handled: a track loaded on a deck at that moment
// (storeHotCue also swaps the deck's own cue copy) shows the change when it is loaded again.

struct TKEntryTrack;  // ContentEntryTrack, only ever passed by reference
struct TKTypedKey { uint32_t key; uint32_t pad; };  // TypedEntryKey<unsigned>, as getTrack takes it

// Add a cue of `type` at `start_ms` named `name` to collection track `key`, as hotcue `slot`
// (0..7, -1 = no slot). Returns the cue's index in the track, or -1.
static int cue_write(uint32_t key, int slot, double start_ms, int type, const char* name) {
  if (!pthread_main_np()) { tklog("CUEWRITE: refused off the main thread"); return -1; }
  if (key == 0 || slot < -1 || slot > 7 || start_ms < 0) { tklog("CUEWRITE: bad arguments"); return -1; }
  if (name && strlen(name) > 22) { tklog("CUEWRITE: name over 22 chars refused (no CuePoint dtor)"); return -1; }
  void* mgr = get_collection_mgr();
  if (!mgr) return -1;

  alignas(16) unsigned char cue[0x80];  // CuePoint: 0x60 in the collection's vector
  memset(cue, 0, sizeof(cue));
  ((void (*)(void*))R(A_cue_ctor))(cue);
  const std::string cue_name(name ? name : "");
  ((void (*)(void*, const std::string*))R(A_cue_setName))(cue, &cue_name);
  ((void (*)(void*, int))R(A_cue_setType))(cue, type);
  ((void (*)(void*, double))R(A_cue_setStart))(cue, start_ms);
  ((void (*)(void*, double))R(A_cue_setLength))(cue, 0.0);

  int idx = -1;
  std::function<bool(TKEntryTrack&)> fn = [&](TKEntryTrack& track) -> bool {
    idx = ((int (*)(TKEntryTrack*, const void*, bool))R(A_cet_addCue))(&track, cue, false);
    if (idx < 0) return false;
    if (slot >= 0) ((void (*)(TKEntryTrack*, int, int))R(A_cet_assignHotCue))(&track, idx, slot);
    ((void (*)(TKEntryTrack*, int, bool))R(A_cet_setUpdateAuthor))(&track, TK_UPDATE_AUTHOR_DECK, true);
    return true;
  };
  const TKTypedKey tkey = { key, 0 };
  const bool ok = ((bool (*)(void*, const TKTypedKey*, const void*))R(A_ccm_executeOnTrack))(mgr, &tkey, &fn);
  tklog("CUEWRITE: key=%u slot=%d start=%.3f ms type=%d name='%s' -> ok=%d idx=%d", key, slot, start_ms, type,
        cue_name.c_str(), (int)ok, idx);
  return ok ? idx : -1;
}

// Remove from collection track `key` every hotcue (slot >= 1, not a grid marker) whose name starts
// with `prefix` ("tk: " = the ones TK detected). Returns how many went, or -1.
static int cue_remove(uint32_t key, const char* prefix) {
  if (!pthread_main_np()) { tklog("CUEREMOVE: refused off the main thread"); return -1; }
  if (key == 0 || !prefix || !*prefix) return -1;
  void* mgr = get_collection_mgr();
  if (!mgr) return -1;
  const size_t plen = strlen(prefix);
  int removed = 0;
  std::function<bool(TKEntryTrack&)> fn = [&](TKEntryTrack& track) -> bool {
    const void* coll = (const char*)&track + OFF_CET_CUES;
    const int n = ((int (*)(const void*))R(A_cpc_getCueCount))(coll);
    for (int i = (n < 256 ? n : 256) - 1; i >= 0; i--) {  // from the end: indices stay valid
      if (!(((uint32_t (*)(const void*, int))R(A_cpc_hasCue))(coll, i) & 0xffu)) continue;
      const char* cue = (const char*)((void* (*)(const void*, int))R(A_cpc_getCuePoint))(coll, i);
      if (!cue || !gum_memory_is_readable(cue, OFF_CUE_REPEATS + 4)) continue;
      if (*(const int32_t*)(cue + OFF_CUE_TYPE) == CUE_TYPE_GRID || *(const int32_t*)(cue + OFF_CUE_HOTCUE) < 1) continue;
      if (decode_std_string(cue).compare(0, plen, prefix) != 0) continue;
      ((void (*)(TKEntryTrack*, int))R(A_cet_removeCue))(&track, i);
      removed++;
    }
    if (removed) ((void (*)(TKEntryTrack*, int, bool))R(A_cet_setUpdateAuthor))(&track, TK_UPDATE_AUTHOR_DECK, true);
    return removed > 0;
  };
  const TKTypedKey tkey = { key, 0 };
  ((bool (*)(void*, const TKTypedKey*, const void*))R(A_ccm_executeOnTrack))(mgr, &tkey, &fn);
  tklog("CUEREMOVE: key=%u prefix='%s' -> %d removed", key, prefix, removed);
  return removed;
}

// For TK (ctypes, Traktor's main thread = Qt's): by file path, as TK knows tracks.
TK_EXPORT int tk_cue_write_path(const char* path, int slot, double start_ms, int type, const char* name) {
  const uint32_t key = key_of_path(path);
  if (key == 0) { tklog("CUEWRITE: not in the collection: %s", path ? path : "(null)"); return -1; }
  return cue_write(key, slot, start_ms, type, name);
}

TK_EXPORT int tk_cue_remove_path(const char* path, const char* prefix) {
  const uint32_t key = key_of_path(path);
  if (key == 0) return -1;
  return cue_remove(key, prefix);
}

// "cuewrite <slot> <start_ms> <type> <name|-> <path>": one cue by hand (results in the log).
static void handle_cuewrite_cmd(int fd, const char* line) {
  int slot = -1, type = 0, used = 0;
  double start = -1;
  char name[32] = {0};
  if (sscanf(line, "%*s %d %lf %d %31s %n", &slot, &start, &type, name, &used) < 4 || used == 0 || !line[used]) {
    cmd_reply(fd, "ERR usage: cuewrite <slot 0-7|-1> <start_ms> <type> <name|-> <path>\n");
    return;
  }
  std::string path(line + used);
  std::string nm(strcmp(name, "-") == 0 ? "" : name);
  for (char& c : nm) if (c == '_') c = ' ';
  cmd_reply(fd, "OK\n");
  dispatch_async(dispatch_get_main_queue(), ^{
    const uint32_t key = key_of_path(path.c_str());
    if (key == 0) { tklog("CUEWRITE: not in the collection: %s", path.c_str()); return; }
    cue_write(key, slot, start, type, nm.c_str());
  });
}
