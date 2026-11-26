// tk_nlist.mm — a TK page drawn with Traktor's own labels (native page, stage 1: the Spotify
// page, User 2026-11-24 "i want our stuff to feel native"). Included by tk_inject.mm.
//
// TK sends a tree (folders) and a table (tracks) as text lines; this draws them in the track
// list's area (Traktor's list is hidden under them) with LabelControls, so they move and paint
// with Traktor's own browser. Text is one font (a label's text colour IS its font, and Traktor's
// coloured fonts differ in size and have no green: fontprobe, 2026-11-24); a state's colour is a
// small dot before the text, a label filled with TK's exact colour. Only the visible rows have
// labels (a pool), re-filled when scrolled.
//
// Clicks and the wheel are read from Form::onMouseEvent and hit-tested against this page's own
// layout (the labels' own events never arrived, 2026-11-24). They go to TK as /uiAction
// "NL:TREE:<i>:<button>" / "NL:ROW:<i>:<button>" / "NL:LINK" / "NL:NEW". MAIN THREAD ONLY.

#define NL_MAX_COLS 8
#define NL_ROW_H 26
#define NL_HEAD_H 20  // Traktor's own list header (getHeaderHeight measured 20 px, 2026-11-24)
#define NL_POOL_ROWS 48
#define NL_FOOT_H 34   // the tree pane's bottom strip: + and Link/Unlink
#define NL_DOT 7
#define NGL_EVT_WHEEL 1003

static const int kNLFontText = 66, kNLFontTree = 65, kNLFontHead = 33;
static const unsigned kNLRowA = 0xff141414u, kNLRowB = 0xff1c1c1cu, kNLSel = 0xff253b5bu;
static const unsigned kNLHead = 0xff262626u, kNLPane = 0xff181818u;

struct NLCell { std::string text; unsigned argb; };  // argb 0: no dot
struct NLRow { NLCell cells[NL_MAX_COLS]; };
struct NLTreeItem { std::string text; int depth; unsigned argb; };

static std::vector<std::string> g_nl_heads;
static std::vector<int> g_nl_weights;
static std::vector<NLRow> g_nl_rows;
static std::vector<NLTreeItem> g_nl_tree;
static int g_nl_row_top = 0, g_nl_tree_top = 0;  // first shown row / tree item (scroll)
static int g_nl_sel_row = -1, g_nl_sel_tree = -1;
static std::string g_nl_link_text = "Link…";
// Where the last layout put things, for hit-testing.
static int g_nl_L = 0, g_nl_T = 0, g_nl_R = 0, g_nl_B = 0, g_nl_x0 = 0;
// The label pools.
static void* g_nl_cell[NL_POOL_ROWS][NL_MAX_COLS];
static void* g_nl_cdot[NL_POOL_ROWS][NL_MAX_COLS];
static void* g_nl_rowbg[NL_POOL_ROWS];
static void* g_nl_head[NL_MAX_COLS];
static void* g_nl_titem[NL_POOL_ROWS];
static void* g_nl_tdot[NL_POOL_ROWS];
static void* g_nl_tree_bg = nullptr;
static void* g_nl_link = nullptr;
static void* g_nl_new = nullptr;
static void* g_nl_link_icon = nullptr;  // TK's pictures on those two buttons (NL_* icons)
static void* g_nl_new_icon = nullptr;

static void* nl_label(int font) {
  void* l = ((void* (*)(void))R(A_ngllabel_createControl))();
  if (!l) return nullptr;
  ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(l, (unsigned)font);  // BEFORE addControl
  ((void (*)(void*, void*))R(A_nglform_addControl))(g_browserform, l);
  ((void (*)(void*, int))R(A_ngllabel_setHorzAlignment))(l, 0);
  return l;
}

static void nl_text(void* l, const std::string& t) {
  ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(l, t.c_str());
}

static void nl_hide(void* l) { if (l) ((void (*)(void*, bool))R(A_nglctl_setVisible))(l, false); }

// Where the page draws: the list's area under the band (and cards).
static bool nl_area(int* l, int* t, int* r, int* b) {
  if (!g_browserform || g_band_top < 0 || !g_band_visible) return false;
  *l = g_list_orig[0]; *t = g_list_pushed_top; *r = g_list_orig[2]; *b = g_list_orig[3];
  return *r - *l > NL_TREE_W + 100 && *b - *t > NL_HEAD_H + NL_ROW_H;
}

static void nl_hide_all(void) {
  for (int i = 0; i < NL_POOL_ROWS; i++) {
    nl_hide(g_nl_rowbg[i]); nl_hide(g_nl_titem[i]); nl_hide(g_nl_tdot[i]);
    for (int c = 0; c < NL_MAX_COLS; c++) { nl_hide(g_nl_cell[i][c]); nl_hide(g_nl_cdot[i][c]); }
  }
  for (int c = 0; c < NL_MAX_COLS; c++) nl_hide(g_nl_head[c]);
  nl_hide(g_nl_tree_bg); nl_hide(g_nl_link); nl_hide(g_nl_new);
  nl_hide(g_nl_link_icon); nl_hide(g_nl_new_icon);
}

// TK's picture <key> (pushed with tk_band_icon, grey) at x, y on a transparent label: false when
// TK has not sent it (then the button keeps its text only).
static bool nl_icon(void*& lbl, const char* key, int x, int y, bool show) {
  const TKIcon* icon = icon_for(key);
  void* pic = icon ? icon->pic[0] : nullptr;
  if (!pic || !show) { nl_hide(lbl); return false; }
  if (!lbl) lbl = nl_label(kNLFontText);
  if (!lbl) return false;
  *(void**)((char*)lbl + OFF_LBL_PICTURE) = pic;
  ((void (*)(void*, int))R(A_ngllabel_setDrawMode))(lbl, 2);
  ((void (*)(void*, unsigned))R(A_ngllabel_setPictureIndex))(lbl, 0);
  place(lbl, x, y, x + 24, y + 20, 0x00000000u, true);  // drawn from its top-left corner
  return true;
}

static int nl_tree_rows(void) { return (g_nl_B - g_nl_T - NL_FOOT_H) / NL_ROW_H; }
static int nl_table_rows(void) { return (g_nl_B - g_nl_T - NL_HEAD_H) / NL_ROW_H; }

// A dot (when argb) then the text, in [x, r) of row [y, y + NL_ROW_H).
static void nl_cell(void*& text_lbl, void*& dot_lbl, int font, const std::string& text, unsigned argb,
                    int x, int y, int r, unsigned bg) {
  if (!text_lbl) text_lbl = nl_label(font);
  if (!dot_lbl) dot_lbl = nl_label(font);
  if (!text_lbl || !dot_lbl) return;
  const int dy = y + (NL_ROW_H - NL_DOT) / 2;
  if (argb) place(dot_lbl, x + 6, dy, x + 6 + NL_DOT, dy + NL_DOT, argb, true);
  else nl_hide(dot_lbl);
  nl_text(text_lbl, text);
  place(text_lbl, x + (argb ? 6 + NL_DOT + 4 : 6), y, r, y + NL_ROW_H, bg, true);
}

static void nl_layout(void) {
  if (g_quitting) return;
  int L, T, R_, B;
  if (!g_nl_mode || !nl_area(&L, &T, &R_, &B)) { nl_hide_all(); return; }
  g_nl_L = L; g_nl_T = T; g_nl_R = R_; g_nl_B = B;

  // The tree pane: items, then + and Link/Unlink at the bottom.
  if (!g_nl_tree_bg) g_nl_tree_bg = nl_label(kNLFontTree);
  place(g_nl_tree_bg, L, T, L + NL_TREE_W, B, kNLPane, true);
  for (int i = 0; i < NL_POOL_ROWS; i++) {
    const int item = g_nl_tree_top + i;
    if (i >= nl_tree_rows() || item >= (int)g_nl_tree.size()) { nl_hide(g_nl_titem[i]); nl_hide(g_nl_tdot[i]); continue; }
    const NLTreeItem& it = g_nl_tree[item];
    const int y = T + i * NL_ROW_H;
    const unsigned bg = item == g_nl_sel_tree ? kNLSel : kNLPane;
    nl_cell(g_nl_titem[i], g_nl_tdot[i], kNLFontTree, it.text, it.argb, L + 2 + it.depth * 16, y, L + NL_TREE_W - 4, bg);
  }
  if (!g_nl_new) g_nl_new = nl_label(kNLFontText);
  if (!g_nl_link) g_nl_link = nl_label(kNLFontText);
  // The buttons the Qt page had: + and Link…/Unlink, with their icons (User, 2026-11-24).
  const bool link_on = !g_nl_link_text.empty();
  const bool unlink = g_nl_link_text.rfind("Unlink", 0) == 0;
  place(g_nl_new, L + 6, B - 30, L + 46, B - 6, g_btn_bg, true);
  const bool new_icon = nl_icon(g_nl_new_icon, "NL_NEW", L + 14, B - 28, true);
  nl_text(g_nl_new, new_icon ? "" : "  +");
  place(g_nl_link, L + NL_TREE_W - 100, B - 30, L + NL_TREE_W - 8, B - 6, g_btn_bg, link_on);
  const bool link_icon = nl_icon(g_nl_link_icon, unlink ? "NL_UNLINK" : "NL_LINK", L + NL_TREE_W - 96, B - 28, link_on);
  nl_text(g_nl_link, (link_icon ? "         " : "  ") + g_nl_link_text);

  // The table: header, then the visible rows, columns by weight.
  const int x0 = L + NL_TREE_W + 2, width = R_ - x0, ncols = g_nl_mode == 2 ? (int)g_nl_heads.size() : 0;
  g_nl_x0 = x0;
  int total = 0;
  for (int w : g_nl_weights) total += w > 0 ? w : 1;
  int xs[NL_MAX_COLS + 1] = {0};
  for (int c = 0, x = x0; c < ncols && c < NL_MAX_COLS; c++) {
    xs[c] = x;
    x += width * (g_nl_weights[c] > 0 ? g_nl_weights[c] : 1) / (total ? total : 1);
    xs[c + 1] = x;
  }
  for (int c = 0; c < NL_MAX_COLS; c++) {
    const bool on = c < ncols;
    if (on && !g_nl_head[c]) {
      g_nl_head[c] = nl_label(kNLFontHead);
      // Centred in the strip, as Traktor's own headers are (User: "the whole header is too high").
      if (g_nl_head[c]) ((void (*)(void*, int))R(A_ngltextitem_setVertAlignment))((char*)g_nl_head[c] + 0xd8, 1);
    }
    if (!g_nl_head[c]) continue;
    if (on) nl_text(g_nl_head[c], "  " + g_nl_heads[c]);
    place(g_nl_head[c], xs[c], T, xs[c + 1] - 1, T + NL_HEAD_H, kNLHead, on);
  }
  for (int i = 0; i < NL_POOL_ROWS; i++) {
    const int row = g_nl_row_top + i;
    const bool on = ncols > 0 && i < nl_table_rows() && row < (int)g_nl_rows.size();
    const int y = T + NL_HEAD_H + i * NL_ROW_H;
    const unsigned bg = row == g_nl_sel_row ? kNLSel : (row % 2 ? kNLRowB : kNLRowA);
    if (on && !g_nl_rowbg[i]) g_nl_rowbg[i] = nl_label(kNLFontText);
    if (g_nl_rowbg[i]) place(g_nl_rowbg[i], x0, y, R_, y + NL_ROW_H, bg, on);
    for (int c = 0; c < NL_MAX_COLS; c++) {
      if (!on || c >= ncols) { nl_hide(g_nl_cell[i][c]); nl_hide(g_nl_cdot[i][c]); continue; }
      const NLCell& cell = g_nl_rows[row].cells[c];
      nl_cell(g_nl_cell[i][c], g_nl_cdot[i][c], kNLFontText, cell.text, cell.argb, xs[c], y, xs[c + 1] - 1, bg);
    }
  }
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

static std::vector<std::string> nl_split(const std::string& line) {
  std::vector<std::string> f;
  for (size_t a = 0;;) {
    size_t z = line.find('\t', a);
    f.push_back(line.substr(a, z == std::string::npos ? std::string::npos : z - a));
    if (z == std::string::npos) break;
    a = z + 1;
  }
  return f;
}

static std::vector<std::string> nl_lines(const char* text) {
  std::vector<std::string> out;
  std::string all(text);
  for (size_t pos = 0; pos < all.size();) {
    size_t end = all.find('\n', pos);
    if (end == std::string::npos) end = all.size();
    if (end > pos) out.push_back(all.substr(pos, end - pos));
    pos = end + 1;
  }
  return out;
}

// First line: the header "weight\tNAME\t…"; then one row per line, "argbhex\ttext\t…" per cell.
TK_EXPORT void tk_nl_set_table(const char* text) {
  if (!pthread_main_np() || !text) return;
  g_nl_heads.clear(); g_nl_weights.clear(); g_nl_rows.clear();
  bool head = true;
  for (const std::string& line : nl_lines(text)) {
    std::vector<std::string> f = nl_split(line);
    NLRow row;
    for (size_t k = 0; k + 1 < f.size() && k / 2 < NL_MAX_COLS; k += 2) {
      if (head) { g_nl_weights.push_back(atoi(f[k].c_str())); g_nl_heads.push_back(f[k + 1]); }
      else row.cells[k / 2] = { f[k + 1], (unsigned)strtoul(f[k].c_str(), nullptr, 16) };
    }
    if (!head) g_nl_rows.push_back(row);
    head = false;
  }
  if (g_nl_row_top >= (int)g_nl_rows.size()) g_nl_row_top = 0;
  if (g_nl_sel_row >= (int)g_nl_rows.size()) g_nl_sel_row = -1;
  nl_layout();
}

// Lines "depth\targbhex\ttext"; the selected item and the link button's text ("" = none).
TK_EXPORT void tk_nl_set_tree(const char* text, int selected, const char* link_text) {
  if (!pthread_main_np() || !text) return;
  g_nl_tree.clear();
  for (const std::string& line : nl_lines(text)) {
    std::vector<std::string> f = nl_split(line);
    if (f.size() < 3) continue;
    g_nl_tree.push_back({ f[2], atoi(f[0].c_str()), (unsigned)strtoul(f[1].c_str(), nullptr, 16) });
  }
  g_nl_sel_tree = selected;
  if (link_text) g_nl_link_text = link_text;
  nl_layout();
}

TK_EXPORT void tk_nl_select_row(int row) { if (pthread_main_np()) { g_nl_sel_row = row; nl_layout(); } }

// 0 = no native page; 1 = its tree beside Traktor's own (narrowed) list; 2 = its tree and its own
// table (Traktor's list hidden). cards_relayout narrows / hides the list, then calls nl_layout.
TK_EXPORT void tk_nl_show(int mode) {
  if (!pthread_main_np()) return;
  g_nl_mode = mode < 0 || mode > 2 ? 0 : mode;
  if (!g_nl_mode) nl_hide_all();
  if (g_browserform && g_band_top >= 0) cards_relayout();
  else nl_layout();
  tklog("NL: native page mode %d", g_nl_mode);
}

static void nl_post(const char* kind, int index, int button) {
  char body[96];
  if (index >= 0) snprintf(body, sizeof(body), "{\"action\":\"NL:%s:%d:%d\"}", kind, index, button);
  else snprintf(body, sizeof(body), "{\"action\":\"NL:%s\"}", kind);
  post_json("/uiAction", body);
}

// Control::onEvent on one of our labels: swallowed (the form-level handler below acts).
static bool nl_ctl_event(void* self, unsigned, const char*) {
  if (!g_nl_mode || !self) return false;
  if (self == g_nl_link || self == g_nl_new || self == g_nl_tree_bg) return true;
  for (int i = 0; i < NL_POOL_ROWS; i++) {
    if (g_nl_titem[i] == self || g_nl_tdot[i] == self || g_nl_rowbg[i] == self) return true;
    for (int c = 0; c < NL_MAX_COLS; c++) if (g_nl_cell[i][c] == self || g_nl_cdot[i][c] == self) return true;
  }
  return false;
}

static void nl_scroll(bool tree, int delta) {
  // The wheel's unit is taken as 1/100 px (NOT verified; step and direction may want a tweak).
  static int acc_tree = 0, acc_rows = 0;
  int& acc = tree ? acc_tree : acc_rows;
  acc += delta;
  const int step = NL_ROW_H * 100, rows = acc / step;
  if (!rows) return;
  acc -= rows * step;
  int& top = tree ? g_nl_tree_top : g_nl_row_top;
  const int count = tree ? (int)g_nl_tree.size() : (int)g_nl_rows.size();
  const int visible = tree ? nl_tree_rows() : nl_table_rows();
  const int last = count - visible > 0 ? count - visible : 0;
  int next = top - rows;  // a positive delta scrolls up, toward the first rows
  next = next < 0 ? 0 : next > last ? last : next;
  if (next == top) return;
  top = next;
  nl_layout();
}

static void nl_handle_press(int x, int y, int button, int state);

// Every mouse event of the browser form while the page shows (before Traktor handles it): a
// press (left / right) hit-tested against this page's layout, or the wheel (event 1003: data
// {x, y, 0, delta}, measured 2026-11-24). Form coordinates. Then the event is emptied (button,
// state, wheel amount 0): Traktor's track list, hidden under the page, still took them — its own
// right-click menu opened over ours, and a double-click could have loaded a track from it.
static void nl_form_event(unsigned evt, char* d) {
  if (!g_nl_mode || !d || !gum_memory_is_readable(d, OFF_EVT_STATE + 4)) return;
  int* v = (int*)d;
  const int x = v[0], y = v[1];
  if (x < g_nl_L || x > g_nl_R || y < g_nl_T || y > g_nl_B) return;
  if (evt == NGL_EVT_WHEEL) {
    nl_scroll(x < g_nl_L + NL_TREE_W, v[3]);
    v[3] = 0;
    return;
  }
  if (evt != NGL_EVT_MOUSE) return;
  nl_handle_press(x, y, *(int*)(d + OFF_EVT_BUTTON), *(int*)(d + OFF_EVT_STATE));
  *(int*)(d + OFF_EVT_BUTTON) = 0;
  *(int*)(d + OFF_EVT_STATE) = 0;
}

static void nl_handle_press(int x, int y, int button, int state) {
  const bool in_tree = x < g_nl_L + NL_TREE_W;
  if (state != 1 || (button != 1 && button != 2) || !press_edge()) return;
  if (in_tree) {
    if (y >= g_nl_B - 30 && y <= g_nl_B - 6) {  // the bottom strip
      if (x <= g_nl_L + 46) nl_post("NEW", -1, 0);
      else if (x >= g_nl_L + NL_TREE_W - 100 && !g_nl_link_text.empty()) nl_post("LINK", -1, 0);
      return;
    }
    const int i = (y - g_nl_T) / NL_ROW_H, item = g_nl_tree_top + i;
    if (i < 0 || i >= nl_tree_rows() || item >= (int)g_nl_tree.size()) return;
    g_nl_sel_tree = item;
    nl_layout();
    nl_post("TREE", item, button);
    return;
  }
  if (g_nl_mode != 2 || y < g_nl_T + NL_HEAD_H) return;
  const int i = (y - g_nl_T - NL_HEAD_H) / NL_ROW_H, row = g_nl_row_top + i;
  if (i < 0 || i >= nl_table_rows() || row >= (int)g_nl_rows.size()) return;
  g_nl_sel_row = row;
  nl_layout();
  nl_post("ROW", row, button);
}
