// tkw_grid.cpp — the deck GRID panel's ALIGN button on Windows (port of ../../src/tk_deckgrid.mm).
//
// The deck's GRID panel (AdvancedGridForm) gets an ALIGN button: "align phase with master tempo"
// (User, 2026-11-24). The grid is changed live, on the loaded track, by Traktor's own app property
// app.traktor.decks.N.track.grid.copy_phase_from_master. MAIN THREAD (hooks + main queue).
//
// The panel is built from a resource and its controls are fetched by ID with Form::getControl(id)
// (the Windows AdvancedGridForm::resize fetches id 14 too: same resource ids). On the Mac, deck A
// form coordinates: the BPM value field is id 14 (its label id 17 over it), the right-hand
// buttons start with id 12, the tall right-most one is id 9. ALIGN takes the right end of the BPM
// field: the field is narrowed, nothing else moves.
//
// Windows: AdvancedGridForm(this rcx, IAppBase* rdx, deck r8d); LabelControl::createControl is
// inlined (Traktor's operator new(0x180) + the constructor); Control rect at +0x48; the TextItem
// at +0xf0. A destroyed panel is forgotten in its destructor (0xb54270, the scalar deleting
// destructor in slot 0 of .?AVAdvancedGridForm@@'s vtable 0x321bf80; map entry A_advgrid_dtor), so
// the poll never touches a freed form or button.
#include <math.h>
#include <string.h>

#include "tkw.h"
#include "tkw_band.h"

// tkw_props.cpp (same DLL).
extern "C" int tk_prop_handle(const char* path);
extern "C" double tk_prop_get_num(int id);
extern "C" int tk_prop_set_num(int id, double v);

namespace {

const int kMaxForms = 8;
const unsigned kGridBpmField = 14, kGridBpmLabel = 17, kGridRightFirst = 12, kGridTall = 9;
const int kAlignGap = 3;
const unsigned kAlignBg = 0xff3a3a3au;          // the band's unlit button
const unsigned kBtnSelectedBg = 0xff2b8cf5u;    // Traktor blue: lit while it acts
const char kAlignText[] = "ALIGN GRID\nWITH MASTER";  // what it does (User, 2026-11-24)
// Off (dark text, darker tile, clicks ignored) while the deck is the tempo master or has SYNC
// on: there is nothing to align it with then (User, 2026-11-24).
const int kAlignFontOff = 156;
const unsigned kAlignBgOff = 0xff2a2a2au;
const unsigned kNglEvtMouse = 1000;
const unsigned kOffEvtButton = 0x0c, kOffEvtState = 0x10;
const uint32_t kRvaAdvGridDtor = 0xb54270;  // AdvancedGridForm scalar deleting destructor (see header)
// SYNC fully on vs dimmed = the phase meter tempo.phase: about 0 with SYNC lit, a steady -0.21
// dimmed (measured on the Mac, 2026-11-24).
const double kInPhase = 1e-3;

// Small grey text on two lines (font 159 = Traktor's "MOVE / CUE" labels on the Mac); tunable
// live with "gridstyle".
int g_align_w = 96, g_align_font = 159;

struct GridForm {
  void* form;
  unsigned deck;
  void* btn;
  int off;  // -1 = look not applied yet
};
GridForm g_forms[kMaxForms];
int g_nforms = 0;
// The constructor runs before the object has its vtable, so it only notes form -> deck; the
// form itself is taken at its first resize, once it is built.
GridForm g_built[kMaxForms];
int g_nbuilt = 0;
void* g_resizing = nullptr;  // the form whose resize is running (enter -> leave)
bool g_watching = false;
bool g_press_taken = false;
double g_last[kMaxForms][3];

template <typename R, typename... A>
R call(int entry, A... a) { return tkw_call<R>(WF(entry), a...); }

void form_built(void* form, unsigned deck) {
  if (g_nbuilt < kMaxForms) g_built[g_nbuilt++] = {form, deck, nullptr, -1};
}

void form_seen(void* form) {
  g_resizing = form;
  if (tkw_rtti_name(form).empty()) return;
  for (int i = 0; i < g_nforms; i++)
    if (g_forms[i].form == form) return;
  if (g_nforms >= kMaxForms) return;
  unsigned deck = 99;
  for (int i = 0; i < g_nbuilt; i++)
    if (g_built[i].form == form) deck = g_built[i].deck;
  g_forms[g_nforms++] = {form, deck, nullptr, -1};
  tklog("GRID: AdvancedGridForm %p deck=%u (%d known)", form, deck, g_nforms);
}

void* grid_ctl(void* form, unsigned id) {
  void* c = call<void*>(WF_A_nglform_getControl, form, id);
  return (c && !tkw_rtti_name(c).empty() && tkw_readable(c, WOFF_CTRL_RECT + 16)) ? c : nullptr;
}

void grid_rect(void* c, int out[4]) { memcpy(out, (char*)c + WOFF_CTRL_RECT, 4 * sizeof(int)); }

void grid_set_rect(void* c, int l, int t, int r, int b) {
  int rect[4] = {l, t, r, b};
  call<void>(WF_A_nglctl_setPosition, c, (void*)rect);
}

void invalidate(void* form) { call<void>(WF_A_nglform_invalidate, form, 0u); }

void* new_button(void* form) {
  void* b = call<void*>(WF_A_op_new, (size_t)WSIZE_LABEL);  // Traktor's heap: Traktor deletes it
  if (!b) return nullptr;
  memset(b, 0, WSIZE_LABEL);
  tkw_call<void*>(W(W_label_ctor), b);
  call<void>(WF_A_ngllabel_setFontID, b, (unsigned)g_align_font);  // BEFORE addControl
  call<void>(WF_A_ngllabel_setText_cstr, b, kAlignText);
  tkw_ui_assign_id(b);  // a unique id before addControl: Traktor's tooltip lookup knows only ids
  call<void>(WF_A_nglform_addControl, form, b);
  call<void>(WF_A_ngllabel_setColor, b, kAlignBg);
  call<void>(WF_A_ngllabel_setHorzAlignment, b, 1);
  call<bool>(WF_A_ngltextitem_setVertAlignment, (char*)b + WOFF_LABEL_TEXTITEM, 1);
  return b;
}

void watch_start();

// Make (once) and place this form's ALIGN button; narrow the BPM field to make room. Positions
// come from the right-hand buttons, not the field's current width, so running it again after
// Traktor's own layout is harmless.
void grid_layout(GridForm* g) {
  void* field = grid_ctl(g->form, kGridBpmField);
  void* label = grid_ctl(g->form, kGridBpmLabel);
  void* right = grid_ctl(g->form, kGridRightFirst);
  void* tall = grid_ctl(g->form, kGridTall);
  if (!field || !right || !tall) { tklog("GRID: deck %u panel controls not found", g->deck); return; }
  int fr[4], rr[4], tr[4];
  grid_rect(field, fr);
  grid_rect(right, rr);
  grid_rect(tall, tr);
  const int right_x = rr[0] - kAlignGap, L = right_x - g_align_w;
  if (L - kAlignGap <= fr[0] + 40) { tklog("GRID: deck %u no room for ALIGN", g->deck); return; }
  if (!g->btn) {
    g->btn = new_button(g->form);
    if (!g->btn) { tklog("GRID: deck %u ALIGN button not made (operator new)", g->deck); return; }
    g->off = -1;  // the watch sets on/off at its next tick
    tklog("GRID: deck %u ALIGN button %p", g->deck, g->btn);
    watch_start();
  }
  grid_set_rect(field, fr[0], fr[1], L - kAlignGap, fr[3]);
  if (label) {
    int lr[4];
    grid_rect(label, lr);
    grid_set_rect(label, lr[0], lr[1], L - kAlignGap, lr[3]);
  }
  grid_set_rect(g->btn, L, tr[1], right_x, tr[3]);
  call<void>(WF_A_nglctl_setVisible, g->btn, true);
  invalidate(g->form);
}

void relayout_pending() {
  void* form = g_resizing;
  g_resizing = nullptr;
  for (int i = 0; i < g_nforms; i++)
    if (g_forms[i].form == form) grid_layout(&g_forms[i]);
}

GridForm* grid_of_button(const void* control) {
  if (!control) return nullptr;
  for (int i = 0; i < g_nforms; i++)
    if (g_forms[i].btn == control) return &g_forms[i];
  return nullptr;
}

// The live entry for form whose button is btn, or null once the form was destroyed.
GridForm* live_form(const void* form, const void* btn) {
  for (int i = 0; i < g_nforms; i++)
    if (g_forms[i].form == form && g_forms[i].btn == btn) return &g_forms[i];
  return nullptr;
}

// Traktor destroys a panel (layout change, deck reconfigured): its controls, ours included, go
// with it. Drop every pointer into it; a later panel gets its own button at its first resize.
void form_destroyed(void* form) {
  for (int i = 0; i < g_nbuilt; i++) {
    if (g_built[i].form != form) continue;
    tkw_ui_release_id(g_built[i].btn);  // its tooltip id is free again (it goes with the form)
    g_built[i--] = g_built[--g_nbuilt];
  }
  if (g_resizing == form) g_resizing = nullptr;
  for (int i = 0; i < g_nforms; i++) {
    if (g_forms[i].form != form) continue;
    tklog("GRID: AdvancedGridForm %p deck=%u destroyed: forgotten", form, g_forms[i].deck);
    for (int k = i; k + 1 < g_nforms; k++) {
      g_forms[k] = g_forms[k + 1];
      memcpy(g_last[k], g_last[k + 1], sizeof(g_last[k]));
    }
    g_nforms--;
    memset(g_last[g_nforms], 0, sizeof(g_last[g_nforms]));
    return;
  }
}

// A deck property's value (bool/int/float), or fallback when it does not resolve.
double deck_prop(unsigned deck, const char* leaf, double fallback) {
  char path[96];
  snprintf(path, sizeof(path), "app.traktor.decks.%u.%s", deck + 1, leaf);
  const int id = tk_prop_handle(path);
  return id < 0 ? fallback : tk_prop_get_num(id);
}

// Master = masterclock.source_id (0-based deck; -1 = the clock): measured on the Mac 2026-11-24.
double clock_source() {
  const int id = tk_prop_handle("app.traktor.masterclock.source_id");
  return id < 0 ? -2.0 : tk_prop_get_num(id);
}

// Nothing to align with: the deck is the tempo master, or SYNC is on AND lit (in phase). A
// dimmed SYNC (on, out of phase) is exactly when ALIGN helps (User, 2026-11-24).
bool align_off(const GridForm* g) {
  const bool master = clock_source() == (double)g->deck;
  const bool synced = deck_prop(g->deck, "sync.enabled", 0) != 0 && fabs(deck_prop(g->deck, "tempo.phase", 1)) < kInPhase;
  return master || synced;
}

void apply_state(GridForm* g) {
  if (!g->btn) return;
  const int off = align_off(g) ? 1 : 0;
  // Logged when the on/off inputs change (the phase meter only as in phase or not: it moves).
  const double en = deck_prop(g->deck, "sync.enabled", -1), src = clock_source();
  const double inph = fabs(deck_prop(g->deck, "tempo.phase", 1)) < kInPhase ? 1 : 0;
  const int k = (int)(g - g_forms);
  if (k >= 0 && k < kMaxForms && (g_last[k][0] != en || g_last[k][1] != inph || g_last[k][2] != src)) {
    g_last[k][0] = en;
    g_last[k][1] = inph;
    g_last[k][2] = src;
    tklog("GRID: deck %u sync=%g in_phase=%g clock source=%g -> ALIGN %s", g->deck, en, inph, src, off ? "off" : "on");
  }
  if (off == g->off) return;
  g->off = off;
  call<void>(WF_A_ngllabel_setFontID, g->btn, (unsigned)(off ? kAlignFontOff : g_align_font));
  call<void>(WF_A_ngllabel_setColor, g->btn, off ? kAlignBgOff : kAlignBg);
  invalidate(g->form);
}

// Sync and master change from Traktor's own buttons, controllers and mappings: a light poll
// (Mac: a 250 ms dispatch timer on the main queue).
void watch_tick() {
  if (g_tkw_quitting) return;  // its panels are going away
  for (int i = 0; i < g_nforms; i++) apply_state(&g_forms[i]);
  tkw_after_ms(250, watch_tick);
}

void watch_start() {
  if (g_watching) return;
  g_watching = true;
  tkw_after_ms(0, watch_tick);
}

// Traktor's own action, as its controller mappings send it: on (press), then off (release).
void grid_align(GridForm* g) {
  if (align_off(g)) { tklog("GRID: deck %u ALIGN ignored (master or sync on)", g->deck); return; }
  char path[96];
  snprintf(path, sizeof(path), "app.traktor.decks.%u.track.grid.copy_phase_from_master", g->deck + 1);
  const int id = tk_prop_handle(path);
  if (id < 0) { tklog("GRID: deck %u ALIGN: no property %s", g->deck, path); return; }
  const int rc = tk_prop_set_num(id, 1.0);
  tklog("GRID: deck %u ALIGN -> %s = 1 (rc %d)", g->deck, path, rc);
  void* btn = g->btn;
  void* form = g->form;
  call<void>(WF_A_ngllabel_setColor, btn, kBtnSelectedBg);  // lit while it acts
  invalidate(form);
  tkw_after_ms(150, [id, btn, form] {
    tk_prop_set_num(id, 0.0);  // the release, even when the panel went away meanwhile
    GridForm* live = live_form(form, btn);
    if (!live) return;
    call<void>(WF_A_ngllabel_setColor, btn, kAlignBg);
    invalidate(form);
    live->off = -1;  // its on/off look, at the next tick
  });
}

// One action per physical press: Traktor repeats a held press and offers it to every control
// under the mouse (the band does the same with the real button state).
bool mouse_down() { return (GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0; }
void press_track() {
  if (!mouse_down()) g_press_taken = false;
}
bool press_edge() {
  press_track();
  if (g_press_taken) return false;
  g_press_taken = true;
  return true;
}

// Control::onEvent on one of our ALIGN buttons.
void ctl_event(void* self, unsigned evt, const char* data) {
  GridForm* g = grid_of_button(self);
  if (!g) return;
  press_track();
  if (!tkw_readable(data, kOffEvtState + 4)) return;
  const int button = *(const int*)(data + kOffEvtButton), state = *(const int*)(data + kOffEvtState);
  if (evt == kNglEvtMouse && button == 1 && state == 1 && press_edge()) grid_align(g);
}

// "gridstyle <font> <width>": restyle the ALIGN buttons live (as "band font" does for the band).
std::string grid_style(int font, int width) {
  if (font > 0) g_align_font = font;
  if (width >= 30 && width <= 200) g_align_w = width;
  for (int i = 0; i < g_nforms; i++) {
    GridForm* g = &g_forms[i];
    if (g->btn) {
      call<void>(WF_A_ngllabel_setFontID, g->btn, (unsigned)g_align_font);
      call<void>(WF_A_ngllabel_setText_cstr, g->btn, kAlignText);
      g->off = -1;  // the on/off look again, with the new font, at the next tick
    }
    grid_layout(g);
  }
  tklog("GRID: ALIGN style font=%d width=%d", g_align_font, g_align_w);
  return "OK font " + std::to_string(g_align_font) + " width " + std::to_string(g_align_w);
}

// --- hooks --------------------------------------------------------------------------------------
void on_ctor_enter(GumInvocationContext* ic) {
  form_built(tkw_arg(ic, 0), (unsigned)(uintptr_t)tkw_arg(ic, 2));
}
void on_resize_enter(GumInvocationContext* ic) { form_seen(tkw_arg(ic, 0)); }
void on_resize_leave(GumInvocationContext*) { relayout_pending(); }  // Traktor laid it out: ours again

void on_dtor_enter(GumInvocationContext* ic) { form_destroyed(tkw_arg(ic, 0)); }

void on_ctl_event_enter(GumInvocationContext* ic) {
  if (g_nforms == 0) return;  // hot path: every control without its own onEvent passes here
  ctl_event(tkw_arg(ic, 0), (unsigned)(uintptr_t)tkw_arg(ic, 1), (const char*)tkw_arg(ic, 2));
}

}  // namespace

// The ALIGN button's tooltip (title, "\n", text), or null when control is not one: for the
// band's tooltip lookup (as the Mac tk_getTooltip asks grid_tooltip first).
const char* tkw_grid_tooltip(const void* control) {
  if (!grid_of_button(control)) return nullptr;
  return "Align beat grid with master\nMoves this deck's beat grid so its beats line up with the "
         "master deck's (Traktor's copy phase from master). The track's grid is changed at once. "
         "Off while this deck is the tempo master or has SYNC on.";
}

void tkw_mod_grid_init() {
  tkw_hook(WF(WF_A_advgrid_ctor), "AdvancedGridForm::AdvancedGridForm", on_ctor_enter, nullptr);
  tkw_hook(WF(WF_A_advgrid_resize), "AdvancedGridForm::resize", on_resize_enter, on_resize_leave);
  // RVA until the header is regenerated with A_advgrid_dtor (win/map/traktor_4_5_1_21_win.json).
  tkw_hook(tkw_rva(kRvaAdvGridDtor), "AdvancedGridForm::~AdvancedGridForm", on_dtor_enter, nullptr);
  tkw_hook(W(W_ctl_onEvent), "NGL::Control::onEvent (grid)", on_ctl_event_enter, nullptr);
  tkw_command("gridstyle", [](const std::string& a, const std::vector<std::string>&) -> std::string {
    int font = 0, width = 0;
    if (sscanf_s(a.c_str(), "%d %d", &font, &width) != 2) return "ERR usage: gridstyle <fontID> <width>";
    return grid_style(font, width);
  });
}
