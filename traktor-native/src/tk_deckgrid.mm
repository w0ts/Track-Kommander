// The deck's GRID panel (AdvancedGridForm) gets an ALIGN button: "align phase with master tempo"
// (User, 2026-11-24). It replaces TK's grid-fix queue inside Traktor: the grid is changed live,
// on the loaded track, by Traktor's own app property
// app.traktor.decks.N.track.grid.copy_phase_from_master. Included by tk_inject.mm (one
// translation unit), after style_button and tk_props.mm. MAIN THREAD (hooks + main queue).
//
// The panel is built from resource 0x2a8 and its controls are fetched by ID with
// Form::getControl(id) (AdvancedGridForm's constructor, read with otool). Measured on deck A,
// form coordinates: the BPM value field is id 14 (x 124..520, its label id 17 over it), the
// right-hand buttons start with id 12 (x 523), the tall right-most one is id 9 (y 7..50).
// ALIGN takes the right end of the BPM field: the field is narrowed, nothing else moves.

#define MAX_GRID_FORMS 8
static const unsigned kGridBpmField = 14, kGridBpmLabel = 17, kGridRightFirst = 12, kGridTall = 9;
static const int kAlignGap = 3;
static const unsigned kAlignBg = 0xff3a3a3au;  // the band's unlit button
static const char kAlignText[] = "ALIGN GRID\nWITH MASTER";  // what it does (User, 2026-11-24)
// Small grey text on two lines (font 159 = Traktor's "MOVE / CUE" labels, compared side by side
// 2026-11-24), wide enough for it; tunable live: "gridstyle".
static int g_align_w = 96, g_align_font = 159;
// Off (dark text, darker tile, clicks ignored) while the deck is the tempo master or has SYNC on:
// there is nothing to align it with then (User, 2026-11-24).
static const int kAlignFontOff = 156;
static const unsigned kAlignBgOff = 0xff2a2a2au;

struct TKGridForm { void* form; unsigned deck; void* btn; int off; };
static TKGridForm g_grid_forms[MAX_GRID_FORMS];
static int g_ngrid_forms = 0;
// The constructor runs before the object has its vtable, so it only notes form -> deck; the
// form itself is taken at its first resize, once it is built.
static TKGridForm g_grid_built[MAX_GRID_FORMS];
static int g_ngrid_built = 0;
static void* g_grid_resizing = nullptr;  // the form whose resize is running (enter -> leave)
static void grid_watch_start(void);

static void grid_form_built(void* form, unsigned deck) {
  if (g_ngrid_built < MAX_GRID_FORMS) g_grid_built[g_ngrid_built++] = { form, deck, nullptr, -1 };
}

static void grid_form_seen(void* form) {
  g_grid_resizing = form;
  if (!looks_like_object(form)) return;
  for (int i = 0; i < g_ngrid_forms; i++)
    if (g_grid_forms[i].form == form) return;
  if (g_ngrid_forms >= MAX_GRID_FORMS) return;
  unsigned deck = 99;
  for (int i = 0; i < g_ngrid_built; i++)
    if (g_grid_built[i].form == form) deck = g_grid_built[i].deck;
  g_grid_forms[g_ngrid_forms++] = { form, deck, nullptr, -1 };
  tklog("GRID: AdvancedGridForm %p deck=%u (%d known)", form, deck, g_ngrid_forms);
}

static void* grid_ctl(void* form, unsigned id) {
  void* c = ((void* (*)(void*, unsigned))R(A_nglform_getControl))(form, id);
  return (c && looks_like_object(c) && gum_memory_is_readable(c, OFF_CTRL_RECT_L + 16)) ? c : nullptr;
}

static void grid_rect(void* c, int out[4]) { memcpy(out, (char*)c + OFF_CTRL_RECT_L, 4 * sizeof(int)); }

static void grid_set_rect(void* c, int l, int t, int r, int b) {
  int rect[4] = { l, t, r, b };
  ((void (*)(void*, void*))R(A_nglctl_setPosition))(c, rect);
}

// Make (once) and place this form's ALIGN button; narrow the BPM field to make room. Positions
// come from the right-hand buttons, not the field's current width, so running it again after
// Traktor's own layout is harmless.
static void grid_layout(TKGridForm* g) {
  void* field = grid_ctl(g->form, kGridBpmField);
  void* label = grid_ctl(g->form, kGridBpmLabel);
  void* right = grid_ctl(g->form, kGridRightFirst);
  void* tall = grid_ctl(g->form, kGridTall);
  if (!field || !right || !tall) { tklog("GRID: deck %u panel controls not found", g->deck); return; }
  int fr[4], rr[4], tr[4];
  grid_rect(field, fr); grid_rect(right, rr); grid_rect(tall, tr);
  const int right_x = rr[0] - kAlignGap, L = right_x - g_align_w;
  if (L - kAlignGap <= fr[0] + 40) { tklog("GRID: deck %u no room for ALIGN", g->deck); return; }
  if (!g->btn) {
    void* b = ((void* (*)(void))R(A_ngllabel_createControl))();
    if (!b) return;
    ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(b, (unsigned)g_align_font);  // BEFORE addControl
    ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(b, kAlignText);
    ((void (*)(void*, void*))R(A_nglform_addControl))(g->form, b);
    style_button(b, kAlignBg, 1, 1);
    g->btn = b;
    g->off = -1;  // the watch sets on/off at its next tick
    tklog("GRID: deck %u ALIGN button %p", g->deck, b);
    grid_watch_start();
  }
  grid_set_rect(field, fr[0], fr[1], L - kAlignGap, fr[3]);
  if (label) { int lr[4]; grid_rect(label, lr); grid_set_rect(label, lr[0], lr[1], L - kAlignGap, lr[3]); }
  grid_set_rect(g->btn, L, tr[1], right_x, tr[3]);
  ((void (*)(void*, bool))R(A_nglctl_setVisible))(g->btn, true);
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g->form, 0);
}

static void grid_relayout_pending(void) {
  void* form = g_grid_resizing;
  g_grid_resizing = nullptr;
  for (int i = 0; i < g_ngrid_forms; i++)
    if (g_grid_forms[i].form == form) grid_layout(&g_grid_forms[i]);
}

static TKGridForm* grid_of_button(const void* control) {
  if (!control) return nullptr;
  for (int i = 0; i < g_ngrid_forms; i++)
    if (g_grid_forms[i].btn == control) return &g_grid_forms[i];
  return nullptr;
}

// A deck property's value (bool/int/float), or `fallback` when it does not resolve.
static double grid_deck_prop(unsigned deck, const char* leaf, double fallback) {
  char path[96];
  snprintf(path, sizeof(path), "app.traktor.decks.%u.%s", deck + 1, leaf);
  const int id = tk_prop_handle(path);
  return id < 0 ? fallback : tk_prop_get_num(id);
}

static double grid_clock_source(void) {
  const int id = tk_prop_handle("app.traktor.masterclock.source_id");
  return id < 0 ? -2.0 : tk_prop_get_num(id);
}

// Nothing to align with: the deck is the tempo master, or SYNC is on AND lit (in phase). A dimmed
// SYNC (on, out of phase) is exactly when ALIGN helps (User, 2026-11-24).
// Master = masterclock.source_id (0-based deck; -1 = the clock): measured 2026-11-24, it went
// -1 -> 0 when deck A was made master, while decks.N.set_as_master stayed 0 (a trigger, not a state).
// SYNC fully on vs dimmed = the phase meter, tempo.phase: measured 2026-11-24 on deck A (two
// snapshots per state of all 1,526 decks.1 properties): about 0 (-1.6e-7) with SYNC lit, a steady
// -0.21 with it dimmed. sync.phase stayed 0 in both (not the lit state).
static const double kInPhase = 1e-3;

static bool grid_align_off(const TKGridForm* g) {
  const bool master = grid_clock_source() == (double)g->deck;
  const bool synced = grid_deck_prop(g->deck, "sync.enabled", 0) != 0 &&
                      fabs(grid_deck_prop(g->deck, "tempo.phase", 1)) < kInPhase;
  return master || synced;
}

static void grid_apply_state(TKGridForm* g) {
  if (!g->btn) return;
  const int off = grid_align_off(g) ? 1 : 0;
  // Every change of the inputs is logged (not only on/off flips): it checks the sync.phase reading.
  // Logged when the on/off inputs change (the phase meter only as in phase or not: it moves).
  const double en = grid_deck_prop(g->deck, "sync.enabled", -1), src = grid_clock_source();
  const double inph = fabs(grid_deck_prop(g->deck, "tempo.phase", 1)) < kInPhase ? 1 : 0;
  static double last[MAX_GRID_FORMS][3];
  const int k = (int)(g - g_grid_forms);
  if (k >= 0 && k < MAX_GRID_FORMS && (last[k][0] != en || last[k][1] != inph || last[k][2] != src)) {
    last[k][0] = en; last[k][1] = inph; last[k][2] = src;
    tklog("GRID: deck %u sync=%g in_phase=%g clock source=%g -> ALIGN %s", g->deck, en, inph, src,
          off ? "off" : "on");
  }
  if (off == g->off) return;
  g->off = off;
  ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(g->btn, (unsigned)(off ? kAlignFontOff : g_align_font));
  ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(g->btn, off ? kAlignBgOff : kAlignBg);
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g->form, 0);
}

// Sync and master change from Traktor's own buttons, controllers and mappings: a light poll.
static void grid_watch_start(void) {
  static dispatch_source_t timer = nullptr;
  if (timer) return;
  timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
  dispatch_source_set_timer(timer, dispatch_time(DISPATCH_TIME_NOW, 0), 250 * NSEC_PER_MSEC, 50 * NSEC_PER_MSEC);
  dispatch_source_set_event_handler(timer, ^{
    if (g_quitting) { dispatch_source_cancel(timer); return; }  // its panels are going away
    for (int i = 0; i < g_ngrid_forms; i++) grid_apply_state(&g_grid_forms[i]);
  });
  dispatch_resume(timer);
}

// Traktor's own action, as its controller mappings send it: on (press), then off (release).
static void grid_align(TKGridForm* g) {
  if (grid_align_off(g)) { tklog("GRID: deck %u ALIGN ignored (master or sync on)", g->deck); return; }
  char path[96];
  snprintf(path, sizeof(path), "app.traktor.decks.%u.track.grid.copy_phase_from_master", g->deck + 1);
  const int id = tk_prop_handle(path);
  if (id < 0) { tklog("GRID: deck %u ALIGN: no property %s", g->deck, path); return; }
  const int rc = tk_prop_set_num(id, 1.0);
  tklog("GRID: deck %u ALIGN -> %s = 1 (rc %d)", g->deck, path, rc);
  void* btn = g->btn;
  ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(btn, kBtnSelectedBg);  // lit while it acts
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g->form, 0);
  void* form = g->form;
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(150 * NSEC_PER_MSEC)), dispatch_get_main_queue(), ^{
    tk_prop_set_num(id, 0.0);
    ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(btn, kAlignBg);
    ((void (*)(void*, unsigned))R(A_nglform_invalidate))(form, 0);
    for (int i = 0; i < g_ngrid_forms; i++)  // its on/off look, at the next tick
      if (g_grid_forms[i].btn == btn) g_grid_forms[i].off = -1;
  });
}

// Control::onEvent on one of our ALIGN buttons: true when it was ours (handled).
static bool grid_ctl_event(void* self, unsigned evt, const char* data) {
  TKGridForm* g = grid_of_button(self);
  if (!g) return false;
  press_track();
  if (!data || !gum_memory_is_readable(data, OFF_EVT_STATE + 4)) return true;
  const int button = *(const int*)(data + OFF_EVT_BUTTON), state = *(const int*)(data + OFF_EVT_STATE);
  if (evt == NGL_EVT_MOUSE && button == 1 && state == 1 && press_edge()) grid_align(g);
  return true;
}

static const char* grid_tooltip(const void* control) {
  if (!grid_of_button(control)) return nullptr;
  return "Align beat grid with master\nMoves this deck's beat grid so its beats line up with the "
         "master deck's (Traktor's copy phase from master). The track's grid is changed at once. "
         "Off while this deck is the tempo master or has SYNC on.";
}

// "gridstyle <font> <width>": restyle the ALIGN buttons live (as "bandfont" does for the band).
static void grid_style(int font, int width) {
  if (font > 0) g_align_font = font;
  if (width >= 30 && width <= 200) g_align_w = width;
  for (int i = 0; i < g_ngrid_forms; i++) {
    TKGridForm* g = &g_grid_forms[i];
    if (g->btn) {
      ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(g->btn, (unsigned)g_align_font);
      ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(g->btn, kAlignText);
      g->off = -1;  // the on/off look again, with the new font, at the next tick
    }
    grid_layout(g);
  }
  tklog("GRID: ALIGN style font=%d width=%d", g_align_font, g_align_w);
}

// "colprobe" (2026-11-24, read-only): where the track list keeps its visible columns. Logs the
// list control's class and every std::vector<int> inside it (begin/end/cap at 8-byte steps) whose
// values look like column types (EContentListColumns: 0..36), for new TK columns.
// The mangled class name of a polymorphic object, from its vtable's typeinfo; "?" if unreadable.
static const char* rtti_name(void* obj) {
  if (!looks_like_object(obj)) return "?";
  void** vt = *(void***)obj;
  if (!vt || !gum_memory_is_readable(vt - 1, sizeof(void*))) return "?";
  void* ti = vt[-1];
  if (!ti || !gum_memory_is_readable(ti, 2 * sizeof(void*))) return "?";
  const char* name = (const char*)((uintptr_t)((void**)ti)[1] & 0x7fffffffffffffffULL);
  return (name && gum_memory_is_readable(name, 4)) ? name : "?";
}

// Breadth-first through objects' pointer members (depth <= 3) for one whose class name contains
// <cls> (the list area is a TabSubFormControl holding the TraktorListControl, 2026-11-24).
static void* find_member_object(void* root, const char* cls) {
  std::vector<void*> level = { root }, seen = { root };
  for (int depth = 0; depth < 3 && !level.empty(); depth++) {
    std::vector<void*> next;
    for (void* o : level) {
      for (unsigned off = 8; off < 0x800; off += 8) {
        if (!gum_memory_is_readable((char*)o + off, 8)) break;
        void* p = *(void**)((char*)o + off);
        if (!p || ((uintptr_t)p & 7) || !looks_like_object(p)) continue;
        if (std::find(seen.begin(), seen.end(), p) != seen.end()) continue;
        seen.push_back(p);
        if (strstr(rtti_name(p), cls)) {
          tklog("COLPROBE: %s at %p (from %p +0x%x, depth %d)", cls, p, o, off, depth);
          return p;
        }
        next.push_back(p);
      }
    }
    level = next;
  }
  return nullptr;
}

static void col_probe(void) {
  void* area = g_browserform ? *(void**)((char*)g_browserform + 0x518) : nullptr;
  if (!area || !looks_like_object(area)) { tklog("COLPROBE: no list control"); return; }
  tklog("COLPROBE: list area %p class %s", area, rtti_name(area));
  void* list = find_member_object(area, "TraktorListControl");
  if (!list) list = find_member_object(g_browserform, "TraktorListControl");
  if (!list) { tklog("COLPROBE: no TraktorListControl found"); return; }
  for (unsigned off = 0x40; off < 0x2000; off += 8) {
    if (!gum_memory_is_readable((char*)list + off, 24)) break;
    int* b = *(int**)((char*)list + off);
    int* e = *(int**)((char*)list + off + 8);
    int* c = *(int**)((char*)list + off + 16);
    if (!b || e < b || c < e || ((uintptr_t)b & 3) || e - b < 3 || e - b > 64) continue;
    if (!gum_memory_is_readable(b, (size_t)(e - b) * 4)) continue;
    bool cols = true;
    for (int* p = b; p < e; p++) if (*p < 0 || *p > 36) { cols = false; break; }
    if (!cols) continue;
    char vals[256] = {0};
    for (int* p = b; p < e && strlen(vals) < 240; p++) snprintf(vals + strlen(vals), 8, "%d ", *p);
    tklog("COLPROBE:   +0x%x vector<int>[%ld]: %s", off, (long)(e - b), vals);
  }
}

// "gridprobe": each panel's controls by ID (read-only; results in the log).
static void grid_probe_all(void) {
  if (g_ngrid_forms == 0) tklog("GRIDPROBE: no AdvancedGridForm seen yet");
  for (int i = 0; i < g_ngrid_forms; i++) {
    const TKGridForm* g = &g_grid_forms[i];
    tklog("GRIDPROBE: deck %u form %p align=%p", g->deck, g->form, g->btn);
    for (unsigned id = 1; id <= 20; id++) {
      void* c = grid_ctl(g->form, id);
      if (!c) continue;
      int r[4]; grid_rect(c, r);
      tklog("GRIDPROBE:   id %2u %p rect={%d,%d,%d,%d}", id, c, r[0], r[1], r[2], r[3]);
    }
  }
}
