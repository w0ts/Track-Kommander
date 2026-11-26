// tkw_cards.cpp — TK's matching cards inside Windows Traktor, and the list area's layout (port of
// the Mac tk_inject.mm cards code).
//
// TK sends `cards` lines key|label|on|strength|value|hue|src|desc; the SCORE column's header (or
// MATCHING) opens/closes them. They sit between the list's column headers and its rows: the list's
// header grows by the cards' height (the columns module hooks getHeaderHeight) and the cards show
// in that gap. Each card is built only from LabelControls (their fills paint reliably): background,
// playing-track fill, title (the axis's on/off switch), a value fader and an amount fader drawn
// like Traktor's crossfader. The list never lets its own area's clicks through, so the browser
// form's mouse events in the gap are routed to the cards and emptied for the list.
// `relayout` also places the list area under the band (pushed down while the band shows, hidden
// under a TK page, narrowed beside the native page's tree). `card` is the Mac's per-frame Picture
// card (BrowserForm::onDrawBackground + Picture::fillArea), kept for live tests. Private.
#include <math.h>
#include <stdio.h>

#include "tkw_band_int.h"

namespace tkb {
bool g_cards_expanded = false;  // MATCHING / the SCORE header open the cards
}

using namespace tkb;

namespace {

enum { CP_BG, CP_NAME, CP_VTRACK, CP_VMARK, CP_STRACK, CP_SBAR, CP_FILL, CP_VFILL, CP_VKNOB,
       CP_SKOUT, CP_SKNOB, CP_COUNT };
struct Card {
  void* part[CP_COUNT];
  int x, y, w, h;
  bool active;
  int strength, value, hue, src, font;
  char key[24], name[32];
  int last_sent_value, last_sent_strength;  // stored +1 so 0 means "nothing sent yet"
  double touched;  // when the user last changed this card here: TK's echoes wait
  char desc[256];  // TK's description of the axis (tooltips)
};
const int kMaxCards = 40;
Card g_acards[kMaxCards];
int g_nacards = 0;
const int kCardH = 76, kCardGap = 8, kCardMinW = 170;
const bool kCardsInList = true;  // false = the old layout (cards above the list)
int g_cards_gap_h = 0;     // px added to the list's header height (0 = cards closed)
int g_list_header_h = 24;  // the list's own header height (the columns module keeps it fresh)
int g_drag_card = -1, g_drag_part = -1;  // the card slider being dragged

// hsl(h, 55 %, l) -> 0xAARRGGBB: each card gets its axis hue like TK's cards.
unsigned hue_argb(int hue, double l) {
  const double s = 0.55, c = (1 - fabs(2 * l - 1)) * s, hp = (hue % 360) / 60.0;
  const double x = c * (1 - fabs(fmod(hp, 2) - 1));
  double r = 0, g = 0, b = 0;
  if (hp < 1) { r = c; g = x; } else if (hp < 2) { r = x; g = c; } else if (hp < 3) { g = c; b = x; }
  else if (hp < 4) { g = x; b = c; } else if (hp < 5) { r = x; b = c; } else { r = c; b = x; }
  const double m = l - c / 2;
  return 0xff000000u | ((unsigned)((r + m) * 255) << 16) | ((unsigned)((g + m) * 255) << 8) | (unsigned)((b + m) * 255);
}

int track_l(const Card* c) { return c->x + 10; }
int track_r(const Card* c) { return c->x + c->w - 10; }

// Position and colour one card's parts from its state (cheap: used while dragging).
void card_layout(Card* c, bool show) {
  const unsigned bg = c->active ? 0xff2c2c2cu : 0xff1f1f1fu;
  const int L = track_l(c), Rr = track_r(c), span = Rr - L;
  place(c->part[CP_BG], c->x, c->y, c->x + c->w, c->y + c->h, bg, show);
  // Where the playing (source) track sits on this axis: the card fills up to it.
  const int src = c->src < 0 ? -1 : c->src > 100 ? 100 : c->src;
  const int fw = src < 0 ? 0 : c->w * src / 100;
  place(c->part[CP_FILL], c->x, c->y, c->x + fw, c->y + c->h, hue_argb(c->hue, 0.22), show && c->active && fw > 0);
  // The title is the card's on/off switch, a Traktor toggle: grey text on the grey tile when off,
  // black text on the axis's own colour when on.
  place(c->part[CP_NAME], c->x + 6, c->y + 4, c->x + c->w - 6, c->y + 24, c->active ? hue_argb(c->hue, 0.58) : g_btn_bg, show);
  const int nfont = c->active ? g_card_font_on : g_card_font_off;
  if (nfont > 0 && c->font != nfont) {
    set_font(c->part[CP_NAME], nfont);
    set_halign(c->part[CP_NAME], 1);
    set_valign(c->part[CP_NAME], g_btn_valign);
    c->font = nfont;
  }
  const bool has_val = c->value >= 0;
  const int mx = L + span * (has_val ? c->value : 0) / 100;
  place(c->part[CP_VTRACK], L, c->y + 37, Rr, c->y + 39, 0xff555555u, show && has_val);
  place(c->part[CP_VFILL], L, c->y + 37, mx, c->y + 39, c->active ? kBtnBusyBg : 0xff6a6a6au, show && has_val && mx > L);
  place(c->part[CP_VMARK], mx - 4, c->y + 30, mx + 4, c->y + 46, 0xff161616u, show && has_val);
  place(c->part[CP_VKNOB], mx - 3, c->y + 31, mx + 3, c->y + 45, c->active ? 0xffb4b4b4u : 0xff6e6e6eu, show && has_val);
  // The amount: the same Traktor fader as the value, in blue.
  const int sw = span * (c->strength < 0 ? 0 : c->strength > 100 ? 100 : c->strength) / 100;
  const int sx = L + sw;
  place(c->part[CP_STRACK], L, c->y + 60, Rr, c->y + 62, 0xff555555u, show);
  place(c->part[CP_SBAR], L, c->y + 60, sx, c->y + 62, c->active ? kBtnSelectedBg : 0xff6a6a6au, show && sw > 0);
  place(c->part[CP_SKOUT], sx - 4, c->y + 53, sx + 4, c->y + 69, 0xff161616u, show);
  place(c->part[CP_SKNOB], sx - 3, c->y + 54, sx + 3, c->y + 68, c->active ? 0xffb4b4b4u : 0xff6e6e6eu, show);
}

void build_card(Card* c) {  // order = paint order (background first)
  static const int kOrder[CP_COUNT] = {CP_BG, CP_FILL, CP_NAME, CP_VTRACK, CP_VFILL, CP_STRACK,
                                       CP_SBAR, CP_VMARK, CP_VKNOB, CP_SKOUT, CP_SKNOB};
  for (int k : kOrder) c->part[k] = new_label(k == CP_NAME ? c->name : "");
  c->font = 0;
}

// TK's own card flow (ui/np_format.py _flow_columns): the fewest rows that keep a card at least
// kCardMinW wide, spread evenly over them (13 = 7 + 6), each stretched to fill its row.
int place_cards() {
  const int n = g_nacards, W = g_band_right - g_band_left;
  int fit = (W + kCardGap) / (kCardMinW + kCardGap);
  fit = fit < 1 ? 1 : (n > 0 && fit > n ? n : fit);
  const int nrows = n > 0 ? (n + fit - 1) / fit : 1;
  const int per_row = n > 0 ? (n + nrows - 1) / nrows : 1;
  int card_w = (W - (per_row - 1) * kCardGap) / per_row;
  if (card_w < kCardMinW) card_w = kCardMinW;
  const int rows = (cards_on() && n) ? (n + per_row - 1) / per_row : 0;
  int top = g_band_top + g_band_btn_h;
  if (kCardsInList) {  // the list's top is the band's bottom; its rows begin under header + cards
    g_cards_gap_h = rows ? rows * (kCardH + kCardGap) + kCardGap : 0;
    top = g_band_top + g_band_btn_h + g_list_header_h + kCardGap;
  }
  for (int i = 0; i < kMaxCards; i++) {
    Card* c = &g_acards[i];
    const bool show = cards_on() && i < n;
    if (i < n) {
      const int row = i / per_row, in_row = n - row * per_row < per_row ? n - row * per_row : per_row;
      const int w = (W - (in_row - 1) * kCardGap) / in_row;
      c->x = g_band_left + (i % per_row) * (w + kCardGap);
      c->y = top + row * (kCardH + kCardGap);
      c->w = in_row == per_row ? card_w : w;
      c->h = kCardH;
    }
    if (show && !c->part[CP_BG]) build_card(c);
    if (show && c->part[CP_NAME]) set_text(c->part[CP_NAME], c->name);
    if (c->part[CP_BG]) card_layout(c, show);
  }
  return rows;
}

// Traktor moves the list's sides itself (a separator drag) without BrowserForm::resized: start
// from where it is now. Edge by edge: an edge that is not where WE last put it was moved by
// Traktor and becomes the base.
void place_list(int rows) {
  void* list = list_area();
  if (!list) return;
  const int* cur = rect_of(list);
  if (cur[1] != g_list_pushed_top) g_list_orig[1] = cur[1];
  if (cur[0] != g_list_pushed_left) g_list_orig[0] = cur[0];
  g_list_orig[2] = cur[2];
  g_list_orig[3] = cur[3];
  g_band_left = g_list_orig[0] + 4;
  g_band_right = g_list_orig[2] - 4;
  const int push = g_band_visible ? g_band_btn_h + (kCardsInList ? 0 : rows * (kCardH + kCardGap)) : 0;
  const int inset = g_band_visible && g_nl_mode == 1 ? kTkwNlTreeW : 0;  // the native page's tree
  const int nr[4] = {g_list_orig[0] + inset, g_list_orig[1] + push, g_list_orig[2], g_list_orig[3]};
  g_list_pushed_top = nr[1];
  g_list_pushed_left = nr[0];
  set_pos(list, nr[0], nr[1], nr[2], nr[3]);
  // A TK page covers the list: hide Traktor's tracks under it, so a separator drag never shows
  // them through. The native page's mode 1 shows TK's tracks IN that list: it stays.
  set_visible(list, !(g_band_visible && g_active_page[0] && g_nl_mode != 1));
}

// TK's slider moved to form x on card i: redraw, tell TK on change.
void card_slide(int i, int part, int x) {
  Card* c = &g_acards[i];
  const bool is_value = part == CP_VTRACK || part == CP_VMARK || part == CP_VFILL || part == CP_VKNOB;
  if (is_value && c->value < 0) return;
  const int L = track_l(c), Rr = track_r(c);
  int v = Rr > L ? (x - L) * 100 / (Rr - L) : 0;
  v = v < 0 ? 0 : v > 100 ? 100 : v;
  int* last = is_value ? &c->last_sent_value : &c->last_sent_strength;
  if (is_value) c->value = v;
  else c->strength = v;
  c->touched = now_s();
  card_layout(c, true);
  invalidate();
  if (*last == v + 1) return;
  *last = v + 1;
  char body[128];
  snprintf(body, sizeof(body), "{\"key\":\"%s\",\"field\":\"%s\",\"value\":%d}", tkw_json_escape(c->key).c_str(),
           is_value ? "value" : "strength", v);
  tkw_post_json("/uiCard", body);
}

// A press on a card part: the title toggles the axis; the faders set their value from the
// pointer's x (shown at once, then applied by TK), and start a drag that follows the mouse over
// the form (Traktor sends no moves to the pressed label itself) until the button comes up.
void card_event(int i, int part, unsigned evt, const char* d) {
  Card* c = &g_acards[i];
  const int button = *(const int*)(d + kOffEvtButton), state = *(const int*)(d + kOffEvtState);
  static int seen = 0;
  if (seen < 60 && (evt == kEvtMouse || evt == kEvtMouseMove) && part != CP_BG) {
    seen++;
    tklog("CARDEVT %s part=%d evt=%u button=%d state=%d x=%d", c->key, part, evt, button, state, *(const int*)d);
  }
  // The background and the fill sit under every other part, and Traktor offers a click to each
  // control under the mouse, background first: acting on it stole the sliders' clicks.
  if (part == CP_BG || part == CP_FILL) return;
  if (evt != kEvtMouse || button != 1 || state != 1) return;
  if (part == CP_NAME) {
    if (!press_edge()) return;  // a toggle: once per click
    c->active = !c->active;     // show it now; TK's answer confirms it
    c->touched = now_s();
    card_layout(c, true);
    invalidate();
    char body[96];
    snprintf(body, sizeof(body), "{\"action\":\"CARD:%s\"}", tkw_json_escape(c->key).c_str());
    tkw_post_json("/uiAction", body);
    return;
  }
  g_drag_card = i;
  g_drag_part = (part == CP_VMARK || part == CP_VFILL || part == CP_VKNOB) ? CP_VTRACK
              : (part == CP_SBAR || part == CP_SKOUT || part == CP_SKNOB) ? CP_STRACK : part;
  card_slide(i, g_drag_part, *(const int*)d);
}

// The cards sit behind the list (it only leaves the gap unpainted), which takes every mouse
// event in its area as a header click. The browser form sees each event first: a press on a card
// goes to card_event, and every mouse event inside the gap is emptied so the list ignores it
// (header sorting fires on the mouse UP, so ups are emptied too).
void cards_form_event(unsigned evt, char* d) {
  if (!kCardsInList || g_cards_gap_h <= 0 || !cards_on()) return;
  if (evt != kEvtMouse || !d || !tkw_readable(d, kOffEvtState + 4)) return;
  const int x = ((const int*)d)[0], y = ((const int*)d)[1];
  const int gap_t = g_band_top + g_band_btn_h + g_list_header_h, gap_b = gap_t + g_cards_gap_h;
  if (y < gap_t || y >= gap_b || x < g_band_left || x > g_band_right) return;
  for (int i = 0; i < g_nacards; i++) {
    const Card* c = &g_acards[i];
    if (x < c->x || x >= c->x + c->w || y < c->y || y >= c->y + c->h) continue;
    const int dy = y - c->y;  // card_layout: name 4..24, value fader 30..46, amount fader 53..69
    card_event(i, dy < 27 ? CP_NAME : dy < 50 ? CP_VTRACK : CP_STRACK, evt, d);
    break;
  }
  *(int*)(d + kOffEvtButton) = 0;
  *(int*)(d + kOffEvtState) = 0;
}

std::string cards_set(const std::vector<std::string>& lines) {
  int n = 0;
  for (const std::string& ln : lines) {
    if (n >= kMaxCards) break;
    char key[24] = {0}, name[32] = {0}, desc[256] = {0};
    int on = 0, st = 0, val = -1, hue = 0, src = -1;
    const int got = sscanf_s(ln.c_str(), "%23[^|]|%31[^|]|%d|%d|%d|%d|%d|%255[^\n]", key, (unsigned)sizeof(key), name,
                             (unsigned)sizeof(name), &on, &st, &val, &hue, &src, desc, (unsigned)sizeof(desc));
    if (got < 6) continue;  // 6: a TK from before the playing-track fill; 8: with a description
    Card* c = &g_acards[n];
    const bool same_card = n < g_nacards && !strcmp(c->key, key);
    // While the user drags (or just toggled) this card here, TK's echo may be a step behind: keep
    // what the user sees until 0.8 s after the last touch.
    const bool settling = same_card && (n == g_drag_card || now_s() - c->touched < 0.8);
    n++;
    copy_str(c->key, sizeof(c->key), key);
    copy_str(c->name, sizeof(c->name), name);
    if (!settling) {
      c->active = on != 0;
      c->strength = st;
      c->value = val;
    }
    if (!same_card) c->touched = 0;
    c->hue = hue;
    c->src = got >= 7 ? src : -1;
    copy_str(c->desc, sizeof(c->desc), got == 8 ? desc : "");
  }
  g_nacards = n;
  relayout();
  return "OK " + std::to_string(lines.size());
}

// ---- `card x y w h`: the Mac's per-frame Picture card (fill + border + value bar) ---------------
struct PicCard {
  int x, y, w, h;
  unsigned bg, border, barcol;
  int barw;
};
std::vector<PicCard> g_pic_cards;
void* g_card_pic = nullptr;  // the Picture BrowserForm::onDrawBackground paints into (this frame)

std::string card_command(const std::string& a) {
  int x = 0, y = 0, w = 0, h = 0;
  if (sscanf_s(a.c_str(), "%d %d %d %d", &x, &y, &w, &h) != 4 || w <= 0 || h <= 0)
    return "ERR usage: card <x> <y> <w> <h>";
  if (!g_bf) return "ERR no BrowserForm yet";
  if (g_pic_cards.size() >= 16) return "ERR registry full";
  g_pic_cards.push_back({x, y, w, h, 0xff2b2b2bu, 0xff2b8cf5u, 0xff2b8cf5u, (int)(w * 0.6)});
  if (void* lbl = new_label("KOMMANDER")) {
    set_color(lbl, 0xffffffffu);
    set_pos(lbl, x + 6, y + 4, x + w - 6, y + 20);
    set_visible(lbl, true);
  }
  invalidate();
  tklog("CARD: #%zu {x=%d y=%d w=%d h=%d}", g_pic_cards.size() - 1, x, y, w, h);
  return "OK";
}

}  // namespace

namespace tkb {

bool cards_on() { return g_band_visible && g_cards_expanded && !g_active_page[0]; }

// The cards, the list under the band and the buttons, laid out again from the current state.
void relayout() {
  if (g_tkw_quitting || !g_bf || g_band_top < 0 || !g_list_orig_set) return;
  const ULONGLONG t0 = GetTickCount64();
  place_list(0);  // the list's edges first: the cards span its width
  const int rows = place_cards();
  if (!kCardsInList && rows) place_list(rows);
  place_buttons();
  invalidate();
  notify_layout();  // the native page and TK's page follow the list's area
  tklog("CARDS: %d card(s), %s, %d row(s), laid out in %llu ms", g_nacards, g_cards_expanded ? "open" : "closed",
        rows, (unsigned long long)(GetTickCount64() - t0));
}

// Control::onEvent on a card part (only while the cards are open). True = it was a card's.
bool cards_ctl_event(void* self, unsigned evt, const char* data) {
  if (!g_cards_expanded || g_nacards == 0) return false;
  for (int i = 0; i < g_nacards; i++)
    for (int k = 0; k < CP_COUNT; k++) {
      if (g_acards[i].part[k] != self) continue;
      if (data && tkw_readable(data, kOffEvtState + 4)) card_event(i, k, evt, data);
      return true;
    }
  return false;
}

// Form::onMouseEvent on the browser form: the cards' clicks, the other modules' handlers, then a
// slider drag. Released: the mouse event's own fields do not say (moves carry button 0 and no
// state), the real button state does.
void form_mouse(unsigned evt, char* d) {
  cards_form_event(evt, d);
  run_form_mouse_handlers(evt, d);  // the native page: its clicks and wheel
  press_track();  // any mouse event with the button up ends "this press was taken"
  if (g_drag_card < 0) return;
  if (!mouse_down()) {
    g_drag_card = g_drag_part = -1;
    return;
  }
  if (d && tkw_readable(d, 8) && evt == kEvtMouseMove && g_drag_card < g_nacards)
    card_slide(g_drag_card, g_drag_part, *(const int*)d);
}

// A card part's tooltip (title / value fader / amount fader / card), built per call.
const char* card_tooltip(const void* control) {
  static std::string text;
  for (int i = 0; i < g_nacards; i++) {
    const Card* c = &g_acards[i];
    int part = -1;
    for (int k = 0; k < CP_COUNT; k++)
      if (c->part[k] && c->part[k] == control) part = k;
    if (part < 0) continue;
    const std::string name(c->name), desc(c->desc);
    switch (part) {
      case CP_NAME:
        text = name + "\n" + (desc.empty() ? std::string() : desc + " ") + "Click to switch this axis " +
               (c->active ? "off" : "on") + ".";
        break;
      case CP_VTRACK: case CP_VMARK: case CP_VFILL: case CP_VKNOB:
        text = name + ": target value\nDrag the orange handle: tracks closest to this value rank first. "
                      "The coloured fill behind the card shows where the playing track sits.";
        break;
      case CP_STRACK: case CP_SBAR: case CP_SKOUT: case CP_SKNOB:
        text = name + ": amount\nDrag the blue handle: how much this axis counts in the match. "
                      "All the way left, it is ignored.";
        break;
      default:
        text = name + "\nThe coloured fill shows where the playing track sits on this axis." +
               (desc.empty() ? std::string() : " " + desc);
    }
    return text.c_str();
  }
  return nullptr;
}

void cards_draw_enter(void* self, void* picture) { g_card_pic = (self && self == g_bf) ? picture : nullptr; }

void cards_draw_leave() {
  if (!g_card_pic || g_pic_cards.empty() || g_tkw_quitting) return;
  for (const PicCard& c : g_pic_cards) {
    tkw_call<void>(WF(WF_A_pic_fillArea), g_card_pic, c.x, c.y, c.w, c.h, c.bg);
    if (c.barw > 0) tkw_call<void>(WF(WF_A_pic_fillArea), g_card_pic, c.x + 4, c.y + c.h - 8, c.barw, 5, c.barcol);
    tkw_call<void>(WF(WF_A_pic_drawRect), g_card_pic, c.x, c.y, c.w, c.h, c.border);
  }
  g_card_pic = nullptr;
}

void forget_cards() {  // the parts lived in the BrowserForm that is gone; TK's values stay
  for (Card& c : g_acards) memset(c.part, 0, sizeof(c.part));
  g_pic_cards.clear();
  g_card_pic = nullptr;
  g_drag_card = g_drag_part = -1;
  g_cards_gap_h = 0;
}

}  // namespace tkb

// ---- tkw_band.h -----------------------------------------------------------------------------------
bool tkw_cards_on() { return cards_on(); }
bool tkw_cards_expanded() { return g_cards_expanded; }
void tkw_cards_toggle() {
  g_cards_expanded = !g_cards_expanded;
  tklog("CARDS: SCORE header -> cards %s", g_cards_expanded ? "open" : "closed");
  relayout();
}
int tkw_cards_gap_h() { return kCardsInList && cards_on() ? g_cards_gap_h : 0; }
void tkw_set_list_header_h(int h) {
  if (h > 0 && h < 200) g_list_header_h = h;
}
int tkw_list_header_h() { return g_list_header_h; }

void tkw_mod_cards_init() {
  tkw_command("cards", [](const std::string&, const std::vector<std::string>& body) { return cards_set(body); },
              true);
  tkw_command("card", [](const std::string& a, const std::vector<std::string>&) { return card_command(a); });
}
