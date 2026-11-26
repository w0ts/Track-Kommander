// tkw_nlist.cpp — a TK page drawn with Traktor's own labels on Windows (port of the Mac
// tk_nlist.mm, the native page: stage 1 is the Spotify page, User 2026-11-24 "i want our stuff to
// feel native").
//
// TK sends a tree (folders) and a table (tracks) as text lines; this draws them in the track
// list's area (Traktor's list is hidden under them) with LabelControls, so they move and paint
// with Traktor's own browser. Text is one font (a label's text colour IS its font); a state's
// colour is a small dot before the text, a label filled with TK's exact colour. Only the visible
// rows have labels (a pool), re-filled when scrolled.
//
// Clicks and the wheel are read from Form::onMouseEvent (this module's own hook on the browser
// form) and hit-tested against this page's own layout. They go to TK as /uiAction
// "NL:TREE:<i>:<button>" / "NL:ROW:<i>:<button>" / "NL:LINK" / "NL:NEW". MAIN THREAD ONLY.
// (The Mac's nl_ctl_event only kept the band's button code away from these labels; the Windows
// band ignores controls that are not its own, so there is nothing to port.)
//
// Windows: labels are made as the band makes them (Traktor's operator new(0x180) + the
// LabelControl constructor; createControl is inlined away). The labels belong to the BrowserForm:
// when Traktor makes a new one, the old pointers are dropped (Traktor deleted those controls).
#include <string.h>

#include <string>
#include <vector>

#include "tkw.h"

// --- from the band (tkw_band.cpp; same signatures as its tkw_band.h) -----------------------------
// The track list's area below the band ({l,t,r,b}, form coordinates): false while TK's view is
// hidden (Mac g_list_orig left/right/bottom + g_list_pushed_top).
bool tkw_band_list_area(int* l, int* t, int* r, int* b);
int tkw_nl_mode();             // the page mode, kept by the band (its relayout narrows/hides the list)
void tkw_set_nl_mode(int mode);  // sets it and relayouts the list for it
void* tkw_band_icon_picture(const char* key, bool lit);  // TK's picture (tk_band_icon), or null
void tkw_band_add_layout_listener(void (*fn)());          // called after every list re-layout
unsigned tkw_band_button_bg();  // NEW request: the band's button fill, TK's style (Mac g_btn_bg)
unsigned tkw_band_form_gen();   // +1 whenever the BrowserForm is forgotten (address may recur)

#define NL_MAX_COLS 8
#define NL_ROW_H 26
#define NL_HEAD_H 20  // Traktor's own list header (getHeaderHeight measured 20 px on the Mac)
#define NL_POOL_ROWS 48
#define NL_FOOT_H 34  // the tree pane's bottom strip: + and Link/Unlink
#define NL_DOT 7
#define NGL_EVT_MOUSE 1000  // map: NGL mouse event ids 0x3e8..0x3ed
#define NGL_EVT_WHEEL 1003

namespace {

const int kFontText = 66, kFontTree = 65, kFontHead = 33;
const unsigned kRowA = 0xff141414u, kRowB = 0xff1c1c1cu, kSel = 0xff253b5bu;
const unsigned kHead = 0xff262626u, kPane = 0xff181818u;
const unsigned kOffEvtButton = 0x0c, kOffEvtState = 0x10;  // map OFF_EVT_BUTTON / OFF_EVT_STATE
const unsigned kOffLblPicture = WOFF_LBL_PICTURE__LABEL_S_PICTURE;  // 0xe0 (Mac 0xc8)
const int kTreeW = 300;  // the tree pane; mode 1 narrows Traktor's list by this (band: kTkwNlTreeW)

struct NLCell {
  std::string text;
  unsigned argb;  // 0: no dot
};
struct NLRow {
  NLCell cells[NL_MAX_COLS];
};
struct NLTreeItem {
  std::string text;
  int depth;
  unsigned argb;
};

std::vector<std::string> g_heads;
std::vector<int> g_weights;
std::vector<NLRow> g_rows;
std::vector<NLTreeItem> g_tree;
int g_row_top = 0, g_tree_top = 0;  // first shown row / tree item (scroll)
int g_sel_row = -1, g_sel_tree = -1;
std::string g_link_text = "Link\xE2\x80\xA6";  // "Link..." with an ellipsis (UTF-8)
// Where the last layout put things, for hit-testing.
int g_L = 0, g_T = 0, g_R = 0, g_B = 0;
// The label pools, all children of g_bf of form generation g_gen.
void* g_bf = nullptr;
unsigned g_gen = 0;
void* g_cell[NL_POOL_ROWS][NL_MAX_COLS];
void* g_cdot[NL_POOL_ROWS][NL_MAX_COLS];
void* g_rowbg[NL_POOL_ROWS];
void* g_head[NL_MAX_COLS];
void* g_titem[NL_POOL_ROWS];
void* g_tdot[NL_POOL_ROWS];
void* g_tree_bg = nullptr;
void* g_link = nullptr;
void* g_new = nullptr;
void* g_link_icon = nullptr;  // TK's pictures on those two buttons (NL_* icons)
void* g_new_icon = nullptr;

template <typename R, typename... A>
R call(int fn, A... a) { return tkw_call<R>(W(fn), a...); }

// The live BrowserForm, or null. The pools belong to one form GENERATION: when the band forgets
// the form (destroyed or replaced; a new one can come back at the same address) every label
// pointer is dropped, unused (they died with the old form). Null form: nothing may touch the
// pools, and they are dropped too. Every use of the pools goes through here first.
void* form() {
  void* bf = tkw_browserform();
  const unsigned gen = tkw_band_form_gen();
  if (bf && bf == g_bf && gen == g_gen) return bf;
  if (g_bf) tklog("NL: BrowserForm gone or replaced (gen %u -> %u): labels dropped", g_gen, gen);
  g_bf = bf;
  g_gen = gen;
  g_L = g_T = 0;
  g_R = g_B = -1;  // no hit-test against the old form's layout
  memset(g_cell, 0, sizeof(g_cell));
  memset(g_cdot, 0, sizeof(g_cdot));
  memset(g_rowbg, 0, sizeof(g_rowbg));
  memset(g_head, 0, sizeof(g_head));
  memset(g_titem, 0, sizeof(g_titem));
  memset(g_tdot, 0, sizeof(g_tdot));
  g_tree_bg = g_link = g_new = g_link_icon = g_new_icon = nullptr;
  return bf;
}

void* nl_label(int font) {
  void* bf = form();
  if (!bf) return nullptr;
  void* l = call<void*>(W_operator_new, (size_t)WSIZE_LABEL);  // Traktor's heap: Traktor deletes it
  if (!l) { tklog("NL: operator new failed"); return nullptr; }
  memset(l, 0, WSIZE_LABEL);
  call<void*>(W_label_ctor, l);
  call<void>(W_label_setFontID, l, (unsigned)font);  // BEFORE addControl, or no text is drawn
  call<void>(W_form_addControl, bf, l);
  call<void>(W_label_setHorzAlignment, l, 0);
  return l;
}

void nl_text(void* l, const std::string& t) { call<void>(W_label_setText_cstr, l, t.c_str()); }

void nl_hide(void* l) {
  if (l) call<void>(W_ctl_setVisible, l, false);
}

void place(void* l, int left, int t, int r, int b, unsigned fill, bool show) {
  if (!l) return;
  const bool on = show && r > left && b > t;
  if (on) {
    int rect[4] = {left, t, r, b};
    call<void>(W_ctl_setPosition, l, (void*)rect);
    call<void>(W_label_setColor, l, fill);
  }
  call<void>(W_ctl_setVisible, l, on);
}

// Where the page draws: the list's area under the band (and cards).
bool nl_area(int* l, int* t, int* r, int* b) {
  if (!form() || !tkw_band_list_area(l, t, r, b)) return false;
  return *r - *l > kTreeW + 100 && *b - *t > NL_HEAD_H + NL_ROW_H;
}

void nl_hide_all() {
  if (form() == nullptr) return;
  for (int i = 0; i < NL_POOL_ROWS; i++) {
    nl_hide(g_rowbg[i]);
    nl_hide(g_titem[i]);
    nl_hide(g_tdot[i]);
    for (int c = 0; c < NL_MAX_COLS; c++) {
      nl_hide(g_cell[i][c]);
      nl_hide(g_cdot[i][c]);
    }
  }
  for (int c = 0; c < NL_MAX_COLS; c++) nl_hide(g_head[c]);
  nl_hide(g_tree_bg);
  nl_hide(g_link);
  nl_hide(g_new);
  nl_hide(g_link_icon);
  nl_hide(g_new_icon);
}

// TK's picture <key> (pushed with tk_band_icon, grey) at x, y on a transparent label: false when
// TK has not sent it (then the button keeps its text only).
bool nl_icon(void*& lbl, const char* key, int x, int y, bool show) {
  void* pic = show ? tkw_band_icon_picture(key, false) : nullptr;
  if (!pic) { nl_hide(lbl); return false; }
  if (!lbl) lbl = nl_label(kFontText);
  if (!lbl) return false;
  *(void**)((char*)lbl + kOffLblPicture) = pic;
  call<void>(W_label_setDrawMode, lbl, 2);
  call<void>(W_label_setPictureIndex, lbl, 0u);
  place(lbl, x, y, x + 24, y + 20, 0x00000000u, true);  // drawn from its top-left corner
  return true;
}

int tree_rows() { return (g_B - g_T - NL_FOOT_H) / NL_ROW_H; }
int table_rows() { return (g_B - g_T - NL_HEAD_H) / NL_ROW_H; }

// A dot (when argb) then the text, in [x, r) of row [y, y + NL_ROW_H).
void nl_cell(void*& text_lbl, void*& dot_lbl, int font, const std::string& text, unsigned argb, int x, int y, int r,
             unsigned bg) {
  if (!text_lbl) text_lbl = nl_label(font);
  if (!dot_lbl) dot_lbl = nl_label(font);
  if (!text_lbl || !dot_lbl) return;
  const int dy = y + (NL_ROW_H - NL_DOT) / 2;
  if (argb) place(dot_lbl, x + 6, dy, x + 6 + NL_DOT, dy + NL_DOT, argb, true);
  else nl_hide(dot_lbl);
  nl_text(text_lbl, text);
  place(text_lbl, x + (argb ? 6 + NL_DOT + 4 : 6), y, r, y + NL_ROW_H, bg, true);
}

// The tree pane: items, then + and Link/Unlink at the bottom (the buttons the Qt page had, with
// their icons, User 2026-11-24).
void layout_tree(int L, int T, int B) {
  if (!g_tree_bg) g_tree_bg = nl_label(kFontTree);
  place(g_tree_bg, L, T, L + kTreeW, B, kPane, true);
  for (int i = 0; i < NL_POOL_ROWS; i++) {
    const int item = g_tree_top + i;
    if (i >= tree_rows() || item >= (int)g_tree.size()) {
      nl_hide(g_titem[i]);
      nl_hide(g_tdot[i]);
      continue;
    }
    const NLTreeItem& it = g_tree[item];
    const int y = T + i * NL_ROW_H;
    const unsigned bg = item == g_sel_tree ? kSel : kPane;
    nl_cell(g_titem[i], g_tdot[i], kFontTree, it.text, it.argb, L + 2 + it.depth * 16, y, L + kTreeW - 4, bg);
  }
  if (!g_new) g_new = nl_label(kFontText);
  if (!g_link) g_link = nl_label(kFontText);
  const unsigned btn_bg = tkw_band_button_bg();
  const bool link_on = !g_link_text.empty();
  const bool unlink = g_link_text.rfind("Unlink", 0) == 0;
  place(g_new, L + 6, B - 30, L + 46, B - 6, btn_bg, true);
  const bool new_icon = nl_icon(g_new_icon, "NL_NEW", L + 14, B - 28, true);
  if (g_new) nl_text(g_new, new_icon ? "" : "  +");
  place(g_link, L + kTreeW - 100, B - 30, L + kTreeW - 8, B - 6, btn_bg, link_on);
  const bool link_icon = nl_icon(g_link_icon, unlink ? "NL_UNLINK" : "NL_LINK", L + kTreeW - 96, B - 28, link_on);
  if (g_link) nl_text(g_link, (link_icon ? "         " : "  ") + g_link_text);
}

// The table: header, then the visible rows, columns by weight.
void layout_table(int T, int R_, int x0) {
  const int width = R_ - x0, ncols = tkw_nl_mode() == 2 ? (int)g_heads.size() : 0;
  int total = 0;
  for (int w : g_weights) total += w > 0 ? w : 1;
  int xs[NL_MAX_COLS + 1] = {0};
  for (int c = 0, x = x0; c < ncols && c < NL_MAX_COLS; c++) {
    xs[c] = x;
    x += width * (g_weights[c] > 0 ? g_weights[c] : 1) / (total ? total : 1);
    xs[c + 1] = x;
  }
  for (int c = 0; c < NL_MAX_COLS; c++) {
    const bool on = c < ncols;
    if (on && !g_head[c]) {
      g_head[c] = nl_label(kFontHead);
      // Centred in the strip, as Traktor's own headers are (User: "the whole header is too high").
      if (g_head[c]) call<bool>(W_textitem_setVertAlignment, (char*)g_head[c] + WOFF_LABEL_TEXTITEM, 1);
    }
    if (!g_head[c]) continue;
    if (on) nl_text(g_head[c], "  " + g_heads[c]);
    place(g_head[c], xs[c], T, xs[c + 1] - 1, T + NL_HEAD_H, kHead, on);
  }
  for (int i = 0; i < NL_POOL_ROWS; i++) {
    const int row = g_row_top + i;
    const bool on = ncols > 0 && i < table_rows() && row < (int)g_rows.size();
    const int y = T + NL_HEAD_H + i * NL_ROW_H;
    const unsigned bg = row == g_sel_row ? kSel : (row % 2 ? kRowB : kRowA);
    if (on && !g_rowbg[i]) g_rowbg[i] = nl_label(kFontText);
    if (g_rowbg[i]) place(g_rowbg[i], x0, y, R_, y + NL_ROW_H, bg, on);
    for (int c = 0; c < NL_MAX_COLS; c++) {
      if (!on || c >= ncols) {
        nl_hide(g_cell[i][c]);
        nl_hide(g_cdot[i][c]);
        continue;
      }
      const NLCell& cell = g_rows[row].cells[c];
      nl_cell(g_cell[i][c], g_cdot[i][c], kFontText, cell.text, cell.argb, xs[c], y, xs[c + 1] - 1, bg);
    }
  }
}

void layout_now() {
  int L, T, R_, B;
  if (!tkw_nl_mode() || !nl_area(&L, &T, &R_, &B)) { nl_hide_all(); return; }
  g_L = L; g_T = T; g_R = R_; g_B = B;
  layout_tree(L, T, B);
  layout_table(T, R_, L + kTreeW + 2);
  call<void>(W_form_invalidate, g_bf, 0u);
}

// Reached from the band's relayout, Traktor's mouse hook and ctypes: no exception leaves it.
void nl_layout() {
  if (g_tkw_quitting) return;
  try {
    layout_now();
  } catch (...) {
    tklog("NL: layout failed");
  }
}

std::vector<std::string> nl_split(const std::string& line) {
  std::vector<std::string> f;
  for (size_t a = 0;;) {
    const size_t z = line.find('\t', a);
    f.push_back(line.substr(a, z == std::string::npos ? std::string::npos : z - a));
    if (z == std::string::npos) break;
    a = z + 1;
  }
  return f;
}

std::vector<std::string> nl_lines(const char* text) {
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

void nl_post(const char* kind, int index, int button) {
  char body[96];
  if (index >= 0) snprintf(body, sizeof(body), "{\"action\":\"NL:%s:%d:%d\"}", kind, index, button);
  else snprintf(body, sizeof(body), "{\"action\":\"NL:%s\"}", kind);
  tkw_post_json("/uiAction", body);
}

void nl_scroll(bool tree, int delta) {
  // The wheel's unit is taken as 1/100 px (NOT verified, on the Mac nor here; step and direction
  // may want a tweak).
  static int acc_tree = 0, acc_rows = 0;
  int& acc = tree ? acc_tree : acc_rows;
  acc += delta;
  const int step = NL_ROW_H * 100, rows = acc / step;
  if (!rows) return;
  acc -= rows * step;
  int& top = tree ? g_tree_top : g_row_top;
  const int count = tree ? (int)g_tree.size() : (int)g_rows.size();
  const int visible = tree ? tree_rows() : table_rows();
  const int last = count - visible > 0 ? count - visible : 0;
  int next = top - rows;  // a positive delta scrolls up, toward the first rows
  next = next < 0 ? 0 : next > last ? last : next;
  if (next == top) return;
  top = next;
  nl_layout();
}

void handle_tree_press(int x, int y, int button) {
  if (y >= g_B - 30 && y <= g_B - 6) {  // the bottom strip
    if (x <= g_L + 46) nl_post("NEW", -1, 0);
    else if (x >= g_L + kTreeW - 100 && !g_link_text.empty()) nl_post("LINK", -1, 0);
    return;
  }
  const int i = (y - g_T) / NL_ROW_H, item = g_tree_top + i;
  if (i < 0 || i >= tree_rows() || item >= (int)g_tree.size()) return;
  g_sel_tree = item;
  nl_layout();
  nl_post("TREE", item, button);
}

// One action per physical press: the real button state decides (Traktor repeats a held press).
// Every browser-form mouse event with the buttons up ends "this press was taken".
bool g_press_taken = false;
bool any_button_down() { return ((GetAsyncKeyState(VK_LBUTTON) | GetAsyncKeyState(VK_RBUTTON)) & 0x8000) != 0; }
bool press_edge() {
  if (!any_button_down()) g_press_taken = false;
  if (g_press_taken) return false;
  g_press_taken = true;
  return true;
}

void handle_press(int x, int y, int button, int state) {
  if (state != 1 || (button != 1 && button != 2) || !press_edge()) return;
  if (x < g_L + kTreeW) { handle_tree_press(x, y, button); return; }
  if (tkw_nl_mode() != 2 || y < g_T + NL_HEAD_H) return;
  const int i = (y - g_T - NL_HEAD_H) / NL_ROW_H, row = g_row_top + i;
  if (i < 0 || i >= table_rows() || row >= (int)g_rows.size()) return;
  g_sel_row = row;
  nl_layout();
  nl_post("ROW", row, button);
}

bool main_or_log(const char* what) {
  if (tkw_is_main_thread()) return true;
  tklog("NL: %s off the main thread: ignored", what);
  return false;
}

// Form::onMouseEvent(this, evt, MouseEventData*) on entry, for the browser form: while the page
// shows, a press (left / right) hit-tested against this page's layout, or the wheel (event 1003:
// data {x, y, 0, delta}, measured on the Mac). Form coordinates. Then the event is emptied
// (button, state, wheel amount 0): Traktor's track list, hidden under the page, would still take
// it (its own right-click menu, a double-click loading a track).
void nl_form_event(unsigned evt, char* d) {
  if (!any_button_down()) g_press_taken = false;
  if (!tkw_nl_mode() || !form() || !tkw_readable(d, kOffEvtState + 4)) return;
  int* v = (int*)d;
  const int x = v[0], y = v[1];
  if (x < g_L || x > g_R || y < g_T || y > g_B) return;
  if (evt == NGL_EVT_WHEEL) {
    nl_scroll(x < g_L + kTreeW, v[3]);
    v[3] = 0;
    return;
  }
  if (evt != NGL_EVT_MOUSE) return;
  handle_press(x, y, *(int*)(d + kOffEvtButton), *(int*)(d + kOffEvtState));
  *(int*)(d + kOffEvtButton) = 0;
  *(int*)(d + kOffEvtState) = 0;
}

}  // namespace

// --- C API for TK's Python (ctypes), main thread ---------------------------------------------------

// First line: the header "weight\tNAME\t..."; then one row per line, "argbhex\ttext\t..." per cell.
TKW_EXPORT void tk_nl_set_table(const char* text) {
  if (!text || !main_or_log("tk_nl_set_table")) return;
  g_heads.clear();
  g_weights.clear();
  g_rows.clear();
  try {  // called by ctypes: nothing may unwind into Python
    bool head = true;
    for (const std::string& line : nl_lines(text)) {
      const std::vector<std::string> f = nl_split(line);
      NLRow row;
      for (size_t k = 0; k + 1 < f.size() && k / 2 < NL_MAX_COLS; k += 2) {
        if (head) {
          g_weights.push_back(atoi(f[k].c_str()));
          g_heads.push_back(f[k + 1]);
        } else {
          row.cells[k / 2] = {f[k + 1], (unsigned)strtoul(f[k].c_str(), nullptr, 16)};
        }
      }
      if (!head) g_rows.push_back(row);
      head = false;
    }
  } catch (...) {
    g_heads.clear();
    g_weights.clear();
    g_rows.clear();
    tklog("NL: tk_nl_set_table failed: table emptied");
  }
  if (g_row_top >= (int)g_rows.size()) g_row_top = 0;
  if (g_sel_row >= (int)g_rows.size()) g_sel_row = -1;
  nl_layout();
}

// Lines "depth\targbhex\ttext"; the selected item and the link button's text ("" = none).
TKW_EXPORT void tk_nl_set_tree(const char* text, int selected, const char* link_text) {
  if (!text || !main_or_log("tk_nl_set_tree")) return;
  g_tree.clear();
  try {  // called by ctypes: nothing may unwind into Python
    for (const std::string& line : nl_lines(text)) {
      const std::vector<std::string> f = nl_split(line);
      if (f.size() < 3) continue;
      g_tree.push_back({f[2], atoi(f[0].c_str()), (unsigned)strtoul(f[1].c_str(), nullptr, 16)});
    }
    if (link_text) g_link_text = link_text;
  } catch (...) {
    g_tree.clear();
    tklog("NL: tk_nl_set_tree failed: tree emptied");
  }
  g_sel_tree = selected;
  nl_layout();
}

TKW_EXPORT void tk_nl_select_row(int row) {
  if (!main_or_log("tk_nl_select_row")) return;
  g_sel_row = row;
  nl_layout();
}

// 0 = no native page; 1 = its tree beside Traktor's own (narrowed) list; 2 = its tree and its own
// table (Traktor's list hidden). The band keeps the mode and narrows / hides its list for it
// (tkw_set_nl_mode relayouts); the layout listener and the call below lay the page out.
TKW_EXPORT void tk_nl_show(int mode) {
  if (!main_or_log("tk_nl_show")) return;
  tkw_set_nl_mode(mode);
  if (!tkw_nl_mode()) nl_hide_all();
  nl_layout();
  tklog("NL: native page mode %d", tkw_nl_mode());
}

namespace {
// Form::onMouseEvent(this rcx, evt edx, MouseEventData* r8), on entry; runs after the band's hook
// on the same function (registration order = module order), as the Mac ran the cards' first.
void on_form_mouse_enter(GumInvocationContext* ic) {
  void* self = tkw_arg(ic, 0);
  if (!self || self != tkw_browserform()) return;
  nl_form_event((unsigned)(uintptr_t)tkw_arg(ic, 1), (char*)tkw_arg(ic, 2));
}
}  // namespace

void tkw_mod_nlist_init() {
  tkw_hook(WF(WF_A_nglform_onMouseEvent), "NGL::Form::onMouseEvent (nlist)", on_form_mouse_enter, nullptr);
  tkw_band_add_layout_listener(nl_layout);  // the list area moved: the page follows it
}
