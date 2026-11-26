// tk_columns.mm — TK's own columns in Traktor's track list (User, 2026-11-24): SCORE, BPM MATCH
// and KEY MATCH (TK's BPM and key distance, coloured as in TK's table), DIRECTORY (the file's folder), Directory (short) (the music folder's name and the path in it, e.g.
// "Tracks/2025-10/"). Included by tk_inject.mm. MAIN THREAD ONLY.
//
// They are real columns of Traktor's list, not an overlay:
//   - TraktorListControl builds ListControlCustomColumns(38): types 0..37 (EContentListColumns;
//     34..37 have no name and stay out of the menu). The constructor hook makes it 43, so types
//     38..42 are TK's. Traktor's header menu lists every type that has a getPopupMenuEntry name:
//     DIRECTORY and Directory (short) are added, removed and moved there like any other column, in every
//     playlist (tk_col_menu: Traktor's builder put the dots by type, not by position).
//   - SCORE, BPM MATCH and KEY MATCH are not in the menu (User): TK shows them as the first
//     columns while its own list is up and hides them elsewhere; they are never stored visible. TK's list is sorted by "#" (its own
//     order, which is TK's score order) and header sort clicks are ignored there. Traktor keeps
//     sorting per playlist path, so the other lists keep theirs.
//   - getCellValue / getHeaderText / getPopupMenuEntry answer for 38..42. A cell's track is
//     Traktor's own (row -> entry index via the row map at +0x1958 -> getTrackByIndex), so the
//     values are right in any playlist; SCORE is empty for tracks not in TK's results.
//   - The layout string ("count type width ..." in display order, a hidden column's width
//     negative) goes to Traktor's settings file through get/setSettingsNormal and History. The
//     unpatched Traktor reads the same file, so TK's types never reach it: they are taken out on
//     the way to the file and kept in TK's own store (list-columns.tsv, keyed by the Traktor part),
//     and put back on the way in. A Traktor part TK has not seen (changed by the unpatched app)
//     gets TK's columns hidden at the end.

#include <sstream>
#include <list>
#include <unordered_map>

static const int kColScore = 38, kColDir = 39, kColDirShort = 40, kColBpm = 41, kColKey = 42;
static const int kTraktorCols = 38, kAllCols = 43;
static const int kScoreMinW = 230;  // px: the SCORE header's words fit
static const int kBpmMinW = 120, kKeyMinW = 100;  // px: "BPM MATCH" / "126 ×2  +5.0%", "KEY MATCH"
#define OFF_TLC_ROWMAP      0x1958  // std::vector<int>*: row -> entry index (null: the same)
#define OFF_TLC_CONTENTLIST 0x18d0  // SmartPtr<ContentList> (atomic); entries vector at +0x30

// One TK result's cells (tk_cols_set_values): text, and the BPM/KEY text colour (0 = Traktor's).
struct ColVals { std::string score, bpm, key; unsigned bpm_argb = 0, key_argb = 0; };
static std::map<std::string, ColVals> g_col_vals;  // path -> TK's cells
static std::vector<std::string> g_col_roots;              // Traktor's music folders, no trailing /
static std::unordered_map<uint32_t, std::string> g_col_paths;  // collection key -> file path

static bool is_tk_col(int col) { return col >= kColScore && col <= kColKey; }
// The columns TK shows first in its own list only (never in the menu, never stored visible).
static const int kTkListCols[3] = { kColScore, kColBpm, kColKey };
static bool is_tk_list_col(int col) { return col == kColScore || col == kColBpm || col == kColKey; }

static std::string col_dir(const std::string& path) {
  const size_t slash = path.rfind('/');
  return slash == std::string::npos ? std::string() : path.substr(0, slash + 1);
}

static std::string col_dir_short(const std::string& path) {
  const std::string dir = col_dir(path);
  for (const std::string& root : g_col_roots) {
    if (dir.compare(0, root.size() + 1, root + "/") != 0) continue;
    const size_t name = root.rfind('/');
    return root.substr(name == std::string::npos ? 0 : name + 1) + dir.substr(root.size());
  }
  return dir;
}

// The entry index of list row <row>, as getCellValue computes it; -1 when out of range.
static int col_row_entry(void* self, int row) {
  if (row < 0) return -1;
  void* map = *(void**)((char*)self + OFF_TLC_ROWMAP);
  if (map) {
    if (!gum_memory_is_readable(map, 16)) return -1;
    const int* b = *(int* const*)map;
    const int* e = *(int* const*)((char*)map + 8);
    return b && row < (int)(e - b) ? b[row] : -1;
  }
  return row;
}

// One spelling for accented paths: Traktor keeps some decomposed ("u" + U+0308), TK sends them
// composed, so "Grüv" never matched (User, 2026-11-24: a result without a score). NFC both sides.
static std::string unicode_form(const std::string& s, CFStringNormalizationForm form) {
  bool ascii = true;
  for (unsigned char c : s) if (c >= 0x80) { ascii = false; break; }
  if (ascii) return s;
  CFStringRef cf = CFStringCreateWithBytes(nullptr, (const UInt8*)s.data(), (CFIndex)s.size(),
                                           kCFStringEncodingUTF8, false);
  if (!cf) return s;
  CFMutableStringRef m = CFStringCreateMutableCopy(nullptr, 0, cf);
  CFRelease(cf);
  if (!m) return s;
  CFStringNormalize(m, form);
  const CFIndex len = CFStringGetLength(m);
  CFIndex bytes = 0;
  CFStringGetBytes(m, CFRangeMake(0, len), kCFStringEncodingUTF8, 0, false, nullptr, 0, &bytes);
  std::string out((size_t)bytes, '\0');
  CFStringGetBytes(m, CFRangeMake(0, len), kCFStringEncodingUTF8, 0, false, (UInt8*)out.data(), bytes, nullptr);
  CFRelease(m);
  return out;
}

static std::string nfc(const std::string& s) { return unicode_form(s, kCFStringNormalizationFormC); }
static std::string nfd(const std::string& s) { return unicode_form(s, kCFStringNormalizationFormD); }

// The file path (NFC) of list row <row>: Traktor's own track for that row, its key, its path
// (cached).
static std::string col_row_path(void* self, int row) {
  const int entry = col_row_entry(self, row);
  if (entry < 0) return std::string();
  void* track = nullptr;  // SmartPtr<ContentEntryTrack> via x8: one raw pointer, one reference
  call_sret2(R(A_tlc_getTrackByIndex), self, (void*)(intptr_t)entry, &track);
  if (!track) return std::string();
  std::string path;
  if (looks_like_object(track) && gum_memory_is_readable(track, OFF_ENTRY_KEY + 4)) {
    const uint32_t key = read_u32_field(track, OFF_ENTRY_KEY);
    auto it = g_col_paths.find(key);
    if (it == g_col_paths.end()) it = g_col_paths.emplace(key, nfc(path_of_key(key))).first;
    path = it->second;
  }
  ((void (*)(void*))R(A_refcount_release))(track);
  return path;
}

static long (*g_orig_cell)(void*, int, int, void*) = nullptr;
static long tk_cell(void* self, int row, int col, void* value) {
  if (!is_tk_col(col)) return g_orig_cell(self, row, col, value);
  const std::string path = col_row_path(self, row);
  std::string text;
  if (is_tk_list_col(col)) {
    auto it = g_col_vals.find(path);
    if (it != g_col_vals.end())
      text = col == kColScore ? it->second.score : col == kColBpm ? it->second.bpm : it->second.key;
  } else if (!path.empty()) {
    text = col == kColDir ? col_dir(path) : col_dir_short(path);
  }
  ((void (*)(void*, const std::string*))R(A_anv_setString))(value, &text);
  return 1;
}

// The header menu's names, written like Traktor's own ("File Path").
static const std::string& tk_col_name(int col) {
  static const std::string names[5] = { "Score", "Directory", "Directory (short)", "BPM Match", "Key Match" };
  return names[col - kColScore];
}

// Header texts, in capitals like Traktor's ("FILE PATH"). The SCORE header says what a click on it
// does (it opens TK's matching cards, User 2026-11-24).
static const void* (*g_orig_head)(void*, int) = nullptr;
static const void* tk_head(void* self, int col) {
  static const std::string heads[5] = { "SCORE", "DIRECTORY", "DIRECTORY (SHORT)", "BPM MATCH", "KEY MATCH" };
  static const std::string score_closed = "SCORE (CLICK FOR DETAILS)";
  static const std::string score_open = "SCORE (CLICK TO HIDE DETAILS)";
  if (col == kColScore) return g_cards_expanded ? &score_open : &score_closed;
  return is_tk_col(col) ? (const void*)&heads[col - kColScore] : g_orig_head(self, col);
}

// The header menu's entry for a column type (std::string by value: x8, as Traktor's). SCORE, BPM
// MATCH and KEY MATCH are not in the menu: TK shows them, first, in its own list and hides them
// everywhere else.
static std::string (*g_orig_popup)(const void*, int) = nullptr;
static std::string tk_popup(const void* self, int col) {
  if (is_tk_list_col(col)) return std::string();
  return is_tk_col(col) ? tk_col_name(col) : g_orig_popup(self, col);
}

static int col_view_of(const void* list, int type) {
  void* const* vt = *(void* const* const*)list;
  return ((int (*)(const void*, int))vt[VT_LCC_GETVIEWCOLUMN / 8])(list, type);
}

// Traktor's createColumnContextMenu sets each dot with setState(<type>), but setState takes the
// entry's POSITION: types 34..37 have no entry, so every type after them lost its dot. Same menu,
// positions counted.
static void tk_col_menu(const void* self, void* menu) {
  const int types = *(const int*)((const char*)self + 0x17b0);
  int pos = 0;
  for (int type = 0; type < types; type++) {
    const std::string name = tk_popup(self, type);
    if (name.empty()) continue;
    ((void (*)(void*, const char*, const char*, int))R(A_popup_addEntry))(menu, name.c_str(), nullptr, type);
    ((void (*)(void*, int, int))R(A_popup_setState))(menu, pos++, col_view_of(self, type) == -1 ? 2 : 1);
  }
}

// --- TK's own list: SCORE first, the list in TK's order (the "#" column) ---

// The member search can land on a base sub-object (measured: list+0x3b0, whose vtable names the
// same class): the whole object is the pointer plus the vtable's offset-to-top (vptr[-2]).
static void* tlc(void) {
  static void* cached = nullptr;  // the browser's list lives as long as the window
  if (cached && looks_like_object(cached)) return cached;
  cached = nullptr;
  void* area = g_browserform ? *(void**)((char*)g_browserform + 0x518) : nullptr;
  char* p = area && looks_like_object(area) ? (char*)find_member_object(area, "TraktorListControl") : nullptr;
  if (!p) return nullptr;
  const intptr_t* vptr = *(const intptr_t* const*)p;
  if (!gum_memory_is_readable(vptr - 2, 16)) return nullptr;
  const intptr_t to_top = vptr[-2];
  cached = to_top <= 0 && to_top > -0x4000 ? p + to_top : nullptr;
  return cached;
}

// The list's header grows by the cards' gap while they are open (tk_inject.mm
// cards_relayout puts the cards there). ListControlDefImpl::getHeaderHeight is virtual: font height
// + paddings; rows, scrolling and hit-tests all ask it.
// While the header is tall (cards open), each header cell is drawn in its normal height at the
// top, so its words stay where they were instead of centring in the gap.
static void (*g_orig_draw_head)(void*, void*, int, const int*) = nullptr;
static void tk_draw_head(void* self, void* pic, int view, const int* rect) {
  if (!rect || g_cards_gap_h <= 0 || !cards_on() || self != tlc()) { g_orig_draw_head(self, pic, view, rect); return; }
  const int top[4] = { rect[0], rect[1], rect[2], rect[1] + g_list_header_h };
  g_orig_draw_head(self, pic, view, top);
}

static int (*g_orig_header_h)(const void*) = nullptr;
static int tk_header_h(const void* self) {
  const int h = g_orig_header_h(self);
  if (self != tlc()) return h;
  if (h != g_list_header_h) { g_list_header_h = h; tklog("COLS: list header %d px", h); }
  return kCardsInList && g_cards_gap_h > 0 && cards_on() ? h + g_cards_gap_h : h;
}

// TK's list columns in place: SCORE, BPM MATCH, KEY MATCH as views 0..2, each at least as wide
// as its header's words; false when they already were.
static bool tk_list_cols_first(void* list) {
  static const int min_w[3] = { kScoreMinW, kBpmMinW, kKeyMinW };
  bool changed = false;
  for (int i = 0; i < 3; i++) {
    const int type = kTkListCols[i];
    int view = col_view_of(list, type);
    if (view == i) continue;
    changed = true;
    if (view < 0) {
      ((void (*)(void*, int, bool))R(A_lcc_enableColumn))(list, type, true);
      view = col_view_of(list, type);
    }
    if (view >= 0 && view != i) ((void (*)(void*, int, int))R(A_lcc_moveColumn))(list, view, i);
    void* const* vt = *(void* const* const*)list;
    const int w = ((int (*)(const void*, int))vt[VT_LCB_GETCOLUMNWIDTH / 8])(list, i);
    if (w < min_w[i]) ((void (*)(void*, int, int))R(A_lcb_setColumnWidth))(list, i, min_w[i]);
  }
  return changed;
}

// TK's list columns hidden; false when none was showing.
static bool tk_list_cols_hidden(void* list) {
  bool changed = false;
  for (int type : kTkListCols) {
    if (col_view_of(list, type) < 0) continue;
    ((void (*)(void*, int, bool))R(A_lcc_enableColumn))(list, type, false);
    changed = true;
  }
  return changed;
}

// SCORE, BPM MATCH and KEY MATCH shown as the first columns (on) or hidden (off). On: once Traktor
// has switched to the results playlist, it is also put back in TK's order.
static void tk_score_column(bool on) {
  if (on)
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 300 * (int64_t)NSEC_PER_MSEC), dispatch_get_main_queue(),
                   ^{ tk_force_tk_sort(g_browserform); });
  void* list = tlc();
  if (!list) return;
  if (!(on ? tk_list_cols_first(list) : tk_list_cols_hidden(list))) return;
  tklog("COLS: SCORE, BPM MATCH, KEY MATCH %s", on ? "first" : "hidden");
  if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

// TK's results are a real playlist (do_playlist_set, "...\aResults"): showing when the browser's
// shown ContentList (+0x708) has that playlist's structure path (a std::string at list+0x10, the
// one getCurrentListSorting reads).
static std::string g_results_sp;
static bool tk_results_showing(void) {
  if (!g_browserform) return false;
  const char* list = *(char* const*)((char*)g_browserform + OFF_BF_CONTENTLIST);
  if (!list || !gum_memory_is_readable(list + 0x10, 24)) return false;
  if (g_results_sp.empty()) {
    const std::string tp = results_tree_path();
    alignas(16) char sp[32];
    if (tp.empty() || !structure_path(tp, sp)) return false;
    g_results_sp = decode_std_string(sp);
    tklog("COLS: TK results playlist is '%s'", g_results_sp.c_str());
  }
  return decode_std_string(list + 0x10) == g_results_sp;
}

struct TKSortCriteria { int32_t attribute; int32_t direction; };  // 8 bytes, as in the NML
typedef std::list<TKSortCriteria> TKSortList;

// TK's list is built in TK's score order: sorting it by "#" (ascending) shows exactly that order.
// Traktor keeps sorting per playlist path, so no other list's sorting changes.
static void tk_force_tk_sort(void* bf) {
  if (!bf || !tk_results_showing()) return;  // never another playlist's sorting
  alignas(TKSortList) unsigned char buf[sizeof(TKSortList)];
  call_sret2(R(A_bf_getCurrentListSorting), bf, nullptr, buf);
  TKSortList* now = (TKSortList*)buf;
  const bool right = now->size() == 1 && now->front().attribute == TK_SORT_ATTR_INDEX &&
                     now->front().direction == 0;
  std::string seen;
  for (const auto& c : *now) seen += std::to_string(c.attribute) + "/" + std::to_string(c.direction) + " ";
  now->~TKSortList();
  tklog("COLS: TK list sorting was [%s]%s", seen.c_str(), right ? "" : " -> # ascending");
  if (right) return;
  TKSortList want{ { TK_SORT_ATTR_INDEX, 0 } };
  ((void (*)(void*, TKSortList*))R(A_bf_sortCurrentList))(bf, &want);
}

// A header click is ListControlBase::doSelectionOnMouseUp -> sortColumn(VIEW column, direction)
// (sets the header's sort arrow) -> a control event -> the browser sorts the playlist. In TK's
// results: the SCORE header is the cards' button (User, 2026-11-24: it opens and closes TK's
// matching cards, as the band's MATCHING button did); "#" may sort (it IS TK's order); every other
// header does nothing. Elsewhere: Traktor's own.
static void (*g_orig_sort_column)(void*, int, int) = nullptr;
static void tk_sort_column(void* self, int view, int direction) {
  if (self != tlc() || !tk_results_showing()) { g_orig_sort_column(self, view, direction); return; }
  void* const* vt = *(void* const* const*)self;
  const int type = ((int (*)(const void*, int))vt[VT_LCC_GETDATACOLUMN / 8])(self, view);
  if (type == 1) { g_orig_sort_column(self, view, direction); return; }  // "#"
  if (type != kColScore) { tklog("COLS: header sort ignored in TK's list (type %d)", type); return; }
  g_cards_expanded = !g_cards_expanded;
  tklog("COLS: SCORE header -> cards %s", g_cards_expanded ? "open" : "closed");
  cards_relayout();
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);  // the header's words
}

// The browser's own sort request (TraktorListControl::sortRequest, after the click's event):
// nothing to do in TK's results (the order stays TK's).
static void (*g_orig_sort_click)(void*, unsigned, bool) = nullptr;
static void tk_sort_click(void* self, unsigned col, bool add) {
  if (self == tlc() && tk_results_showing()) return;
  g_orig_sort_click(self, col, add);
}

// Every other way to sort the shown list (menus, shortcuts) ends in sortCurrentList: in TK's list
// it always sorts by "#" (TK's order). The list is the caller's by-value temporary: not ours to
// change, so ours is passed instead.
static void (*g_orig_sort_list)(void*, TKSortList*) = nullptr;
static void tk_sort_list(void* bf, TKSortList* criteria) {
  if (!tk_results_showing()) { g_orig_sort_list(bf, criteria); return; }
  TKSortList want{ { TK_SORT_ATTR_INDEX, 0 } };
  g_orig_sort_list(bf, &want);
}

// TK's score colour (style.score_color_smooth: red -> amber -> green), a touch dimmer to sit
// with Traktor's list colours.
static unsigned score_bar_argb(double score) {
  static const double stops[3][4] = { { 0.0, 239, 83, 80 }, { 0.5, 255, 179, 0 }, { 1.0, 0, 230, 118 } };
  const double t = std::max(0.0, std::min(1.0, score / 100.0));
  const int seg = t <= 0.5 ? 0 : 1;
  const double f = (t - stops[seg][0]) / (stops[seg + 1][0] - stops[seg][0]);
  unsigned rgb = 0;
  for (int c = 1; c <= 3; c++) {
    const double v = (stops[seg][c] + (stops[seg + 1][c] - stops[seg][c]) * f) * 0.9;
    rgb = (rgb << 8) | (unsigned)std::lround(v);
  }
  return 0xFF000000u | rgb;
}

// SCORE cells: Traktor draws the number (and the cell's background, so nothing can go under it),
// then a progress bar right of it on a dim track: as long as the score, filled with TK's
// red -> amber -> green ramp up to that point (a high score ends green).
// rect = NI::GP::Rect {left, top, right, bottom}, cell-local.
static const int kScoreTextW = 26, kScoreBarH = 6, kScoreSlice = 2;
static void (*g_orig_draw_cell)(void*, void*, int, int, const int*) = nullptr;

// The list's cell text colour, as TraktorListControl::drawListCell reaches it for its Key column
// (0x10082bfe0..c158): style = *(vcall(this+0x1670, slot 0x20) + 0x10); two ARGB words at
// style+0x6c (both set to keyColor(), saved before, put back after the base draw). Null when a
// pointer on the way does not look right.
static uint32_t* cell_text_argb(void* self) {
  char* owner = (char*)self + OFF_TLC_TEXT_OWNER;
  if (!looks_like_object(owner)) return nullptr;
  void* const* vt = *(void* const* const*)owner;
  void* got = ((void* (*)(void*))vt[VT_TEXT_OWNER_STYLE / 8])(owner);
  if (!got || !gum_memory_is_readable((char*)got + OFF_TEXT_STYLE, 8)) return nullptr;
  char* style = *(char**)((char*)got + OFF_TEXT_STYLE);
  if (!style || !gum_memory_is_readable(style + OFF_STYLE_TEXT_ARGB, 8)) return nullptr;
  return (uint32_t*)(style + OFF_STYLE_TEXT_ARGB);
}

// BPM MATCH and KEY MATCH cells: Traktor draws TK's text in TK's colour (as TK's table: BPM by
// its match score, Key one colour per harmonic relationship), the way it colours its own Key text.
static void tk_draw_match_cell(void* self, void* pic, int row, int view, const int* rect, int type) {
  auto it = g_col_vals.find(col_row_path(self, row));
  const unsigned argb = it == g_col_vals.end() ? 0 : type == kColBpm ? it->second.bpm_argb : it->second.key_argb;
  uint32_t* text = argb ? cell_text_argb(self) : nullptr;
  if (!text) { g_orig_draw_cell(self, pic, row, view, rect); return; }
  const uint32_t was[2] = { text[0], text[1] };
  text[0] = text[1] = argb;
  g_orig_draw_cell(self, pic, row, view, rect);
  text[0] = was[0];
  text[1] = was[1];
}

static void tk_draw_cell(void* self, void* pic, int row, int view, const int* rect) {
  void* const* vt = *(void* const* const*)self;
  const int type = ((int (*)(const void*, int))vt[VT_LCC_GETDATACOLUMN / 8])(self, view);
  if (type == kColBpm || type == kColKey) { tk_draw_match_cell(self, pic, row, view, rect, type); return; }
  g_orig_draw_cell(self, pic, row, view, rect);
  if (type != kColScore || !rect || !pic) return;
  auto it = g_col_vals.find(col_row_path(self, row));
  if (it == g_col_vals.end() || it->second.score.empty()) return;
  const double score = std::max(0.0, std::min(100.0, strtod(it->second.score.c_str(), nullptr)));
  const int w = rect[2] - rect[0], h = rect[3] - rect[1];
  const int x = rect[0] + kScoreTextW, track = w - kScoreTextW - 6;
  if (track < 8 || h < kScoreBarH + 2) return;
  const int y = rect[1] + (h - kScoreBarH) / 2;
  typedef void (*fn_fill)(void*, int, int, int, int, unsigned);
  ((fn_fill)R(A_pic_fillArea))(pic, x, y, track, kScoreBarH, 0xFF2A2A2Au);
  const int bar = (int)std::lround(track * score / 100.0);
  for (int at = 0; at < bar; at += kScoreSlice) {
    const int sw = std::min(kScoreSlice, bar - at);
    ((fn_fill)R(A_pic_fillArea))(pic, x + at, y, sw, kScoreBarH, score_bar_argb(100.0 * (at + sw) / track));
  }
}

// Traktor's list asks for 38 column types; it gets 41 (the only caller: TraktorListControl).
static void* (*g_orig_cols_ctor)(void*, int) = nullptr;
static void* tk_cols_ctor(void* self, int n) {
  if (n == kTraktorCols) tklog("COLS: list columns %d -> %d", n, kAllCols);
  return g_orig_cols_ctor(self, n == kTraktorCols ? kAllCols : n);
}

// --- The layout string: TK's types out on the way to Traktor's file, back in on the way in ---

struct ColLayout { std::vector<std::pair<int, int>> pairs; bool ok = false; };

static ColLayout layout_parse(const std::string& s) {
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
static std::string layout_text(const std::vector<std::pair<int, int>>& pairs) {
  std::string s = std::to_string(pairs.size()) + " ";
  for (const auto& p : pairs) s += std::to_string(p.first) + " " + std::to_string(p.second) + " ";
  return s;
}

static bool layout_has_tk(const ColLayout& l) {
  for (const auto& p : l.pairs) if (is_tk_col(p.first)) return true;
  return false;
}

// SCORE, BPM MATCH and KEY MATCH are never stored visible: TK shows them only while its own list
// is up.
static ColLayout layout_score_hidden(ColLayout l) {
  for (auto& p : l.pairs) if (is_tk_list_col(p.first) && p.second > 0) p.second = -p.second;
  return l;
}

static std::string layout_traktor_part(const ColLayout& l) {
  std::vector<std::pair<int, int>> kept;
  for (const auto& p : l.pairs) if (!is_tk_col(p.first)) kept.push_back(p);
  return layout_text(kept);
}

static std::map<std::string, std::string> g_col_store;  // Traktor part -> the full layout
static bool g_col_store_loaded = false;

static std::string col_store_file(void) {
  return std::string([NSHomeDirectory() UTF8String]) +
         "/Library/Application Support/Track Kommander/traktor-native/list-columns.tsv";
}

static void col_store_load(void) {
  if (g_col_store_loaded) return;
  g_col_store_loaded = true;
  std::ifstream in(col_store_file());
  std::string line;
  while (std::getline(in, line)) {
    const size_t tab = line.find('\t');
    if (tab != std::string::npos) g_col_store[line.substr(0, tab)] = line.substr(tab + 1);
  }
  tklog("COLS: %zu stored layout(s)", g_col_store.size());
}

static void col_store_save(void) {
  const std::string file = col_store_file(), tmp = file + ".tmp";
  {
    std::ofstream out(tmp, std::ios::trunc);
    if (!out) { tklog("COLS: cannot write %s", tmp.c_str()); return; }
    for (const auto& kv : g_col_store) out << kv.first << '\t' << kv.second << '\n';
  }
  if (rename(tmp.c_str(), file.c_str()) != 0) tklog("COLS: cannot replace %s", file.c_str());
}

// Keep the store small: Traktor's layouts are few (Normal, History); stale ones go first.
static void col_store_put(const std::string& traktor_part, const std::string& full) {
  col_store_load();
  auto it = g_col_store.find(traktor_part);
  if (it != g_col_store.end() && it->second == full) return;
  if (g_col_store.size() >= 32 && it == g_col_store.end()) g_col_store.erase(g_col_store.begin());
  g_col_store[traktor_part] = full;
  col_store_save();
}

// Outgoing (to Traktor's settings file): TK's types taken out, the full layout kept.
static void col_layout_out(std::string& s) {
  const ColLayout l = layout_parse(s);
  if (!l.ok || !layout_has_tk(l)) return;
  const std::string traktor_part = layout_traktor_part(l);
  col_store_put(traktor_part, layout_text(layout_score_hidden(l).pairs));
  s = traktor_part;
}

// Incoming (from Traktor's settings file): TK's types put back where the user left them.
static std::string col_layout_in(const std::string& s) {
  const ColLayout l = layout_parse(s);
  if (!l.ok || layout_has_tk(l)) return s;
  col_store_load();
  auto it = g_col_store.find(layout_text(l.pairs));
  if (it != g_col_store.end()) {
    const ColLayout stored = layout_parse(it->second);
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

typedef void (*fn_get_layout)(const void*, std::string&);
typedef void (*fn_set_layout)(void*, const std::string&);
static fn_get_layout g_orig_get_normal = nullptr, g_orig_get_history = nullptr;
static fn_set_layout g_orig_set_normal = nullptr, g_orig_set_history = nullptr;

static void tk_get_normal(const void* self, std::string& out) { g_orig_get_normal(self, out); col_layout_out(out); }
static void tk_get_history(const void* self, std::string& out) { g_orig_get_history(self, out); col_layout_out(out); }
static void tk_set_normal(void* self, const std::string& in) { g_orig_set_normal(self, col_layout_in(in)); }
static void tk_set_history(void* self, const std::string& in) { g_orig_set_history(self, col_layout_in(in)); }

// All of the above, inside the caller's interceptor transaction.
static void tk_cols_hook(GumInterceptor* it) {
  struct { uintptr_t at; void* with; void** orig; const char* what; } hooks[] = {
    { A_lcc_ctor, (void*)tk_cols_ctor, (void**)&g_orig_cols_ctor, "ListControlCustomColumns(int)" },
    { A_tlc_getCellValue, (void*)tk_cell, (void**)&g_orig_cell, "getCellValue" },
    { A_tlc_getHeaderText, (void*)tk_head, (void**)&g_orig_head, "getHeaderText" },
    { A_tlc_getPopupMenuEntry, (void*)tk_popup, (void**)&g_orig_popup, "getPopupMenuEntry" },
    { A_lcb_sortColumn, (void*)tk_sort_column, (void**)&g_orig_sort_column, "sortColumn" },
    { A_lcd_getHeaderHeight, (void*)tk_header_h, (void**)&g_orig_header_h, "getHeaderHeight" },
    { A_tlc_drawHeaderCell, (void*)tk_draw_head, (void**)&g_orig_draw_head, "drawHeaderCell" },
    { A_tlc_getSettingsNormal, (void*)tk_get_normal, (void**)&g_orig_get_normal, "getSettingsNormal" },
    { A_tlc_getSettingsHistory, (void*)tk_get_history, (void**)&g_orig_get_history, "getSettingsHistory" },
    { A_tlc_setSettingsNormal, (void*)tk_set_normal, (void**)&g_orig_set_normal, "setSettingsNormal" },
    { A_tlc_setSettingsHistory, (void*)tk_set_history, (void**)&g_orig_set_history, "setSettingsHistory" },
    { A_lcc_createColumnMenu, (void*)tk_col_menu, nullptr, "createColumnContextMenu" },
    { A_tlc_drawListCell, (void*)tk_draw_cell, (void**)&g_orig_draw_cell, "drawListCell" },
    { A_tlc_sortRequest, (void*)tk_sort_click, (void**)&g_orig_sort_click, "sortRequest" },
    { A_bf_sortCurrentList, (void*)tk_sort_list, (void**)&g_orig_sort_list, "sortCurrentList" },
  };
  for (auto& h : hooks) {
    const GumReplaceReturn rc = gum_interceptor_replace(it, R(h.at), h.with, h.orig, nullptr);
    tklog("COLS: replace %s -> %d", h.what, (int)rc);
  }
}

// A field of a tab-separated line; empty past the end.
static std::vector<std::string> tab_fields(const std::string& line) {
  std::vector<std::string> out;
  size_t at = 0;
  for (;;) {
    const size_t tab = line.find('\t', at);
    out.push_back(line.substr(at, tab == std::string::npos ? std::string::npos : tab - at));
    if (tab == std::string::npos) return out;
    at = tab + 1;
  }
}

static unsigned argb_field(const std::string& hex) {
  return hex.empty() ? 0 : (unsigned)strtoul(hex.c_str(), nullptr, 16);
}

// "path\tscore\tbpm\tbpm argb\tkey\tkey argb" lines (workers/native_columns.py), one per TK
// result: only those paths get cells, the rest stay empty. A colour is hex AARRGGBB, empty =
// Traktor's own.
TK_EXPORT void tk_cols_set_values(const char* text) {
  if (!pthread_main_np() || !text) return;
  g_col_vals.clear();
  g_col_paths.clear();  // a relocated file gets its new path on the next paint
  for (const std::string& line : nl_lines(text)) {
    std::vector<std::string> f = tab_fields(line);
    if (f.size() < 2) continue;
    f.resize(6);
    ColVals v;
    v.score = f[1];
    v.bpm = f[2];
    v.bpm_argb = argb_field(f[3]);
    v.key = f[4];
    v.key_argb = argb_field(f[5]);
    g_col_vals[nfc(f[0])] = v;
  }
  if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

// One music folder per line (Traktor's), for Directory (short).
TK_EXPORT void tk_cols_set_roots(const char* text) {
  if (!pthread_main_np() || !text) return;
  g_col_roots.clear();
  for (std::string line : nl_lines(text)) {
    while (line.size() > 1 && line.back() == '/') line.pop_back();
    g_col_roots.push_back(line);
  }
}
