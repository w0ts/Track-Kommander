// tkw_band_int.h — state and helpers shared by the band's own files (tkw_band.cpp,
// tkw_band_icons.cpp, tkw_band_tree.cpp, tkw_band_tips.cpp, tkw_cards.cpp). Not for other modules:
// they use tkw_band.h. Everything here runs on Traktor's main thread.
#pragma once
#include <string.h>

#include <string>
#include <vector>

#include "tkw.h"
#include "tkw_band.h"

namespace tkb {

// Windows offsets the band uses (map: win/map/traktor_4_5_1_21_win.json, "offsets").
const int kOffTextItem = 0xf0;      // LabelControl's TextItem (Mac 0xd8)
const int kOffLblPicture = 0xe0;    // LabelControl's Picture* (Mac 0xc8)
const int kOffOwnerForm = 0xa8;     // Control -> its Form (Mac 0x90)
const int kOffFormType = 0x0c;      // Form type (tooltip key)
const int kOffEvtButton = 0x0c, kOffEvtState = 0x10;  // EventData / MouseEventData
const unsigned kEvtMouse = 1000, kEvtMouseMove = 1002;  // NGL_EVT_MOUSE, +2 = move
const int kSzLabel = 0x180;         // sizeof(LabelControl) (Mac 0x158)

// Colours: Traktor's tile grey, Traktor blue ("on"), orange (something in progress).
const unsigned kBtnSelectedBg = 0xff2b8cf5u, kBtnBusyBg = 0xffe8891cu;
extern unsigned g_btn_bg;
extern int g_btn_halign, g_btn_valign;
extern int g_font, g_font_on, g_font_off, g_font_caption, g_card_font_on, g_card_font_off;

// The browser form, the band's geometry and the list area under it.
extern void* g_bf;
extern bool g_built;
extern bool g_band_visible;
extern unsigned g_status_from_tk;  // bumped by every tk_band_status (main thread)
extern int g_band_top, g_band_left, g_band_right, g_band_btn_h;
extern int g_list_orig[4];
extern bool g_list_orig_set;
extern int g_list_pushed_top, g_list_pushed_left;
extern char g_active_page[16];
extern int g_nl_mode;
extern bool g_tk_selected;

// AUTO button state (tkw_band.cpp) and icons (tkw_band_icons.cpp).
extern char g_auto_mode[16];
extern unsigned g_auto_pic;
extern unsigned g_auto_bg;
extern char g_auto_text[16];

// --- Traktor calls (NGL) ---
void set_pos(void* c, int l, int t, int r, int b);
void set_visible(void* c, bool on);
void set_text(void* c, const char* text);
void set_font(void* c, int font);
void set_color(void* c, unsigned argb);
void set_halign(void* c, int a);
void set_valign(void* c, int a);
void set_draw_mode(void* c, int mode);
void set_picture_id(void* c, unsigned id);
void set_picture_index(void* c, unsigned i);
void invalidate();
int* rect_of(void* c);
void* new_label(const char* text);  // font g_font, added to the browser form, with a tooltip id
void place(void* l, int l_, int t, int r, int b, unsigned fill, bool show);
void* list_area();                  // BrowserForm's list area (RTTI-checked), or null
void copy_str(char* dst, size_t n, const char* src);
double now_s();

// --- band (tkw_band.cpp) ---
void place_buttons();
void band_set_visible(bool on);
void set_status(const char* text);
void notify_layout();               // layout listeners + TK's layout callback
void run_form_mouse_handlers(unsigned evt, char* data);
bool mouse_down();
void press_track();
bool press_edge();
const char* button_at(const void* control);  // band button (or its tile) -> its name
bool is_source_label(const void* control);
void set_deck_sel(char deck);       // 'A'..'D', 0 = none: lights that source button
void apply_deck_fonts();            // A–D again with g_font_on / g_font_off
void auto_changed();                // AUTO's tile and picture from g_auto_*
void set_button_hidden(const char* name, bool hide);

// --- band verbs (tkw_band_cmd.cpp) ---
void band_cmds_init();

// --- icons (tkw_band_icons.cpp) ---
unsigned band_icon(const char* name);
unsigned icon_lit_frame(unsigned pic);
void* icon_pic(const char* button, bool lit);  // TK's silhouette for a button, or null
void forget_icons();

// --- cards (tkw_cards.cpp) ---
void relayout();
bool cards_on();
bool cards_ctl_event(void* self, unsigned evt, const char* data);
void form_mouse(unsigned evt, char* data);
const char* card_tooltip(const void* control);
void cards_draw_enter(void* self, void* picture);
void cards_draw_leave();
void forget_cards();
extern bool g_cards_expanded;

// --- tree (tkw_band_tree.cpp) ---
void tree_init();
void tree_adopt_from_form(void* bf);
void select_our_node();
void forget_tree();

// --- tooltips (tkw_band_tips.cpp) ---
void tips_init();
unsigned assign_id(void* control, void* form);  // form: the band's BrowserForm, or null
void forget_ids(void* form);

}  // namespace tkb
