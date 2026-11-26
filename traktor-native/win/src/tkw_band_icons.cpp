// tkw_band_icons.cpp — the band's icons (port of the Mac tk_inject.mm icon code).
//
// Icon buttons show Traktor's own pictures (LabelControl::setPictureID — it exists on Windows,
// 0x1e036d0) or, when TK sent them, TK's silhouettes: grey when off, black when lit, like the A–D
// text. Traktor cannot tint a picture, so TK either draws the pixels (tk_band_icon) or asks for a
// Traktor picture's frame recoloured (tk_band_icon_from_picture). The pictures are Picture objects
// we make with Traktor's own Picture code and hang into the label's picture slot (+0xe0, Mac
// +0xc8).
#include <stdio.h>

#include "tkw_band_int.h"

namespace {

// Keyed by button name, and "AUTO:<mode>" for the AUTO button (one per mode).
struct Icon {
  char key[24];
  void* pic[2];  // [0] off, [1] lit
};
Icon g_icons[32];
int g_nicons = 0;
void* g_pic_loader = nullptr;  // a hidden label: loads Traktor pictures by id for recolouring

const int kSzPicture = 0x80;
const int kOffPicPixels = 0x30, kOffPicW = 0x38, kOffPicH = 0x3c;
const unsigned kVtPicSetAlpha = 0x08;  // Picture vtable slot 1: setAlphaChannel(bool) (Mac +0x10)

void* make_picture(int w, int h, const uint32_t* argb) {
  if (w <= 0 || h <= 0 || w > 512 || h > 512 || !argb) return nullptr;
  void* p = tkw_new(kSzPicture);  // Traktor's heap: a label may hold it after we are gone
  if (!p) return nullptr;
  memset(p, 0, kSzPicture);
  tkw_call<void*>(WF(WF_A_pic_ctor), p);
  tkw_call<void>(WF(WF_A_pic_setSize), p, w, h, true);
  tkw_call<void>(tkw_vfn(p, kVtPicSetAlpha), p, true);
  tkw_call<void>(WF(WF_A_pic_clear), p, 0u);
  uint32_t* px = *(uint32_t**)((char*)p + kOffPicPixels);
  if (!px || *(int*)((char*)p + kOffPicW) != w || *(int*)((char*)p + kOffPicH) != h) {
    tklog("ICON: picture %dx%d not as asked (px=%p)", w, h, px);
    return nullptr;  // leaked on purpose: never seen; better than freeing a half-made object
  }
  memcpy(px, argb, (size_t)w * (size_t)h * 4);
  return p;
}

Icon* find_icon(const char* key) {
  for (int i = 0; i < g_nicons; i++)
    if (!strcmp(g_icons[i].key, key)) return &g_icons[i];
  return nullptr;
}

}  // namespace

namespace tkb {

// Traktor picture ids for icon buttons (found with picgrid on the Mac, 2026-11-23): its list,
// gear, note, headphones, faders. 0 = keep the text. X1..X9 and TK's pages: TK draws their icons.
unsigned band_icon(const char* name) {
  if (!strcmp(name, "LIST")) return 513;
  if (!strcmp(name, "SETTINGS")) return 514;
  if (!strcmp(name, "LIB")) return 322;  // plain note (239 and 407 are always boxed)
  if (!strcmp(name, "AUTO")) return g_auto_pic;
  if (!strcmp(name, "STATS")) return 470;
  if (!strcmp(name, "MATCHING")) return 103;  // mixer faders: TK's sliders and cards
  const bool spare = name[0] == 'X' && name[1] >= '1' && name[1] <= '9' && name[2] == 0;
  if (!strncmp(name, "S_", 2) || !strncmp(name, "L_", 2) || spare) return 514;
  return 0;
}

// The frame of a Traktor picture that looks "lit": the note (322) has Traktor's lit look in frame
// 1, the headphones (435) a dark frame 2, the faders (103) none darker, the others a dim frame 6.
unsigned icon_lit_frame(unsigned pic) {
  switch (pic) {
    case 322: return 1;
    case 435: return 2;
    case 103: return 0;
    default: return 6;
  }
}

void* icon_pic(const char* button, bool lit) {
  char key[24];
  if (!strcmp(button, "AUTO")) snprintf(key, sizeof(key), "AUTO:%s", g_auto_mode);
  else copy_str(key, sizeof(key), button);
  const Icon* ic = find_icon(key);
  return ic ? ic->pic[lit ? 1 : 0] : nullptr;
}

// The loader label lived in the BrowserForm that is gone; the pictures are ours and stay.
void forget_icons() { g_pic_loader = nullptr; }

}  // namespace tkb

using namespace tkb;

// key, lit 0/1, w, h, ARGB pixels (w*h). Main thread. 0 = stored.
TKW_EXPORT int tk_band_icon(const char* key, int lit, int w, int h, const uint32_t* argb) {
  if (!key || !*key) return -1;
  if (!tkw_is_main_thread()) {
    tklog("ICON: tk_band_icon called off the main thread — refused");
    return -1;
  }
  void* p = make_picture(w, h, argb);
  if (!p) return -1;
  Icon* slot = find_icon(key);
  if (!slot && g_nicons < (int)(sizeof(g_icons) / sizeof(g_icons[0]))) {
    slot = &g_icons[g_nicons++];
    memset(slot, 0, sizeof(*slot));
    copy_str(slot->key, sizeof(slot->key), key);
  }
  if (!slot) {
    tklog("ICON: no room for '%s'", key);
    return -1;
  }
  slot->pic[lit ? 1 : 0] = p;  // the previous one stays alive: a label may still show it
  place_buttons();
  invalidate();
  return 0;
}

// Traktor's picture <id>, frame <frame>, as a silhouette in <argb> (its alpha, our colour): how a
// Traktor icon gets the band's grey/black. Stored like tk_band_icon. Main thread; 0 = stored.
TKW_EXPORT int tk_band_icon_from_picture(const char* key, int lit, unsigned id, unsigned frame, uint32_t argb) {
  if (!key || !g_bf) return -1;
  if (!tkw_is_main_thread()) {
    tklog("ICON: tk_band_icon_from_picture called off the main thread — refused");
    return -1;
  }
  if (!g_pic_loader) {
    g_pic_loader = new_label("");
    if (!g_pic_loader) return -1;
    set_visible(g_pic_loader, false);
  }
  set_picture_id(g_pic_loader, id);
  const char* src = *(const char* const*)((char*)g_pic_loader + kOffLblPicture);
  if (!src || !tkw_readable(src, 0x48)) {
    tklog("ICON: no picture %u", id);
    return -1;
  }
  const int W = *(const int*)(src + kOffPicW), H = *(const int*)(src + kOffPicH);
  const int aw = tkw_call<int>(WF(WF_A_pic_animWidth), (const void*)src);
  const uint32_t* px = *(const uint32_t* const*)(src + kOffPicPixels);
  if (!px || aw <= 0 || H <= 0 || W < aw || frame >= (unsigned)(W / aw) ||
      !tkw_readable(px, (size_t)W * (size_t)H * 4)) {
    tklog("ICON: picture %u frame %u unusable", id, frame);
    return -1;
  }
  std::vector<uint32_t> out((size_t)aw * (size_t)H);
  for (int y = 0; y < H; y++)
    for (int x = 0; x < aw; x++)
      out[(size_t)y * aw + x] = (px[(size_t)y * W + frame * aw + x] & 0xff000000u) | (argb & 0x00ffffffu);
  return tk_band_icon(key, lit, aw, H, out.data());
}

void* tkw_band_icon_picture(const char* key, bool lit) {
  if (!key) return nullptr;
  const Icon* ic = find_icon(key);
  return ic ? ic->pic[lit ? 1 : 0] : nullptr;
}
