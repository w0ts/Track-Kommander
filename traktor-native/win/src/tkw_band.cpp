// tkw_band.cpp — TK's band inside Windows Traktor (port of the Mac tk_inject.mm band): a row of
// Traktor's own LabelControls carved above the track list — "MATCHING SOURCE:" A–D, AUTO, LIST,
// the library pages, the add-on's spare slots X1..X4, Settings/About — in groups with a line
// between them, icon buttons on Traktor-style tiles, lit tiles for the source deck, the page shown
// and the AUTO mode. The band shows only while TK's tree node (or the Results playlist it opens)
// is selected; elsewhere it hides and the list gets its height back.
//
// The BrowserForm is taken from its own onDrawBackground/resized (checked by RTTI) and forgotten
// in its destructor. The list area (BrowserForm+0x570, a TabSubFormControl; Mac +0x518) is moved
// only when RTTI says it is that class. Labels are made like Traktor makes them (operator new +
// LabelControl's constructor; createControl is inlined on Windows). Clicks use EventData's
// button/state (+0xc/+0x10, as on the Mac); the real button state only ends a press.
// Other files of the band: tkw_band_icons.cpp, tkw_band_tree.cpp, tkw_band_tips.cpp, tkw_cards.cpp.
#include <stdio.h>
#include <stdlib.h>

#include "tkw_band_int.h"

namespace tkb {

unsigned g_btn_bg = 0xff3a3a3au;
int g_btn_halign = 1, g_btn_valign = 1;
// Band fonts (an NGL font id carries its colour): lit buttons black text (48), off ones grey (30),
// captions small grey (157); card titles a size smaller (3 / 157). The Mac's ids — "bandfont" and
// "band font" try others live if Windows numbers them differently.
int g_font = 36, g_font_on = 48, g_font_off = 30, g_font_caption = 157, g_card_font_on = 3,
    g_card_font_off = 157;
void* g_bf = nullptr;
bool g_built = false;
bool g_band_visible = false;
unsigned g_status_from_tk = 0;
int g_band_top = -1, g_band_left = 0, g_band_right = 0, g_band_btn_h = 44;
int g_list_orig[4];
bool g_list_orig_set = false;
int g_list_pushed_top = -1, g_list_pushed_left = -1;
char g_active_page[16] = "";
int g_nl_mode = 0;
bool g_tk_selected = false;
char g_auto_mode[16] = "play_cue";
unsigned g_auto_pic = 435;  // AUTO's picture follows TK's mode: headphones 435, play 133, record 680
unsigned g_auto_bg = 0;     // 0 = the normal tile
char g_auto_text[16] = "Off";

// ---- Traktor calls ------------------------------------------------------------------------------
template <typename R, typename... A>
R wf(int entry, A... a) {
  return tkw_call<R>(WF(entry), a...);
}

void set_pos(void* c, int l, int t, int r, int b) {
  int rc[4] = {l, t, r, b};
  wf<void>(WF_A_nglctl_setPosition, c, (void*)rc);
}
void set_visible(void* c, bool on) { wf<void>(WF_A_nglctl_setVisible, c, on); }
void set_text(void* c, const char* text) { wf<void>(WF_A_ngllabel_setText_cstr, c, text ? text : ""); }
void set_font(void* c, int font) { wf<void>(WF_A_ngllabel_setFontID, c, (unsigned)font); }
void set_color(void* c, unsigned argb) { wf<void>(WF_A_ngllabel_setColor, c, argb); }
void set_halign(void* c, int a) { wf<void>(WF_A_ngllabel_setHorzAlignment, c, a); }
void set_valign(void* c, int a) { wf<bool>(WF_A_ngltextitem_setVertAlignment, (char*)c + kOffTextItem, a); }
void set_draw_mode(void* c, int mode) { wf<void>(WF_A_ngllabel_setDrawMode, c, mode); }
void set_picture_id(void* c, unsigned id) { wf<void>(WF_A_ngllabel_setPictureID, c, id); }
void set_picture_index(void* c, unsigned i) { wf<void>(WF_A_ngllabel_setPictureIndex, c, i); }
void invalidate() {
  if (g_bf) wf<void>(WF_A_nglform_invalidate, g_bf, 0u);
}
int* rect_of(void* c) { return (int*)((char*)c + WOFF_CTRL_RECT); }

void copy_str(char* dst, size_t n, const char* src) { snprintf(dst, n, "%s", src ? src : ""); }
double now_s() { return (double)GetTickCount64() / 1000.0; }

void* new_label(const char* text) {
  if (!g_bf) return nullptr;
  void* mem = wf<void*>(WF_A_op_new, (size_t)kSzLabel);  // Traktor's heap: Traktor deletes it
  if (!mem) {
    tklog("BAND: operator new(0x%x) failed — no label", kSzLabel);
    return nullptr;
  }
  memset(mem, 0, kSzLabel);
  tkw_call<void*>(W(W_label_ctor), mem);
  set_font(mem, g_font);  // before addControl, or no text is drawn
  set_text(mem, text);
  assign_id(mem, g_bf);   // its tooltip key (TooltipManager is inlined on Windows)
  wf<void>(WF_A_nglform_addControl, g_bf, mem);
  return mem;
}

static void style(void* c, unsigned bg, int halign, int valign) {
  set_color(c, bg);
  set_halign(c, halign);
  set_valign(c, valign);
}

void place(void* l, int l_, int t, int r, int b, unsigned fill, bool show) {
  if (!l) return;
  const bool ok = show && r > l_ && b > t;
  if (ok) {
    set_pos(l, l_, t, r, b);
    set_color(l, fill);
  }
  set_visible(l, ok);
}

static int g_area_off = WOFF_BF_LISTAREA;  // "band area <offset>" when +0x570 is not the list area
static void* g_area_ok = nullptr;          // the last list area RTTI confirmed

void* list_area() {
  if (!g_bf || g_area_off < 0 || g_area_off > 0x2000 || !tkw_readable((char*)g_bf + g_area_off, 8))
    return nullptr;
  void* a = *(void**)((char*)g_bf + g_area_off);
  if (!a) return nullptr;
  if (a == g_area_ok) return a;
  if (tkw_rtti_name(a).find("TabSubFormControl") == std::string::npos) return nullptr;
  g_area_ok = a;
  return a;
}

// ---- press tracking: one action per physical press ---------------------------------------------
static bool g_press_taken = false;
bool mouse_down() {
  const int vk = GetSystemMetrics(SM_SWAPBUTTON) ? VK_RBUTTON : VK_LBUTTON;
  return (GetAsyncKeyState(vk) & 0x8000) != 0;
}
void press_track() {
  if (!mouse_down()) g_press_taken = false;
}
bool press_edge() {
  press_track();
  if (g_press_taken) return false;
  g_press_taken = true;
  return true;
}

}  // namespace tkb

using namespace tkb;

namespace {

// ---- the buttons ---------------------------------------------------------------------------------
struct Btn {
  const char* name;  // also the action a click sends (a literal: lives forever)
  int w;
  void* ctrl;
  void* tile;   // Traktor-style tile under an icon button
  bool hidden;  // "bandhide": e.g. a spare slot nobody took
};
// MATCHING and AUTO are one group; with LIST, the page buttons are one selector: the library
// pages with Stats last | spare buttons an add-on may take (TK sets their icon and tooltip, and
// hides the ones nobody took: ui/band_extensions.py) | Settings, then About last.
const struct {
  const char* name;
  int w;
} kTable[] = {
    {"A", 40},        {"B", 40},           {"C", 40},         {"D", 40},           {"MATCHING", 40},
    {"AUTO", 40},     {"LIST", 40},        {"L_SCAN", 40},    {"L_HOTCUES", 40},   {"L_TAGGING", 40},
    {"L_PLAYLISTS", 40}, {"L_EXPORT", 40}, {"STATS", 40},     {"X1", 40},          {"X2", 40},
    {"X3", 40},       {"X4", 40},          {"S_SETTINGS", 40}, {"S_ABOUT", 40},
};
const int kMaxBtns = 24;
Btn g_btn[kMaxBtns];
int g_nbtn = 0;
void* g_source_lbl = nullptr;  // "MATCHING SOURCE:" before A–D (text only, not clickable)
void* g_status_lbl = nullptr;  // band text right of the buttons: "Loading …" etc.
void* g_sep[8] = {nullptr};    // a line between two button groups
std::string g_status_text;     // the status TK asked for, kept for when the band shows
char g_deck_sel = 0;           // 'A'..'D' from TK's `deck` command, 0 = none
bool g_build_queued = false;
bool g_build_failed = false;      // the last build() of this form found no list area
ULONGLONG g_last_build_try = 0;   // builds are retried from paints, at most every 500 ms
// Event log budget: 40 lines at start, `band evtlog` re-arms it. Only events that could be a
// press are logged: Traktor's broadcasts to every control (1000005, 1000008), hover traffic
// (move 1002, enter/leave 1004/1005, 100003) and 1103 used the whole budget on the first Windows
// runs before the first click.
int g_btn_evt_seen = 0;
const int kEvtLogBudget = 40;
bool is_noise_evt(unsigned evt) {
  return evt == 1000005 || evt == 1000008 || evt == 1002 || evt == 1004 || evt == 1005 || evt == 100003 ||
         evt == 1103;
}
// The event's first 8 words, raw: the press layout is what this log is for.
void log_evt(const char* who, unsigned evt, const char* d) {
  const int* w = (const int*)d;
  tklog("%s evt=%u words %d %d %d %d %d %d %d %d", who, evt, w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[7]);
}
const int kSourceW = 124, kAfterSourceGap = 22, kSepGap = 22, kBtnH = 28;
void (*g_click_cb)(const char*) = nullptr;  // stage 1's Python callback (embed_win.py)
void (*g_layout_cb)(void) = nullptr;        // TK's page follows the list (tk_set_layout_callback)

// Registries other modules fill from their init (tkw_band.h); read on the main thread after.
std::vector<TkwCtlEventFn>& ctl_handlers() {
  static std::vector<TkwCtlEventFn> v;
  return v;
}
std::vector<TkwFormMouseFn>& form_handlers() {
  static std::vector<TkwFormMouseFn> v;
  return v;
}
std::vector<void (*)()>& layout_listeners() {
  static std::vector<void (*)()> v;
  return v;
}
std::vector<void (*)(bool)>& visible_listeners() {
  static std::vector<void (*)(bool)> v;
  return v;
}

int band_group(const char* n) {
  if (n[1] == 0 && n[0] >= 'A' && n[0] <= 'D') return 0;
  if (!strcmp(n, "MATCHING") || !strcmp(n, "AUTO")) return 1;
  if (!strcmp(n, "LIST")) return 2;
  if (n[0] == 'X' && n[1] >= '1' && n[1] <= '9' && n[2] == 0) return 4;  // an add-on's pages
  if (!strncmp(n, "S_", 2)) return 5;                                   // Settings, About
  if (!strncmp(n, "L_", 2) || !strcmp(n, "STATS")) return 3;            // library pages, Stats
  return 6;
}

unsigned tile_fill(const Btn& b) {
  unsigned fill = g_btn_bg;
  if (!strcmp(b.name, "MATCHING") && g_cards_expanded) fill = kBtnSelectedBg;
  // LIST and the page buttons are one view switch: exactly one is lit (LIST = the playlist).
  if (g_active_page[0] && !strcmp(b.name, g_active_page)) fill = kBtnSelectedBg;
  if (!g_active_page[0] && !strcmp(b.name, "LIST")) fill = kBtnSelectedBg;
  if (!strcmp(b.name, "AUTO") && g_auto_bg) fill = g_auto_bg;
  return fill;
}

// An icon button's picture: TK's silhouette (grey off, black lit) when TK sent one, else
// Traktor's own picture in its lit or plain frame.
void apply_icon(const Btn& b, bool lit) {
  if (void* mine = icon_pic(b.name, lit)) {
    // The raw Picture* goes into the label's ResourceRef (+0xe0); its picture id (+0xd8) stays
    // Traktor's. The Mac does the same. Checked on Windows (2026-11-25): LabelControl's destructor
    // 0x1ddaa80 only resets the ResourceRef's vtables (+0xc8/+0xd0) and the Control destructor
    // 0x1dd93d0 never touches +0xc8..+0xe0, so Traktor never frees this pointer (ours leaks with
    // the label, as on the Mac); a later setPictureID releases the old picture by ID only.
    *(void**)((char*)b.ctrl + kOffLblPicture) = mine;
    set_draw_mode(b.ctrl, 2);
    set_picture_index(b.ctrl, 0);
    return;
  }
  const unsigned pic = !strcmp(b.name, "AUTO") ? g_auto_pic : band_icon(b.name);
  set_picture_index(b.ctrl, lit ? icon_lit_frame(pic) : 0);
}

// Traktor draws a label's picture from its top-left corner: size an icon label to its picture
// and centre it in the button's slot.
void button_rect(const Btn& b, int x, int y, int* rect) {
  rect[0] = x; rect[1] = y; rect[2] = x + b.w; rect[3] = y + kBtnH;
  const char* pic = tkw_readable((char*)b.ctrl + kOffLblPicture, 8)
                        ? *(const char* const*)((char*)b.ctrl + kOffLblPicture) : nullptr;
  if (!band_icon(b.name) || !pic || !tkw_readable(pic, 0x48)) return;
  const int pw = tkw_call<int>(WF(WF_A_pic_animWidth), (const void*)pic);
  const int ph = tkw_call<int>(WF(WF_A_pic_animHeight), (const void*)pic);
  if (pw <= 0 || ph <= 0 || pw > b.w + 8 || ph > kBtnH + 8) return;
  const int cx = x + b.w / 2, cy = y + kBtnH / 2;
  rect[0] = cx - pw / 2; rect[1] = cy - ph / 2; rect[2] = rect[0] + pw; rect[3] = rect[1] + ph;
}

}  // namespace

namespace tkb {

// SOURCE:, the buttons and the lines between groups, along the band.
void place_buttons() {
  if (g_tkw_quitting || !g_built) return;
  int x = g_band_left;
  const int y = g_band_top + 8, h = kBtnH;
  if (g_source_lbl) set_pos(g_source_lbl, x, y, x + kSourceW, y + h);
  x += kSourceW + 6;
  int nsep = 0, last_group = -1;
  for (int i = 0; i < g_nbtn; i++) {
    Btn& b = g_btn[i];
    if (b.hidden) {
      set_visible(b.ctrl, false);
      if (b.tile) set_visible(b.tile, false);
      continue;
    }
    set_visible(b.ctrl, g_band_visible);
    const int group = band_group(b.name);
    if (last_group >= 0 && group != last_group && nsep < (int)(sizeof(g_sep) / sizeof(g_sep[0]))) {
      if (!g_sep[nsep]) g_sep[nsep] = new_label("");
      place(g_sep[nsep], x + kSepGap / 2 - 4, y + 4, x + kSepGap / 2 - 3, y + h - 4, 0xff505050u, g_band_visible);
      nsep++;
      x += kSepGap;
    }
    last_group = group;
    if (b.tile) {
      const unsigned fill = tile_fill(b);
      place(b.tile, x, y, x + b.w, y + h, fill, g_band_visible);
      apply_icon(b, fill != g_btn_bg);
    }
    int rect[4];
    button_rect(b, x, y, rect);
    set_pos(b.ctrl, rect[0], rect[1], rect[2], rect[3]);
    x += b.w + 6;
  }
  for (int k = nsep; k < (int)(sizeof(g_sep) / sizeof(g_sep[0])); k++)  // lines no longer needed
    if (g_sep[k]) set_visible(g_sep[k], false);
}

void set_status(const char* text) {
  g_status_text = text ? text : "";
  if (!g_bf || g_band_top < 0 || g_nbtn == 0) return;
  if (!g_status_lbl) {
    g_status_lbl = new_label("");
    if (!g_status_lbl) return;
    style(g_status_lbl, 0x00000000u, 0, g_btn_valign);  // transparent, left-aligned
  }
  const int* last = rect_of(g_btn[g_nbtn - 1].ctrl);  // after the last button (About)
  set_text(g_status_lbl, g_status_text.c_str());
  set_pos(g_status_lbl, last[2] + 16, last[1], g_band_right, last[3]);
  set_visible(g_status_lbl, g_band_visible && !g_status_text.empty());
  invalidate();
}

void notify_layout() {
  for (auto fn : layout_listeners()) fn();
  tkw_layout_changed();
}

void run_form_mouse_handlers(unsigned evt, char* data) {
  for (auto fn : form_handlers()) fn(evt, data);
}

void band_set_visible(bool on) {
  if (!g_bf || g_band_top < 0 || !g_built) {
    g_band_visible = on;
    return;
  }
  const bool changed = on != g_band_visible;
  g_band_visible = on;
  if (g_source_lbl) set_visible(g_source_lbl, on);
  for (int i = 0; i < g_nbtn; i++) set_visible(g_btn[i].ctrl, on);
  for (void* sep : g_sep)
    if (sep) set_visible(sep, on);
  if (g_status_lbl && !on) set_visible(g_status_lbl, false);
  relayout();  // shows/hides the cards and puts the list where it belongs (and places buttons)
  for (auto fn : visible_listeners()) fn(on);  // TK's SCORE column: first in TK's view only
  if (changed) tklog("BAND: %s", on ? "shown (TK view)" : "hidden");
}

const char* button_at(const void* control) {
  if (!control) return nullptr;
  for (int i = 0; i < g_nbtn; i++)
    if (g_btn[i].ctrl == control || (g_btn[i].tile && g_btn[i].tile == control)) return g_btn[i].name;
  return nullptr;
}

bool is_source_label(const void* control) { return control && control == g_source_lbl; }

}  // namespace tkb

namespace {

// A–D show TK's source deck: that button blue with black text, the others grey.
void apply_deck_sel() {
  for (int i = 0; i < g_nbtn; i++) {
    const char* n = g_btn[i].name;
    if (n[1] != 0 || n[0] < 'A' || n[0] > 'D') continue;
    const bool lit = n[0] == g_deck_sel;
    set_color(g_btn[i].ctrl, lit ? kBtnSelectedBg : g_btn_bg);
    const int font = lit ? g_font_on : g_font_off;
    if (font > 0) set_font(g_btn[i].ctrl, font);
  }
  if (g_source_lbl && g_font_caption > 0) set_font(g_source_lbl, g_font_caption);
  invalidate();
}

// AUTO shows TK's mode with Traktor's pictures; the tile behind carries its colour. The icon
// label stays transparent (a filled label sized to the picture showed as a box behind it).
void apply_auto_state() {
  for (int i = 0; i < g_nbtn; i++) {
    if (strcmp(g_btn[i].name, "AUTO") != 0) continue;
    set_picture_id(g_btn[i].ctrl, g_auto_pic);
    set_color(g_btn[i].ctrl, 0x00000000u);
  }
  place_buttons();  // each mode's picture has its own size: centre it again on the tile
  invalidate();
}

Btn make_button(const char* name, int w, int x, int y) {
  Btn b = {name, w, nullptr, nullptr, false};
  const unsigned icon = band_icon(name);
  // Traktor's buttons always sit on a tile: added first so it draws under the icon.
  if (icon) b.tile = new_label("");
  b.ctrl = new_label(!strcmp(name, "AUTO") ? g_auto_text : name);
  if (!b.ctrl) return b;
  style(b.ctrl, icon ? 0x00000000u : g_btn_bg, g_btn_halign, g_btn_valign);
  set_pos(b.ctrl, x, y, x + w, y + kBtnH);
  set_visible(b.ctrl, true);
  if (icon) {  // Traktor's own picture instead of text (draw mode 2 shows a label's picture)
    set_text(b.ctrl, "");
    set_draw_mode(b.ctrl, 2);
    set_picture_id(b.ctrl, icon);
  }
  // The cards open from the SCORE column's header now: MATCHING stays built (its code, its
  // tooltip) but is left out of the band.
  b.hidden = !strcmp(name, "MATCHING");
  return b;
}

void start_embedded_once() {
  static bool started = false;
  const std::string embed = tkw_env("TK_EMBED");
  if (started || embed.empty()) return;
  started = true;
  // As the Mac band does (main 79c023b): a missing boot script is said in the band, not only logged.
  if (GetFileAttributesA(embed.c_str()) == INVALID_FILE_ATTRIBUTES) {
    tklog("EMBED: Track Kommander not found at %s", embed.c_str());
    tkw_band_set_status(("Track Kommander not found at " + embed).c_str());
    return;
  }
  // Open on TK's view as a click would, once Traktor has restored its own last selection.
  tkw_after_ms(500, [] { select_our_node(); });
  tkw_after_ms(1000, [embed] {
    if (g_tkw_quitting) return;
    tklog("EMBED: starting Track Kommander inside Traktor (%s)", embed.c_str());
    tkw_pyexec(embed);
  });
}

void build() {
  g_build_queued = false;
  if (g_built || !g_bf || g_tkw_quitting) return;
  void* area = list_area();
  g_build_failed = !area;
  if (!area) {
    tklog("BAND: BrowserForm+0x%x is not the list area ('%s') — band NOT built (run 'band probe')",
          g_area_off, tkw_readable((char*)g_bf + g_area_off, 8)
                          ? tkw_rtti_name(*(void**)((char*)g_bf + g_area_off)).c_str() : "unreadable");
    return;
  }
  const int* lr = rect_of(area);
  if (!g_list_orig_set) {
    memcpy(g_list_orig, lr, sizeof(g_list_orig));
    g_list_orig_set = true;
  }
  g_band_top = lr[1];
  g_band_left = lr[0] + 4;
  g_band_right = lr[2] - 4;
  int x = g_band_left;
  const int y = g_band_top + 8;
  g_source_lbl = new_label("MATCHING SOURCE:");
  if (g_source_lbl) {
    style(g_source_lbl, 0x00000000u, 2, g_btn_valign);  // transparent, right-aligned
    set_pos(g_source_lbl, x, y, x + kSourceW, y + kBtnH);
  }
  x += kSourceW + 6;
  g_nbtn = 0;
  for (const auto& t : kTable) {
    if (g_nbtn >= kMaxBtns) break;
    Btn b = make_button(t.name, t.w, x, y);
    if (!b.ctrl) continue;
    x += t.w + 6 + (!strcmp(t.name, "MATCHING") ? kAfterSourceGap : 0);
    g_btn[g_nbtn++] = b;
  }
  g_built = true;
  place_buttons();
  apply_deck_sel();
  apply_auto_state();
  band_set_visible(g_band_visible);  // hidden until TK's view is selected
  if (!g_status_text.empty()) set_status(g_status_text.c_str());
  tree_adopt_from_form(g_bf);  // the tree node, should onReloadForm have run before our hooks
  tklog("BAND: built (%d buttons) at top %d left %d right %d", g_nbtn, g_band_top, g_band_left, g_band_right);
  tkw_post_json("/uiAction", "{\"action\":\"BAND\"}");  // TK answers with its source deck
  start_embedded_once();
}

// Traktor destroyed the BrowserForm (or showed another one): its controls, ours included, are
// gone. Drop every pointer into it; a later BrowserForm gets a new band.
unsigned g_form_gen = 1;  // +1 each time the BrowserForm is forgotten (tkw_band_form_gen)

void forget_form(const char* why) {
  g_form_gen++;  // other modules drop what they cached for this form (its address may come back)
  tklog("BAND: forgetting BrowserForm %p (%s), generation now %u", g_bf, why, g_form_gen);
  forget_ids(g_bf);
  g_bf = nullptr;
  g_built = false;
  g_build_queued = false;
  g_build_failed = false;
  g_area_ok = nullptr;
  memset(g_btn, 0, sizeof(g_btn));
  g_nbtn = 0;
  g_source_lbl = g_status_lbl = nullptr;
  memset(g_sep, 0, sizeof(g_sep));
  g_band_top = -1;
  g_list_orig_set = false;
  g_list_pushed_top = g_list_pushed_left = -1;
  forget_cards();
  forget_tree();
  forget_icons();
}

// BrowserForm::resized laid its children out again (window resize, layout change): the track
// list is back at Traktor's own top, over our buttons. Take that as the new base and lay out.
void relayout_after_resize(void* self) {
  if (!g_built || self != g_bf || g_band_top < 0 || g_nbtn == 0) return;
  void* list = list_area();
  if (!list) return;
  const int* lr = rect_of(list);
  if (lr[1] == g_list_pushed_top && lr[0] == g_list_pushed_left && lr[2] == g_list_orig[2]) return;
  memcpy(g_list_orig, lr, sizeof(g_list_orig));
  g_band_top = lr[1];
  g_band_left = lr[0] + 4;
  g_band_right = lr[2] - 4;
  place_buttons();
  tklog("BAND: Traktor re-laid out the browser -> band re-applied (list top %d)", g_band_top);
  relayout();
}

// A separator drag moves the list's edges without BrowserForm::resized (first Windows run,
// 2026-11-25: the band and the cards kept the old left edge, A–D under the tree). Checked after
// every paint of the form; a move queues one re-layout on the message loop, never inside the paint.
// relayout() puts the list where this check expects it, so it settles.
bool g_relayout_queued = false;
void relayout_if_list_moved() {
  if (g_relayout_queued || !g_built || !g_bf || g_band_top < 0 || !g_list_orig_set) return;
  void* list = list_area();
  if (!list || !tkw_readable(rect_of(list), 16)) return;
  const int* cur = rect_of(list);
  if (cur[0] == g_list_pushed_left && cur[1] == g_list_pushed_top && cur[2] == g_list_orig[2]) return;
  g_relayout_queued = true;
  tkw_on_main([] {
    g_relayout_queued = false;
    if (g_tkw_quitting || !g_bf) return;
    relayout();
  });
}

// Not inside Traktor's paint: controls are added from the message loop. Throttled, because a
// form whose band cannot be built yet (no list area) asks again on every paint.
void queue_build() {
  const ULONGLONG now = GetTickCount64();
  if (g_build_queued || now - g_last_build_try < 500) return;
  g_build_queued = true;
  g_last_build_try = now;
  tkw_on_main(build);
}

// A BrowserForm painting or resizing. The form we hold keeps the band until its destructor, even
// when another BrowserForm is alive and paints too (no tear-down/rebuild ping-pong); we move to
// another form only while ours has no band because its build failed.
void seen_form(void* self) {
  if (g_bf == self) {
    if (!g_built) queue_build();
    return;
  }
  if (g_bf && (g_built || !g_build_failed)) return;
  if (tkw_rtti_name(self) != ".?AVBrowserForm@@") return;
  if (g_bf) forget_form("its band could not be built; another BrowserForm paints");
  g_bf = self;
  g_build_failed = false;
  tklog("BAND: BrowserForm %p", self);
  queue_build();
}

void button_clicked(const char* name) {
  if (!strcmp(name, "MATCHING")) {  // the band's own business: no round trip to TK
    g_cards_expanded = !g_cards_expanded;
    relayout();
    return;
  }
  tklog("BTN click %s -> POST /uiAction", name);
  char body[96];
  snprintf(body, sizeof(body), "{\"action\":\"%s\"}", name);
  tkw_post_json("/uiAction", body);
  tkw_on_main([name] {  // TK's Python runs from the message loop, not inside Traktor's dispatch
    if (g_click_cb) g_click_cb(name);
  });
}

// NGL::Control::onEvent(this, evt, EventData*): every control without its own onEvent passes
// here (hot path: bail out fast unless `this` is ours).
void control_event(void* self, unsigned evt, const char* data) {
  for (auto fn : ctl_handlers())
    if (fn(self, evt, data)) return;  // a deck's ALIGN button, the native page
  if (!g_built) return;
  if (cards_ctl_event(self, evt, data)) return;
  const char* name = button_at(self);
  if (!name) return;
  press_track();
  if (!data || !tkw_readable(data, kOffEvtState + 4)) return;
  const int button = *(const int*)(data + kOffEvtButton), state = *(const int*)(data + kOffEvtState);
  if (g_btn_evt_seen < kEvtLogBudget && !is_noise_evt(evt) && tkw_readable(data, 32)) {
    g_btn_evt_seen++;
    log_evt((std::string("BTN ") + name).c_str(), evt, data);
  }
  // One press = one action: an icon label over its tile both answer, and Traktor repeats a held
  // press (20+ per click): act once per physical press.
  if (evt != kEvtMouse || button != 1 || state != 1 || !press_edge()) return;
  button_clicked(name);
}

std::string probe() {
  if (!g_bf) return "no BrowserForm yet";
  std::string out;
  for (int off = 0x500; off <= 0x700; off += 8) {
    if (!tkw_readable((char*)g_bf + off, 8)) break;
    void* p = *(void**)((char*)g_bf + off);
    const std::string n = tkw_rtti_name(p);
    if (n.empty()) continue;
    char line[300];
    snprintf(line, sizeof(line), "+0x%x=%s ", off, n.c_str());
    out += line;
    tklog("PROBE BrowserForm+0x%x -> %p %s", off, p, n.c_str());
  }
  return out.empty() ? "no C++ objects in +0x500..0x700" : out;
}

std::string band_command(const std::string& a) {
  if (a.empty()) return g_built ? "built" : (g_bf ? "BrowserForm seen, not built" : "no BrowserForm yet");
  if (a == "probe") return probe();
  if (a == "evtlog") {  // log the next 40 button/form mouse events (BTN evt / FORM lines)
    g_btn_evt_seen = 0;
    return "logging the next " + std::to_string(kEvtLogBudget) + " band events";
  }
  if (a == "build") {
    build();
    return g_built ? "built" : "not built (see log)";
  }
  if (a.rfind("area ", 0) == 0) {  // try another list-area offset, then build
    g_area_off = (int)strtol(a.c_str() + 5, nullptr, 0);
    g_area_ok = nullptr;
    build();
    return g_built ? "built" : "not built (see log)";
  }
  if (a.rfind("font ", 0) == 0 && g_built) {  // the band's base font (A–D keep on/off fonts)
    g_font = atoi(a.c_str() + 5);
    for (int i = 0; i < g_nbtn; i++)
      if (!band_icon(g_btn[i].name)) {
        set_font(g_btn[i].ctrl, g_font);
        set_text(g_btn[i].ctrl, g_btn[i].name);
      }
    apply_deck_sel();
    return "font " + std::to_string(g_font);
  }
  return "ERR band [probe|build|area <off>|font <id>]";
}

// ---- hooks ---------------------------------------------------------------------------------------
void on_form_enter(GumInvocationContext* ic) {  // onDrawBackground / resized: (this, ...)
  tkw_adopt_main_thread();
  seen_form(tkw_arg(ic, 0));
}
void on_drawbg_enter(GumInvocationContext* ic) {
  on_form_enter(ic);
  cards_draw_enter(tkw_arg(ic, 0), tkw_arg(ic, 1));  // (this, Picture& rdx, Region const& r8)
}
void on_drawbg_leave(GumInvocationContext*) {
  cards_draw_leave();
  relayout_if_list_moved();
}
void on_resized_leave(GumInvocationContext* ic) {
  relayout_after_resize(tkw_arg(ic, 0));
  if (tkw_arg(ic, 0) == g_bf) notify_layout();
}
void on_dtor_enter(GumInvocationContext* ic) {
  if (tkw_arg(ic, 0) == g_bf) forget_form("destroyed");
}
void on_ctl_event_enter(GumInvocationContext* ic) {
  control_event(tkw_arg(ic, 0), (unsigned)(uintptr_t)tkw_arg(ic, 1), (const char*)tkw_arg(ic, 2));
}
void on_form_mouse_enter(GumInvocationContext* ic) {  // Form::onMouseEvent(this, evt, data)
  if (!g_bf || tkw_arg(ic, 0) != g_bf) return;
  const unsigned evt = (unsigned)(uintptr_t)tkw_arg(ic, 1);
  const char* d = (const char*)tkw_arg(ic, 2);
  // While `band evtlog` is armed: the form's mouse events too (hover excluded), to see where a
  // click on a band button arrives when the control path stays silent.
  if (g_btn_evt_seen < kEvtLogBudget && !is_noise_evt(evt) && d && tkw_readable(d, 32)) {
    g_btn_evt_seen++;
    log_evt("FORM", evt, d);
  }
  form_mouse((unsigned)(uintptr_t)tkw_arg(ic, 1), (char*)tkw_arg(ic, 2));
}

}  // namespace

// ---- for tkw_band_cmd.cpp (TK's band verbs) -------------------------------------------------------
namespace tkb {
void set_deck_sel(char deck) {
  g_deck_sel = deck;
  apply_deck_sel();
}
void apply_deck_fonts() { apply_deck_sel(); }
void auto_changed() { apply_auto_state(); }
void set_button_hidden(const char* name, bool hide) {
  for (int i = 0; i < g_nbtn; i++)
    if (!strcmp(name, g_btn[i].name)) g_btn[i].hidden = hide;
  place_buttons();
  invalidate();
}
}  // namespace tkb

// ---- C API for TK's Python (ctypes) --------------------------------------------------------------
TKW_EXPORT int tk_band_ready(void) { return g_built ? 1 : 0; }

// Any thread: the text is copied and applied on Traktor's UI thread.
TKW_EXPORT void tk_band_status(const char* text) {
  tkw_on_main([t = std::string(text ? text : "")] {
    g_status_from_tk++;  // TK spoke: the "No answer" timer must not overwrite this
    set_status(t.c_str());
  });
}

TKW_EXPORT void tk_set_click_callback(void (*cb)(const char*)) { g_click_cb = cb; }

TKW_EXPORT void tk_set_layout_callback(void (*cb)(void)) { g_layout_cb = cb; }

// Where the track list is (BrowserForm coordinates, below the band): out[0..3] = {l,t,r,b},
// out[4..19] = the BrowserForm's int fields from +0x18 as on the Mac. TK's boot script reads
// out[9] as the form's height: Mac +0x2c, Windows +0x34 (map OFF_BF_HEIGHT), so it is set apart.
TKW_EXPORT int tk_list_rect(int* out) {
  if (!g_bf || !out) return 0;
  void* list = list_area();
  if (!list || !tkw_readable(rect_of(list), 16)) return 0;
  memcpy(out, rect_of(list), 16);
  if (tkw_readable((char*)g_bf + 0x18, 64)) memcpy(out + 4, (char*)g_bf + 0x18, 64);
  if (tkw_readable((char*)g_bf + WOFF_BF_HEIGHT, 4)) out[9] = *(const int*)((char*)g_bf + WOFF_BF_HEIGHT);
  return 1;
}

// Where band button <name> is ({l, t, r, b}, its tile when it has one): TK places a short hint
// under it. 0 = unknown / hidden.
TKW_EXPORT int tk_band_button_rect(const char* name, int* out) {
  if (!name || !out || !g_bf || !g_band_visible) return 0;
  for (int i = 0; i < g_nbtn; i++) {
    if (strcmp(g_btn[i].name, name) != 0 || g_btn[i].hidden) continue;
    void* c = g_btn[i].tile ? g_btn[i].tile : g_btn[i].ctrl;
    if (!c || !tkw_readable(rect_of(c), 16)) return 0;
    memcpy(out, rect_of(c), 16);
    return 1;
  }
  return 0;
}

// Which of TK's pages shows in the list area ("" = the playlist): its tile turns blue, the cards
// hide and the list hides under the page. Main thread.
TKW_EXPORT void tk_band_active_page(const char* name) {
  if (!tkw_is_main_thread()) {
    tklog("BAND: tk_band_active_page called off the main thread — ignored");
    return;
  }
  copy_str(g_active_page, sizeof(g_active_page), name);
  if (g_bf && g_band_top >= 0) relayout();
  place_buttons();
  invalidate();
}

// ---- framework + tkw_band.h ----------------------------------------------------------------------
void* tkw_browserform() { return g_bf; }
unsigned tkw_band_form_gen() { return g_form_gen; }
bool tkw_band_visible() { return g_band_visible; }
void tkw_band_set_visible(bool on) { band_set_visible(on); }
void tkw_layout_changed() {
  if (g_layout_cb) g_layout_cb();
}
bool tkw_tk_selected() { return g_tk_selected; }
bool tkw_mouse_down() { return mouse_down(); }
void tkw_press_track() { press_track(); }
bool tkw_press_edge() { return press_edge(); }
bool tkw_band_list_area(int* l, int* t, int* r, int* b) {
  if (!g_bf || g_band_top < 0 || !g_band_visible || !l || !t || !r || !b) return false;
  *l = g_list_orig[0]; *t = g_list_pushed_top; *r = g_list_orig[2]; *b = g_list_orig[3];
  return true;
}
int tkw_nl_mode() { return g_nl_mode; }
void tkw_set_nl_mode(int mode) {
  g_nl_mode = mode < 0 || mode > 2 ? 0 : mode;
  if (g_bf && g_band_top >= 0) relayout();
}
void tkw_band_relayout() { relayout(); }
void tkw_band_set_status(const char* text) { set_status(text); }
unsigned tkw_band_button_bg() { return g_btn_bg; }
void tkw_band_add_ctl_event(TkwCtlEventFn fn) { if (fn) ctl_handlers().push_back(fn); }
void tkw_band_add_form_mouse(TkwFormMouseFn fn) { if (fn) form_handlers().push_back(fn); }
void tkw_band_add_layout_listener(void (*fn)()) { if (fn) layout_listeners().push_back(fn); }
void tkw_band_add_visible_listener(void (*fn)(bool)) { if (fn) visible_listeners().push_back(fn); }

void tkw_mod_band_init() {
  // Stage 1's capture: BrowserForm from its own paint/resize, dropped in its destructor.
  tkw_hook(WF(WF_A_bf_resized), "BrowserForm::resized", on_form_enter, on_resized_leave);
  tkw_hook(WF(WF_A_bf_onDrawBg), "BrowserForm::onDrawBackground", on_drawbg_enter, on_drawbg_leave);
  tkw_hook(W(W_bf_dtor), "BrowserForm::~BrowserForm", on_dtor_enter, nullptr);
  tkw_hook(WF(WF_A_nglctl_onEvent), "NGL::Control::onEvent", on_ctl_event_enter, nullptr);
  tkw_hook(WF(WF_A_nglform_onMouseEvent), "NGL::Form::onMouseEvent", on_form_mouse_enter, nullptr);
  tree_init();
  tips_init();
  tkw_command("band", [](const std::string& a, const std::vector<std::string>&) { return band_command(a); });
  band_cmds_init();
}
