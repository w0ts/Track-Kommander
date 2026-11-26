// tkw_band_tips.cpp — tooltips for TK's controls, through Traktor's own tooltip system (port of
// the Mac tk_getTooltip). Traktor shows them only while its [i] button is on, with its own delay
// and look.
//
// On the Mac libtk replaced TooltipManager::getTooltip(form, control, ToolTip&). On Windows that
// function is inlined into TooltipManager::updateToolTip (0xa411b0); what remains is the lookup it
// calls: bool lookup(TooltipCollection*, uint formType, uint ctrlId, ToolTip*) at 0xa3d950 (key =
// formType<<16 | ctrlId). So every control of ours gets a unique id (Control::setID 0x1e02d00, a
// field at +0x58 that only Form::getControl and this key read) and the replacement answers for
// those ids; everything else goes to Traktor's lookup. ToolTip = {title std::string @0, text
// std::string @0x20} (Mac text @0x18), written with Traktor's own std::string::assign so Traktor's
// heap owns the bytes.
#include <map>
#include <string>

#include "tkw_band_int.h"

namespace {

// Addresses not in the generated header yet (map: A_ttm_getTooltip's "replacement"; added to the
// JSON as A_ttc_lookup / A_nglctl_setID / A_std_string_assign). Their first bytes are checked here.
const uint32_t kRvaLookup = 0xa3d950;  // TooltipCollection lookup (A_ttc_lookup)
const uint8_t kHeadLookup[12] = {0x48, 0x89, 0x5c, 0x24, 0x10, 0x48, 0x89, 0x6c, 0x24, 0x18, 0x41, 0x56};
const uint32_t kRvaSetID = 0x1e02d00;  // NI::NGL::Control::setID(uint) (A_nglctl_setID)
const uint8_t kHeadSetID[12] = {0x3b, 0x51, 0x58, 0x74, 0x03, 0x89, 0x51, 0x58, 0xc3, 0xcc, 0xcc, 0xcc};
const uint32_t kRvaAssign = 0x519690;  // std::string::assign(char const*, size_t) (A_std_string_assign)
const uint8_t kHeadAssign[12] = {0x48, 0x89, 0x5c, 0x24, 0x10, 0x48, 0x89, 0x6c, 0x24, 0x18, 0x56, 0x57};
const int kOffTooltipText = 0x20;

// Our ids: above anything Traktor's resources use, below 0x10000 (the key's low 16 bits).
const unsigned kIdBase = 0xE000, kIdMax = 0xFF00;
struct IdEntry {
  void* ctl;   // null = free slot
  void* form;  // the BrowserForm it was made in (band controls), null for other modules'
  unsigned gen;  // tkw_band_form_gen() when assigned: a stale id resolves to nothing
};
std::vector<IdEntry> g_ids;  // id - kIdBase -> control (main thread)
void* g_set_id = nullptr;
void* g_assign = nullptr;

typedef bool (*LookupFn)(void*, unsigned, unsigned, void*);
LookupFn g_orig_lookup = nullptr;

}  // namespace

// The grid module's ALIGN buttons (tkw_grid.cpp), asked first like the Mac's grid_tooltip.
const char* tkw_grid_tooltip(const void* control);

namespace {

std::vector<TkwTooltipFn>& providers() {
  static std::vector<TkwTooltipFn> v;
  return v;
}

// Tooltips TK gives the band's buttons (an add-on's, on the spare slots): "Title\nText".
std::map<std::string, std::string> g_btn_tips;

void* checked(uint32_t rva, const uint8_t head[12], const char* name) {
  void* p = tkw_rva(rva);
  if (!tkw_readable(p, 12) || memcmp(p, head, 12) != 0) {
    tklog("TIPS: %s at 0x%x does not start as expected — not used", name, rva);
    return nullptr;
  }
  return p;
}

const char* band_tooltip(const void* control) {
  if (tkb::g_cards_expanded)
    if (const char* t = tkb::card_tooltip(control)) return t;
  const char* n = tkb::button_at(control);
  if (!n) return tkb::is_source_label(control) ? "Matching source\nThe deck whose track the results match." : nullptr;
  auto set = g_btn_tips.find(n);  // set by TK (tk_band_set_tooltip): an add-on's buttons
  if (set != g_btn_tips.end()) return set->second.c_str();
  static const struct {
    const char* name;
    const char* text;
  } kTips[] = {
      {"A", "Matching source: deck A\nMatch the results against the track on deck A."},
      {"B", "Matching source: deck B\nMatch the results against the track on deck B."},
      {"C", "Matching source: deck C\nMatch the results against the track on deck C."},
      {"D", "Matching source: deck D\nMatch the results against the track on deck D."},
      {"MATCHING", "Matching\nShow or hide the matching cards: what the results are matched on, and how much."},
      {"LIST", "Results\nThe matching tracks, in the playlist."},
      {"S_SETTINGS", "Settings\nKommander's settings: matching, AutoMix, tagging (Discogs, Spotify) and add-ons."},
      {"S_ABOUT", "About\nVersion, updates and credits."},
      {"L_SCAN", "Library: Scan\nAnalyse the collection's tracks for matching."},
      {"L_HOTCUES", "Library: Hotcues\nFind and place hotcues automatically."},
      {"L_TAGGING", "Library: Tagging\nLook up and fill in track metadata."},
      {"L_PLAYLISTS", "Library: Playlists\nSmart playlists by date, in Traktor."},
      {"L_EXPORT", "Library: Export\nExport playlists to USB / other players."},
      {"STATS", "Stats\nThe library's metrics and statistics, in place of the playlist."},
      {"AUTO", "Automation\nClick to cycle: off, play + cue, AutoMix, Auto DJ."},
  };
  for (const auto& t : kTips)
    if (!strcmp(n, t.name)) return t.text;
  return nullptr;
}

// The control an id of ours names, if it still belongs to a form of that type.
void* control_of(unsigned form_type, unsigned id) {
  if (id < kIdBase || id - kIdBase >= g_ids.size()) return nullptr;
  const IdEntry& e = g_ids[id - kIdBase];
  if (e.gen != 0 && e.gen != tkw_band_form_gen()) return nullptr;  // made before the rebuild
  void* c = e.ctl;
  if (!c || !tkw_readable((char*)c + tkb::kOffOwnerForm, 8)) return nullptr;
  const char* form = *(const char* const*)((char*)c + tkb::kOffOwnerForm);
  if (!form || !tkw_readable(form + tkb::kOffFormType, 4)) return nullptr;
  return *(const unsigned*)(form + tkb::kOffFormType) == form_type ? c : nullptr;
}

bool tooltips_on() {
  const void* flag = WF(WF_A_hint_tooltips_enabled);
  return tkw_readable(flag, 1) && *(const unsigned char*)flag != 0;
}

void assign(void* str, const char* text, size_t len) {
  tkw_call<void*>(g_assign, str, text, len);
}

// First Windows run (2026-11-25): no TK tooltip showed with [i] on, and nothing said why. The first
// lookups, and the first ones for our ids, are logged with what decided them.
void log_lookup(unsigned form_type, unsigned ctrl_id, const void* c) {
  static int any = 0, ours = 0;
  const bool mine = ctrl_id >= kIdBase && ctrl_id < kIdBase + g_ids.size();
  if (mine ? ours++ >= 20 : any++ >= 10) return;
  tklog("TIPS: lookup form 0x%x id 0x%x (%s, %zu ids) -> control %p, [i] %s, main %d", form_type, ctrl_id,
        mine ? "ours" : "Traktor's", g_ids.size(), c, tooltips_on() ? "on" : "off", (int)tkw_is_main_thread());
}

bool tk_lookup(void* coll, unsigned form_type, unsigned ctrl_id, void* tooltip) {
  const char* text = nullptr;
  try {
    void* c = (tooltip && g_assign && tkw_is_main_thread() && tooltips_on()) ? control_of(form_type, ctrl_id) : nullptr;
    log_lookup(form_type, ctrl_id, c);
    if (c) text = tkw_grid_tooltip(c);  // decks: band or not
    for (size_t i = 0; c && !text && i < providers().size(); i++) text = providers()[i](c);
    if (c && !text && tkb::g_band_visible) text = band_tooltip(c);
    if (text) {
      const char* nl = strchr(text, '\n');
      assign(tooltip, text, nl ? (size_t)(nl - text) : 0);
      const char* body = nl ? nl + 1 : text;
      assign((char*)tooltip + kOffTooltipText, body, strlen(body));
      return true;
    }
  } catch (...) {
    tklog("TIPS: lookup threw — Traktor's own lookup answers");
  }
  return g_orig_lookup(coll, form_type, ctrl_id, tooltip);
}

}  // namespace

namespace tkb {

void tips_init() {
  g_set_id = checked(kRvaSetID, kHeadSetID, "Control::setID");
  g_assign = checked(kRvaAssign, kHeadAssign, "std::string::assign");
  void* lookup = checked(kRvaLookup, kHeadLookup, "TooltipCollection lookup");
  if (lookup && g_set_id && g_assign)
    tkw_replace(lookup, "TooltipCollection lookup", (void*)tk_lookup, (void**)&g_orig_lookup);
  else
    tklog("TIPS: tooltips for TK's controls are off");
}

// A free or stale slot is reused, so rebuilding forms never runs the ids out. A reused id that a
// stale control still carries resolves to the new control only, and only for its own form type.
// gen 0 = an id of a control outside the BrowserForm (grid ALIGN…): it never goes stale with the
// browser; its owner frees it with tkw_ui_release_id when it drops the control.
unsigned assign_id(void* control, void* form) {
  if (!control || !g_set_id) return 0;
  const unsigned now = tkw_band_form_gen();
  const unsigned gen = form ? now : 0u;
  size_t slot = g_ids.size();
  for (size_t i = 0; i < g_ids.size() && slot == g_ids.size(); i++)
    if (!g_ids[i].ctl || (g_ids[i].gen != 0 && g_ids[i].gen != now)) slot = i;
  if (slot == g_ids.size()) {
    if (kIdBase + g_ids.size() >= kIdMax) {
      tklog("TIPS: no tooltip id left for %p", control);
      return 0;
    }
    g_ids.push_back({nullptr, nullptr, 0});
  }
  g_ids[slot] = {control, form, gen};
  const unsigned id = kIdBase + (unsigned)slot;
  tkw_call<void>(g_set_id, control, id);
  static int logged = 0;
  if (logged++ < 3) tklog("TIPS: id 0x%x -> control %p (form %p, gen %u)", id, control, form, gen);
  return id;
}

// That BrowserForm is gone and its controls with it: their slots are free again. (Every id made
// before the rebuild is stale anyway, other modules' included: they assign again.)
void forget_ids(void* form) {
  if (!form) return;
  for (IdEntry& e : g_ids)
    if (e.form == form) e = {nullptr, nullptr, 0};
}

}  // namespace tkb

// TK: <name>'s tooltip ("Title\nText"). Main thread; 0 = kept.
TKW_EXPORT int tk_band_set_tooltip(const char* name, const char* text) {
  if (!name || !*name || !text) return -1;
  if (!tkw_is_main_thread()) {
    tklog("TIPS: tk_band_set_tooltip called off the main thread — refused");
    return -1;
  }
  g_btn_tips[name] = text;
  return 0;
}

unsigned tkw_ui_assign_id(void* control) { return tkb::assign_id(control, nullptr); }

void tkw_ui_release_id(void* control) {
  for (IdEntry& e : g_ids)
    if (e.ctl == control) e = {nullptr, nullptr, 0};
}

void tkw_band_add_tooltip(TkwTooltipFn fn) {
  if (fn) providers().push_back(fn);
}
