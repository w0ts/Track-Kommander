// tkw_columns.h — private to the columns module (tkw_columns.cpp, tkw_columns_store.cpp).
#pragma once
#include <string>

// Column types: Traktor's list has 38 (0..37); the constructor hook makes it 43, 38..42 are TK's.
const int kColScore = 38, kColDir = 39, kColDirShort = 40, kColBpm = 41, kColKey = 42;
const int kTraktorCols = 38, kAllCols = 43;
const int kBpmMinW = 120, kKeyMinW = 100;  // px: "BPM MATCH" / "126 x2  +5.0%", "KEY MATCH"

inline bool col_is_tk(int col) { return col >= kColScore && col <= kColKey; }
// The columns TK shows first in its own list only (never in the menu, never stored visible).
inline bool col_is_tk_list(int col) { return col == kColScore || col == kColBpm || col == kColKey; }

// tkw_columns_store.cpp: the layout string on its way to / from Traktor's settings file.
void cols_layout_out(std::string* s);                 // TK's types taken out, the full layout kept
std::string cols_layout_in(const std::string& s);     // TK's types put back where the user left them
void cols_store_hook();  // get/setSettingsNormal/History replaced (from tkw_mod_columns_init)
