// tkw_columns.cpp — TK's own columns in Traktor's track list on Windows (port of the Mac
// tk_columns.mm; the layout string half is tkw_columns_store.cpp): SCORE, BPM MATCH and KEY MATCH
// (TK's BPM and key distance, coloured as in TK's table), DIRECTORY (the file's folder) and
// Directory (short) (the music folder's name and the path in it, e.g. "Tracks\2025-10\").
// MAIN THREAD ONLY.
//
// They are real columns of Traktor's list, not an overlay:
//   - TraktorListControl builds ListControlCustomColumns(38) (Win 0xaa4389 `mov edx,0x26`): types
//     0..37. The constructor hook makes it 43, so types 38..42 are TK's. Traktor's header menu
//     lists every type that has a getPopupMenuEntry name: DIRECTORY and Directory (short) are
//     added, removed and moved there like any other column (tk_col_menu: Traktor's builder put the
//     dots by type, not by position).
//   - SCORE, BPM MATCH and KEY MATCH are not in the menu (User): TK shows them as the first
//     columns while its own list is up and hides them elsewhere. TK's list is sorted by "#" (its
//     own order, TK's score order) and header sort clicks are ignored there.
//   - getCellValue / getHeaderText / getPopupMenuEntry answer for 38..42. A cell's track is
//     Traktor's own (row -> entry index via the row map -> getTrackByIndex), so the values are
//     right in any playlist; SCORE is empty for tracks not in TK's results.
//
// Windows: TraktorListControl's vtable is NOT "Mac slot - 1" (slots from the map); getHeaderText
// is a 5-byte jmp, so its body 0xab0480 is replaced; getPopupMenuEntry / getTrackByIndex return
// through a hidden pointer in rdx; sortCurrentList's by-value std::list is destroyed by the CALLEE.
// Paths are matched separator-, case- (ASCII) and normalisation- (NFC) blind.
#include <algorithm>
#include <cmath>
#include <cstring>
#include <list>
#include <map>
#include <new>
#include <unordered_map>

#include "tkw.h"
#include "tkw_collection.h"  // a row's path; the Results playlist's tree/structure path
#include "tkw_columns.h"

// --- from the band / cards (tkw_band.cpp, tkw_cards.cpp; same signatures as its tkw_band.h) ------
bool tkw_cards_on();                // the cards show (band visible, MATCHING open, no TK page)
bool tkw_cards_expanded();          // MATCHING open
void tkw_cards_toggle();            // the SCORE header's click: open/close the cards + relayout
int tkw_cards_gap_h();              // px the list header grows by while the cards show
void tkw_set_list_header_h(int h);  // the list's own header height (the cards go under it)
void tkw_band_add_visible_listener(void (*fn)(bool));  // band shown (TK's view) / hidden
void tkw_band_add_layout_listener(void (*fn)());       // after every list re-layout
unsigned tkw_band_form_gen();       // +1 whenever the BrowserForm is forgotten (address may recur)

namespace {

// Windows offsets and vtable slots (map: OFF_TLC_ROWMAP, VT_LCC_*, OFF_TLC_TEXT_OWNER, ...).
const unsigned kOffRowMap = WOFF_TLC_ROWMAP;                         // 0x19a0 vector<int>*
const unsigned kOffNTypes = WOFF_TRAKTORLISTCONTROL_0X17B0__NUMBER_OF_COLUMN_TYPES;  // 0x17d8
const unsigned kOffTextOwner = WOFF_TLC_TEXT_OWNER;                  // 0x1688 (embedded object)
const unsigned kVtTextOwnerStyle = WOFF_VT_TEXT_OWNER_STYLE;         // 0x20
const unsigned kOffTextStyle = WOFF_TEXT_STYLE;                      // 0x10
const unsigned kOffStyleArgb = WOFF_STYLE_TEXT_ARGB__TWO_ARGB_WORDS_IN_THE_LIST_TEXT_STYLE;  // 0x6c
const unsigned kVtColumnWidth = WOFF_VT_LCB_GETCOLUMNWIDTH;          // 0x100
const unsigned kVtViewColumn = WOFF_VT_LCC_GETVIEWCOLUMN;            // 0x238
const unsigned kVtDataColumn = WOFF_VT_LCC_GETDATACOLUMN;            // 0x240
const unsigned kOffEntryKey = WOFF_ENTRY_KEY;                        // 0x10
// TraktorListControl::getHeaderText's body (the vtable entry 0xab0750 is `jmp 0xab0480`; map
// A_tlc_getHeaderText evidence). Its first bytes are checked before it is replaced.
const uint32_t kRvaHeaderTextBody = 0xab0480;
const unsigned char kHeaderTextBodyBytes[12] = {0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x6c, 0x24, 0x18, 0x48, 0x89};
const int kSortAttrIndex = 0;  // attributeForColumn(1) ("#"): Win 0x8349f0 default case `xor eax,eax`

const int kScoreMinW = 230;  // px: the SCORE header's words fit
const int kTkListCols[3] = {kColScore, kColBpm, kColKey};

// One TK result's cells (tk_cols_set_values): text, and the BPM/KEY text colour (0 = Traktor's).
struct ColVals {
  std::string score, bpm, key;
  unsigned bpm_argb = 0, key_argb = 0;
};
struct RowPath {
  std::string shown;  // the path with Windows separators, as Traktor has it otherwise
  std::string match;  // shown, NFC, ASCII lower case: the key into g_vals
};
std::map<std::string, ColVals> g_vals;              // match key -> TK's cells
std::vector<std::string> g_roots;                   // music folders: shown form, no trailing '\'
std::unordered_map<uint32_t, RowPath> g_paths;      // collection key -> its path
int g_header_h = 24;                                // the list's own header height

template <typename R, typename... A>
R callf(int entry, A... a) { return tkw_call<R>(WF(entry), a...); }

void invalidate_form() {
  if (void* bf = tkw_browserform()) callf<void>(WF_A_nglform_invalidate, bf, 0u);
}

// px the list header grows by for the cards (Mac: kCardsInList && g_cards_gap_h > 0 && cards_on()).
int cards_gap() {
  const int gap = tkw_cards_on() ? tkw_cards_gap_h() : 0;
  return gap > 0 ? gap : 0;
}

// TK's results are a real playlist (the playlists module's "Kommander\Results"): showing when the
// browser's shown ContentList (BrowserForm+0x790) has that playlist's structure path (an MSVC
// std::string at list+0x10, the one getCurrentListSorting reads; map
// CONTENTLIST_PATH / COLLECTION_MGR 0x10 / 0x30).
const unsigned kOffListPath = 0x10;
std::string g_results_sp;
bool results_showing() {
  void* bf = tkw_browserform();
  if (!bf) return false;
  const char* list = *(char* const*)((char*)bf + WOFF_BF_CONTENTLIST);
  std::string shown;
  if (!list || !tkw_read_string(list + kOffListPath, &shown)) return false;
  if (g_results_sp.empty()) {
    // The path once the playlists module knows it; a lookup at most every 2 s until then.
    static ULONGLONG last_lookup = 0;
    std::string tp = tkw_playlists_results_known_path();
    if (tp.empty() && GetTickCount64() - last_lookup >= 2000) {
      last_lookup = GetTickCount64();
      tp = tkw_playlists_results_tree_path();
    }
    if (tp.empty() || !tkw_playlists_structure_path(tp, &g_results_sp) || g_results_sp.empty()) {
      g_results_sp.clear();
      return false;
    }
    tklog("COLS: TK results playlist is '%s'", g_results_sp.c_str());
  }
  return shown == g_results_sp;
}

// --- paths ---------------------------------------------------------------------------------------

// NormalizeString (Normaliz.dll, forwarded to the kernel on Windows 8+), loaded once: libtk links
// no Normaliz.lib. Null: paths are compared as they are.
typedef int(WINAPI* FnNormalize)(int, LPCWSTR, int, LPWSTR, int);
FnNormalize normalize_fn() {
  static bool tried = false;
  static FnNormalize fn = nullptr;
  if (tried) return fn;
  tried = true;
  HMODULE m = LoadLibraryW(L"normaliz.dll");
  fn = m ? (FnNormalize)GetProcAddress(m, "NormalizeString") : nullptr;
  if (!fn) tklog("COLS: no NormalizeString (%lu): accented paths compared as they are", GetLastError());
  return fn;
}

// One spelling for accented paths ("u" + U+0308 vs U+00FC: User 2026-11-24, a result without a
// score on the Mac). NFC both sides.
std::string nfc(const std::string& s) {
  bool ascii = true;
  for (unsigned char c : s)
    if (c >= 0x80) { ascii = false; break; }
  FnNormalize fn = ascii ? nullptr : normalize_fn();
  if (!fn) return s;
  const std::wstring w = tkw_widen(s);
  int cap = fn(1 /* NormalizationC */, w.data(), (int)w.size(), nullptr, 0);
  for (int tries = 0; cap > 0 && tries < 4; tries++) {
    std::wstring out((size_t)cap, L'\0');
    const int n = fn(1, w.data(), (int)w.size(), &out[0], cap);
    if (n > 0) { out.resize((size_t)n); return tkw_narrow(out); }
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER) break;
    cap = -n > cap ? -n : cap * 2;  // the call returns the negated estimate when short
  }
  return s;
}

std::string shown_path(std::string p) {
  std::replace(p.begin(), p.end(), '/', '\\');
  return p;
}

std::string lower_ascii(std::string s) {
  for (char& c : s)
    if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
  return s;
}

std::string match_key(const std::string& path) { return lower_ascii(nfc(shown_path(path))); }

std::string col_dir(const std::string& shown) {
  const size_t slash = shown.rfind('\\');
  return slash == std::string::npos ? std::string() : shown.substr(0, slash + 1);
}

std::string col_dir_short(const std::string& shown) {
  const std::string dir = col_dir(shown), dir_l = lower_ascii(dir);
  for (const std::string& root : g_roots) {
    const std::string prefix = lower_ascii(root) + "\\";
    if (dir_l.compare(0, prefix.size(), prefix) != 0) continue;
    const size_t name = root.rfind('\\');
    return root.substr(name == std::string::npos ? 0 : name + 1) + dir.substr(root.size());
  }
  return dir;
}

// --- rows -> tracks -> paths ---------------------------------------------------------------------

// The entry index of list row <row>, as getCellValue computes it; -1 when out of range.
int row_entry(const void* self, int row) {
  if (row < 0) return -1;
  const char* map = *(char* const*)((const char*)self + kOffRowMap);
  if (map) {
    if (!tkw_readable(map, 16)) return -1;
    const int* b = *(int* const*)map;
    const int* e = *(int* const*)(map + 8);
    return b && row < (int)(e - b) ? b[row] : -1;
  }
  return row;
}

// The path of list row <row>: Traktor's own track for that row, its key, its path (cached).
const RowPath* row_path(void* self, int row) {
  const int entry = row_entry(self, row);
  if (entry < 0) return nullptr;
  void* track = nullptr;  // SmartPtr<ContentEntryTrack>: one raw pointer, sret in rdx
  callf<void*>(WF_A_tlc_getTrackByIndex, self, (void*)&track, entry);
  if (!track) return nullptr;
  const RowPath* out = nullptr;
  if (tkw_readable(track, kOffEntryKey + 4)) {
    const uint32_t key = *(const uint32_t*)((const char*)track + kOffEntryKey);
    try {  // reached from Traktor's draw code: no exception may leave
      auto it = g_paths.find(key);
      if (it == g_paths.end()) {
        const std::string shown = shown_path(tkw_collection_path_of_key(key));
        it = g_paths.emplace(key, RowPath{shown, match_key(shown)}).first;
      }
      out = &it->second;
    } catch (...) {
      tklog("COLS: path of key %u failed", key);
    }
  }
  callf<void>(WF_A_refcount_release, track);  // the SmartPtr's reference, ours to drop
  return out;
}

const ColVals* row_vals(void* self, int row) {
  const RowPath* p = row_path(self, row);
  if (!p) return nullptr;
  auto it = g_vals.find(p->match);
  return it == g_vals.end() ? nullptr : &it->second;
}

int view_of(const void* list, int type) { return tkw_call<int>(tkw_vfn(list, kVtViewColumn), list, type); }
int type_of(const void* list, int view) { return tkw_call<int>(tkw_vfn(list, kVtDataColumn), list, view); }

// --- the browser's own list (cached per BrowserForm) ----------------------------------------------

// The complete object of a C++ (sub)object: MSVC's CompleteObjectLocator holds the vptr's offset.
void* complete_object(void* p) {
  const uintptr_t vt = *(const uintptr_t*)p;
  if (!tkw_readable((void*)(vt - 8), 8)) return nullptr;
  const uint32_t* col = *(const uint32_t* const*)(vt - 8);
  if (!tkw_readable(col, 8) || col[1] > 0x4000) return nullptr;
  return (char*)p - col[1];
}

// The first TraktorListControl among <root>'s members, three levels deep (the Mac search).
void* find_list(void* root) {
  std::vector<void*> level = {root}, seen = {root};
  for (int depth = 0; depth < 3 && !level.empty(); depth++) {
    std::vector<void*> next;
    for (void* o : level) {
      for (unsigned off = 8; off < 0x800; off += 8) {
        if (!tkw_readable((char*)o + off, 8)) break;
        void* p = *(void**)((char*)o + off);
        if (!p || ((uintptr_t)p & 7) || std::find(seen.begin(), seen.end(), p) != seen.end()) continue;
        seen.push_back(p);
        const std::string name = tkw_rtti_name(p);
        if (name.empty()) continue;
        if (name == ".?AVTraktorListControl@@") return complete_object(p);
        next.push_back(p);
      }
    }
    level.swap(next);
  }
  return nullptr;
}

// The browser's track list, cached per BrowserForm GENERATION (a new form can reuse a destroyed
// one's address; the list died with the old form). The member search runs once per generation,
// and again only on demand (tlc_search_again: TK's view shown, the list area re-laid out) while
// not found, never from the draw hooks' rate. Main thread only: other threads get the cache.
void* g_tlc = nullptr;
unsigned g_tlc_gen = 0;
bool g_tlc_searched = false;

void* tlc() {
  void* bf = tkw_browserform();
  const unsigned gen = tkw_band_form_gen();
  if (!tkw_is_main_thread()) return bf && gen == g_tlc_gen ? g_tlc : nullptr;
  if (!bf || gen != g_tlc_gen) {  // no form, or another one: nothing cached is valid
    g_tlc = nullptr;
    g_tlc_searched = false;
    g_tlc_gen = bf ? gen : 0;
    if (!bf) return nullptr;
  }
  if (g_tlc || g_tlc_searched) return g_tlc;
  g_tlc_searched = true;
  void* area = tkw_readable((char*)bf + WOFF_BF_LISTAREA, 8) ? *(void**)((char*)bf + WOFF_BF_LISTAREA) : nullptr;
  void* list = area && !tkw_rtti_name(area).empty() ? find_list(area) : nullptr;
  if (!list) { tklog("COLS: no TraktorListControl under BrowserForm+0x%x (form gen %u)", WOFF_BF_LISTAREA, gen); return nullptr; }
  g_tlc = list;
  tklog("COLS: browser list %p (form gen %u)", list, gen);
  return list;
}

// On demand: a failed search may run once more (the list may exist by now).
void tlc_search_again() {
  if (!g_tlc) g_tlc_searched = false;
}

// --- cells, headers, menu -----------------------------------------------------------------------

// getCellValue returns nothing: Win 0xaaf290 leaves every path through one epilogue (0xaaf5db,
// cookie check, pops, ret) with rax not set, i.e. whatever its last call left (void, as declared).
typedef void (*FnCell)(void*, int, int, void*);
FnCell g_orig_cell = nullptr;
void tk_cell(void* self, int row, int col, void* value) {
  if (!col_is_tk(col)) { g_orig_cell(self, row, col, value); return; }
  try {
    std::string text;
    if (col_is_tk_list(col)) {
      if (const ColVals* v = row_vals(self, row)) text = col == kColScore ? v->score : col == kColBpm ? v->bpm : v->key;
    } else if (const RowPath* p = row_path(self, row)) {
      text = col == kColDir ? col_dir(p->shown) : col_dir_short(p->shown);
    }
    callf<void>(WF_A_anv_setString, value, (const std::string*)&text);
  } catch (...) {
    tklog("COLS: cell %d/%d failed", row, col);
  }
}

// The header menu's names, written like Traktor's own ("File Path").
const std::string& col_name(int col) {
  static const std::string names[5] = {"Score", "Directory", "Directory (short)", "BPM Match", "Key Match"};
  return names[col - kColScore];
}

// Header texts, in capitals like Traktor's. The SCORE header says what a click on it does (it
// opens TK's matching cards, User 2026-11-24). The body returns a reference (address in rax).
typedef const std::string* (*FnHead)(void*, int);
FnHead g_orig_head = nullptr;
const std::string* tk_head(void* self, int col) {
  static const std::string heads[5] = {"SCORE", "DIRECTORY", "DIRECTORY (SHORT)", "BPM MATCH", "KEY MATCH"};
  static const std::string score_closed = "SCORE (CLICK FOR DETAILS)";
  static const std::string score_open = "SCORE (CLICK TO HIDE DETAILS)";
  if (col == kColScore) return tkw_cards_expanded() ? &score_open : &score_closed;
  return col_is_tk(col) ? &heads[col - kColScore] : g_orig_head(self, col);
}

// The header menu's entry for a column type: an MSVC std::string built in the caller's buffer
// (sret in rdx). SCORE, BPM MATCH and KEY MATCH are not in the menu.
typedef std::string* (*FnPopup)(const void*, std::string*, int);
FnPopup g_orig_popup = nullptr;
std::string* tk_popup(const void* self, std::string* out, int col) {
  if (!col_is_tk(col)) return g_orig_popup(self, out, col);
  try {
    return new (out) std::string(col_is_tk_list(col) ? std::string() : col_name(col));
  } catch (...) {
    return new (out) std::string();
  }
}

std::string popup_text(const void* self, int type) {
  alignas(std::string) unsigned char buf[sizeof(std::string)];
  std::string* s = tk_popup(self, (std::string*)buf, type);
  std::string text = std::move(*s);
  s->~basic_string();
  return text;
}

// Traktor's createColumnContextMenu sets each dot with setState(<type>), but setState takes the
// entry's POSITION: types 34..37 have no entry, so every type after them lost its dot. Same menu,
// positions counted.
void tk_col_menu(const void* self, void* menu) {
  try {
    const int types = *(const int*)((const char*)self + kOffNTypes);
    int pos = 0;
    for (int type = 0; type < types && type < 256; type++) {
      const std::string name = popup_text(self, type);
      if (name.empty()) continue;
      callf<void>(WF_A_popup_addEntry, menu, name.c_str(), (const char*)nullptr, type);
      callf<bool>(WF_A_popup_setState, menu, pos++, view_of(self, type) == -1 ? 2 : 1);
    }
  } catch (...) {
    tklog("COLS: column menu failed");
  }
}

// --- header height and header cells (the cards open under the header) ----------------------------

// The list's header grows by the cards' gap while they are open (the cards module puts them there).
// getHeaderHeight is virtual: font height + paddings; rows, scrolling and hit-tests all ask it.
typedef int (*FnHeaderH)(const void*);
FnHeaderH g_orig_header_h = nullptr;
int tk_header_h(const void* self) {
  const int h = g_orig_header_h(self);
  try {  // replaced function: no exception may reach Traktor (the plain height then)
    if (self != tlc()) return h;
    if (h != g_header_h) {
      g_header_h = h;
      tkw_set_list_header_h(h);  // the cards sit right under it
      tklog("COLS: list header %d px", h);
    }
    return h + cards_gap();
  } catch (...) {
    return h;
  }
}

// While the header is tall (cards open), each header cell is drawn in its normal height at the
// top, so its words stay where they were instead of centring in the gap.
typedef void (*FnDrawHead)(void*, void*, int, const int*);
FnDrawHead g_orig_draw_head = nullptr;
void tk_draw_head(void* self, void* pic, int view, const int* rect) {
  bool top_only = false;
  try {
    top_only = rect && cards_gap() > 0 && self == tlc();
  } catch (...) {
    top_only = false;
  }
  if (!top_only) { g_orig_draw_head(self, pic, view, rect); return; }
  const int top[4] = {rect[0], rect[1], rect[2], rect[1] + g_header_h};
  g_orig_draw_head(self, pic, view, top);
}

// --- TK's own list: SCORE, BPM MATCH, KEY MATCH first, the list in TK's order ("#") ---------------

// TK's list columns in place: views 0..2, each at least as wide as its header's words; false when
// they already were.
bool list_cols_first(void* list) {
  static const int min_w[3] = {kScoreMinW, kBpmMinW, kKeyMinW};
  bool changed = false;
  for (int i = 0; i < 3; i++) {
    const int type = kTkListCols[i];
    int view = view_of(list, type);
    if (view == i) continue;
    changed = true;
    if (view < 0) {
      callf<void>(WF_A_lcc_enableColumn, list, type, true);
      view = view_of(list, type);
    }
    if (view >= 0 && view != i) callf<void>(WF_A_lcc_moveColumn, list, view, i);
    const int w = tkw_call<int>(tkw_vfn(list, kVtColumnWidth), (const void*)list, i);
    if (w < min_w[i]) callf<void>(WF_A_lcb_setColumnWidth, list, i, min_w[i]);
  }
  return changed;
}

// TK's list columns hidden; false when none was showing.
bool list_cols_hidden(void* list) {
  bool changed = false;
  for (int type : kTkListCols) {
    if (view_of(list, type) < 0) continue;
    callf<void>(WF_A_lcc_enableColumn, list, type, false);
    changed = true;
  }
  return changed;
}

struct SortCriteria {  // traktor::browser::SortCriteria: {u32 attribute, u32 direction} at node+0x10
  int32_t attribute;
  int32_t direction;
};
typedef std::list<SortCriteria> SortList;  // MSVC std::list {head*, size}: 0x10 bytes, as Traktor's

typedef void (*FnSortList)(void*, SortList*);
FnSortList g_orig_sort_list = nullptr;

// A "#" ascending sort list in <buf>, for a by-value argument: the callee destroys it.
SortList* tk_order(void* buf) { return new (buf) SortList{{kSortAttrIndex, 0}}; }

// TK's list is built in TK's score order: sorting it by "#" (ascending) shows exactly that order.
// Traktor keeps sorting per playlist path, so no other list's sorting changes.
void force_tk_sort() {
  void* bf = tkw_browserform();
  if (!bf || !results_showing()) return;  // never another playlist's sorting
  alignas(SortList) unsigned char now_buf[sizeof(SortList)];
  callf<void*>(WF_A_bf_getCurrentListSorting, bf, (void*)now_buf);  // sret in rdx: we own it
  SortList* now = (SortList*)now_buf;
  const bool right = now->size() == 1 && now->front().attribute == kSortAttrIndex && now->front().direction == 0;
  std::string seen;
  for (const auto& c : *now) seen += std::to_string(c.attribute) + "/" + std::to_string(c.direction) + " ";
  now->~SortList();
  tklog("COLS: TK list sorting was [%s]%s", seen.c_str(), right ? "" : " -> # ascending");
  if (right) return;
  alignas(SortList) unsigned char want_buf[sizeof(SortList)];
  callf<void>(WF_A_bf_sortCurrentList, bf, tk_order(want_buf));  // destroyed by sortCurrentList
}

// A header click is ListControlBase::doSelectionOnMouseUp -> sortColumn(VIEW column, direction)
// -> a control event -> the browser sorts the playlist. In TK's results: the SCORE header is the
// cards' button (User, 2026-11-24); "#" may sort (it IS TK's order); every other header does
// nothing. Elsewhere: Traktor's own.
typedef void (*FnSortColumn)(void*, int, int);
FnSortColumn g_orig_sort_column = nullptr;
// The replaced sort functions decide inside try (an exception there means "not TK's list": Traktor's
// own sorting), then act outside it.
bool in_tk_list(const void* self) {
  try {
    return self == tlc() && results_showing();
  } catch (...) {
    return false;
  }
}

void tk_sort_column(void* self, int view, int direction) {
  if (!in_tk_list(self)) { g_orig_sort_column(self, view, direction); return; }
  const int type = type_of(self, view);
  if (type == 1) { g_orig_sort_column(self, view, direction); return; }  // "#"
  if (type != kColScore) { tklog("COLS: header sort ignored in TK's list (type %d)", type); return; }
  try {
    tkw_cards_toggle();
    tklog("COLS: SCORE header -> cards %s", tkw_cards_expanded() ? "open" : "closed");
    invalidate_form();  // the header's words
  } catch (...) {
    tklog("COLS: SCORE header click failed");
  }
}

// The browser's own sort request (TraktorListControl::sortRequest, after the click's event):
// nothing to do in TK's results (the order stays TK's).
typedef void (*FnSortClick)(void*, unsigned, bool);
FnSortClick g_orig_sort_click = nullptr;
void tk_sort_click(void* self, unsigned col, bool add) {
  if (in_tk_list(self)) return;
  g_orig_sort_click(self, col, add);
}

// Every other way to sort the shown list (menus, shortcuts) ends in sortCurrentList: in TK's list
// it always sorts by "#". The criteria are the caller's by-value temporary, which this (the
// callee) must destroy; ours goes on instead, and Traktor's original destroys that one.
void tk_sort_list(void* bf, SortList* criteria) {
  bool tk = false;
  try {
    tk = results_showing();
  } catch (...) {
    tk = false;
  }
  if (!tk) { g_orig_sort_list(bf, criteria); return; }
  alignas(SortList) unsigned char want_buf[sizeof(SortList)];
  SortList* want = nullptr;
  try {
    want = tk_order(want_buf);
  } catch (...) {
    g_orig_sort_list(bf, criteria);
    return;
  }
  criteria->~SortList();
  g_orig_sort_list(bf, want);
}

// --- coloured cells --------------------------------------------------------------------------------

// TK's score colour (style.score_color_smooth: red -> amber -> green), a touch dimmer to sit
// with Traktor's list colours.
unsigned score_bar_argb(double score) {
  static const double stops[3][4] = {{0.0, 239, 83, 80}, {0.5, 255, 179, 0}, {1.0, 0, 230, 118}};
  const double t = (std::max)(0.0, (std::min)(1.0, score / 100.0));
  const int seg = t <= 0.5 ? 0 : 1;
  const double f = (t - stops[seg][0]) / (stops[seg + 1][0] - stops[seg][0]);
  unsigned rgb = 0;
  for (int c = 1; c <= 3; c++) {
    const double v = (stops[seg][c] + (stops[seg + 1][c] - stops[seg][c]) * f) * 0.9;
    rgb = (rgb << 8) | (unsigned)std::lround(v);
  }
  return 0xFF000000u | rgb;
}

// The list's cell text colour, as drawListCell reaches it for its Key column (Win 0xaad914):
// style = *(vcall(this+0x1688, slot 0x20) + 0x10); two ARGB words at style+0x6c. Null when a
// pointer on the way does not look right.
uint32_t* cell_text_argb(void* self) {
  char* owner = (char*)self + kOffTextOwner;
  if (tkw_rtti_name(owner).empty()) return nullptr;
  void* got = tkw_call<void*>(tkw_vfn(owner, kVtTextOwnerStyle), (void*)owner);
  if (!tkw_readable((char*)got + kOffTextStyle, 8)) return nullptr;
  char* style = *(char**)((char*)got + kOffTextStyle);
  if (!tkw_readable(style + kOffStyleArgb, 8)) return nullptr;
  return (uint32_t*)(style + kOffStyleArgb);
}

typedef void (*FnDrawCell)(void*, void*, int, int, const int*);
FnDrawCell g_orig_draw_cell = nullptr;

// BPM MATCH and KEY MATCH cells: Traktor draws TK's text in TK's colour (as TK's table), the way
// it colours its own Key text.
void draw_match_cell(void* self, void* pic, int row, int view, const int* rect, int type) {
  unsigned argb = 0;
  uint32_t* text = nullptr;
  try {  // inside Traktor's draw: on an exception, its own colour
    const ColVals* v = row_vals(self, row);
    argb = !v ? 0 : type == kColBpm ? v->bpm_argb : v->key_argb;
    text = argb ? cell_text_argb(self) : nullptr;
  } catch (...) {
    text = nullptr;
  }
  if (!text) { g_orig_draw_cell(self, pic, row, view, rect); return; }
  const uint32_t was[2] = {text[0], text[1]};
  text[0] = text[1] = argb;
  g_orig_draw_cell(self, pic, row, view, rect);
  text[0] = was[0];
  text[1] = was[1];
}

// SCORE cells: Traktor draws the number (and the cell's background), then a progress bar right of
// it on a dim track, filled with TK's red -> amber -> green ramp up to the score.
// rect = NI::GP::Rect {left, top, right, bottom}, cell-local.
const int kScoreTextW = 26, kScoreBarH = 6, kScoreSlice = 2;
void draw_score_bar(void* pic, const int* rect, double score) {
  const int w = rect[2] - rect[0], h = rect[3] - rect[1];
  const int x = rect[0] + kScoreTextW, track = w - kScoreTextW - 6;
  if (track < 8 || h < kScoreBarH + 2) return;
  const int y = rect[1] + (h - kScoreBarH) / 2;
  callf<void>(WF_A_pic_fillArea, pic, x, y, track, kScoreBarH, 0xFF2A2A2Au);
  const int bar = (int)std::lround(track * score / 100.0);
  for (int at = 0; at < bar; at += kScoreSlice) {
    const int sw = (std::min)(kScoreSlice, bar - at);
    callf<void>(WF_A_pic_fillArea, pic, x + at, y, sw, kScoreBarH, score_bar_argb(100.0 * (at + sw) / track));
  }
}

void tk_draw_cell(void* self, void* pic, int row, int view, const int* rect) {
  const int type = type_of(self, view);
  if (type == kColBpm || type == kColKey) { draw_match_cell(self, pic, row, view, rect, type); return; }
  g_orig_draw_cell(self, pic, row, view, rect);
  if (type != kColScore || !rect || !pic) return;
  double score = -1;
  try {
    const ColVals* v = row_vals(self, row);
    if (v && !v->score.empty()) score = (std::max)(0.0, strtod(v->score.c_str(), nullptr));
  } catch (...) {
    return;  // the number Traktor drew stays, without its bar
  }
  if (score >= 0) draw_score_bar(pic, rect, (std::min)(100.0, score));
}

// Traktor's list asks for 38 column types; it gets 43 (the only caller: TraktorListControl).
typedef void* (*FnColsCtor)(void*, int);
FnColsCtor g_orig_cols_ctor = nullptr;
void* tk_cols_ctor(void* self, int n) {
  if (n == kTraktorCols) tklog("COLS: list columns %d -> %d", n, kAllCols);
  return g_orig_cols_ctor(self, n == kTraktorCols ? kAllCols : n);
}

// --- text from TK's Python ----------------------------------------------------------------------

std::vector<std::string> lines_of(const char* text) {
  std::vector<std::string> out;
  const std::string all(text);
  for (size_t pos = 0; pos < all.size();) {
    size_t end = all.find('\n', pos);
    if (end == std::string::npos) end = all.size();
    if (end > pos) out.push_back(all.substr(pos, end - pos));
    pos = end + 1;
  }
  return out;
}

std::vector<std::string> tab_fields(const std::string& line) {
  std::vector<std::string> out;
  for (size_t at = 0;;) {
    const size_t tab = line.find('\t', at);
    out.push_back(line.substr(at, tab == std::string::npos ? std::string::npos : tab - at));
    if (tab == std::string::npos) return out;
    at = tab + 1;
  }
}

unsigned argb_field(const std::string& hex) { return hex.empty() ? 0 : (unsigned)strtoul(hex.c_str(), nullptr, 16); }

// --- hooks -----------------------------------------------------------------------------------------

// getHeaderText's body, after checking it is still the code the map describes.
void hook_header_text() {
  void* body = tkw_rva(kRvaHeaderTextBody);
  const unsigned char* thunk = (const unsigned char*)WF(WF_A_tlc_getHeaderText);
  const bool thunk_ok = thunk[0] == 0xe9 && thunk + 5 + *(const int32_t*)(thunk + 1) == body;
  if (!thunk_ok || memcmp(body, kHeaderTextBodyBytes, sizeof(kHeaderTextBodyBytes)) != 0) {
    tklog("COLS: getHeaderText body at +0x%x is not the expected code: TK headers off", kRvaHeaderTextBody);
    return;
  }
  tkw_replace(body, "TLC::getHeaderText (body)", (void*)tk_head, (void**)&g_orig_head);
}

}  // namespace

// SCORE, BPM MATCH and KEY MATCH shown as the first columns (on: TK's view is up) or hidden (off).
// On: once Traktor has switched to the results playlist, it is also put back in TK's order.
// The band's visible listener: TK's view shown / hidden (Mac tk_score_column). Main thread.
void tkw_columns_force_tk_sort();
void tkw_columns_tk_view(bool on) {
  try {
    if (on) tkw_after_ms(300, [] { tkw_columns_force_tk_sort(); });
    tlc_search_again();  // TK's view: worth one more look for the list
    void* list = tlc();
    if (!list) return;
    if (!(on ? list_cols_first(list) : list_cols_hidden(list))) return;
    tklog("COLS: SCORE, BPM MATCH, KEY MATCH %s", on ? "first" : "hidden");
    invalidate_form();
  } catch (...) {
    tklog("COLS: TK view %s failed", on ? "on" : "off");
  }
}

// TK's results list in TK's order (the playlists module calls it once it showed the list).
void tkw_columns_force_tk_sort() {
  try {
    force_tk_sort();
  } catch (...) {
    tklog("COLS: TK order failed");
  }
}

// "path\tscore\tbpm\tbpm argb\tkey\tkey argb" lines (workers/native_columns.py), one per TK
// result: only those paths get cells, the rest stay empty. A colour is hex AARRGGBB, empty =
// Traktor's own.
static void set_values(const char* text) {
  g_vals.clear();
  g_paths.clear();  // a relocated file gets its new path on the next paint
  for (const std::string& line : lines_of(text)) {
    std::vector<std::string> f = tab_fields(line);
    if (f.size() < 2) continue;
    f.resize(6);
    ColVals v;
    v.score = f[1];
    v.bpm = f[2];
    v.bpm_argb = argb_field(f[3]);
    v.key = f[4];
    v.key_argb = argb_field(f[5]);
    g_vals[match_key(f[0])] = v;
  }
  invalidate_form();
}

TKW_EXPORT void tk_cols_set_values(const char* text) {
  if (!text) return;
  if (!tkw_is_main_thread()) { tklog("COLS: tk_cols_set_values off the main thread: ignored"); return; }
  try {  // called by ctypes: nothing may unwind into Python
    set_values(text);
  } catch (...) {
    g_vals.clear();
    tklog("COLS: tk_cols_set_values failed: TK cells cleared");
  }
}

// One music folder per line (Traktor's), for Directory (short).
TKW_EXPORT void tk_cols_set_roots(const char* text) {
  if (!text) return;
  if (!tkw_is_main_thread()) { tklog("COLS: tk_cols_set_roots off the main thread: ignored"); return; }
  try {
    g_roots.clear();
    for (const std::string& line : lines_of(text)) {
      std::string root = shown_path(line);
      while (root.size() > 1 && root.back() == '\\') root.pop_back();
      g_roots.push_back(root);
    }
    invalidate_form();
  } catch (...) {
    tklog("COLS: tk_cols_set_roots failed");
  }
}

void tkw_mod_columns_init() {
  struct Hook {
    int entry;
    void* with;
    void** orig;
    const char* what;
  };
  const Hook hooks[] = {
      {WF_A_lcc_ctor, (void*)tk_cols_ctor, (void**)&g_orig_cols_ctor, "ListControlCustomColumns(int)"},
      {WF_A_tlc_getCellValue, (void*)tk_cell, (void**)&g_orig_cell, "TLC::getCellValue"},
      {WF_A_tlc_getPopupMenuEntry, (void*)tk_popup, (void**)&g_orig_popup, "TLC::getPopupMenuEntry"},
      {WF_A_lcb_sortColumn, (void*)tk_sort_column, (void**)&g_orig_sort_column, "LCB::sortColumn"},
      {WF_A_lcd_getHeaderHeight, (void*)tk_header_h, (void**)&g_orig_header_h, "LCD::getHeaderHeight"},
      {WF_A_tlc_drawHeaderCell, (void*)tk_draw_head, (void**)&g_orig_draw_head, "TLC::drawHeaderCell"},
      {WF_A_tlc_drawListCell, (void*)tk_draw_cell, (void**)&g_orig_draw_cell, "TLC::drawListCell"},
      {WF_A_tlc_sortRequest, (void*)tk_sort_click, (void**)&g_orig_sort_click, "TLC::sortRequest"},
      {WF_A_bf_sortCurrentList, (void*)tk_sort_list, (void**)&g_orig_sort_list, "BrowserForm::sortCurrentList"},
  };
  for (const Hook& h : hooks) tkw_replace(WF(h.entry), h.what, h.with, h.orig);
  hook_header_text();
  // Our menu asks getPopupMenuEntry through tk_popup: only with Traktor's original reachable.
  if (g_orig_popup) tkw_replace(WF(WF_A_lcc_createColumnMenu), "LCC::createColumnContextMenu", (void*)tk_col_menu, nullptr);
  cols_store_hook();
  tkw_band_add_visible_listener(tkw_columns_tk_view);
  tkw_band_add_layout_listener(tlc_search_again);  // the list area re-laid out: look again if needed
}