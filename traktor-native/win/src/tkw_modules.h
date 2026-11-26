// tkw_modules.h — the modules of libtk.dll and their start order (each in tkw_<name>.cpp).
// The band comes first: it learns the UI thread and the BrowserForm the others build on.
#pragma once

struct TkwModule {
  const char* name;
  void (*init)();
};

void tkw_mod_window_init();      // tkw_window.cpp: Traktor's main window, quit, mouse-downs
void tkw_mod_band_init();        // tkw_band.cpp: band, tree node, tooltips, list area
void tkw_mod_cards_init();       // tkw_cards.cpp: the matching cards under the column headers
void tkw_mod_props_init();       // tkw_props.cpp: app properties (read/write/watch)
void tkw_mod_collection_init();  // tkw_collection.cpp: tracks, loads, library write, settings
void tkw_mod_playlists_init();   // tkw_playlists.cpp: Kommander playlists, results list
void tkw_mod_columns_init();     // tkw_columns.cpp: SCORE / BPM MATCH / KEY MATCH / DIRECTORY
void tkw_mod_cues_init();        // tkw_cues.cpp: hotcue writes
void tkw_mod_grid_init();        // tkw_grid.cpp: ALIGN GRID
void tkw_mod_audio_init();       // tkw_audio.cpp: audio device choice
void tkw_mod_nlist_init();       // tkw_nlist.cpp: native page (tree + table drawn by Traktor)

static const TkwModule kTkwModules[] = {
    {"window", tkw_mod_window_init},       {"band", tkw_mod_band_init},
    {"cards", tkw_mod_cards_init},
    {"props", tkw_mod_props_init},
    {"collection", tkw_mod_collection_init}, {"playlists", tkw_mod_playlists_init},
    {"columns", tkw_mod_columns_init},     {"cues", tkw_mod_cues_init},
    {"grid", tkw_mod_grid_init},           {"audio", tkw_mod_audio_init},
    {"nlist", tkw_mod_nlist_init},
};
