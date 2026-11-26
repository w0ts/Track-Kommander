// tkw_band_cmd.cpp — the band's command verbs TK sends (deck, auto, bandhide) and the font probe
// (bandfont), apart from tkw_band.cpp so that file stays a readable size. Same verbs, arguments
// and replies as the Mac libtk. They run on Traktor's main thread (tkw_cmd), acknowledged at once
// for TK's own verbs.
#include <stdio.h>

#include "tkw_band_int.h"

namespace {

using namespace tkb;

std::string deck_command(const std::string& a) {  // "deck <A-D|->"
  const char d = a.empty() ? 0 : a[0];
  if (!((d >= 'A' && d <= 'D') || d == '-')) return "ERR usage: deck <A-D|->";
  set_deck_sel(d == '-' ? 0 : d);
  return "OK";
}

std::string auto_command(const std::string& a) {  // "auto <off|play_cue|automix|auto> <0|1>"
  char mode[16] = {0};
  int busy = 0;
  if (sscanf_s(a.c_str(), "%15s %d", mode, (unsigned)sizeof(mode), &busy) < 1) return "ERR usage: auto <mode> <0|1>";
  const char* text = !strcmp(mode, "play_cue") ? "Play + Cue" : !strcmp(mode, "automix") ? "AutoMix"
                   : !strcmp(mode, "auto") ? "Auto DJ" : !strcmp(mode, "off") ? "Off" : nullptr;
  if (!text) return "ERR unknown mode";
  // Off = the grey tile, on = Traktor blue, a running Auto DJ transition = orange. Off shows the
  // headphones too (the OFF picture would read "OFF Off").
  g_auto_bg = !strcmp(mode, "off") ? 0u : busy ? kBtnBusyBg : kBtnSelectedBg;
  g_auto_pic = !strcmp(mode, "automix") ? 133u : !strcmp(mode, "auto") ? 680u : 435u;
  copy_str(g_auto_text, sizeof(g_auto_text), text);
  copy_str(g_auto_mode, sizeof(g_auto_mode), mode);
  auto_changed();
  tklog("BTN: AUTO -> %s%s", text, g_auto_bg == kBtnBusyBg ? " (transition)" : "");
  return "OK";
}

std::string bandhide_command(const std::string& a) {  // "bandhide <BUTTON> <0|1>"
  char name[24] = {0};
  int hide = 1;
  if (sscanf_s(a.c_str(), "%23s %d", name, (unsigned)sizeof(name), &hide) < 1) return "ERR usage: bandhide <BUTTON> <0|1>";
  set_button_hidden(name, hide != 0);
  return "OK";
}

std::string bandfont_command(const std::string& a) {  // "bandfont <on> <off>": try fonts live
  int on = 0, off = 0;
  if (sscanf_s(a.c_str(), "%d %d", &on, &off) != 2) return "ERR usage: bandfont <on> <off>";
  g_font_on = on;
  g_font_off = off;
  apply_deck_fonts();
  tklog("BTN: fonts on=%d off=%d", on, off);
  return "OK";
}

void cmd(const char* verb, std::string (*fn)(const std::string&)) {
  tkw_command(verb, [fn](const std::string& a, const std::vector<std::string>&) { return fn(a); });
}

}  // namespace

namespace tkb {

void band_cmds_init() {
  cmd("deck", deck_command);
  cmd("auto", auto_command);
  cmd("bandhide", bandhide_command);
  cmd("bandfont", bandfont_command);
}

}  // namespace tkb
