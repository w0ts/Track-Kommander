// tkw_band.h — what the band (tkw_band*.cpp, tkw_cards.cpp) offers the other modules.
//
//
// On the Mac these were plain statics shared inside one tk_inject.mm. On Windows the modules are
// separate files, and gum attaches only ONE listener per function (tkw_hooks.cpp shares a single
// listener: a second tkw_hook on the same address fails with "already attached"). So the band
// owns the hooks several modules need (NGL::Control::onEvent, NGL::Form::onMouseEvent, the
// tooltip lookup, BrowserForm::treeSelect) and the other modules plug in here. Register from a
// module's init; everything else on Traktor's main thread.
#pragma once
#include <string>

#include "tkw.h"

// ---- state other modules read (Mac: g_stdtree, g_tk_selected, g_list_orig, ...) ----------------
void* tkw_band_stdtree();  // the live BrowserStdTree (left tree), or null
bool tkw_tk_selected();   // inside the treeSelect of a click on TK's node (Mac g_tk_selected)

// One action per physical mouse press (Mac press_track / press_edge / mouse_down): Traktor offers
// a press to every control under the mouse and repeats it while held.
bool tkw_mouse_down();
void tkw_press_track();
bool tkw_press_edge();

// The track list's area below the band ({l,t,r,b}, BrowserForm coordinates): false when the band
// is hidden or not built (Mac nl_area's g_list_orig / g_list_pushed_top).
bool tkw_band_list_area(int* l, int* t, int* r, int* b);
// The native page's mode (Mac g_nl_mode): 1 = its tree left of Traktor's list (the list starts
// kTkwNlTreeW px further right), 2 = its own table (Traktor's list hidden). Setting it lays out.
static const int kTkwNlTreeW = 300;
// +1 whenever the BrowserForm is forgotten (destroyed or replaced). Anything cached from the form
// (its list control, labels added to it) is stale when this differs from the value seen then:
// compare generations, never form addresses (a new form can reuse the old one's address).
unsigned tkw_band_form_gen();
int tkw_nl_mode();
void tkw_set_nl_mode(int mode);
void tkw_band_relayout();  // Mac cards_relayout: cards, list push-down, buttons, listeners

// ---- matching cards, for TK's columns (tk_columns.mm: SCORE header, getHeaderHeight) ----------
bool tkw_cards_on();          // cards showing (band visible, MATCHING open, no TK page)
bool tkw_cards_expanded();
void tkw_cards_toggle();      // the SCORE header's click: open/close the cards
int tkw_cards_gap_h();        // px the list header grows by while the cards show (0 = closed)
void tkw_set_list_header_h(int h);  // the list's own header height, kept fresh by the columns
int tkw_list_header_h();

// ---- icons, status ------------------------------------------------------------------------------
void* tkw_band_icon_picture(const char* key, bool lit);  // TK's silhouette (tk_band_icon), or null
unsigned tkw_band_button_bg();                // the band's tile grey (Mac g_btn_bg)
void tkw_band_set_status(const char* text);  // band text right of the buttons ("" hides it)

// ---- tooltips: Traktor's TooltipManager::getTooltip is inlined on Windows; the band replaces
// the lookup it calls and answers by control id. A module whose controls want tooltips gives each
// control an id here (before Form::addControl) and registers a provider ("Title\nText" or null).
unsigned tkw_ui_assign_id(void* control);
// Frees control's id when its owner drops it (ids of controls outside the BrowserForm never go
// stale by themselves: their form may live longer or shorter than the browser's).
void tkw_ui_release_id(void* control);
typedef const char* (*TkwTooltipFn)(const void* control);
void tkw_band_add_tooltip(TkwTooltipFn fn);  // asked first, whether the band shows or not (grid)

// ---- event chains (the band's hooks call these first) ------------------------------------------
// NGL::Control::onEvent(this, evt, EventData*): true = the event was yours (grid ALIGN, nlist).
typedef bool (*TkwCtlEventFn)(void* self, unsigned evt, const char* data);
void tkw_band_add_ctl_event(TkwCtlEventFn fn);
// NGL::Form::onMouseEvent on the browser form (data may be emptied to keep it from the list).
typedef void (*TkwFormMouseFn)(unsigned evt, char* data);
void tkw_band_add_form_mouse(TkwFormMouseFn fn);
void tkw_band_add_layout_listener(void (*fn)());       // after every re-layout (nlist)
void tkw_band_add_visible_listener(void (*fn)(bool));  // band shown/hidden (columns' SCORE)

// TK's node in the tree shows the "Results" playlist: the band's treeSelect replacement calls
// tkw_playlists_results_* (tkw_collection.h); the playlists module ends "Loading…" with
// tk_band_status("").
