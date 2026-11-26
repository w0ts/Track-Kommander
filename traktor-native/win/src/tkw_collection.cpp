// tkw_collection.cpp — Traktor's collection through its own C++ (port of the Mac tk_inject.mm
// collection/track/load/read code and the library write): find tracks by key, by file path or by
// the browser's selection, read their tags (POST /nativeTrack to TK), load them into a deck, set
// a track's Comment. MAIN THREAD ONLY: Traktor's C++ is not thread-safe.
//
// Windows differences (map win/map/traktor_4_5_1_21_win.json): every SmartPtr comes back through
// a hidden pointer (rdx for members, rcx for the free helpers::getMainCollection); URL(path) takes
// a boost path = std::wstring (UTF-16) and a URL's kind is a signed byte at +0x20; the entity key
// is at +0x10; dynamic_cast is __RTDynamicCast with Traktor's TypeDescriptors.
//
// Verbs (Mac syntax and replies): load <deck> <key>, read <key>, loadsel <deck>, readsel,
// load_path <deck> <path>, read_path <path>, libwrite <key> <text>, libwritesel <text>.
// As on the Mac, "OK" means accepted: the work runs right after the reply and logs its outcome.
#include "tkw_collection.h"

#include <stdio.h>
#include <string.h>

#pragma comment(lib, "normaliz.lib")

namespace {

// Traktor's import thunk `jmp [__imp___RTDynamicCast]` (README "Windows differences"; used at
// 0x4fe150, see the A_ti_entity entry). Not in the full map: checked for FF 25 before use.
const uint32_t kRvaRtDynamicCast = 0x2a339ee;
const size_t kUrlBuf = 0x40;        // URL is 0x30 bytes on Windows ("URL object size")
const size_t kUrlKind = 0x20;       // signed byte: 1 boost path (std::wstring), 2 std::string
const size_t kEkmReadable = 0x30;   // SRWLOCK @0, URL vector @8, key map @0x20
const size_t kMixerChans = 0x188;   // MixerDeck channel vector begin/end, 0x10 per channel
// TraktorApp: its IAppBase sub-object is at +0x168 (IAppBase::getInstance returns app-0x80+0x168;
// the IAppBase vtables' COL offset is 0x168) and its MixerDeck is EMBEDDED at +0x338: the
// IAppBase-side TraktorApp::getMixerDeckChannel 0x524f20 is `add rcx,0x1d0; jmp
// MixerDeck::getMixerDeckChannel 0x5a3690` (slot 52 of the IAppBase vtables of TraktorApp,
// TraktorAppWin32<TraktorApp> and TraktorStudioAppWin32), 0x168+0x1d0 = 0x338, and Traktor calls
// loadMixerDeck with `lea rcx,[rdi+0x338]` (0x5363ec). Mac x86_64: TraktorApp+0x350
// (TraktorApp::getMixerDeckChannel `add rdi,0x350`). MixerDeck itself has no vtable/RTTI.
const size_t kAppIAppBase = 0x168;
const size_t kAppMixerDeck = 0x338;
const char* const kAppClasses[] = {".?AVTraktorApp@@", ".?AV?$TraktorAppWin32@VTraktorApp@@@@",
                                   ".?AVTraktorStudioAppWin32@@"};
const int kFirstTrackProbe = 400;   // key 0 = "the first track" (a testing convenience)

bool g_quiet_paths = false;          // a list resolves hundreds of paths: no per-path log

struct TypedKey {  // TypedEntryKey<unsigned>, as getTrack / getURLFromKey take it
  uint32_t key;
  uint32_t pad;
};

std::string read_str(const void* track, size_t off) {
  std::string s;
  tkw_read_string((const char*)track + off, &s);
  return s;
}

uint32_t read_u32(const void* track, size_t off) {
  const void* p = (const char*)track + off;
  return tkw_readable(p, 4) ? *(const uint32_t*)p : 0u;
}

void* ekm_instance() {
  // The guard at A_ekm_instance_guard is MSVC's thread-safe-static int: gate on the pointer.
  void* const* slot = (void* const*)WF(WF_A_ekm_instance);
  void* ekm = tkw_readable(slot, 8) ? *slot : nullptr;
  return ekm && tkw_readable(ekm, kEkmReadable) ? ekm : nullptr;
}

uint32_t key_of_wpath(void* ekm, const std::wstring& wpath) {
  alignas(16) unsigned char url[kUrlBuf] = {};
  tkw_call<void*>(WF(WF_A_url_ctor_from_path), (void*)url, &wpath);  // copies the path
  uint64_t key = 0;  // TypedEntryKey<unsigned>, returned through rdx
  tkw_call<void*>(WF(WF_A_ekm_getKeyFromURL), ekm, (void*)&key, (void*)url);
  tkw_call<void>(WF(WF_A_url_dtor), (void*)url);
  return (uint32_t)key;
}

}  // namespace

// ---- shared helpers (tkw_collection.h) ---------------------------------------------------------
std::wstring tkw_widen(const std::string& s) {
  if (s.empty()) return std::wstring();
  const int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
  if (n <= 0) return std::wstring();
  std::wstring w((size_t)n, L'\0');
  MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
  return w;
}

std::string tkw_narrow(const std::wstring& w) {
  if (w.empty()) return std::string();
  const int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), nullptr, 0, nullptr, nullptr);
  if (n <= 0) return std::string();
  std::string s((size_t)n, '\0');
  WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), &s[0], n, nullptr, nullptr);
  return s;
}

std::string tkw_collection_nfd(const std::string& s) {
  bool ascii = true;
  for (unsigned char c : s)
    if (c >= 0x80) { ascii = false; break; }
  if (ascii) return s;
  const std::wstring w = tkw_widen(s);
  int n = NormalizeString(NormalizationD, w.c_str(), (int)w.size(), nullptr, 0);
  if (n <= 0) return s;
  std::wstring out((size_t)n, L'\0');
  n = NormalizeString(NormalizationD, w.c_str(), (int)w.size(), &out[0], n);
  if (n <= 0) return s;
  out.resize((size_t)n);
  return tkw_narrow(out);
}

bool tkw_collection_is_object(const void* p) {
  return tkw_readable(p, 16) && tkw_readable(*(void* const*)p, 8);
}

void* tkw_collection_cast(const void* obj, int src_ti, int dst_ti) {
  static void* fn = nullptr;
  static bool checked = false;
  if (!checked) {
    checked = true;
    const unsigned char* p = (const unsigned char*)tkw_rva(kRvaRtDynamicCast);
    if (tkw_readable(p, 6) && p[0] == 0xff && p[1] == 0x25) fn = (void*)p;
    else tklog("COLL: no __RTDynamicCast thunk at RVA 0x%x — collection casts off", kRvaRtDynamicCast);
  }
  if (!fn || !obj) return nullptr;
  return tkw_call<void*>(fn, obj, (long)0, WF(src_ti), WF(dst_ti), (int)FALSE);
}

void tkw_collection_quiet_paths(bool quiet) { g_quiet_paths = quiet; }

void tkw_collection_release(void* refcounted) {
  if (refcounted) tkw_call<int>(WF(WF_A_refcount_release), refcounted);
}

// helpers::getMainCollection is a free function (SmartPtr through rcx): it addRefs the raw
// ContentCollectionMgr* into the result and its own temporary is destroyed as
// `release(raw)` (0xab0f7f). We drop our reference at once, as that SmartPtr's destructor would:
// CollectionsManager keeps the main collection alive for Traktor's lifetime, so the raw pointer
// stays valid for the main-thread call it is used in.
void* tkw_collection_mgr() {
  void* sp = nullptr;
  tkw_call<void*>(WF(WF_A_helpersGetMainCollection), (void*)&sp);
  if (!tkw_collection_is_object(sp)) { tklog("LOAD: abort (no collection manager)"); return nullptr; }
  tkw_collection_release(sp);
  return sp;
}

// getTrack(this, sret, key*) -> ContentEntryTrack* (MediaItem at offset 0), or null. The caller
// owns one reference: getTrack addRefs the raw ContentEntryTrack* into the result (0x84d449) and
// releases its temporaries as release(raw) (0x84d46b) — so release it with
// tkw_collection_release (TkwRef) after use, as Traktor's SmartPtr destructor does.
void* tkw_collection_track(void* mgr, uint32_t key) {
  if (!mgr || key == 0) return nullptr;
  const TypedKey k = {key, 0};
  void* sp = nullptr;
  tkw_call<void*>(WF(WF_A_ccmGetTrack), mgr, (void*)&sp, &k);
  return tkw_collection_is_object(sp) ? sp : nullptr;
}

// The collection key of the track at <path> (UTF-8), or 0. Traktor keys a URL built from a wide
// boost path; TK may spell the separators either way, so '/' is retried as '\'.
uint32_t tkw_collection_key_of_path(const char* path) {
  if (!path || !*path) return 0;
  if (!tkw_is_main_thread()) { tklog("PATH: refused off the main thread"); return 0; }
  void* ekm = ekm_instance();
  if (!ekm) { tklog("PATH: EntryKeyManager instance not ready"); return 0; }
  std::string p(path);
  uint32_t key = key_of_wpath(ekm, tkw_widen(p));
  if (key == 0 && p.find('/') != std::string::npos) {
    for (char& c : p)
      if (c == '/') c = '\\';
    key = key_of_wpath(ekm, tkw_widen(p));
  }
  if (!g_quiet_paths) tklog("PATH: '%s' -> key=%u", path, key);
  return key;
}

void* tkw_collection_track_at(void* mgr, const char* path) {
  if (!mgr || !path || !*path) { tklog("PATH: abort (no mgr/path)"); return nullptr; }
  const uint32_t key = tkw_collection_key_of_path(path);
  return key ? tkw_collection_track(mgr, key) : nullptr;
}

// The file path (UTF-8) of collection key <key>, or "" (not a file URL / unknown key).
std::string tkw_collection_path_of_key(uint32_t key) {
  void* ekm = ekm_instance();
  if (!ekm) return std::string();
  alignas(16) unsigned char url[kUrlBuf] = {};
  const TypedKey k = {key, 0};
  tkw_call<void*>(WF(WF_A_ekm_getURLFromKey), ekm, (void*)url, &k);
  const int8_t kind = *(const int8_t*)(url + kUrlKind);
  std::string path;
  if (kind == 1) path = tkw_narrow(*(const std::wstring*)(const void*)url);
  else if (kind == 2) path = *(const std::string*)(const void*)url;
  tkw_call<void>(WF(WF_A_url_dtor), (void*)url);
  return path;
}

void* tkw_collection_track_of_entity(void* raw) {
  if (!tkw_collection_is_object(raw)) return nullptr;
  void* item = tkw_collection_cast(raw, WF_A_ti_entity, WF_A_ti_contententrytrack);
  return item && tkw_readable(item, WOFF_CET_CUES + 0x10) ? item : nullptr;
}

bool tkw_collection_for_each_entity(const char* what, const std::function<void(TkwBorrowedPtr)>& each) {
  if (!tkw_is_main_thread()) { tklog("%s: refused off the main thread", what); return false; }
  void* mgr = tkw_collection_mgr();
  if (!mgr) return false;
  void* repo = *(void**)((char*)mgr + WOFF_CCM_REPO);
  if (!tkw_readable(repo, 0x60)) {  // MSVC map head @0x50, size @0x58
    tklog("%s: no MediaItemRepository at mgr+0x%x", what, WOFF_CCM_REPO);
    return false;
  }
  tkw_call<void>(WF(WF_A_mir_forEachEntity), repo, &each);
  return true;
}

// ---- reading a track and telling TK -----------------------------------------------------------
namespace {

// deck 0..3 => "deck":"A".."D" in the payload; < 0 => omitted.
void read_and_post_track(const void* track, const char* source, int deck = -1) {
  if (!tkw_collection_is_object(track)) return;
  const std::string title = read_str(track, WOFF_MI_TITLE), artist = read_str(track, WOFF_MI_ARTIST);
  const std::string album = read_str(track, WOFF_MI_ALBUM), genre = read_str(track, WOFF_MI_GENRE);
  const std::string label = read_str(track, WOFF_MI_LABEL), comment = read_str(track, WOFF_MI_COMMENT);
  const std::string comment2 = read_str(track, WOFF_MI_COMMENT2);
  const void* bp = (const char*)track + WOFF_MI_BPM;
  const double bpm = tkw_readable(bp, 4) ? (double)*(const float*)bp : 0.0;
  const uint32_t rating = read_u32(track, WOFF_MI_RATING), plays = read_u32(track, WOFF_MI_PLAYCOUNT);
  const uint32_t imported = read_u32(track, WOFF_MI_IMPORTDATE), released = read_u32(track, WOFF_MI_RELEASEDATE);
  tklog("  nativeRead[%s] deck=%d track=%p title='%s' artist='%s' album='%s' genre='%s' bpm=%.2f rating=%u plays=%u",
        source, deck, track, title.c_str(), artist.c_str(), album.c_str(), genre.c_str(), bpm, rating, plays);
  char nums[256];
  snprintf(nums, sizeof(nums),
           "\"bpm\":%.2f,\"rating\":%u,\"play_count\":%u,\"import_date\":%u,\"release_date\":%u,", bpm,
           rating, plays, imported, released);
  std::string body = "{\"title\":\"" + tkw_json_escape(title) + "\",\"artist\":\"" + tkw_json_escape(artist) +
                     "\",\"album\":\"" + tkw_json_escape(album) + "\",\"genre\":\"" + tkw_json_escape(genre) +
                     "\",\"label\":\"" + tkw_json_escape(label) + "\",\"comment\":\"" + tkw_json_escape(comment) +
                     "\",\"comment2\":\"" + tkw_json_escape(comment2) + "\"," + nums + "\"source\":\"" + source + "\"";
  if (deck >= 0 && deck <= 3) body += std::string(",\"deck\":\"") + (char)('A' + deck) + "\"";
  body += "}";
  tkw_post_json("/nativeTrack", body.c_str());
}

// ---- the mixer --------------------------------------------------------------------------------
bool mixer_ok(const void* m, long* nchan) {
  if (!tkw_readable(m, kMixerChans + 0x10)) return false;
  const char* b = *(const char* const*)((const char*)m + kMixerChans);
  const char* e = *(const char* const*)((const char*)m + kMixerChans + 8);
  if (!b || e < b || (e - b) % 0x10) return false;
  const long n = (long)((e - b) / 0x10);
  if (n < 1 || n > 16 || !tkw_readable(b, (size_t)n * 0x10)) return false;
  if (!tkw_collection_is_object(*(void* const*)b)) return false;
  if (nchan) *nchan = n;
  return true;
}

bool is_traktor_app(const std::string& rtti) {
  for (const char* c : kAppClasses)
    if (rtti == c) return true;
  return false;
}

// TraktorApp's own MixerDeck (the one loadMixerDeck is called on), checked by the owner's RTTI
// and the channel vector. Null (logged) otherwise: no other object is ever used as a mixer.
void* find_mixer(long* nchan) {
  char* iapp = tkw_call<char*>(WF(WF_A_appGetInstance));
  if (!iapp) { tklog("LOAD: abort (no IAppBase)"); return nullptr; }
  char* app = iapp - kAppIAppBase;
  const std::string name = tkw_rtti_name(app);
  if (!is_traktor_app(name)) {
    tklog("LOAD: abort (IAppBase-0x%zx is '%s', not TraktorApp)", kAppIAppBase, name.c_str());
    return nullptr;
  }
  char* m = app + kAppMixerDeck;
  if (!mixer_ok(m, nchan)) { tklog("LOAD: abort (no channel vector in %s's MixerDeck %p)", name.c_str(), m); return nullptr; }
  return m;
}

int deck_index_of_channel(const void* mixer, const void* channel) {
  long n = 0;
  if (!channel || !mixer || mixer != find_mixer(&n)) return -1;
  const char* arr = *(const char* const*)((const char*)mixer + kMixerChans);
  for (long i = 0; i < n; i++)
    if (*(const void* const*)(arr + i * 0x10) == channel) return (int)i;
  return -1;
}

// loadMixerDeck(this, int deck, SmartPtr const&, bool play): it addRefs the SmartPtr itself.
void load_track(void* mixer, long nchan, int deck, void* track) {
  if (!mixer || !track) { tklog("LOAD: abort (no mixer/track to load)"); return; }
  if (deck < 0 || deck >= nchan) { tklog("LOAD: abort (deck %d out of range 0..%ld)", deck, nchan - 1); return; }
  void* raw = track;
  tklog("LOAD: loadMixerDeck(mixer=%p deck=%d track=%p)", mixer, deck, track);
  tkw_call<void>(WF(WF_A_loadMixerDeck), mixer, deck, (void*)&raw, true);
  tklog("LOAD: loadMixerDeck returned — deck %d should now hold the track", deck);
}

// The mixer, checked for <deck>, before the collection is touched (Mac order).
void* mixer_for(const char* what, int deck, long* nchan) {
  void* mixer = find_mixer(nchan);
  if (mixer && (deck < 0 || deck >= *nchan)) {
    tklog("%s: abort (deck %d out of range 0..%ld)", what, deck, *nchan - 1);
    return nullptr;
  }
  return mixer;
}

void* first_or_key(void* mgr, uint32_t key, uint32_t* found) {
  *found = key;
  if (key) return tkw_collection_track(mgr, key);
  for (uint32_t k = 1; k <= (uint32_t)kFirstTrackProbe; k++)
    if (void* t = tkw_collection_track(mgr, k)) { *found = k; return t; }
  return nullptr;
}

void do_native_load(int deck, uint32_t key) {
  long nchan = 0;
  void* mixer = mixer_for("LOAD", deck, &nchan);
  void* mgr = mixer ? tkw_collection_mgr() : nullptr;
  if (!mgr) return;
  uint32_t found = 0;
  TkwRef ref(first_or_key(mgr, key, &found));
  void* track = ref.get();
  tklog("LOAD: track=%p key=%u", track, found);
  if (!track) { tklog("LOAD: abort (no track for key %u)", key); return; }
  read_and_post_track(track, "load", deck);
  load_track(mixer, nchan, deck, track);
}

void do_native_read(uint32_t key) {
  void* mgr = tkw_collection_mgr();
  if (!mgr) return;
  uint32_t found = 0;
  TkwRef ref(first_or_key(mgr, key, &found));
  void* track = ref.get();
  tklog("READ: track=%p key=%u", track, found);
  if (!track) { tklog("READ: abort (no track for key %u)", key); return; }
  read_and_post_track(track, "read");
}

// The track highlighted in Traktor's browser: the selected entry's collection key (entity +0x10)
// through getTrack, which gives a real MediaItem (the entry itself is not one at offset 0).
// The result is owned by the caller (TkwRef). getCurrentlySelectedContentEntry's SmartPtr<Entity>
// is ours too (released as release(raw), like getTrack's Entity temporary at 0x84d3df).
void* get_selected_track(void* mgr) {
  void* bf = tkw_browserform();
  if (!tkw_collection_is_object(bf)) { tklog("SEL: no BrowserForm seen yet"); return nullptr; }
  void* sp = nullptr;
  tkw_call<void*>(WF(WF_A_bf_getSelectedEntry), bf, (void*)&sp);
  TkwRef entry(sp);
  if (!tkw_collection_is_object(sp)) { tklog("SEL: nothing selected"); return nullptr; }
  void* cet = tkw_collection_cast(sp, WF_A_ti_entity, WF_A_ti_contententrytrack);
  if (!tkw_collection_is_object(cet)) { tklog("SEL: selection is not a track"); return nullptr; }
  const uint32_t key = read_u32(cet, WOFF_ENTRY_KEY);
  tklog("SEL: entry=%p cet=%p key=%u", sp, cet, key);
  if (!mgr || key == 0) { tklog("SEL: no collection key on selection"); return nullptr; }
  void* track = tkw_collection_track(mgr, key);
  if (!track) tklog("SEL: getTrack(key=%u) returned null", key);
  return track;
}

void do_native_read_sel() {
  void* mgr = tkw_collection_mgr();
  TkwRef ref(mgr ? get_selected_track(mgr) : nullptr);
  void* track = ref.get();
  if (!track) { tklog("READSEL: abort (no selected track)"); return; }
  read_and_post_track(track, "readsel");
}

void do_native_load_sel(int deck) {
  long nchan = 0;
  void* mixer = mixer_for("LOADSEL", deck, &nchan);
  void* mgr = mixer ? tkw_collection_mgr() : nullptr;
  TkwRef ref(mgr ? get_selected_track(mgr) : nullptr);
  void* track = ref.get();
  if (!track) { tklog("LOADSEL: abort (no selected track)"); return; }
  read_and_post_track(track, "loadsel", deck);
  load_track(mixer, nchan, deck, track);
}

void do_native_read_path(const std::string& path) {
  void* mgr = tkw_collection_mgr();
  TkwRef ref(mgr ? tkw_collection_track_at(mgr, path.c_str()) : nullptr);
  void* track = ref.get();
  if (!track) { tklog("READPATH: abort (no collection track for '%s')", path.c_str()); return; }
  read_and_post_track(track, "readpath");
}

bool do_native_load_path(int deck, const std::string& path) {
  long nchan = 0;
  void* mixer = mixer_for("LOADPATH", deck, &nchan);
  void* mgr = mixer ? tkw_collection_mgr() : nullptr;
  TkwRef ref(mgr ? tkw_collection_track_at(mgr, path.c_str()) : nullptr);
  void* track = ref.get();
  if (!track) { tklog("LOADPATH: abort (no mixer, or no collection track for '%s')", path.c_str()); return false; }
  read_and_post_track(track, "loadpath", deck);
  load_track(mixer, nchan, deck, track);
  return true;
}

// ---- library write: MediaItem::setComment + commitEntry (stored in memory; Traktor writes its
// own collection on quit — libtk never writes Traktor's files) -------------------------------------
void do_native_libwrite_track(void* mgr, void* track, uint32_t key, const std::string& text) {
  if (!track) { tklog("LIBWRITE: no track (key=%u)", key); return; }
  tklog("LIBWRITE: setComment(track=%p key=%u text='%s')", track, key, text.c_str());
  tkw_call<void>(WF(WF_A_mi_setComment), track, &text);
  tklog("LIBWRITE: readback +0x%x = '%s'", WOFF_MI_COMMENT, read_str(track, WOFF_MI_COMMENT).c_str());
  void* sp = track;  // SmartPtr<Entity const> = the raw pointer; commitEntry addRefs itself
  tkw_call<int>(WF(WF_A_ccm_commitEntry), mgr, (void*)&sp, 0);
  tklog("LIBWRITE: commitEntry done (key=%u)", key);
}

// ---- hook: a deck load (TK's native deckLoaded) --------------------------------------------------
// MixerDeck::loadDeck(this, MixerDeckChannel*, Controllable*, SmartPtr<ContentEntryTrack const>
// const*, bool): the SmartPtr is arg 3 (r9), the raw track at its offset 0.
// Only TraktorApp's own MixerDeck gets its channels read (deck_index_of_channel checks it);
// another one is logged and posted without a deck.
void on_load_deck(GumInvocationContext* ic) {
  void* mixer = tkw_arg(ic, 0);
  void* sp = tkw_arg(ic, 3);
  if (!tkw_readable(sp, 8)) return;
  const int deck = deck_index_of_channel(mixer, tkw_arg(ic, 1));
  if (deck < 0) tklog("loadDeck on MixerDeck %p: not TraktorApp's (or channel unknown) — no deck", mixer);
  tklog("loadDeck fired (deck=%d)", deck);
  read_and_post_track(*(void* const*)sp, "deckLoad", deck);
}

// ---- verbs ---------------------------------------------------------------------------------------
std::string rest_after(const std::string& args, size_t from) {
  size_t i = from;
  while (i < args.size() && args[i] == ' ') i++;
  return i < args.size() ? args.substr(i) : std::string();
}

std::string cmd_load(const std::string& a, const std::vector<std::string>&) {
  int deck = -1;
  unsigned key = 0;
  if (sscanf_s(a.c_str(), "%d %u", &deck, &key) != 2 || deck < 0 || deck > 15) return "ERR usage: load <deck> <key>";
  tklog("CMD load deck=%d key=%u", deck, key);
  tkw_on_main([deck, key] { do_native_load(deck, key); });
  return "OK";
}

std::string cmd_read(const std::string& a, const std::vector<std::string>&) {
  unsigned key = 0;
  if (sscanf_s(a.c_str(), "%u", &key) != 1) return "ERR usage: read <key>";
  tklog("CMD read key=%u", key);
  tkw_on_main([key] { do_native_read(key); });
  return "OK";
}

std::string cmd_loadsel(const std::string& a, const std::vector<std::string>&) {
  int deck = -1;
  if (sscanf_s(a.c_str(), "%d", &deck) != 1 || deck < 0 || deck > 15) return "ERR usage: loadsel <deck>";
  tklog("CMD loadsel deck=%d", deck);
  tkw_on_main([deck] { do_native_load_sel(deck); });
  return "OK";
}

std::string cmd_readsel(const std::string&, const std::vector<std::string>&) {
  tklog("CMD readsel");
  tkw_on_main([] { do_native_read_sel(); });
  return "OK";
}

std::string cmd_load_path(const std::string& a, const std::vector<std::string>&) {
  int deck = -1, used = 0;
  if (sscanf_s(a.c_str(), "%d %n", &deck, &used) < 1 || deck < 0 || deck > 15 || used <= 0)
    return "ERR usage: load_path <deck> <path>";
  const std::string path = rest_after(a, (size_t)used);
  if (path.empty()) return "ERR usage: load_path <deck> <path>";
  tklog("CMD load_path deck=%d path=%s", deck, path.c_str());
  tkw_on_main([deck, path] { do_native_load_path(deck, path); });
  return "OK";
}

std::string cmd_read_path(const std::string& a, const std::vector<std::string>&) {
  const std::string path = rest_after(a, 0);
  if (path.empty()) return "ERR usage: read_path <path>";
  tklog("CMD read_path path=%s", path.c_str());
  tkw_on_main([path] { do_native_read_path(path); });
  return "OK";
}

std::string cmd_libwrite(const std::string& a, const std::vector<std::string>&) {
  unsigned key = 0;
  int used = 0;
  if (sscanf_s(a.c_str(), "%u %n", &key, &used) < 1 || key == 0 || used <= 0) return "ERR usage: libwrite <key> <text>";
  const std::string text = rest_after(a, (size_t)used);
  if (text.empty()) return "ERR usage: libwrite <key> <text>";
  tklog("CMD libwrite key=%u text=%s", key, text.c_str());
  tkw_on_main([key, text] {
    void* mgr = tkw_collection_mgr();
    if (!mgr) return;
    TkwRef ref(tkw_collection_track(mgr, key));
    do_native_libwrite_track(mgr, ref.get(), key, text);
  });
  return "OK";
}

std::string cmd_libwritesel(const std::string& a, const std::vector<std::string>&) {
  const std::string text = rest_after(a, 0);
  if (text.empty()) return "ERR usage: libwritesel <text>";
  tklog("CMD libwritesel text=%s", text.c_str());
  tkw_on_main([text] {
    void* mgr = tkw_collection_mgr();
    if (!mgr) return;
    TkwRef ref(get_selected_track(mgr));
    do_native_libwrite_track(mgr, ref.get(), 0, text);
  });
  return "OK";
}

}  // namespace

// For the props module's tk_load_path (main thread).
bool tkw_collection_load_path(int deck, const char* path) {
  if (!path || !*path) return false;
  if (!tkw_is_main_thread()) { tklog("LOADPATH: refused off the main thread"); return false; }
  return do_native_load_path(deck, path);
}

void tkw_mod_collection_init() {
  tkw_hook(WF(WF_A_loadDeck), "MixerDeck::loadDeck", on_load_deck, nullptr);
  tkw_command("load", cmd_load);
  tkw_command("read", cmd_read);
  tkw_command("loadsel", cmd_loadsel);
  tkw_command("readsel", cmd_readsel);
  tkw_command("load_path", cmd_load_path);
  tkw_command("read_path", cmd_read_path);
  tkw_command("libwrite", cmd_libwrite);
  tkw_command("libwritesel", cmd_libwritesel);
  tkw_collection_dump_init();
}
