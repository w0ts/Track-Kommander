// libtk.dylib — Track Kommander native opener for Traktor Pro 4 (Mac, private).
//
// Goal 1, MVP: open Track Kommander's view from inside Traktor, using only the
// hook that is already proven to fire. We do NOT add a tab or a widget to
// Traktor's Qt UI (that needs model/widget surgery). Instead:
//
//   1. The user makes one browser entry in Traktor named "Track Kommander"
//      (a playlist or folder) — a real, clickable row in Traktor's own tree.
//   2. libtk hooks BrowserForm::treeSelect(std::string const& path, category),
//      which fires on every tree selection with the real path string.
//   3. When the selected path matches the sentinel name, libtk POSTs /show to
//      Track Kommander's loopback HTTP server (webhook_server.py), which raises
//      TK's window.
//
// So an existing, native, clickable entry in Traktor's browser becomes the
// "button that opens our view". No re-sign-fragile UI injection.
//
// The hook logs every decoded path, so the exact string Traktor passes for the
// entry is visible in the log on the first run. If the entry's display name is
// not literally in the path, set TK_SHOW_MATCH to whatever the log shows — no
// rebuild needed.
//
// Env knobs (all optional):
//   TK_DYLIB_LOG   log file           (default /tmp/tk_dylib.log)
//   TK_SHOW_PORT   TK webhook port    (default 12345, = core.constants.WEBHOOK_PORT)
//   TK_SHOW_MATCH  case-insensitive substring to match in the path
//                                      (default "track kommander")
//   TK_CMD_PORT    loopback port of the TK->dylib command channel
//                                      (default 8767; never 8766, reserved)
//
// Command channel (the mirror of the /show opener): a loopback TCP listener on
// 127.0.0.1:TK_CMD_PORT accepts one newline-terminated text command per
// connection so Track Kommander can drive native actions on demand:
//     load <deckIdx> <key>\n   load collection track <key> into deck <deckIdx>
//     read <key>\n             read track <key>'s metadata, POST /nativeTrack
//     loadsel <deckIdx>\n      load the track SELECTED in Traktor's browser (#3)
//     readsel\n                read the selected track's metadata, POST /nativeTrack
//     load_path <deckIdx> <path>\n  load the collection track at <path> (#2)
//     read_path <path>\n       read the collection track at <path>, POST /nativeTrack
// (key 0 = "the first track in the collection", a convenience for testing.)
// <path> is the rest of the line (it may contain spaces); it must be the same
// absolute file path Traktor imported the track under.
// Reply: "OK\n" once the command is parsed and accepted, "ERR <why>\n" if not.
// OK means ACCEPTED, not finished: the Traktor C++ work is dispatched to the
// MAIN THREAD (Traktor's C++ is not thread-safe) and its outcome goes to the
// log. The TK sender is src/workers/native_cmd.py.
//
// Build: src/build.sh. Inject via DYLD_INSERT_LIBRARIES into the re-signed copy.

#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#include <dispatch/dispatch.h>
#include <CoreAudio/CoreAudio.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <dlfcn.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <cctype>
#include <cerrno>
#include <cstdarg>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <map>
#include <pwd.h>
#include <string>
#include <vector>
#include "frida-gum.h"
#include "addrs_4_5_1_21.h"

static intptr_t g_slide = 0;
static FILE*    g_logfp = nullptr;
static int      g_port  = 12345;
// Plain char[] on purpose: a global std::string has a runtime constructor, and
// its initialization order against this dylib's constructor is not guaranteed
// (static init order fiasco) — the constructor could overwrite an env value, or
// be overwritten by the string's own ctor. A char[] is initialised at compile
// time, so g_match is already valid when tk_init runs. Kept lowercase; the path
// is lowercased before the compare.
static char g_match[128] = "track kommander";

// The live BrowserForm* seen by the treeSelect hook (its arg0), stashed on every
// fire so the selection commands (#3, loadsel/readsel) can query Traktor's own
// browser for whatever row the user has highlighted. Guarded before every use.
static void* g_browserform = nullptr;

// The live DataSetModel instance, captured from the rowCount hook. Used by the
// native-UI injection to find the list VIEW (the QAbstractItemView whose
// model() == this). See do_native_ui_inject.
static void* g_dsm = nullptr;

// The live QQuickWindow, captured from the QQuickWindowPrivate::polishItems hook
// (Traktor is a Qt Quick app). Used by the QML scene dump / item injection to
// reach contentItem() without QGuiApplication::topLevelWindows(). q_ptr is the
// QObject back-pointer at QObjectPrivate+0x8.
static void* g_qq_window = nullptr;
static int   g_qq_seen = 0;
// Traktor renders several offscreen QML scenes (browser, decks, mixer…), each a
// QQuickWindow. Captured from QOffscreenView::onWindowChanged.
static void* g_qq_windows[12] = {0};
static int   g_qq_window_count = 0;
// The live QBrowser QQuickItem (the browser list), captured from QBrowser::update.
// Its window() is the MAIN UI window; the item itself is the cards-bar injection point.
static void* g_qbrowser = nullptr;
// Traktor's main-UI view and its panel/form manager (NI::NGL toolkit), captured from
// MainWindowNGLView::createBrowserForm at startup. These are the handles to add our own Form.
static void* g_mainview = nullptr;
static void* g_panelmgr = nullptr;
// A valid NGL font id, captured from Traktor's own LabelControl::setFontID calls, so our
// injected label can render legible text (a label with no font draws no glyphs).
static int g_fontid = -1;
static int g_fontid_seen = 0;

// Itanium ABI runtime downcast, from the system libc++abi this dylib already
// links. Narrowing an Entity* to a ContentEntryTrack* with Traktor's OWN
// type_info objects is byte-for-byte the call ContentCollectionMgr::getTrack
// makes internally (it goes through the same ___dynamic_cast stub), so it is a
// replica of proven code, not a guess. Returns null when the object is not a
// ContentEntryTrack.
extern "C" void* __dynamic_cast(const void* sub, const void* src_type,
                                const void* dst_type, long src2dst_hint);

static void tklog(const char* fmt, ...) {
  if (!g_logfp) {
    const char* p = getenv("TK_DYLIB_LOG");
    g_logfp = fopen(p && *p ? p : "/tmp/tk_dylib.log", "a");
    if (!g_logfp) return;
  }
  // Timestamped, so a stall seen elsewhere can be matched to what libtk was doing then.
  struct timeval tv; gettimeofday(&tv, nullptr);
  struct tm tmv; localtime_r(&tv.tv_sec, &tmv);
  fprintf(g_logfp, "%02d:%02d:%02d.%03d ", tmv.tm_hour, tmv.tm_min, tmv.tm_sec, (int)(tv.tv_usec / 1000));
  va_list ap; va_start(ap, fmt);
  vfprintf(g_logfp, fmt, ap); va_end(ap);
  fputc('\n', g_logfp); fflush(g_logfp);
}

static inline gpointer R(uintptr_t pref) { return (gpointer)(pref + (uintptr_t)g_slide); }

static intptr_t main_exe_slide(void) {
  uint32_t n = _dyld_image_count();
  for (uint32_t i = 0; i < n; i++) {
    const struct mach_header* h = _dyld_get_image_header(i);
    if (h && h->filetype == MH_EXECUTE) return _dyld_get_image_vmaddr_slide(i);
  }
  return _dyld_get_image_vmaddr_slide(0);
}

// --- libc++ std::string decode (SSO) -------------------------------------
// Short: byte0 LSB == 0, size = byte0 >> 1, data at offset 1.
// Long:  byte0 LSB == 1, size at offset 8, char* at offset 16.
static std::string decode_std_string(const void* s) {
  // libc++ STANDARD layout on arm64 (verified 2026-11-23 against Traktor's own code, which tests
  // byte 23's sign bit): short = chars inline from +0, length in byte 23 (< 23, high bit clear);
  // long = {char* data @0, size_t size @8, cap @16} with byte 23's high bit set. The earlier
  // "alternate layout" decoder read the leading '$' of every tree path (0x24) as length 18.
  if (!s || !gum_memory_is_readable(s, 24)) return std::string();
  const unsigned char tag = ((const unsigned char*)s)[23];
  const char* data;
  size_t size;
  if (tag & 0x80u) {  // long
    data = *(const char* const*)s;
    size = *(const size_t*)((const char*)s + 8);
  } else {            // short
    data = (const char*)s;
    size = tag;
    if (size > 22) return std::string();
  }
  if (size > 4096) return std::string();  // not a sane path: refuse
  if (!data || !gum_memory_is_readable(data, size)) return std::string();
  return std::string(data, size);
}

static std::string to_lower(std::string v) {
  for (char& c : v) c = (char)std::tolower((unsigned char)c);
  return v;
}

// Case-insensitive "does haystack contain the (already-lowercase) needle".
static bool ci_contains(const std::string& hay, const char* needle_lc) {
  if (!needle_lc || !*needle_lc) return false;
  return to_lower(hay).find(needle_lc) != std::string::npos;
}

// --- loopback POST to TK, off the UI thread ------------------------------
// A detached thread sends one POST to 127.0.0.1:g_port. No Origin/Sec-Fetch
// headers + Host 127.0.0.1 → passes TK's webhook_guard.
struct PostJob { char path[64]; char body[1024]; };

static void* post_job(void* arg) {
  PostJob* j = (PostJob*)arg;
  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons((uint16_t)g_port);
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);  // 127.0.0.1

  // Retry with a FRESH socket each attempt: a failed connect() can't be retried
  // on the same fd, and TK's threaded server can momentarily refuse a loopback
  // connect (accept race) even while bound — which dropped posts in a cmd run.
  int fd = -1;
  for (int attempt = 0; attempt < 5; attempt++) {
    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { usleep(40000); continue; }
    if (connect(fd, (struct sockaddr*)&addr, sizeof(addr)) == 0) break;
    close(fd); fd = -1;
    usleep(40000);  // 40 ms
  }
  if (fd >= 0) {
    char req[1280];
    int n = snprintf(req, sizeof(req),
                     "POST %s HTTP/1.0\r\n"
                     "Host: 127.0.0.1\r\n"
                     "Content-Type: application/json\r\n"
                     "Content-Length: %zu\r\n"
                     "Connection: close\r\n"
                     "\r\n"
                     "%s",
                     j->path, strlen(j->body), j->body);
    if (n > 0) (void)write(fd, req, (size_t)n);
    // Wait for TK's reply before closing. TK's webhook writes its 200 BEFORE it acts on the
    // post; closed at once, that write could fail and the action was dropped (2026-11-26: a
    // click on the Track Kommander node left "No answer from Track Kommander"; Windows had the
    // same, tkw_cmd.cpp send_post).
    shutdown(fd, SHUT_WR);
    struct timeval tv = {2, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    char reply[256];
    ssize_t got = 0, r;
    while ((r = read(fd, reply, sizeof(reply))) > 0) got += r;
    if (got > 0) tklog("  -> POST %s sent to 127.0.0.1:%d", j->path, g_port);
    else tklog("  -> POST %s: no answer from TK (127.0.0.1:%d)", j->path, g_port);
    close(fd);
  } else {
    tklog("  -> POST %s: connect 127.0.0.1:%d failed after retries (TK not running?)", j->path, g_port);
  }
  free(j);
  return nullptr;
}

static void post_json(const char* path, const char* body) {
  PostJob* j = (PostJob*)calloc(1, sizeof(PostJob));
  if (!j) return;
  strlcpy(j->path, path, sizeof(j->path));
  strlcpy(j->body, body ? body : "{}", sizeof(j->body));
  pthread_t t;
  if (pthread_create(&t, nullptr, post_job, j) == 0) pthread_detach(t);
  else free(j);
}

static void fire_show(void) { post_json("/show", "{}"); }

// JSON-escape a string into out (bounded). Handles the characters that would
// otherwise break the small hand-built JSON body: " \ and control chars.
static void json_escape(const std::string& in, char* out, size_t cap) {
  size_t o = 0;
  for (char ch : in) {
    unsigned char c = (unsigned char)ch;
    const char* esc = nullptr; char buf[8];
    if (c == '"')      esc = "\\\"";
    else if (c == '\\') esc = "\\\\";
    else if (c == '\n') esc = "\\n";
    else if (c == '\r') esc = "\\r";
    else if (c == '\t') esc = "\\t";
    else if (c < 0x20) { snprintf(buf, sizeof(buf), "\\u%04x", c); esc = buf; }
    if (esc) { size_t l = strlen(esc); if (o + l >= cap) break; memcpy(out + o, esc, l); o += l; }
    else { if (o + 1 >= cap) break; out[o++] = ch; }
  }
  out[o < cap ? o : cap - 1] = '\0';
}

enum { HK_TREESEL = 2, HK_VALUEFORTRACK = 3, HK_ROWCOUNT = 4, HK_DATAQT = 5, HK_LOADDECK = 6, HK_POLISH = 7, HK_SCENE = 8, HK_ACTIVATE = 9, HK_ITEM = 10, HK_OSVIEW = 11, HK_QBROWSER = 12, HK_MAINVIEW = 13, HK_FONTID = 14, HK_CARDBG = 15, HK_CTLEVENT = 16, HK_STDTREE = 17, HK_RELOAD = 18,
       HK_DIAG_CAT = 19, HK_DIAG_TNI = 20, HK_DIAG_DND = 21, HK_DIAG_CTX = 22, HK_BFRESIZED = 23, HK_PICID = 24, HK_SWINIT = 25, HK_FORMMOUSE = 26, HK_SETENTRY = 27, HK_AUDIOSETUP = 28, HK_NSASELECT = 29, HK_CCMANUAL = 30, HK_CCENABLE = 31,
       HK_GRIDCTOR = 32, HK_GRIDRESIZE = 33, HK_BFDTOR = 34 };
static int g_diag_seen[32];  // per-hook call counter for the HK_DIAG_* probes

// === Left tree (2026-11-23): the live BrowserStdTree (captured in selectTreeNode), our
// "Track Kommander" root node, and the root categories the user wants gone (e.g. Beatport).
// g_tree_auto: re-apply both after every onReloadForm (Traktor rebuilds the tree from scratch).
static void*    g_stdtree = nullptr;
static void*    g_tk_node = nullptr;
// Default on: Beatport + Beatport (Preview) + Apple Music (ITUNES, cat 0x1a) removed, our node added (TK_TREE=0 turns it off).
static unsigned g_tree_delcats[8] = { 0x1c, 0x22, 0x1a };  // Beatport, Beatport (Preview), Apple Music
static int      g_ntree_delcats = 3;
static bool     g_tree_auto = true;
static bool     g_band_done = false;
// > 0 while BrowserStdTree::selectTreeNode runs: a treeSelect from inside it is a real tree
// selection; any other treeSelect is Traktor re-selecting the current view by itself.
static int      g_tree_depth = 0;  // the button band is drawn once per browser form
static char     g_tk_node_label[64] = "Kommander";
// Traktor's tree pictures come in pairs, the glyph grey at an even id and white (selected) at the
// next (Track Collection: 2/3; TreeNodeInfo uses icon+1 when selected). 41 was odd: Kommander showed
// another glyph when not selected (User, 2026-11-24). The node-graph glyph is the pair 42/43.
// Set when Traktor announces its quit (NSApplicationWillTerminate): from then on libtk leaves
// Traktor's controls alone. Traktor tears its forms down after saving, and a libtk timer or
// block that touched one then crashed it on the way out (2026-11-24, minidump: the GRID
// panel watcher in Control code during shutdown).
static bool g_quitting = false;
static int      g_tree_icon = 42;     // "treeicon N" to try another
// Band text fonts (an NGL font id carries its colour): lit buttons black text, off ones grey, as
// Traktor's own buttons. 0 = the band's default font. "bandfont <on> <off>" tries others live.
// Chosen 2026-11-24 from a sweep of Traktor's font ids, against its own CUE/CUP/FLX row:
// 48 = black, 30 = grey, same size and weight; 157 = small grey, for captions ("MATCHING SOURCE:",
// the AUTO mode name), which the button size would cut off.
static int      g_font_on = 48, g_font_off = 30, g_font_caption = 157;
// Card titles (toggle buttons): the same black/grey, a size smaller (User: "text too big").
static int      g_card_font_on = 3, g_card_font_off = 157;
static void do_tree_apply(void);
static void band_relayout(void);
// TK (inside Traktor) wants to hear at once when the track list moved: its page covers the list
// and lagged behind a separator drag (User, 2026-11-24: "feels clunky"). Main thread.
static void (*g_layout_cb)(void) = nullptr;
static void layout_changed(void) { if (g_layout_cb) g_layout_cb(); }
extern "C" __attribute__((visibility("default"))) void tk_set_layout_callback(void (*cb)(void)) {
  g_layout_cb = cb;
}
static void band_set_visible(bool on);
static void set_band_status(const char* text);
// Why TK is not answering, when libtk knows (shown in the band in place of "No answer …"): its
// script missing at the configured path, or its start failed. A wrong repo folder in the slim
// app's tk-embed.conf gave only the generic message (User, 2026-11-25).
static std::string g_tk_problem;
static void do_button_strip(int fontid);

// === Band buttons (2026-11-23): the LabelControls do_button_strip adds, and the action each one
// reports. Control::onEvent (HK_CTLEVENT) matches a left press on one of them and POSTs
// /uiAction {"action":name} to TK. Written on the main queue, read in the hook (also main thread).
#define MAX_BAND_BTNS 24
static void*       g_btn_ctrl[MAX_BAND_BTNS];
static const char* g_btn_name[MAX_BAND_BTNS];
static bool        g_btn_hidden[MAX_BAND_BTNS];  // "bandhide": e.g. the add-on's page without the add-on
static int         g_nbtn = 0;
static int         g_btn_evt_seen = 0;  // first-N diagnostics of events reaching our buttons

// Every DataSetModel seen in rowCount (main browser and controller/D2 screens may each have one),
// and how many treeSelect backtraces have been logged.
static void* g_dsms[8];
static int   g_ndsms = 0;
static int   g_treesel_bt = 0;
// First-fire diagnostics: is the model queried at all while headless?
static int g_rowcount_seen = 0, g_dataqt_seen = 0, g_activate_seen = 0;
static const char* qobject_classname(const void* obj);  // defined below; used by HK_ACTIVATE

// getTitle/getArtist/... return a pointer to a plain libc++ std::string inside the MediaItem
// (getTitle=this+0x38, getArtist=this+0x50, ...), STANDARD layout like the tree paths: decode with
// decode_std_string. Measured 2026-11-24 over 882 tracks: an earlier {char* data, size_t len}
// decoder read every value of 23 bytes or more and none shorter — the inline (SSO) strings. That
// is what the 2026-11-22 notes took for "lazily hydrated" strings; the data was always there.
typedef const void* (*fn_getstr)(const void* mediaitem);

// Call an arm64 function that returns a struct via the x8 struct-return
// register (a non-trivially-copyable type like SmartPtr): x0=a0, x1=a1, x8=sret.
// Plain C cannot target x8, so set the registers explicitly and blr.
static void call_sret2(void* fn, void* a0, void* a1, void* sret) {
  register long x0 asm("x0") = (long)a0;
  register long x1 asm("x1") = (long)a1;
  register long x8 asm("x8") = (long)sret;
  asm volatile("blr %[fn]"
      : "+r"(x0), "+r"(x1), "+r"(x8)
      : [fn] "r"(fn)
      : "x2","x3","x4","x5","x6","x7","x9","x10","x11","x12","x13","x14","x15",
        "x16","x17","x18","lr","cc","memory");
}

// Goal 4 proof: native metadata reads. valueForTrack is called by Qt for every
// track cell with a live SmartPtr<ContentEntryTrack>; ContentEntryTrack IS-A
// MediaItem (base at offset 0), so getTitle/getArtist apply to the raw pointer
// directly. We only READ, and only a few distinct tracks, to avoid log spam.
static int g_reads_logged = 0;

static bool looks_like_object(const void* p) {
  if (!p || !gum_memory_is_readable(p, 16)) return false;
  const void* vtab = *(const void* const*)p;         // candidate vtable ptr
  return vtab && gum_memory_is_readable(vtab, 8);
}

// Read a plain NI string field at track+off (same decode as the getters, which
// just return this+off).
static std::string read_str_field(const void* track, size_t off) {
  return decode_std_string((const char*)track + off);
}
// Read a u32 value field at track+off, 0 if unreadable.
static uint32_t read_u32_field(const void* track, size_t off) {
  const void* p = (const char*)track + off;
  return gum_memory_is_readable(p, 4) ? *(const uint32_t*)p : 0u;
}

// deck: 0..3 => "A".."D" included in the payload; <0 => omitted (e.g. browse).
// Reads the full parity field set (confirmed MediaItem offsets, 2026-11-22):
// strings title/artist/album/genre/label/comment/comment2, float bpm, and the
// u32 fields rating/play_count/import_date/release_date (dates are packed u32).
static void read_and_post_track(const void* track, const char* source, bool always, int deck = -1) {
  // Browse reads are capped (the table renders many cells); deck loads always
  // post (rare, meaningful — this is the native replacement for the QML
  // deckLoaded bridge).
  if (!always && g_reads_logged >= 8) return;
  if (!looks_like_object(track)) return;
  std::string title    = read_str_field(track, OFF_MI_TITLE);
  std::string artist   = read_str_field(track, OFF_MI_ARTIST);
  std::string album    = read_str_field(track, OFF_MI_ALBUM);
  std::string genre    = read_str_field(track, OFF_MI_GENRE);
  std::string label    = read_str_field(track, OFF_MI_LABEL);
  std::string comment  = read_str_field(track, OFF_MI_COMMENT);
  std::string comment2 = read_str_field(track, OFF_MI_COMMENT2);
  double bpm = 0.0;
  const void* bp = (const char*)track + OFF_MI_BPM;        // float field, read directly
  if (gum_memory_is_readable(bp, 4)) bpm = (double)*(const float*)bp;
  uint32_t rating       = read_u32_field(track, OFF_MI_RATING);
  uint32_t play_count   = read_u32_field(track, OFF_MI_PLAYCOUNT);
  uint32_t import_date  = read_u32_field(track, OFF_MI_IMPORTDATE);
  uint32_t release_date = read_u32_field(track, OFF_MI_RELEASEDATE);
  if (!always) g_reads_logged++;
  char deckbuf[16] = {0};
  if (deck >= 0 && deck <= 3) snprintf(deckbuf, sizeof(deckbuf), ",\"deck\":\"%c\"", 'A' + deck);
  tklog("  nativeRead[%s] deck=%d track=%p title='%s' artist='%s' album='%s' genre='%s' bpm=%.2f rating=%u plays=%u",
        source, deck, track, title.c_str(), artist.c_str(), album.c_str(), genre.c_str(), bpm, rating, play_count);
  char et[512], ea[512], eal[512], eg[512], el[512], ec[1024], ec2[512];
  json_escape(title, et, sizeof(et));
  json_escape(artist, ea, sizeof(ea));
  json_escape(album, eal, sizeof(eal));
  json_escape(genre, eg, sizeof(eg));
  json_escape(label, el, sizeof(el));
  json_escape(comment, ec, sizeof(ec));
  json_escape(comment2, ec2, sizeof(ec2));
  char body[4096];
  snprintf(body, sizeof(body),
           "{\"title\":\"%s\",\"artist\":\"%s\",\"album\":\"%s\",\"genre\":\"%s\",\"label\":\"%s\","
           "\"comment\":\"%s\",\"comment2\":\"%s\",\"bpm\":%.2f,\"rating\":%u,\"play_count\":%u,"
           "\"import_date\":%u,\"release_date\":%u,\"source\":\"%s\"%s}",
           et, ea, eal, eg, el, ec, ec2, bpm, rating, play_count, import_date, release_date,
           source, deckbuf);
  post_json("/nativeTrack", body);
}

// Find the 0-based deck index of a MixerDeckChannel within a MixerDeck's channel
// vector (begin@+0x178,end@+0x180,0x10/elem); -1 if not found. Guarded reads.
static int deck_index_of_channel(const void* mixer, const void* channel) {
  if (!mixer || !channel || !gum_memory_is_readable((const char*)mixer + 0x188, 8)) return -1;
  const char* arr = *(const char* const*)((const char*)mixer + 0x178);
  const char* end = *(const char* const*)((const char*)mixer + 0x180);
  if (!arr || !end || end < arr) return -1;
  long count = (end - arr) / 0x10;
  if (count < 1 || count > 16 || !gum_memory_is_readable(arr, count * 0x10)) return -1;
  for (long i = 0; i < count; i++) {
    if (*(const void* const*)(arr + i * 0x10) == channel) return (int)i;
  }
  return -1;
}

// === Cards registry (2026-11-22): drawn per-frame in BrowserForm::onDrawBackground on_leave via
// Picture fillArea/drawRectangle (x,y,w,h,argb, absolute coords). Written from the main queue,
// read from the render thread — benign race, fine for the proof. Also backs "proper button" bgs. ===
typedef struct { int x, y, w, h; unsigned bg, border, barcol; int barw; bool used; } TKCard;
static TKCard g_cards[16];
static int    g_ncards = 0;
static void*  g_card_pic = nullptr;  // Picture* stashed in on_enter(HK_CARDBG), used in on_leave

// === Matching cards inside Traktor (2026-11-23): TK's axis cards, mirrored by the `cards` command
// into a row (or rows) under the band buttons. Each card: a LabelControl for its name (clickable:
// POST /uiAction CARD:<key>) + a per-frame fill/border/weight bar/value marker drawn below. ===
#define MAX_AXIS_CARDS 40
// A card is built only from LabelControls (their fills paint reliably in the band; per-frame
// Picture fills there never showed): bg, name, value track + marker, weight bar track + bar.
// FILL: playing track. VFILL/VKNOB: the value slider drawn like Traktor's crossfader (a thin line,
// blue up to a small handle with a dark outline = VMARK).
// SKOUT/SKNOB: the amount slider's handle, same Traktor fader style (blue line up to it).
enum { CP_BG, CP_NAME, CP_VTRACK, CP_VMARK, CP_STRACK, CP_SBAR, CP_FILL, CP_VFILL, CP_VKNOB,
       CP_SKOUT, CP_SKNOB, CP_COUNT };
typedef struct {
  void* lbl;  // == part[CP_NAME] (kept for the older code paths)
  void* part[CP_COUNT];
  int x, y, w, h; bool active; int strength, value, hue, src, font; char key[24]; char name[32];
  int last_sent_value, last_sent_strength;
  double touched;  // when the user last changed this card here (CFAbsoluteTime): echoes wait
  char desc[256];  // TK's description of the axis (tooltips)
} TKAxisCard;
static bool g_cards_expanded = false;  // MATCHING toggles the full cards in the band
static void card_event(int i, int part, unsigned evt, const char* d);
static void form_mouse(void* self, unsigned evt, const char* d);
static void press_track(void);
static bool press_edge(void);
static void cards_relayout(void);
static TKAxisCard g_acards[MAX_AXIS_CARDS];
static int g_nacards = 0;     // cards in use (drawn)
static int g_band_top = -1, g_band_left = 0, g_band_right = 0, g_band_btn_h = 44;
static int g_list_orig[4];    // the track list's rect before the band pushed it down
static int g_list_pushed_top = -1;  // where WE last put the list's top (to spot Traktor's re-layout)
static int g_list_pushed_left = -1;  // and its left edge (narrowed beside the native page's tree)
// The native page (tk_nlist.mm): 0 = none; 1 = its tree beside Traktor's own list, narrowed by
// NL_TREE_W and showing TK's tracks; 2 = its tree and its own table, Traktor's list hidden.
static int g_nl_mode = 0;
#define NL_TREE_W 300
static int g_btn_w[MAX_BAND_BTNS];             // band button widths, for re-placing them
static void* g_source_lbl = nullptr;  // the "SOURCE:" text before A–D
// The band shows only while TK's view is selected in the tree (our node, or the Results
// playlist it opens); elsewhere it hides and the track list gets its height back.
static bool g_band_visible = false;
static void* g_status_lbl = nullptr;  // band text right of the buttons: "Loading …" etc.
static unsigned g_list_gen = 0;       // bumped whenever TK's list arrives
static const int kSourceW = 124, kAfterSourceGap = 22;
static const int kSepGap = 22;            // room around each separator next to MATCHING
static void* g_auto_lbl = nullptr;        // that text (clicks on it act as the AUTO button)
static void* g_match_strip = nullptr;     // (old) bar under MATCHING; the tile shows the state now
static void* g_btn_tile[MAX_BAND_BTNS];              // Traktor-style tile behind each icon button (same slot)
static char  g_active_page[16] = "";      // TK page shown in Traktor's list area: its tile is blue
static void* g_sep[8] = { nullptr };  // a line between two button groups (see band_group)
static bool g_list_orig_set = false;

// hsl(h, 55 %, l) -> 0xAARRGGBB, to give each card its axis hue like TK's cards.
static unsigned hue_argb(int hue, double l) {
  double s = 0.55, c = (1 - fabs(2 * l - 1)) * s, hp = (hue % 360) / 60.0;
  double x = c * (1 - fabs(fmod(hp, 2) - 1)), r = 0, g = 0, b = 0;
  if (hp < 1) { r = c; g = x; } else if (hp < 2) { r = x; g = c; } else if (hp < 3) { g = c; b = x; }
  else if (hp < 4) { g = x; b = c; } else if (hp < 5) { r = x; b = c; } else { r = c; b = x; }
  double m = l - c / 2;
  return 0xff000000u | ((unsigned)((r + m) * 255) << 16) | ((unsigned)((g + m) * 255) << 8) | (unsigned)((b + m) * 255);
}

#define TK_EXPORT extern "C" __attribute__((visibility("default")))
// Traktor's audio device choice (observation stage).
#include "tk_audio.mm"
// The deck's GRID panel (tk_deckgrid.mm, included further down): what the hooks call.
static void grid_form_built(void* form, unsigned deck);
static void grid_form_seen(void* form);
static void grid_relayout_pending(void);
static bool grid_ctl_event(void* self, unsigned evt, const char* data);
static const char* grid_tooltip(const void* control);
static void grid_probe_all(void);
// A TK page drawn with Traktor's labels (tk_nlist.mm, included further down).
static void nl_layout(void);
static bool nl_ctl_event(void* self, unsigned evt, const char* data);
static void nl_form_event(unsigned evt, char* data);
// TK's columns (tk_columns.mm, included further down).
static void tk_score_column(bool on);
static void tk_force_tk_sort(void* bf);

static void on_leave(GumInvocationContext* ic, gpointer) {
  intptr_t id = (intptr_t)gum_invocation_context_get_listener_function_data(ic);
  if (id == HK_AUDIOSETUP) { g_in_audio_setup--; return; }
  if (id == HK_GRIDRESIZE) { grid_relayout_pending(); return; }  // Traktor laid it out: ours again
  if (id == HK_CCMANUAL) { g_in_manual--; return; }
  if (id == HK_CCENABLE) { g_in_enable--; return; }
  if (id >= HK_DIAG_CAT && id <= HK_DIAG_CTX) {
    // Which tree/category checks run when the user acts on TK's list, and what they answer.
    if (g_diag_seen[id] >= 40) return;
    g_diag_seen[id]++;
    void* ret = gum_invocation_context_get_return_value(ic);
    if (id == HK_DIAG_TNI) {
      const char* info = (const char*)ret;
      int cat = (info && gum_memory_is_readable(info + OFF_TNI_CAT, 4)) ? *(const int*)(info + OFF_TNI_CAT) : -1;
      tklog("DIAG getSelectedTreeNodeInfo -> %p cat=0x%x", ret, cat);
    } else {
      static const char* names[] = { "getSelectedBrowserCategory", "", "canDragAndDrop", "BrowserForm::show/createContextMenu" };
      tklog("DIAG %s -> 0x%lx", names[id - HK_DIAG_CAT], (unsigned long)(uintptr_t)ret);
    }
    return;
  }
  if (id == HK_STDTREE) { if (g_tree_depth > 0) g_tree_depth--; return; }
  if (id == HK_BFRESIZED) { band_relayout(); layout_changed(); return; }
  if (id == HK_RELOAD) {
    // onReloadForm just rebuilt the tree: our node is gone and Beatport is back.
    g_tk_node = nullptr;
    tklog("TREE: onReloadForm rebuilt the tree (auto=%d)", (int)g_tree_auto);
    // Never edit the tree inside onReloadForm itself: at startup it runs within TraktorApp::init,
    // before the window is laid out, and the first BrowserForm::update then drew past its picture
    // (SIGSEGV in PictureSCALAR::fillLine, 2026-11-23). Edit it once the main loop runs, as the
    // manual commands (which worked) did.
    if (g_tree_auto)
      dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(500 * NSEC_PER_MSEC)),
                     dispatch_get_main_queue(), ^{ if (!g_tk_node) do_tree_apply(); });
    return;
  }
  if (id != HK_CARDBG || !g_card_pic || g_ncards <= 0) return;
  typedef void (*fn5)(void*, int, int, int, int, unsigned);
  fn5 fill = (fn5)R(A_pic_fillArea);
  fn5 rect = (fn5)R(A_pic_drawRect);
  for (int i = 0; i < g_ncards; i++) {
    TKCard* c = &g_cards[i];
    if (!c->used) continue;
    fill(g_card_pic, c->x, c->y, c->w, c->h, c->bg);                              // bg fill
    if (c->barw > 0) fill(g_card_pic, c->x + 4, c->y + c->h - 8, c->barw, 5, c->barcol);  // value bar
    rect(g_card_pic, c->x, c->y, c->w, c->h, c->border);                          // border
  }
}

static void on_enter(GumInvocationContext* ic, gpointer) {
  intptr_t id = (intptr_t)gum_invocation_context_get_listener_function_data(ic);
  if (id == HK_BFDTOR) {
    // Traktor destroys its browser (quitting) BEFORE NSApplicationWillTerminate: a band update
    // queued on the main thread then ran on the freed controls (2026-11-26, a crash in
    // Control::setPosition from set_band_status after TK's "list"). From here libtk touches
    // nothing of Traktor's UI again.
    void* bf = gum_invocation_context_get_nth_argument(ic, 0);
    if (bf == g_browserform && bf) {
      g_quitting = true;
      g_browserform = nullptr;
      tklog("QUIT: Traktor's browser is being destroyed: libtk stops touching its controls");
    }
    return;
  }
  if (id == HK_GRIDCTOR || id == HK_GRIDRESIZE) {
    // AdvancedGridForm(this, IAppBase*, deck) / resize(this, Point const&): remember the form.
    void* form = gum_invocation_context_get_nth_argument(ic, 0);
    if (id == HK_GRIDCTOR)
      grid_form_built(form, (unsigned)(uintptr_t)gum_invocation_context_get_nth_argument(ic, 2));
    else
      grid_form_seen(form);
    return;
  }
  if (id == HK_TREESEL) {
    // treeSelect(BrowserForm* this, std::string const& path, EBrowserCategory)
    // arg0 is the live BrowserForm*; stash it for the selection commands (#3).
    void* bf = gum_invocation_context_get_nth_argument(ic, 0);
    if (!g_quitting && looks_like_object(bf)) g_browserform = bf;
    void* path_arg = gum_invocation_context_get_nth_argument(ic, 1);
    long  category = (long)gum_invocation_context_get_nth_argument(ic, 2);
    std::string path = decode_std_string(path_arg);
    bool matched = !path.empty() && ci_contains(path, g_match);
    tklog("treeSelect path=%s cat=0x%lx match=%d", path.c_str(), category, (int)matched);
    // Who turns a left-tree click into treeSelect? Log the caller chain (image-base addresses,
    // map them with out_all/_ALL.txt on the farm) for the first few selections.
    if (g_treesel_bt < 4) {
      g_treesel_bt++;
      static GumBacktracer* bt = nullptr;
      if (!bt) bt = gum_backtracer_make_accurate();
      if (bt) {
        GumReturnAddressArray ra; memset(&ra, 0, sizeof(ra));
        gum_backtracer_generate(bt, ic->cpu_context, &ra);
        for (guint i = 0; i < ra.len && i < 16; i++) {
          uintptr_t a = (uintptr_t)ra.items[i];
          tklog("  treeSelect bt[%u] 0x%lx", i, (unsigned long)(a - (uintptr_t)g_slide));
        }
      }
    }
    // First browser form seen: draw the button band once it has settled (TK_BAND=0: off).
    if (!g_band_done && g_browserform && !(getenv("TK_BAND") && strcmp(getenv("TK_BAND"), "0") == 0)) {
      g_band_done = true;
      dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1500 * NSEC_PER_MSEC)),
                     dispatch_get_main_queue(), ^{ do_button_strip(0); });
    }
    if (matched) {
      tklog("  match '%s' -> opening Track Kommander", g_match);
      fire_show();
    }
  } else if (id == HK_VALUEFORTRACK) {
    // DataSetModel::valueForTrack(this, TableRole, SmartPtr<ContentEntryTrack>*, ...)
    // x2 is a pointer to a SmartPtr; the SmartPtr holds the raw track at offset 0.
    void* smartptr = gum_invocation_context_get_nth_argument(ic, 2);
    if (smartptr && gum_memory_is_readable(smartptr, 8)) {
      const void* track = *(const void* const*)smartptr;
      read_and_post_track(track, "browse", false);
    }
  } else if (id == HK_LOADDECK) {
    // MixerDeck::loadDeck(this, MixerDeckChannel*, Controllable*,
    //                     SmartPtr<ContentEntryTrack const>&, bool play)
    // x3 is a pointer to the SmartPtr; *(void**)x3 = raw track (= MediaItem*).
    void* mixer   = gum_invocation_context_get_nth_argument(ic, 0);  // this
    void* channel = gum_invocation_context_get_nth_argument(ic, 1);
    void* smartptr = gum_invocation_context_get_nth_argument(ic, 3);
    if (smartptr && gum_memory_is_readable(smartptr, 8)) {
      const void* track = *(const void* const*)smartptr;
      int deck = deck_index_of_channel(mixer, channel);  // which deck is loading
      tklog("loadDeck fired (deck=%d)", deck);
      read_and_post_track(track, "deckLoad", true, deck);
    }
  } else if (id == HK_ROWCOUNT) {
    // Capture the live DataSetModel instance (its `this`) so the native-UI
    // injection can find the list VIEW by matching QAbstractItemView::model().
    void* dsm = gum_invocation_context_get_nth_argument(ic, 0);
    if (looks_like_object(dsm)) {
      g_dsm = dsm;
      bool known = false;
      for (int i = 0; i < g_ndsms; i++) if (g_dsms[i] == dsm) known = true;
      if (!known && g_ndsms < 8) { g_dsms[g_ndsms++] = dsm; tklog("DIAG new DataSetModel[%d] %p", g_ndsms - 1, dsm); }
    }
    if (!g_rowcount_seen) { g_rowcount_seen = 1; tklog("DIAG rowCount() fired dsm=%p", dsm); }
  } else if (id == HK_DATAQT) {
    if (!g_dataqt_seen) { g_dataqt_seen = 1; tklog("DIAG data(QModelIndex) fired"); }
  } else if (id == HK_POLISH) {
    // QQuickRenderControl::sync()/render(this) — runs each frame on the GUI thread
    // in the process that paints (Traktor renders QML offscreen via a render
    // control). The live QQuickWindow is window = *(*(this+8)+0x88).
    void* rc = gum_invocation_context_get_nth_argument(ic, 0);  // QQuickRenderControl*
    void* win = nullptr;
    if (rc && gum_memory_is_readable((const char*)rc + OFF_QQRC_DPTR, 8)) {
      void* d = *(void* const*)((const char*)rc + OFF_QQRC_DPTR);
      if (d && gum_memory_is_readable((const char*)d + OFF_QQRCP_WINDOW, 8)) {
        win = *(void* const*)((const char*)d + OFF_QQRCP_WINDOW);
        if (looks_like_object(win)) g_qq_window = win;
      }
    }
    if (!g_qq_seen) {
      g_qq_seen = 1;
      tklog("DIAG renderctrl fired pid=%d rc=%p window=%p", getpid(), rc, g_qq_window);
    }
  } else if (id == HK_SCENE) {
    // QQuickWindowPrivate::syncSceneGraph()/renderSceneGraph(this) — the big
    // per-frame workers (not inlined). this = QQuickWindowPrivate; the QQuickWindow
    // is q_ptr at QObjectData+0x8.
    void* wp = gum_invocation_context_get_nth_argument(ic, 0);  // QQuickWindowPrivate*
    void* win = nullptr;
    if (wp && gum_memory_is_readable((const char*)wp + 8, 8)) {
      win = *(void* const*)((const char*)wp + 8);
      if (looks_like_object(win)) g_qq_window = win;
    }
    if (!g_qq_seen) {
      g_qq_seen = 1;
      tklog("DIAG sceneGraph fired pid=%d wp=%p window=%p", getpid(), wp, g_qq_window);
    }
  } else if (id == HK_ACTIVATE) {
    // QMetaObject::activate fires on nearly every Qt signal. If this fires, gum DOES
    // hook Qt here. The sender (x0) is a live QObject; when it's a QQuick item/window
    // we get the scene window from it (bypassing the empty window lists).
    void* sender = gum_invocation_context_get_nth_argument(ic, 0);
    if (!g_qq_window && looks_like_object(sender)) {
      const char* cn = qobject_classname(sender);
      if (cn && strstr(cn, "Quick")) {
        if (strstr(cn, "Window")) {
          g_qq_window = sender;
        } else {  // a QQuickItem subclass — ask it for its window
          void* w = ((void* (*)(const void*))R(A_qquickitem_window))(sender);
          if (looks_like_object(w)) g_qq_window = w;
        }
        if (g_qq_window && !g_qq_seen) {
          g_qq_seen = 1;
          tklog("DIAG window via activate pid=%d sender=%s %p -> window=%p", getpid(), cn, sender, g_qq_window);
        }
      }
    }
    // Walk the sender's QObject parent chain for a QQuickWindow/QQuickItem ancestor
    // (QObjectData: d_ptr@+8, parent@+0x10). This leverages the one hook that fires.
    if (!g_qq_window && looks_like_object(sender)) {
      void* o = sender;
      for (int depth = 0; depth < 40 && looks_like_object(o); depth++) {
        const char* cn = qobject_classname(o);
        if (cn && strstr(cn, "Quick") && (strstr(cn, "Window") || strstr(cn, "Item") ||
                                          strstr(cn, "Rectangle") || strstr(cn, "Root"))) {
          void* w = strstr(cn, "Window") ? o
                   : ((void* (*)(const void*))R(A_qquickitem_window))(o);
          if (looks_like_object(w)) {
            g_qq_window = w;
            tklog("DIAG window via parent-chain pid=%d from=%s depth=%d anc=%s %p -> window=%p",
                  getpid(), qobject_classname(sender), depth, cn, o, w);
            break;
          }
        }
        // parent = *(*(o + 8) + 0x10)
        if (!gum_memory_is_readable((const char*)o + 8, 8)) break;
        void* d = *(void* const*)((const char*)o + 8);
        if (!d || !gum_memory_is_readable((const char*)d + 0x10, 8)) break;
        o = *(void* const*)((const char*)d + 0x10);
      }
    }
    if (g_activate_seen < 40) {
      g_activate_seen++;
      tklog("DIAG activate[%d] pid=%d sender=%s %p", g_activate_seen, getpid(),
            qobject_classname(sender), sender);
    }
  } else if (id == HK_ITEM) {
    // QQuickItem::update()/polish(this) — per-frame for animated items (waveform,
    // meters). this = a live QQuickItem; its window gives the scene root.
    void* item = gum_invocation_context_get_nth_argument(ic, 0);
    if (!g_qq_window && looks_like_object(item)) {
      void* w = ((void* (*)(const void*))R(A_qquickitem_window))(item);
      if (looks_like_object(w)) {
        g_qq_window = w;
        if (!g_qq_seen) {
          g_qq_seen = 1;
          tklog("DIAG window via item pid=%d item=%s %p -> window=%s %p", getpid(),
                qobject_classname(item), item, qobject_classname(w), w);
        }
      }
    }
  } else if (id == HK_QBROWSER) {
    // QBrowser ctor/update — this = the browser-list QQuickItem. Just stash it; at
    // ctor time it has no window yet, so window() is resolved later (scene dump).
    void* br = gum_invocation_context_get_nth_argument(ic, 0);
    if (looks_like_object(br)) {
      bool first = (g_qbrowser == nullptr);
      g_qbrowser = br;
      if (first) tklog("DIAG QBrowser captured pid=%d item=%s %p", getpid(),
                       qobject_classname(br), br);
    }
  } else if (id == HK_FONTID) {
    // LabelControl::setFontID(this, uint id) — capture a real font id Traktor uses.
    int fid = (int)(long)gum_invocation_context_get_nth_argument(ic, 1);
    if (g_fontid < 0 && fid > 0) g_fontid = fid;
    if (g_fontid_seen < 60) { g_fontid_seen++; tklog("DIAG setFontID[%d] id=%d", g_fontid_seen, fid); }
  } else if (id == HK_RELOAD) {
    // onReloadForm(this) runs before any selectTreeNode at startup: capture the tree here so
    // on_leave can re-apply our node and the removals on the very first build.
    void* t = gum_invocation_context_get_nth_argument(ic, 0);
    if (looks_like_object(t)) g_stdtree = t;
  } else if (id == HK_PICID || id == HK_SWINIT) {
    // Picture ids Traktor's own UI uses: LabelControl::setPictureID(id) / SwitchControl::onInit
    // (its id at +0xc0). Logged once each, to find icons for the band buttons.
    char* self = (char*)gum_invocation_context_get_nth_argument(ic, 0);
    unsigned pid = id == HK_PICID ? (unsigned)(uintptr_t)gum_invocation_context_get_nth_argument(ic, 1)
                 : (gum_memory_is_readable(self + 0xc0, 4) ? *(unsigned*)(self + 0xc0) : 0);
    static unsigned seen[512]; static int nseen = 0;
    bool known = pid == 0;
    for (int i = 0; i < nseen && !known; i++) known = seen[i] == pid;
    if (!known && nseen < 512) { seen[nseen++] = pid; tklog("PICID %s %u", id == HK_PICID ? "label" : "switch", pid); }
  } else if (id == HK_STDTREE) {
    // BrowserStdTree::selectTreeNode(this x0, ...) — the main window's left tree.
    g_tree_depth++;
    void* t = gum_invocation_context_get_nth_argument(ic, 0);
    if (t != g_stdtree && looks_like_object(t)) { g_stdtree = t; tklog("TREE: BrowserStdTree %p", t); }
  } else if (id == HK_CTLEVENT) {
    // NI::NGL::Control::onEvent(this x0, uint evt x1, EventData* x2) — every control without its
    // own onEvent passes here (hot path: bail out unless `this` is one of our band buttons).
    void* self = gum_invocation_context_get_nth_argument(ic, 0);
    if (grid_ctl_event(self, (unsigned)(uintptr_t)gum_invocation_context_get_nth_argument(ic, 1),
                       (const char*)gum_invocation_context_get_nth_argument(ic, 2)))
      return;  // a deck's ALIGN button
    if (nl_ctl_event(self, (unsigned)(uintptr_t)gum_invocation_context_get_nth_argument(ic, 1),
                     (const char*)gum_invocation_context_get_nth_argument(ic, 2)))
      return;  // the native page's tree / rows / buttons
    if (g_nbtn == 0 && g_nacards == 0) return;  // (tiles are matched below with the buttons)
    if (g_cards_expanded) {
      for (int i = 0; i < g_nacards; i++) {
        int part = -1;
        for (int k = 0; k < CP_COUNT; k++) if (g_acards[i].part[k] == self) part = k;
        if (part < 0) continue;
        unsigned evt = (unsigned)(uintptr_t)gum_invocation_context_get_nth_argument(ic, 1);
        const char* d = (const char*)gum_invocation_context_get_nth_argument(ic, 2);
        if (d && gum_memory_is_readable(d, OFF_EVT_STATE + 4)) card_event(i, part, evt, d);
        return;
      }
    }    int b = -1;
    for (int i = 0; i < g_nbtn; i++) if (g_btn_ctrl[i] == self) { b = i; break; }
    if (b < 0)  // a click on an icon's tile is a click on that button
      for (int i = 0; i < g_nbtn; i++) if (g_btn_tile[i] && g_btn_tile[i] == self) { b = i; break; }
    if (b < 0 && self == g_auto_lbl)  // the mode's name acts as the AUTO button
      for (int i = 0; i < g_nbtn; i++) if (strcmp(g_btn_name[i], "AUTO") == 0) { b = i; break; }
    if (b < 0) return;
    press_track();
    unsigned evt = (unsigned)(uintptr_t)gum_invocation_context_get_nth_argument(ic, 1);
    const char* data = (const char*)gum_invocation_context_get_nth_argument(ic, 2);
    if (!data || !gum_memory_is_readable(data, OFF_EVT_STATE + 4)) return;
    int button = *(const int*)(data + OFF_EVT_BUTTON);
    int state  = *(const int*)(data + OFF_EVT_STATE);
    if (g_btn_evt_seen < 40) {
      g_btn_evt_seen++;
      tklog("BTN evt %s: evt=%u x=%d y=%d button=%d state=%d", g_btn_name[b], evt,
            *(const int*)data, *(const int*)(data + 4), button, state);
    }
    // One press = one action. A button is an icon label over its tile and Traktor offers the press
    // to every control under the mouse: both answered, so a toggle (SETTINGS, MATCHING …) opened
    // and closed again on one click — "acts only on the second click" (User, 2026-11-24).
    // Traktor also repeats the press while it is held (measured: 20+ per click), so: act once per
    // physical press — the flag clears on any event that arrives with the mouse button up.
    if (evt == NGL_EVT_MOUSE && button == 1 && state == 1) {
      if (!press_edge()) return;
      tklog("BTN press %s (app %s)", g_btn_name[b], [NSApp isActive] ? "active" : "inactive");
    }
    if (evt == NGL_EVT_MOUSE && button == 1 && state == 1 && strcmp(g_btn_name[b], "MATCHING") == 0) {
      g_cards_expanded = !g_cards_expanded;  // the band's own business: no round trip to TK
      cards_relayout();
      return;
    }
    if (evt == NGL_EVT_MOUSE && button == 1 && state == 1) {
      char body[64];
      snprintf(body, sizeof(body), "{\"action\":\"%s\"}", g_btn_name[b]);
      tklog("BTN click %s -> POST /uiAction", g_btn_name[b]);
      post_json("/uiAction", body);
    }
  } else if (id == HK_SETENTRY) {
    audio_setentry_seen(gum_invocation_context_get_nth_argument(ic, 0),
                        gum_invocation_context_get_nth_argument(ic, 1),
                        gum_invocation_context_get_nth_argument(ic, 2));
  } else if (id == HK_AUDIOSETUP) {
    g_in_audio_setup++;
  } else if (id == HK_CCMANUAL) {
    g_in_manual++;
  } else if (id == HK_CCENABLE) {
    g_in_enable++;
  } else if (id == HK_NSASELECT) {
    audio_select_seen(gum_invocation_context_get_nth_argument(ic, 0),
                      gum_invocation_context_get_nth_argument(ic, 1),
                      (int)(uintptr_t)gum_invocation_context_get_nth_argument(ic, 2));
  } else if (id == HK_FORMMOUSE) {
    form_mouse(gum_invocation_context_get_nth_argument(ic, 0),
               (unsigned)(uintptr_t)gum_invocation_context_get_nth_argument(ic, 1),
               (const char*)gum_invocation_context_get_nth_argument(ic, 2));
  } else if (id == HK_CARDBG) {
    // BrowserForm::onDrawBackground(this x0, Picture* x1, Region* x2) — stash Picture* for on_leave.
    void* self = gum_invocation_context_get_nth_argument(ic, 0);
    g_card_pic = (self == g_browserform) ? gum_invocation_context_get_nth_argument(ic, 1) : nullptr;
  } else if (id == HK_MAINVIEW) {
    // MainWindowNGLView::createBrowserForm(this=view x0, PanelFormManager* x1).
    // Capture the view + panel manager — the handles to add our own NI::NGL::Form.
    void* view = gum_invocation_context_get_nth_argument(ic, 0);
    void* mgr  = gum_invocation_context_get_nth_argument(ic, 1);
    if (view && gum_memory_is_readable(view, 8)) g_mainview = view;
    if (mgr && gum_memory_is_readable(mgr, 8))   g_panelmgr = mgr;
    tklog("DIAG mainview pid=%d view=%p panelmgr=%p", getpid(), g_mainview, g_panelmgr);
  } else if (id == HK_OSVIEW) {
    // QOffscreenView::onWindowChanged(this=QOffscreenView x0, QQuickWindow* x1).
    // The offscreen scene's window is handed in directly. Traktor has several.
    void* view = gum_invocation_context_get_nth_argument(ic, 0);
    void* win  = gum_invocation_context_get_nth_argument(ic, 1);
    if (looks_like_object(win)) {
      if (!g_qq_window) g_qq_window = win;
      bool known = false;
      for (int i = 0; i < g_qq_window_count; i++) if (g_qq_windows[i] == win) known = true;
      if (!known && g_qq_window_count < 12) {
        g_qq_windows[g_qq_window_count++] = win;
        tklog("DIAG osview window[%d] pid=%d view=%p window=%s %p", g_qq_window_count - 1,
              getpid(), view, qobject_classname(win), win);
      }
    }
  }
}

// === Native-call helpers (shared by the env one-shot AND the command channel).
// Goal 3/4: load a real track into a deck / read its metadata via Traktor's own
// C++, fully headless. Every step logs, so a crash/partial still says how far
// the call chain got. ALL of these MUST run on the MAIN THREAD — Traktor's C++
// is not thread-safe; the command listener dispatches them to the main queue. ==

// Find Traktor's embedded MixerDeck and its channel count. Returns the mixer
// pointer (validated by its channel vector) or null; *nchan_out gets the count.
// Candidates: IAppBase::getInstance() + its vtable[1] (the data() indirection),
// each at +0x1f0 (getMixer thunk) and +0x350 (primary this); validated by the
// channel vector (begin@+0x178, end@+0x180, 0x10/elem) before use. The
// vtable-dispatching getMixerDeckChannel crashes, so fields are read instead.
static void* find_mixer(long* nchan_out) {
  typedef void* (*fn_v0)(void);
  void* base = ((fn_v0)R(A_appGetInstance))();
  tklog("LOAD: IAppBase::getInstance()=%p", base);
  if (!looks_like_object(base)) { tklog("LOAD: abort (no base)"); return nullptr; }

  void* app2 = nullptr;
  void* vtbl = *(void**)base;
  if (gum_memory_is_readable(vtbl, 16)) {
    typedef void* (*fn_vcall)(void*);
    app2 = ((fn_vcall)(*(void**)((char*)vtbl + 8)))(base);
    tklog("LOAD: app via vtable[1]=%p", app2);
  }
  void* mixer_candidates[4] = {
    (char*)base + 0x1f0, (char*)base + 0x350,
    app2 ? (char*)app2 + 0x1f0 : nullptr, app2 ? (char*)app2 + 0x350 : nullptr,
  };
  for (int c = 0; c < 4; c++) {
    void* m = mixer_candidates[c];
    if (!m || !gum_memory_is_readable(m, 0x188)) continue;
    void* arr = *(void**)((char*)m + 0x178);
    void* end = *(void**)((char*)m + 0x180);
    if (!arr || !end || end < arr) continue;
    long count = ((char*)end - (char*)arr) / 0x10;
    if (count < 1 || count > 16) continue;                 // channel vector sanity
    if (!gum_memory_is_readable(arr, count * 0x10)) continue;
    void* ch0 = *(void**)arr;
    if (!looks_like_object(ch0)) continue;                 // first channel is a real object
    tklog("LOAD: MixerDeck=%p (candidate %d) channels=%ld", m, c, count);
    if (nchan_out) *nchan_out = count;
    return m;
  }
  tklog("LOAD: abort (no valid MixerDeck on any candidate)");
  return nullptr;
}

// One-shot x8-sret trampoline self-test (logged once). getTrack(mgr,&key,sret)
// short-circuits (writes *x8=0) when the key is 0, before touching mgr — a safe
// Same, for a callee with three arguments (e.g. createPlaylist(this, StructurePath const&, bool)).
static void call_sret3(void* fn, void* a0, void* a1, long a2, void* sret) {
  register long x0 asm("x0") = (long)a0;
  register long x1 asm("x1") = (long)a1;
  register long x2 asm("x2") = a2;
  register long x8 asm("x8") = (long)sret;
  asm volatile("blr %[fn]"
      : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x8)
      : [fn] "r"(fn)
      : "x3","x4","x5","x6","x7","x9","x10","x11","x12","x13","x14","x15",
        "x16","x17","x18","lr","cc","memory");
}
// check that call_sret2 actually sets x8.
static void trampoline_selftest_once(void) {
  static bool done = false;
  if (done) return;
  done = true;
  struct { uint32_t k; uint32_t p; } k0 = { 0, 0 };
  void* buf = (void*)0xdeadbeef;
  call_sret2(R(A_ccmGetTrack), (void*)0x1, &k0, &buf);
  tklog("LOAD: trampoline self-test buf=%p (0=trampoline OK, 0xdeadbeef=x8 not set)", buf);
}

// helpers::getMainCollection() does the singleton guard and calls
// CollectionsManager::getMainCollection correctly; it ignores its `this` and
// returns a SmartPtr<ContentCollectionMgr> via x8 (sret). Returns the raw
// ContentCollectionMgr* (SmartPtr offset 0) or null.
static void* get_collection_mgr(void) {
  void* mgr_sp = nullptr;  // SmartPtr buffer: raw ContentCollectionMgr* at offset 0
  call_sret2(R(A_helpersGetMainCollection), nullptr, nullptr, &mgr_sp);
  tklog("LOAD: mgr=%p", mgr_sp);
  if (!looks_like_object(mgr_sp)) { tklog("LOAD: abort (no mgr)"); return nullptr; }
  return mgr_sp;
}

// getTrack(mgr, &TypedEntryKey{key}) -> raw ContentEntryTrack* via x8 (sret), or
// null for a missing key / a non-track. One key.
static void* get_track_by_key(void* mgr, uint32_t key) {
  if (!mgr) return nullptr;
  struct { uint32_t key; uint32_t pad; } tk = { key, 0 };
  void* sp = nullptr;  // SmartPtr buffer: one raw pointer at offset 0
  call_sret2(R(A_ccmGetTrack), mgr, &tk, &sp);
  return (sp && looks_like_object(sp)) ? sp : nullptr;
}

// Probe keys 1..maxkey for the first real track (for callers with no key in
// hand, e.g. the env one-shot). *foundkey gets the hit; null if none.
static void* find_first_track(void* mgr, unsigned maxkey, unsigned* foundkey) {
  for (unsigned k = 1; k <= maxkey; k++) {
    void* t = get_track_by_key(mgr, k);
    if (t) { if (foundkey) *foundkey = k; return t; }
  }
  return nullptr;
}

// Load an already-resolved track into deck deckIdx via Traktor's own
// loadMixerDeck(this, int deck, SmartPtr<track>&, bool play): it looks up the
// channel and passes a null Controllable itself, and addRefs the SmartPtr, so
// &raw_ptr works as the arg. deckIdx is validated against the live channel count.
static void load_track(void* mixer, long nchan, int deckIdx, void* track) {
  if (!mixer || !track) { tklog("LOAD: abort (no mixer/track to load)"); return; }
  if (deckIdx < 0 || (long)deckIdx >= nchan) {
    tklog("LOAD: abort (deck %d out of range 0..%ld)", deckIdx, nchan - 1);
    return;
  }
  typedef void (*fn_loadmix)(void* mixer, int deck, void* sptr, bool play);
  void* rawsp = track;
  tklog("LOAD: calling loadMixerDeck(mixer=%p deck=%d &track)", mixer, deckIdx);
  ((fn_loadmix)R(A_loadMixerDeck))(mixer, deckIdx, &rawsp, true);
  tklog("LOAD: loadMixerDeck returned (no crash) — deck %d should now hold the track", deckIdx);
}

// Load a track into a deck. key==0 -> probe for the first track in the
// collection (what the env one-shot does); otherwise load exactly that key.
// MAIN THREAD ONLY. Order preserved from the original do_test_load: mixer first
// (so the deck index is validated before the collection is touched), then track.
static void do_native_load(int deckIdx, uint32_t key) {
  long nchan = 0;
  void* mixer = find_mixer(&nchan);
  if (!mixer) return;
  if (deckIdx < 0 || (long)deckIdx >= nchan) {
    tklog("LOAD: abort (deck %d out of range 0..%ld)", deckIdx, nchan - 1);
    return;
  }
  trampoline_selftest_once();
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  unsigned foundkey = key;
  void* track = key ? get_track_by_key(mgr, key) : find_first_track(mgr, 400, &foundkey);
  tklog("LOAD: track=%p key=%u", track, foundkey);
  if (!track) { tklog("LOAD: abort (no track for key %u)", key); return; }
  read_and_post_track(track, "load", true, deckIdx);  // prove it's a real track + feed TK
  load_track(mixer, nchan, deckIdx, track);
}

// Read a track's metadata and POST /nativeTrack. key==0 -> first track.
// MAIN THREAD ONLY.
static void do_native_read(uint32_t key) {
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  unsigned foundkey = key;
  void* track = key ? get_track_by_key(mgr, key) : find_first_track(mgr, 400, &foundkey);
  tklog("READ: track=%p key=%u", track, foundkey);
  if (!track) { tklog("READ: abort (no track for key %u)", key); return; }
  read_and_post_track(track, "read", true);
}

// === #3: Traktor's CURRENT SELECTION ========================================
// Resolve whatever track the user has highlighted in Traktor's own browser to a
// known-good MediaItem*, or null when there is no selection / it is not a track.
// MAIN THREAD ONLY.
//
// 2026-11-22 FIX: the first cut cast the selected Entity to ContentEntryTrack and
// read MediaItem offsets off it -> empty title/artist (the browser entry is NOT a
// MediaItem at base 0; getByKey proves the real cast is Entity->MediaItem). The
// robust route, confirmed by the binary: the selected ContentEntryTrack carries
// its collection key as a uint at entry+0xc, so read that and reuse the PROVEN
// get_track_by_key(mgr,key), which returns a real MediaItem. The dynamic_cast is
// kept only to locate the ContentEntryTrack subobject the key lives on.
static void* get_selected_track(void* mgr) {
  void* bf = g_browserform;
  if (!looks_like_object(bf)) { tklog("SEL: no BrowserForm seen yet (select a tree row once)"); return nullptr; }
  void* sp = nullptr;  // SmartPtr<Entity const> buffer: raw Entity* at offset 0
  call_sret2(R(A_bf_getSelectedEntry), bf, nullptr, &sp);
  if (!sp || !looks_like_object(sp)) { tklog("SEL: nothing selected"); return nullptr; }
  void* cet = __dynamic_cast(sp, R(A_ti_entity), R(A_ti_contententrytrack), 0);
  if (!cet || !looks_like_object(cet)) { tklog("SEL: selection is not a track"); return nullptr; }
  uint32_t key = 0;
  const void* kp = (const char*)cet + OFF_ENTRY_KEY;
  if (gum_memory_is_readable(kp, 4)) key = *(const uint32_t*)kp;
  tklog("SEL: entry=%p cet=%p key=%u", sp, cet, key);
  if (!mgr || key == 0) { tklog("SEL: no collection key on selection"); return nullptr; }
  void* track = get_track_by_key(mgr, key);
  if (!track) { tklog("SEL: getTrack(key=%u) returned null", key); return nullptr; }
  tklog("SEL: resolved track=%p via key=%u", track, key);
  return track;
}

// Read the selected track's metadata and POST /nativeTrack. MAIN THREAD ONLY.
static void do_native_read_sel(void) {
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  void* track = get_selected_track(mgr);
  if (!track) { tklog("READSEL: abort (no selected track)"); return; }
  read_and_post_track(track, "readsel", true);
}

// Load the selected track into deckIdx. MAIN THREAD ONLY. Mixer first (so the
// deck index is validated before the browser is touched), as do_native_load does.
static void do_native_load_sel(int deckIdx) {
  long nchan = 0;
  void* mixer = find_mixer(&nchan);
  if (!mixer) return;
  if (deckIdx < 0 || (long)deckIdx >= nchan) {
    tklog("LOADSEL: abort (deck %d out of range 0..%ld)", deckIdx, nchan - 1);
    return;
  }
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  void* track = get_selected_track(mgr);
  if (!track) { tklog("LOADSEL: abort (no selected track)"); return; }
  read_and_post_track(track, "loadsel", true, deckIdx);
  load_track(mixer, nchan, deckIdx, track);
}

// === #2: BY FILE PATH =======================================================
// Resolve a file-system path to the collection track that lives at it, via
// Traktor's own key registry: build a FILE URL from the path, ask
// EntryKeyManager for that URL's existing collection key, then reuse the proven
// getTrack(mgr,key) path. Returns null when the path is not in the collection
// (key 0) or the key registry is not ready. MAIN THREAD ONLY.
//
// NEEDS LIVE VERIFICATION: the path must match Traktor's stored canonical form.
// EntryKeyManager keys URLs on "file:" + pathToString(getFileSystemPath()), so
// the <path> Track Kommander sends has to be the same absolute POSIX path (same
// case, no trailing slash, no symlink/alias indirection) that Traktor imported.
static bool g_quiet_paths = false;  // do_list_set resolves hundreds of paths: no per-path log
// The collection key of the track at <path>, or 0 (not in the collection / registry not ready).
static uint32_t key_of_path(const char* path) {
  if (!path || !*path) return 0;
  // A native libc++ std::string. URL::URL(path const&) copies it as the URL's
  // stored path (same system libc++ ABI), so no std::string/boost layout is
  // hand-rolled — Traktor's own constructor does the copy.
  std::string pstr(path);

  // URL object buffer. The constructor writes a std::string@0, a kind tag (=1,
  // FILE) at +0x18 and a media-source u32 at +0x20 — well within URL_OBJ_SIZE.
  alignas(16) unsigned char urlbuf[URL_OBJ_SIZE];
  memset(urlbuf, 0, sizeof(urlbuf));
  typedef void* (*fn_url_ctor)(void* self, const void* pathlike);
  ((fn_url_ctor)R(A_url_ctor_from_path))(urlbuf, &pstr);

  uint32_t key = 0;
  // 2026-11-22 FIX: the EntryKeyManager instance is *(void**)A_ekm_instance (a
  // CollectionsManager member, confirmed via ContentEntryTrack::getURL). The first
  // cut gated on a bogus guard byte at A_ekm_instance_guard that was 0 even though
  // the instance was live, so it always skipped. Gate on the instance pointer
  // itself being a real object instead.
  void* ekm = gum_memory_is_readable((const void*)R(A_ekm_instance), 8)
                ? *(void* const*)R(A_ekm_instance) : nullptr;
  // EntryKeyManager is NOT polymorphic (no vtable at offset 0), so looks_like_object
  // wrongly rejects it; getKeyFromURL reads its map at this+0xc0..0xc8, so require
  // that range readable instead.
  if (!ekm || !gum_memory_is_readable(ekm, 0xd0)) {
    tklog("PATH: EntryKeyManager instance not ready (ptr=%p)", ekm);
  } else {
    // getKeyFromURL(this, URL const&) -> TypedEntryKey<uint> (the uint) via x8.
    call_sret2(R(A_ekm_getKeyFromURL), ekm, urlbuf, &key);
  }

  // Free the URL's copied std::string (its destructor dispatches on the kind
  // tag); pstr frees itself at scope end.
  ((void (*)(void*))R(A_url_dtor))(urlbuf);
  if (!g_quiet_paths) tklog("PATH: '%s' -> key=%u", path, key);
  return key;
}

static void* get_track_by_path(void* mgr, const char* path) {
  if (!mgr || !path || !*path) { tklog("PATH: abort (no mgr/path)"); return nullptr; }
  const uint32_t key = key_of_path(path);
  if (key == 0) return nullptr;
  return get_track_by_key(mgr, key);
}

// Read the metadata of the collection track at <path> and POST /nativeTrack.
static void do_native_read_path(const char* path) {
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  void* track = get_track_by_path(mgr, path);
  if (!track) { tklog("READPATH: abort (no collection track for '%s')", path); return; }
  read_and_post_track(track, "readpath", true);
}

// Load the collection track at <path> into deckIdx. Mixer validated first.
static void do_native_load_path(int deckIdx, const char* path) {
  long nchan = 0;
  void* mixer = find_mixer(&nchan);
  if (!mixer) return;
  if (deckIdx < 0 || (long)deckIdx >= nchan) {
    tklog("LOADPATH: abort (deck %d out of range 0..%ld)", deckIdx, nchan - 1);
    return;
  }
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  void* track = get_track_by_path(mgr, path);
  if (!track) { tklog("LOADPATH: abort (no collection track for '%s')", path); return; }
  read_and_post_track(track, "loadpath", true, deckIdx);
  load_track(mixer, nchan, deckIdx, track);
}

// Env one-shot (TK_TEST_LOAD): behaviour unchanged — probe for the first track
// in the collection and load it into deckIdx.
static void do_test_load(int deckIdx) { do_native_load(deckIdx, 0); }

// === Embedded UI: NATIVE cards bar inside Traktor (Qt 6.5.3 widget injection) ==
// Build a QWidget+QHBoxLayout+QLabel bar in Traktor's OWN Qt and insert it into
// the layout holding the browser's list VIEW, so it renders *inside* Traktor
// (not a floating PySide overlay). MAIN THREAD ONLY (Qt is not thread-safe off
// the GUI thread). DRAFT, UNTESTED LIVE: it walks + logs Traktor's widget tree
// (so we can SEE the structure on the first run) and only attempts an insert
// when the host layout looks like a box layout. Offsets/addrs: addrs_4_5_1_21.h.
// Qt widgets are OVER-ALLOCATED via operator new (the ctor writes within the real
// size; operator delete frees by the allocator's bookkeeping) — no exact sizeof.
static void* g_tk_bar = nullptr;   // our injected bar once built (idempotence)
static int   g_nui_logged = 0;     // bounds the tree-walk log

// A widget's QLayout* (null if none). layout() == *(d_ptr + 0x98).
static void* qwidget_layout(const void* w) {
  if (!gum_memory_is_readable((const char*)w + OFF_QWIDGET_DPTR, 8)) return nullptr;
  const void* d = *(const void* const*)((const char*)w + OFF_QWIDGET_DPTR);
  if (!d || !gum_memory_is_readable((const char*)d + OFF_QWPRIV_LAYOUT, 8)) return nullptr;
  return *(void* const*)((const char*)d + OFF_QWPRIV_LAYOUT);
}

// A QObject's REAL class name, or "?". metaObject() is virtual (QObject vtable
// slot 0), so dispatch through the object's vtable — the base QObject::metaObject
// symbol would return QObject's metaobject for every subclass. For a QWidget the
// QObject vptr is at offset 0 (QObject is the primary base), so vtable[0] is its
// metaObject(). (Slot 0 to VERIFY on the first live run from the logged names.)
static const char* qobject_classname(const void* obj) {
  if (!looks_like_object(obj)) return "?";
  const void* const* vt = *(const void* const* const*)obj;
  if (!vt || !gum_memory_is_readable(vt, 8)) return "?";
  const void* meta = ((const void* (*)(const void*))vt[0])(obj);
  if (!looks_like_object(meta)) return "?";
  const char* n = ((const char* (*)(const void*))R(A_qmeta_className))(meta);
  return (n && gum_memory_is_readable(n, 1)) ? n : "?";
}

// Build the cards bar (QWidget + QHBoxLayout + a placeholder QLabel) parented to
// `host`. Over-allocates each widget. Returns the bar QWidget*, or null.
static void* build_cards_bar(void* host) {
  void* (*opnew)(unsigned long) = (void* (*)(unsigned long))R(A_op_new);
  void* bar = opnew(0x400);
  if (!bar) return nullptr;
  memset(bar, 0, 0x400);
  ((void (*)(void*, void*, int))R(A_qwidget_ctor))(bar, host, 0);
  void* lay = opnew(0x400);
  memset(lay, 0, 0x400);
  ((void (*)(void*, void*))R(A_qhboxlayout_ctor))(lay, bar);  // installs itself as bar's layout
  unsigned char qstr[24];
  memset(qstr, 0, sizeof(qstr));
  const char* txt = "Track Kommander — matching";
  call_sret2(R(A_qstring_fromUtf8), (void*)txt, (void*)(long)strlen(txt), qstr);
  void* lbl = opnew(0x400);
  memset(lbl, 0, 0x400);
  ((void (*)(void*, void*, void*, int))R(A_qlabel_ctor_text))(lbl, qstr, bar, 0);
  ((void (*)(void*, void*, int, int))R(A_qboxlayout_addW))(lay, lbl, 0, 0);
  return bar;
}

// Recursively walk a widget subtree, logging each node, recording the first node
// whose QAbstractItemView::model() == g_dsm (the browser list view). Bounded.
static void walk_widgets(void* w, int depth, void** found_view) {
  if (depth > 14 || !looks_like_object(w) || g_nui_logged > 600) return;
  const char* cn = qobject_classname(w);
  g_nui_logged++;
  tklog("  UI%*s%s %p", depth * 2 + 1, "", cn, w);
  // Only probe model() on something that looks like a view, so we never call it
  // on a non-view (wrong vtable offset).
  if (g_dsm && !*found_view &&
      (strstr(cn, "View") || strstr(cn, "Table") || strstr(cn, "Tree") || strstr(cn, "List"))) {
    void* m = ((void* (*)(const void*))R(A_qav_model))(w);
    if (m == g_dsm) { *found_view = w; tklog("  UI>>> LIST VIEW: %s %p (model matches)", cn, w); }
  }
  if (!gum_memory_is_readable((const char*)w + OFF_QWIDGET_DPTR, 8)) return;
  const char* d = *(const char* const*)((const char*)w + OFF_QWIDGET_DPTR);
  const char* cl = d + OFF_QOBJDATA_CHILDREN;  // QList<QObject*> children
  if (!gum_memory_is_readable(cl, 24)) return;
  void** cptr = *(void** const*)(cl + 8);
  long csize = *(const long*)(cl + 0x10);
  if (!cptr || csize < 0 || csize > 4096 || !gum_memory_is_readable(cptr, csize * 8)) return;
  for (long i = 0; i < csize; i++) walk_widgets(cptr[i], depth + 1, found_view);
}

// MAIN THREAD. Enumerate Traktor's top-level widgets, log the tree, find the
// browser list view, and (guarded) insert a native cards bar above it.
static void do_native_ui_inject(void) {
  if (g_tk_bar) { tklog("NUI: bar already injected (%p)", g_tk_bar); return; }
  g_nui_logged = 0;
  tklog("NUI: dsm=%p — enumerating top-level widgets", g_dsm);
  unsigned char ql[24];
  memset(ql, 0, sizeof(ql));
  // QApplication::topLevelWidgets() is static: ignores `this`, returns QList via x8.
  call_sret2(R(A_qapp_topLevelWidgets), nullptr, nullptr, ql);
  void** tptr = *(void** const*)(ql + 8);
  long tsize = *(const long*)(ql + 0x10);
  tklog("NUI: %ld top-level widget(s)", tsize);
  if (!tptr || tsize < 0 || tsize > 256 || !gum_memory_is_readable(tptr, tsize * 8)) {
    tklog("NUI: top-level list unreadable");
    return;
  }
  void* view = nullptr;
  for (long i = 0; i < tsize && !view; i++) {
    tklog("NUI top-level[%ld]:", i);
    walk_widgets(tptr[i], 0, &view);
  }
  if (!view) {
    tklog("NUI: list view not found (browse a track list once so rowCount fires, then retry)");
    return;
  }
  // The view's parent widget (QObject parent at d_ptr+0x10) hosts the layout we
  // insert into, so the bar sits beside/above the list.
  const char* vd = *(const char* const*)((const char*)view + OFF_QWIDGET_DPTR);
  void* host = gum_memory_is_readable(vd + 0x10, 8) ? *(void* const*)(vd + 0x10) : nullptr;
  if (!looks_like_object(host)) {
    tklog("NUI: view has no parent widget");
    return;
  }
  void* hostlay = qwidget_layout(host);
  const char* layc = hostlay ? qobject_classname(hostlay) : "(none)";
  tklog("NUI: host=%s %p  hostLayout=%s %p", qobject_classname(host), host, layc, hostlay);
  // Insert only when the host layout is a box layout; insertWidget on a grid /
  // custom layout would misbehave. Otherwise the tree is logged for tuning.
  if (hostlay && strstr(layc, "BoxLayout")) {
    void* bar = build_cards_bar(host);
    if (bar) {
      ((void (*)(void*, int, void*, int, int))R(A_qboxlayout_insertW))(hostlay, 0, bar, 0, 0);
      ((void (*)(void*))R(A_qwidget_show))(bar);
      g_tk_bar = bar;
      tklog("NUI: inserted native cards bar %p into %s", bar, layc);
    }
  } else {
    tklog("NUI: host layout '%s' is not a box layout — tree logged; tune insertion point", layc);
  }
}

// === Native cards bar via Traktor's OWN toolkit (NI::NGL::Form) ================
// Build an NI::NGL::Form with a LabelControl and add it through the panel manager,
// exactly how Traktor builds its header/browser forms. Uses the handles captured
// from MainWindowNGLView::createBrowserForm (g_mainview, g_panelmgr). MAIN THREAD.
// FIRST ATTEMPT — recipe from decompiles (Form ctor/addControl, LabelControl, addForm,
// form size@0x28/0x2c, type@0xc, Rect=4 ints). May need live tuning.
static void* g_ngl_form = nullptr;

static int g_ngl_row = 0;  // stack test labels vertically

// Add one test label with a given font id to the live BrowserForm, so we can try many
// font ids in one session (nglbar <fontid>). Each call stacks a new row.
static void do_ngl_label(int fontid) {
  if (!g_browserform || !gum_memory_is_readable(g_browserform, 8)) { tklog("NGL: no browserform"); return; }
  void* label = ((void* (*)(void))R(A_ngllabel_createControl))();
  if (!label) { tklog("NGL: label alloc failed"); return; }
  int y = 2 + g_ngl_row * 22;
  int rect[4] = { 8, y, 640, y + 20 };  // {l,t,r,b}
  // 2026-11-22 (fork B root cause): addControl runs the control's onInit, which loads the
  // font from the fontID ALREADY stored on the control. So fontID + text must be set BEFORE
  // addControl, or onInit caches a null font and only a colored block paints (the old bug).
  char txt[64]; snprintf(txt, sizeof(txt), "KOMMANDER font %d  A B C D  SETTINGS LIB AUTO", fontid);
  if (fontid > 0) ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(label, (unsigned)fontid);
  ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(label, txt);
  ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(label, 0xffffffffu);  // white
  ((void (*)(void*, void*))R(A_nglctl_setPosition))(label, rect);
  ((void (*)(void*, bool))R(A_nglctl_setVisible))(label, true);
  ((void (*)(void*, void*))R(A_nglform_addControl))(g_browserform, label);  // onInit loads the font HERE
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
  void* lfh = gum_memory_is_readable((char*)label + 0xf0, 8) ? *(void**)((char*)label + 0xf0) : (void*)-1;
  tklog("NGL: label row=%d font=%d fonthandle=%p", g_ngl_row, fontid, lfh);
  g_ngl_row++;
}

static void do_ngl_bar(void) {
  do_ngl_label(g_fontid > 0 ? g_fontid : 36);
}

// === Button strip (2026-11-22, fork B): A/B/C/D + action buttons across the TOP of the track
// list, as LabelControl text buttons. fontID is set BEFORE addControl so onInit loads the font
// (the paint fix). Milestone-1 = render-only; click wiring (onEvent) deferred. MAIN THREAD. ===
// Band button look. LabelControl::setColor is the label's FILL (+0xd4), not its text colour —
// white made the old buttons white boxes with faint grey text. Alignments are tunable live
// (`btnstyle`): eAlignment values not mapped yet.
static unsigned g_btn_bg = 0xff3a3a3au;
static const unsigned kBtnSelectedBg = 0xff2b8cf5u;  // Traktor blue: TK's source deck
static const unsigned kBtnBusyBg = 0xffe8891cu;      // orange: something in progress (Auto DJ transition)
// Icon pictures' frames (Traktor's toggle pictures, measured 2026-11-24): 0 = grey (off), 2 = dark
// (on a lit tile it reads like the A-D buttons' black text), 4 = white (hover), 6 = dim.
// NGL cannot tint a picture (PanelItem::draw copies it), so "lit" is each picture's darkest frame:
// the note (322) has Traktor's own lit look in frame 1 (black note on blue); the headphones (435)
// a dark frame 2; the others only a dimmed frame 6.
static const unsigned kIconFrameOff = 0;

// TK's own icons, drawn by TK (Qt) as silhouettes: grey when off, black when lit, as the A-D text.
// Traktor cannot tint a picture, and has no black list/gear/grid icons. Keyed by button name, and
// "AUTO:<mode>" for the AUTO button (one per mode).
struct TKIcon { char key[24]; void* pic[2]; };  // [0] off, [1] lit
static TKIcon g_icons[24];
static int g_nicons = 0;
static char g_auto_mode[16] = "play_cue";

static void* make_picture(int w, int h, const uint32_t* argb) {
  if (w <= 0 || h <= 0 || w > 512 || h > 512 || !argb) return nullptr;
  void* p = operator new(SZ_PICTURE);
  memset(p, 0, SZ_PICTURE);
  ((void (*)(void*))R(A_pic_ctor))(p);
  ((void (*)(void*, int, int, bool))R(A_pic_setSize))(p, w, h, true);
  void* const* vt = *(void* const* const*)p;
  ((void (*)(void*, int))vt[0x10 / 8])(p, 1);
  ((void (*)(void*, unsigned))R(A_pic_clear))(p, 0);
  uint32_t* px = *(uint32_t**)((char*)p + OFF_PIC_PIXELS);
  if (!px || *(int*)((char*)p + OFF_PIC_W) != w || *(int*)((char*)p + OFF_PIC_H) != h) {
    tklog("ICON: picture %dx%d not as asked (px=%p)", w, h, px);
    return nullptr;  // leaked on purpose: never seen; better than freeing a half-made object
  }
  memcpy(px, argb, (size_t)w * (size_t)h * 4);
  return p;
}

static const TKIcon* icon_for(const char* button) {
  char key[24];
  if (!strcmp(button, "AUTO")) snprintf(key, sizeof(key), "AUTO:%s", g_auto_mode);
  else strlcpy(key, button, sizeof(key));
  for (int i = 0; i < g_nicons; i++) if (!strcmp(g_icons[i].key, key)) return &g_icons[i];
  return nullptr;
}

static void band_place_buttons(void);
static void* g_pic_loader = nullptr;  // a hidden label: loads Traktor pictures by id for recolouring
static void* card_label(const char* text);
extern "C" __attribute__((visibility("default"))) int tk_band_icon(const char* key, int lit, int w, int h, const uint32_t* argb);

// Traktor's picture <id>, frame <frame>, as a silhouette in <argb> (its alpha, our colour): how a
// Traktor icon gets the band's grey/black. Stored like tk_band_icon. Main thread; 0 = stored.
extern "C" __attribute__((visibility("default"))) int tk_band_icon_from_picture(const char* key, int lit, unsigned id, unsigned frame, uint32_t argb) {
  if (!key || !pthread_main_np() || !g_browserform) return -1;
  if (!g_pic_loader) {
    g_pic_loader = card_label("");
    ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_pic_loader, false);
  }
  ((void (*)(void*, unsigned))R(A_ngllabel_setPictureID))(g_pic_loader, id);
  const char* src = *(const char* const*)((char*)g_pic_loader + OFF_LBL_PICTURE);
  if (!src || !gum_memory_is_readable(src, 0x48)) { tklog("ICON: no picture %u", id); return -1; }
  const int W = *(const int*)(src + OFF_PIC_W), H = *(const int*)(src + OFF_PIC_H);
  const int aw = ((int (*)(const void*))R(A_pic_animWidth))(src);
  int ah = ((int (*)(const void*))R(A_pic_animHeight))(src);
  if (ah <= 0 || ah > H) ah = H;
  const uint32_t* px = *(const uint32_t* const*)(src + OFF_PIC_PIXELS);
  // A picture's frames sit side by side or stacked: the gear (514) is 36x189, its frames one
  // above the other (measured 2026-11-25). Taking the whole height drew several gears, too tall
  // for the band to centre, from the button's corner (User: "setting icon not centered").
  const bool across = W > aw;
  const int fx = across ? (int)frame * aw : 0, fy = across ? 0 : (int)frame * ah;
  if (!px || aw <= 0 || H <= 0 || W < aw || fx + aw > W || fy + ah > H) { tklog("ICON: picture %u frame %u unusable", id, frame); return -1; }
  // One frame, cropped to its ink as TK's own icons are (ui/native_icons.py): the band centres a
  // picture by its size.
  int x0 = aw, y0 = ah, x1 = -1, y1 = -1;
  for (int y = 0; y < ah; y++)
    for (int x = 0; x < aw; x++)
      if (px[(size_t)(fy + y) * W + fx + x] >> 24) {
        x0 = std::min(x0, x); x1 = std::max(x1, x); y0 = std::min(y0, y); y1 = std::max(y1, y);
      }
  if (x1 < 0) { tklog("ICON: picture %u frame %u is empty", id, frame); return -1; }
  const int cw = x1 - x0 + 1, ch = y1 - y0 + 1;
  std::vector<uint32_t> out((size_t)cw * (size_t)ch);
  for (int y = 0; y < ch; y++)
    for (int x = 0; x < cw; x++)
      out[(size_t)y * cw + x] =
          (px[(size_t)(fy + y0 + y) * W + fx + x0 + x] & 0xff000000u) | (argb & 0x00ffffffu);
  tklog("ICON: picture %u frame %u: %dx%d of %dx%d, ink %d,%d %dx%d", id, frame, aw, ah, W, H, x0, y0, cw, ch);
  return tk_band_icon(key, lit, cw, ch, out.data());
}

// key, lit 0/1, w, h, ARGB pixels (w*h). Main thread. 0 = stored.
extern "C" __attribute__((visibility("default"))) int tk_band_icon(const char* key, int lit, int w, int h, const uint32_t* argb) {
  if (!key || !*key || !pthread_main_np()) return -1;
  void* p = make_picture(w, h, argb);
  if (!p) return -1;
  TKIcon* slot = nullptr;
  for (int i = 0; i < g_nicons; i++) if (!strcmp(g_icons[i].key, key)) slot = &g_icons[i];
  if (!slot && g_nicons < (int)(sizeof(g_icons) / sizeof(g_icons[0]))) {
    slot = &g_icons[g_nicons++];
    memset(slot, 0, sizeof(*slot));
    strlcpy(slot->key, key, sizeof(slot->key));
  }
  if (!slot) return -1;
  slot->pic[lit ? 1 : 0] = p;  // the previous one stays alive: a label may still show it
  band_place_buttons();
  if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
  return 0;
}
static unsigned icon_lit_frame(unsigned pic) {
  switch (pic) {
    case 322: return 1;
    case 435: return 2;
    case 103: return 0;  // MATCHING: three frames, none darker
    default: return 6;
  }
}
static char g_deck_sel = 0;  // 'A'..'D' from TK's `deck` command, 0 = none
// AUTO button: TK's automation mode (`auto <mode> <busy>`): its text and fill.
static char     g_auto_text[16] = "Off";  // the mode's name, shown right of the AUTO icon
static unsigned g_auto_bg = 0;  // 0 = the normal button fill
static void* g_auto_strip = nullptr;  // thin bar under the AUTO icon: its mode colour
// The AUTO icon follows TK's mode like TK's own button (⊘ / 🎧 / ▶ / 🪄), with Traktor pictures:
// OFF 760, headphones 435, play 133, record (Auto DJ) 680.
static unsigned g_auto_pic = 435;
static int      g_btn_halign = 1, g_btn_valign = 1;

static void style_button(void* c, unsigned bg, int halign, int valign) {
  ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(c, bg);
  ((void (*)(void*, int))R(A_ngllabel_setHorzAlignment))(c, halign);
  ((void (*)(void*, int))R(A_ngltextitem_setVertAlignment))((char*)c + 0xd8, valign);
}

// Traktor picture ids for icon buttons (found with picgrid, 2026-11-23): its own gear, note,
// headphones and grid. 0 = keep the text.
// X1..X9: the band's spare buttons, an add-on's pages (TK gives their icons and tooltips).
static bool is_spare_button(const char* name) {
  return name[0] == 'X' && name[1] >= '1' && name[1] <= '9' && name[2] == 0;
}

static unsigned band_icon(const char* name) {
  if (!strcmp(name, "LIST")) return 513;      // Traktor's list picture: back to the playlist
  if (!strcmp(name, "SETTINGS")) return 514;
  if (!strcmp(name, "LIB")) return 322;  // plain note (239 and 407 are toggle-button pictures: always boxed)
  if (!strcmp(name, "AUTO")) return g_auto_pic;
  if (!strcmp(name, "STATS")) return 470;
  if (!strcmp(name, "MATCHING")) return 103;  // mixer faders: TK's sliders and cards
  // TK's pages, one button each (User, 2026-11-24): icon buttons; TK draws their icons.
  if (!strncmp(name, "S_", 2) || !strncmp(name, "L_", 2) || is_spare_button(name)) return 514;
  return 0;
}

static void* card_label(const char* text);
static void place(void* l, int l_, int t, int r, int b, unsigned fill, bool show);
static void band_place_buttons(void);

// Picture labels draw no fill, so the AUTO icon shows its mode in a 3 px bar just under it.
static void place_auto_strip(void) {
  band_place_buttons();  // the AUTO tile carries the mode's colour now
  if (g_auto_strip) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_auto_strip, false);
  for (int i = 0; i < 0 && g_auto_strip; i++) {
    if (strcmp(g_btn_name[i], "AUTO") != 0) continue;
    const int* r = (const int*)((const char*)g_btn_ctrl[i] + OFF_CTRL_RECT_L);
    unsigned c = g_auto_bg ? (g_auto_bg == 0xff8a5a1au ? 0xffd88a2au : 0xff7fbf3au) : 0xff3a3a3au;
    place(g_auto_strip, r[0] - 2, r[3] + 3, r[2] + 2, r[3] + 6, c, g_band_visible);
  }
}

static void apply_auto_state(void) {
  place_auto_strip();
  for (int i = 0; i < g_nbtn; i++) {
    if (strcmp(g_btn_name[i], "AUTO") != 0) continue;
    if (band_icon("AUTO")) {  // the icon shows the mode, its name sits to the right
      ((void (*)(void*, unsigned))R(A_ngllabel_setPictureID))(g_btn_ctrl[i], g_auto_pic);
      // The tile behind is the button's background; the icon label itself stays transparent
      // (a filled label sized to the picture showed as a box behind it).
      ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(g_btn_ctrl[i], 0x00000000u);
      if (g_auto_lbl) ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(g_auto_lbl, g_auto_text);
    }
    else {
      ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(g_btn_ctrl[i], g_auto_text);
      ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(g_btn_ctrl[i], g_auto_bg ? g_auto_bg : g_btn_bg);
    }
  }
  band_place_buttons();  // each mode's picture has its own size: centre it again on the tile
  if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

// A–D show TK's source deck: that button blue, the others the normal fill.
static void apply_deck_sel(void) {
  for (int i = 0; i < g_nbtn; i++) {
    const char* n = g_btn_name[i];
    if (n[1] != 0 || n[0] < 'A' || n[0] > 'D') continue;
    const bool lit = n[0] == g_deck_sel;
    ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(g_btn_ctrl[i], lit ? kBtnSelectedBg : g_btn_bg);
    const int font = lit ? g_font_on : g_font_off;
    if (font > 0) ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(g_btn_ctrl[i], (unsigned)font);
  }
  for (void* caption : {g_source_lbl, g_auto_lbl})
    if (caption && g_font_caption > 0) ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(caption, (unsigned)g_font_caption);
  if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

static int do_pyexec(const std::string& path);  // PyRun_SimpleString's result; -1 = no Python

static void do_button_strip(int fontid) {
  if (!g_browserform || !gum_memory_is_readable(g_browserform, 8)) { tklog("BTN: no browserform"); return; }
  // Carve a 44px band at the top of the track-list body (child @0x518) so the labels have a
  // visible, empty strip to live in (loose controls at y~2 were behind the column header before).
  int band_top = 55, band_h = 44, band_left = 235;
  void* list = *(void**)((char*)g_browserform + 0x518);
  if (list && gum_memory_is_readable(list, OFF_CTRL_RECT_L + 16)) {
    int* lr = (int*)((char*)list + OFF_CTRL_RECT_L);
    band_top = lr[1]; band_left = lr[0] + 4;
    if (!g_list_orig_set) { memcpy(g_list_orig, lr, sizeof(g_list_orig)); g_list_orig_set = true; }
    g_band_top = band_top; g_band_left = band_left; g_band_right = lr[2] - 4; g_band_btn_h = band_h;
    int nr[4] = { lr[0], lr[1] + band_h, lr[2], lr[3] };  // push list down by band_h
    g_list_pushed_top = nr[1]; g_list_pushed_left = nr[0];
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(list, nr);
  }
  if (fontid <= 0) fontid = (g_fontid > 0 ? g_fontid : 36);
  struct { const char* t; int w; } btns[] = {
    // MATCHING and AUTO are one group: the matching cards and the automation (User, 2026-11-24).
    {"A",40},{"B",40},{"C",40},{"D",40},{"MATCHING",40},{"AUTO",40},{"LIST",40},
    // With LIST, one selector (one lit at a time), in groups (User, 2026-11-24): the library pages
    // with Stats last | spare buttons an add-on may take (TK sets their icon and tooltip, and
    // hides the ones nobody took: ui/band_extensions.py) | Settings, then About last.
    {"L_SCAN",40},{"L_HOTCUES",40},{"L_TAGGING",40},{"L_PLAYLISTS",40},{"L_EXPORT",40},{"STATS",40},
    {"X1",40},{"X2",40},{"X3",40},{"X4",40},
    {"S_SETTINGS",40},{"S_ABOUT",40},
  };
  int x = band_left, y = band_top + 8, h = 28, gap = 6;
  // "SOURCE:" before A–D (just text: no fill, not clickable), and a wider gap after D.
  {
    void* src = ((void* (*)(void))R(A_ngllabel_createControl))();
    if (src) {
      int r[4] = { x, y, x + kSourceW, y + h };
      ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(src, (unsigned)fontid);
      ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(src, "MATCHING SOURCE:");
      style_button(src, 0x00000000u, 2, g_btn_valign);  // transparent, right-aligned
      ((void (*)(void*, void*))R(A_nglctl_setPosition))(src, r);
      ((void (*)(void*, bool))R(A_nglctl_setVisible))(src, true);
      ((void (*)(void*, void*))R(A_nglform_addControl))(g_browserform, src);
      g_source_lbl = src;
    }
    x += kSourceW + gap;
  }
  for (int i = 0; i < (int)(sizeof(btns)/sizeof(btns[0])); i++) {
    // Traktor's buttons always sit on a tile, on or off: an icon button gets one, added first so
    // it draws under the icon.
    void* tile = band_icon(btns[i].t) ? card_label("") : nullptr;
    void* c = ((void* (*)(void))R(A_ngllabel_createControl))();
    if (!c) continue;
    int rect[4] = { x, y, x + btns[i].w, y + h };  // {l,t,r,b}, inside the carved band
    // (No per-frame background box any more: the label's own fill is the button's background.
    // Those boxes kept their first positions, and after the band was rearranged one showed as a
    // grey box behind the note icon.)
    x += btns[i].w + gap + (strcmp(btns[i].t, "MATCHING") == 0 ? kAfterSourceGap : 0);
    ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(c, (unsigned)fontid);   // BEFORE addControl
    // The name is also the action a click sends; AUTO only DISPLAYS TK's mode.
    const char* shown = strcmp(btns[i].t, "AUTO") == 0 ? g_auto_text : btns[i].t;
    ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(c, shown);
    style_button(c, band_icon(btns[i].t) ? 0x00000000u : g_btn_bg, g_btn_halign, g_btn_valign);
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(c, rect);
    ((void (*)(void*, bool))R(A_nglctl_setVisible))(c, true);
    ((void (*)(void*, void*))R(A_nglform_addControl))(g_browserform, c);
    unsigned icon = band_icon(btns[i].t);
    if (icon) {  // Traktor's own picture instead of text (draw mode 2 shows a label's picture)
      ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(c, "");
      ((void (*)(void*, int))R(A_ngllabel_setDrawMode))(c, 2);
      ((void (*)(void*, unsigned))R(A_ngllabel_setPictureID))(c, icon);
    }
    if (g_nbtn < (int)(sizeof(g_btn_ctrl) / sizeof(g_btn_ctrl[0]))) {
      g_btn_tile[g_nbtn] = tile;
      g_btn_w[g_nbtn] = btns[i].w;
      g_btn_name[g_nbtn] = btns[i].t;   // string literal: lives forever
      // The cards open from the SCORE column's header now (User, 2026-11-24, tk_columns.mm):
      // MATCHING stays built (its code, its tooltip) but is left out of the band.
      // The spare slots start hidden: TK shows those an add-on takes (User, 2026-11-25: no add-on,
      // no add-on icons, even before TK answers).
      g_btn_hidden[g_nbtn] = strcmp(btns[i].t, "MATCHING") == 0 || is_spare_button(btns[i].t);
      g_btn_ctrl[g_nbtn++] = c;         // publish the pointer last
    }
  }
  if (!g_auto_lbl) {
    g_auto_lbl = card_label(g_auto_text);
    style_button(g_auto_lbl, 0x00000000u, 0, g_btn_valign);  // plain text, left-aligned
  }
  band_place_buttons();
  if (!g_auto_strip) g_auto_strip = card_label("");
  if (!g_match_strip) g_match_strip = card_label("");
  place_auto_strip();
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
  apply_deck_sel();
  apply_auto_state();
  band_set_visible(g_band_visible);  // hidden until TK's view is selected
  // Integrated mode: TK_EMBED names TK's boot script; run it once the browser is up, so one launch
  // starts Traktor AND Track Kommander inside it (re/launch_integrated.sh).
  static bool embedded_started = false;
  const char* embed = getenv("TK_EMBED");
  if (!embedded_started && embed && *embed) {
    embedded_started = true;
    // Open on TK's view: select our node as a click would, once Traktor has restored its own last
    // selection (that is what drew the band, so it is done by now).
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 500 * (int64_t)NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
      if (!g_stdtree || !g_tk_node) { tklog("EMBED: no tree node to select"); return; }
      std::string our_path;  // our node's tree path is "" (unknown category, no prefix)
      ((void (*)(void*, const void*, bool, bool, bool))R(A_bst_selectTreeNode))(g_stdtree, &our_path, true, false, false);
      tklog("EMBED: Track Kommander selected at startup");
    });
    std::string path(embed);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 1 * (int64_t)NSEC_PER_SEC), dispatch_get_main_queue(), ^{
      if (access(path.c_str(), R_OK) != 0) {
        const char* repo = getenv("TK_REPO");
        g_tk_problem = std::string("Track Kommander not found at ") + (repo && *repo ? repo : path.c_str());
        tklog("EMBED: %s (no %s)", g_tk_problem.c_str(), path.c_str());
        set_band_status(g_tk_problem.c_str());
        return;
      }
      tklog("EMBED: starting Track Kommander inside Traktor (%s)", path.c_str());
      if (do_pyexec(path) != 0) {
        g_tk_problem = "Track Kommander failed to start - see /tmp/tk_dylib.log";
        set_band_status(g_tk_problem.c_str());
      }
    });
  }
  post_json("/uiAction", "{\"action\":\"BAND\"}");  // TK answers with its source deck
  tklog("BTN: strip in band (font=%d, top=%d left=%d, %d buttons)", fontid, band_top, band_left,
        (int)(sizeof(btns)/sizeof(btns[0])));
}

// Register + draw one custom CARD in the band: a filled bg + blue border + a value bar, with a
// "KOMMANDER" title LabelControl on top. Drawn per-frame by the HK_CARDBG hook. MAIN THREAD.
static void do_card(int x, int y, int w, int h) {
  if (!g_browserform || !gum_memory_is_readable(g_browserform, 8)) { tklog("CARD: no browserform"); return; }
  if (g_ncards >= (int)(sizeof(g_cards)/sizeof(g_cards[0]))) { tklog("CARD: registry full"); return; }
  TKCard* c = &g_cards[g_ncards++];
  c->x = x; c->y = y; c->w = w; c->h = h;
  c->bg = 0xff2b2b2bu; c->border = 0xff2b8cf5u; c->barcol = 0xff2b8cf5u; c->barw = (int)(w * 0.6); c->used = true;
  int fid = (g_fontid > 0 ? g_fontid : 36);
  void* lbl = ((void* (*)(void))R(A_ngllabel_createControl))();
  if (lbl) {
    int r[4] = { x + 6, y + 4, x + w - 6, y + 20 };
    ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(lbl, (unsigned)fid);
    ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(lbl, "KOMMANDER");
    ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(lbl, 0xffffffffu);
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(lbl, r);
    ((void (*)(void*, bool))R(A_nglctl_setVisible))(lbl, true);
    ((void (*)(void*, void*))R(A_nglform_addControl))(g_browserform, lbl);
  }
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
  tklog("CARD: #%d {x=%d y=%d w=%d h=%d}", g_ncards - 1, x, y, w, h);
}

// === Left-tree root nodes (2026-11-23). DataSetModel keeps its root list as a
// QList<FirstLevelNode> {d@+0x18, ptr@+0x20, size@+0x28}; a node is 0x38 bytes:
// u32 category@0, u32 icon@4, QString name@8 {d, char16_t* ptr, size}, libc++ std::string tag@0x20
// ("$COLLECTION"). treeprobe logs every list we have seen, to find the one the main window shows.
static std::string qstring_ascii(const char* qs) {
  if (!gum_memory_is_readable(qs, 24)) return "<unreadable>";
  const uint16_t* p = *(const uint16_t* const*)(qs + 8);
  long n = *(const long*)(qs + 16);
  if (!p || n <= 0) return "";
  if (n > 128 || !gum_memory_is_readable(p, (gsize)n * 2)) return "<bad>";
  std::string out;
  for (long i = 0; i < n; i++) out += (p[i] < 0x80) ? (char)p[i] : '?';
  return out;
}

static void do_tree_probe(void) {
  tklog("TREE: %d DataSetModel(s) seen", g_ndsms);
  for (int m = 0; m < g_ndsms; m++) {
    const char* dsm = (const char*)g_dsms[m];
    if (!gum_memory_is_readable(dsm, 0x30)) { tklog("TREE: dsm[%d] %p unreadable", m, dsm); continue; }
    const char* arr = *(const char* const*)(dsm + 0x20);
    long n = *(const long*)(dsm + 0x28);
    tklog("TREE: dsm[%d] %p roots=%ld", m, dsm, n);
    if (n < 0 || n > 32 || !arr || !gum_memory_is_readable(arr, (gsize)n * 0x38)) continue;
    for (long i = 0; i < n; i++) {
      const char* node = arr + i * 0x38;
      tklog("TREE:   [%ld] cat=0x%x icon=0x%x name='%s' tag='%s'", i, *(const unsigned*)node,
            *(const unsigned*)(node + 4), qstring_ascii(node + 8).c_str(),
            decode_std_string(node + 0x20).c_str());
    }
  }
}

// === Left tree = BrowserStdTree's NGL TreeControl. Root node = tree+0x5a0; a Node has its
// label std::string@0, TreeNodeInfo*@0x30, children vector@0x48/0x50. MAIN THREAD ONLY. ===
static void* stdtree_ctl(void) {
  if (!g_stdtree || !gum_memory_is_readable((char*)g_stdtree + OFF_BST_TREECTL, 8)) return nullptr;
  void* t = *(void**)((char*)g_stdtree + OFF_BST_TREECTL);
  return (t && gum_memory_is_readable((char*)t + OFF_TREECTL_ROOT, OFF_NODE_KIDS_BEGIN + 16)) ? t : nullptr;
}

// The root's children as a bounded array view; n = -1 when unreadable.
static void* const* root_kids(void* tree, long* n) {
  const char* root = (const char*)tree + OFF_TREECTL_ROOT;
  void* const* b = *(void* const* const*)(root + OFF_NODE_KIDS_BEGIN);
  void* const* e = *(void* const* const*)(root + OFF_NODE_KIDS_BEGIN + 8);
  *n = (b && e >= b) ? (long)(e - b) : -1;
  if (*n > 64 || (*n > 0 && !gum_memory_is_readable(b, (gsize)*n * 8))) *n = -1;
  return b;
}

static int node_cat(const void* node) {
  const char* info = gum_memory_is_readable((const char*)node + OFF_NODE_INFO, 8)
                         ? *(const char* const*)((const char*)node + OFF_NODE_INFO) : nullptr;
  return (info && gum_memory_is_readable(info + OFF_TNI_CAT, 4)) ? *(const int*)(info + OFF_TNI_CAT) : -1;
}

static void tree_redraw(void* tree) {
  ((void (*)(void*, unsigned))R(A_ngltree_invalidate))(tree, 0);
  if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

static void do_tree_list(void) {
  void* tree = stdtree_ctl();
  if (!tree) { tklog("TREE: no BrowserStdTree yet (click any node in the left tree)"); return; }
  long n; void* const* kids = root_kids(tree, &n);
  tklog("TREE: tree=%p roots=%ld (ours=%p)", tree, n, g_tk_node);
  for (long i = 0; i < n; i++) {
    const char* node = (const char*)kids[i];
    if (!gum_memory_is_readable(node, 0x70)) { tklog("TREE:   [%ld] %p unreadable", i, node); continue; }
    const char* info = *(const char* const*)(node + OFF_NODE_INFO);
    std::string path = (info && gum_memory_is_readable(info, SZ_TNI)) ? decode_std_string(info + OFF_TNI_PATH) : "?";
    tklog("TREE:   [%ld] node=%p cat=0x%x label='%s' path='%s'", i, node, node_cat(node),
          decode_std_string(node).c_str(), path.c_str());
  }
}

// Add our root at the end of the tree via Traktor's own createTreeNode (it registers the path,
// creates the NGL node and links info<->node). Returns false if Traktor refused (e.g. a duplicate).
static bool do_tree_add(void) {
  void* tree = stdtree_ctl();
  if (!tree) { tklog("TREE: add: no BrowserStdTree yet"); return false; }
  if (g_tk_node) { tklog("TREE: add: already there (%p)", g_tk_node); return true; }
  alignas(16) char info[SZ_TNI + 0x40];
  memset(info, 0, sizeof(info));
  std::string name(g_tk_node_label);
  // Icon 2 = Track Collection's; icon2 -1 = same. createTreeNode deep-copies the info, so this
  // stack copy's strings are simply left behind (a few bytes, once per add).
  ((void (*)(void*, unsigned, const void*, int, int, bool))R(A_tni_ctor))(info, TK_TREE_CAT, &name, g_tree_icon, -1, false);
  void* root = (char*)tree + OFF_TREECTL_ROOT;
  void* node = ((void* (*)(void*, void*, void*, bool, void*))R(A_bst_createTreeNode))(g_stdtree, root, nullptr, false, info);
  // First in the tree. createTreeNode's position argument inserts AFTER a node (tried: Track
  // Collection stayed first), so move our entry to the front of the root's children vector
  // instead; the tree lays rows out when it draws.
  long nkids; void* const* kids = root_kids(tree, &nkids);
  for (long i = 1; node && i < nkids; i++) {
    if (kids[i] != node) continue;
    void** v = (void**)kids;
    memmove(v + 1, v, (size_t)i * sizeof(void*));
    v[0] = node;
    break;
  }
  g_tk_node = node;
  tree_redraw(tree);
  tklog("TREE: add '%s' cat=0x%x -> node=%p", g_tk_node_label, TK_TREE_CAT, node);
  return node != nullptr;
}

// Forget a node in Traktor's own bookkeeping before deleting it: its TreeNodeInfo keeps a Node*
// (+0x50; selectTreeNode returns early on null) and an external collection's map entry keeps one
// (+0x60; createFirstLevelNode null-checks it). Walk that std::map in order (libc++ rb-tree).
static void forget_node(const void* node) {
  char* info = *(char**)((const char*)node + OFF_NODE_INFO);
  if (info && gum_memory_is_readable(info + OFF_TNI_NODE, 8)) *(void**)(info + OFF_TNI_NODE) = nullptr;
  char* end = (char*)g_stdtree + OFF_BST_EXTCOLL_BEGIN + 8;
  char* it = *(char**)((char*)g_stdtree + OFF_BST_EXTCOLL_BEGIN);
  for (int guard = 0; it && it != end && guard < 64; guard++) {
    if (!gum_memory_is_readable(it, OFF_EXTCOLL_NODE + 8)) break;
    if (*(const void**)(it + OFF_EXTCOLL_NODE) == node) {
      *(void**)(it + OFF_EXTCOLL_NODE) = nullptr;
      tklog("TREE:   cleared external-collection entry key=0x%x", *(const int*)(it + 0x20));
    }
    char* right = *(char**)(it + 8);
    if (right) { it = right; while (*(char**)it) it = *(char**)it; continue; }
    char* parent = *(char**)(it + 0x10);
    while (parent != end && *(char**)(parent + 8) == it) { it = parent; parent = *(char**)(it + 0x10); }
    it = parent;
  }
}

// Delete every root node of one category (never ours). Returns how many went.
static int do_tree_del(unsigned cat) {
  void* tree = stdtree_ctl();
  if (!tree || cat == TK_TREE_CAT) return 0;
  int gone = 0;
  for (int pass = 0; pass < 16; pass++) {  // deleteNode shifts the vector: rescan after each
    long n; void* const* kids = root_kids(tree, &n);
    void* hit = nullptr;
    for (long i = 0; i < n && !hit; i++) if (node_cat(kids[i]) == (int)cat) hit = kids[i];
    if (!hit) break;
    tklog("TREE: delete cat=0x%x label='%s' node=%p", cat, decode_std_string(hit).c_str(), hit);
    forget_node(hit);
    ((void (*)(void*, void*))R(A_ngltree_deleteNode))(tree, hit);
    gone++;
  }
  if (gone) tree_redraw(tree);
  return gone;
}

static std::vector<std::string> tree_path_keys(void);
static std::string printable_path(const std::string& p);

static std::string g_pl_treepath;  // tree path of the Results playlist, once known
static bool structure_path(const std::string& treepath, void* out24);

// Our node's icon, changed in place: TreeNodeInfo (node+0x30) holds icon @0x58, second icon @0x5c
// and their next frames @0x60/@0x64 (TreeNodeInfo ctor). Deleting and re-adding the node instead
// does NOT work: the second createTreeNode returned null and the node was gone (2026-11-24).
static void set_tree_icon(int icon) {
  void* tree = stdtree_ctl();
  char* info = g_tk_node ? *(char**)((char*)g_tk_node + OFF_NODE_INFO) : nullptr;
  if (!tree || !info || !gum_memory_is_readable(info, 0x68)) { tklog("TREE: no node for an icon"); return; }
  int* f = (int*)(info + 0x58);
  f[0] = icon; f[1] = icon; f[2] = icon + 1; f[3] = icon + 1;
  tree_redraw(tree);
  tklog("TREE: icon %d", icon);
}

static const char kPlFolder[] = "Kommander";
static const char kPlLegacyFolder[] = "Track Kommander";

// Once: the playlist folder TK made as "Track Kommander" (date smartlists, Results) is now just
// "Kommander" (User, 2026-11-24). Rename it in Traktor's library the way the tree's own rename
// does, unless a "Kommander" folder already exists.
static void migrate_pl_folder(void) {
  static bool done = false;
  if (done || !g_browserform) return;
  const std::vector<std::string> keys = tree_path_keys();
  if (keys.empty()) return;  // the tree's path map fills in after startup: try again later
  done = true;
  std::string legacy;
  for (const std::string& k : keys) {
    size_t cut = k.rfind('\a');
    if (cut == std::string::npos || k.rfind("$PLAYLIST", 0) != 0) continue;
    const std::string last = k.substr(cut + 1);
    if (last == kPlFolder && k.find('\a') == cut) { tklog("PLAYLIST: '%s' folder already there", kPlFolder); return; }
    if (last == kPlLegacyFolder && k.find('\a') == cut) legacy = k;
  }
  if (legacy.empty()) return;
  alignas(16) char sp[32];
  if (!structure_path(legacy, sp)) { tklog("PLAYLIST: no structure path for the old folder"); return; }
  const std::string to(kPlFolder);
  const long ok = ((long (*)(void*, const void*, const std::string*))R(A_bf_renamePlaylist))(g_browserform, sp, &to);
  g_pl_treepath.clear();
  tklog("PLAYLIST: renamed folder '%s' -> '%s': %s", kPlLegacyFolder, kPlFolder, (ok & 1) ? "ok" : "refused");
}

static void do_tree_apply(void) {
  for (int i = 0; i < g_ntree_delcats; i++) do_tree_del(g_tree_delcats[i]);
  do_tree_add();
  // Diagnostic: what the tree's path keys look like (playlist ones), to locate the TK folder.
  static bool dumped = false;
  if (!dumped) {
    dumped = true;
    int shown = 0;
    for (const std::string& k : tree_path_keys()) {
      if (k.rfind("EXPLORER", 0) == 0) continue;
      tklog("TREEKEY len=%zu '%s'", k.size(), printable_path(k).c_str());
      if (++shown >= 60) break;
    }
  }
}

// === TK's result list (2026-11-23): one ContentList we own, filled from TK's result paths
// (`list` command) and shown in the browser when our tree node is selected. It holds a
// permanent reference of ours, so Traktor releasing it on the next selection never frees it.
// MAIN THREAD ONLY. ===
static void* g_tk_list = nullptr;
static bool g_tk_selected = false;  // our node is the browser's current selection

static void* tk_list_new(void) {
  void* mgr = get_collection_mgr();
  if (!mgr) return nullptr;
  void* list = ::operator new(SZ_CONTENTLIST + 0x40);  // never freed: we keep a reference
  memset(list, 0, SZ_CONTENTLIST + 0x40);
  ((void* (*)(void*, void* const*, bool))R(A_cl_ctor))(list, &mgr, true);
  ((int (*)(void*, bool))R(A_refcount_addRef))(list, true);  // as ContentList::create does
  tklog("LIST: created ContentList %p (mgr=%p)", list, mgr);
  return list;
}

static void* tk_list(void) {
  if (!g_tk_list) g_tk_list = tk_list_new();
  return g_tk_list;
}

static bool tk_list_showing(void) {
  return g_browserform && g_tk_list &&
         *(void**)((char*)g_browserform + OFF_BF_CONTENTLIST) == g_tk_list;
}

// What row->index mapping the browser uses for the list it shows: +0x710 is the vector<int> the
// sort job (UpdateContentListMappingJob) fills; null = identity. getIndexFromRow reads it.
static void log_list_mapping(const char* when) {
  char* bf = (char*)g_browserform;
  if (!bf) return;
  char* list = *(char**)(bf + OFF_BF_CONTENTLIST);
  long n = list ? (long)((*(char**)(list + 0x38) - *(char**)(list + 0x30)) >> 3) : -1;
  long* map = *(long**)(bf + 0x710);
  long m = (map && gum_memory_is_readable(map, 16)) ? (long)((map[1] - map[0]) >> 2) : -1;
  typedef unsigned (*fn_ifr)(void*, int);
  fn_ifr ifr = (fn_ifr)R(A_bf_getIndexFromRow);
  char* page = *(char**)(bf + OFF_BF_LISTPAGE);
  char* vis = (page && gum_memory_is_readable(page + OFF_PAGE_LIST, 8)) ? *(char**)(page + OFF_PAGE_LIST) : nullptr;
  tklog("LISTMAP %s: +0x708=%p (ours=%d) entries=%ld  +0x710=%p mapsize=%ld  row0->%d row1->%d row2->%d  page=%p visible=%p (ours=%d)",
        when, list, (int)(list == g_tk_list), n, map, m, (int)ifr(bf, 0), (int)ifr(bf, 1), (int)ifr(bf, 2),
        page, vis, (int)(vis == g_tk_list));
}

static void show_tk_list(void* bf);

// After a switch, Traktor's async sort job for the list it had (the collection) can land late:
// +0x708 back to the collection, or our list read through the collection's 882-row mapping at
// +0x710 (row 0 -> index 876: nothing selectable, no drag). Put it right if so.
static void tk_list_heal(const char* when) {
  char* bf = (char*)g_browserform;
  if (!bf || !g_tk_selected || !g_tk_list) return;
  char* list = *(char**)(bf + OFF_BF_CONTENTLIST);
  if (list != g_tk_list) { tklog("LISTHEAL %s: list replaced -> ours again", when); show_tk_list(bf); return; }
  long n = (long)((*(char**)(list + 0x38) - *(char**)(list + 0x30)) >> 3);
  long* map = *(long**)(bf + 0x710);
  long m = (map && gum_memory_is_readable(map, 16)) ? (long)((map[1] - map[0]) >> 2) : -1;
  if (m >= 0 && m != n) {
    tklog("LISTHEAL %s: mapping has %ld rows, list %ld -> remap", when, m, n);
    ((void (*)(void*))R(A_bf_updateCLMapping))(bf);
    ((void (*)(void*, unsigned))R(A_nglform_invalidate))(bf, 0);
  }
}

// Put our list in the browser in place of whatever treeSelect chose (+0x708 holds one ref).
static void show_tk_list(void* bf) {
  void* list = tk_list();
  if (!bf || !list) return;
  void** slot = (void**)((char*)bf + OFF_BF_CONTENTLIST);
  void* old = *slot;
  if (old != list) {
    ((int (*)(void*, bool))R(A_refcount_addRef))(list, false);
    *slot = list;
    if (old) ((void (*)(void*))R(A_refcount_release))(old);
  }
  // The list page (BrowserForm+0x6d0) keeps its OWN reference to the shown list (+0x350,
  // getCurrVisibleList). treeSelect points it at the new list; we must too, or clicks and drags
  // act on the list the page still holds (suspected cause of dead rows, 2026-11-23).
  char* page = *(char**)((char*)bf + OFF_BF_LISTPAGE);
  if (page && gum_memory_is_readable(page + OFF_PAGE_LIST, 8)) {
    void** pslot = (void**)(page + OFF_PAGE_LIST);
    void* pold = *pslot;
    tklog("LIST: page list was %p (ours=%d)", pold, (int)(pold == list));
    if (pold != list) {
      ((int (*)(void*, bool))R(A_refcount_addRef))(list, false);
      *pslot = list;
      if (pold) ((void (*)(void*))R(A_refcount_release))(pold);
    }
  }
  tk_force_tk_sort(bf);  // TK's list stays in TK's (score) order
  ((void (*)(void*))R(A_bf_updateCLMapping))(bf);
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(bf, 0);
  tklog("LIST: showing TK list %p in the browser", list);
  log_list_mapping("after show");
  static const int64_t checks_ms[] = { 200, 600, 1500 };
  for (int64_t ms : checks_ms)
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, ms * (int64_t)NSEC_PER_MSEC), dispatch_get_main_queue(),
                   ^{ tk_list_heal(ms == 1500 ? "1.5 s" : "early"); });
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 2 * (int64_t)NSEC_PER_SEC), dispatch_get_main_queue(),
                 ^{ log_list_mapping("2 s later"); });
}

// Replace the list's entries with the collection tracks at these paths (unknown paths skipped).
static std::vector<std::string> g_list_paths;  // what the list holds now
static std::vector<std::string> g_list_rows;   // the paths that became its entries, in order

// === Full matching cards in the band (2026-11-23). TK sends `cards` lines
// key|label|on|strength|value|hue; MATCHING expands/collapses them under the buttons. ===
static const int kCardH = 76, kCardGap = 8, kCardMinW = 170;

static void* card_label(const char* text) {
  void* l = ((void* (*)(void))R(A_ngllabel_createControl))();
  if (!l) return nullptr;
  int fid = g_fontid > 0 ? g_fontid : 36;
  ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(l, (unsigned)fid);  // BEFORE addControl
  ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(l, text);
  ((void (*)(void*, void*))R(A_nglform_addControl))(g_browserform, l);
  return l;
}

static void place(void* l, int l_, int t, int r, int b, unsigned fill, bool show) {
  if (!l) return;
  if (show && r > l_ && b > t) {
    int rect[4] = { l_, t, r, b };
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(l, rect);
    ((void (*)(void*, unsigned))R(A_ngllabel_setColor))(l, fill);
  }
  ((void (*)(void*, bool))R(A_nglctl_setVisible))(l, show && r > l_ && b > t);
}

static int track_l(const TKAxisCard* c) { return c->x + 10; }
static int track_r(const TKAxisCard* c) { return c->x + c->w - 10; }

// Position and colour one card's parts from its state (cheap: used while dragging).
static void card_layout(TKAxisCard* c, bool show) {
  unsigned bg = c->active ? 0xff2c2c2cu : 0xff1f1f1fu;
  int L = track_l(c), Rr = track_r(c), span = Rr - L;
  place(c->part[CP_BG], c->x, c->y, c->x + c->w, c->y + c->h, bg, show);
  // Where the playing (source) track sits on this axis: the card fills up to it, as TK's own card.
  const int src = c->src < 0 ? -1 : c->src > 100 ? 100 : c->src;
  const int fw = src < 0 ? 0 : c->w * src / 100;
  if (c->part[CP_FILL])
    place(c->part[CP_FILL], c->x, c->y, c->x + fw, c->y + c->h, hue_argb(c->hue, 0.22), show && c->active && fw > 0);
  // The title is the card's on/off switch, drawn as a Traktor toggle (User, 2026-11-24): grey text
  // on the grey tile when off, black text on Traktor blue when on — like the band's A-D.
  // Traktor's colour language (User, 2026-11-24): the axis's own colour on its title button,
  // orange for a value (the target fader), blue for an amount (the strength bar).
  place(c->part[CP_NAME], c->x + 6, c->y + 4, c->x + c->w - 6, c->y + 24, c->active ? hue_argb(c->hue, 0.58) : g_btn_bg, show);
  const int nfont = c->active ? g_card_font_on : g_card_font_off;
  if (nfont > 0 && c->font != nfont) {
    ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(c->part[CP_NAME], (unsigned)nfont);
    ((void (*)(void*, int))R(A_ngllabel_setHorzAlignment))(c->part[CP_NAME], 1);
    ((void (*)(void*, int))R(A_ngltextitem_setVertAlignment))((char*)c->part[CP_NAME] + 0xd8, g_btn_valign);
    c->font = nfont;
  }
  bool has_val = c->value >= 0;
  int mx = L + span * (has_val ? c->value : 0) / 100;
  place(c->part[CP_VTRACK], L, c->y + 37, Rr, c->y + 39, 0xff555555u, show && has_val);
  if (c->part[CP_VFILL])
    place(c->part[CP_VFILL], L, c->y + 37, mx, c->y + 39, c->active ? kBtnBusyBg : 0xff6a6a6au, show && has_val && mx > L);
  place(c->part[CP_VMARK], mx - 4, c->y + 30, mx + 4, c->y + 46, 0xff161616u, show && has_val);
  if (c->part[CP_VKNOB])
    place(c->part[CP_VKNOB], mx - 3, c->y + 31, mx + 3, c->y + 45, c->active ? 0xffb4b4b4u : 0xff6e6e6eu, show && has_val);
  // The amount: the same Traktor fader as the value, in blue.
  int sw = span * (c->strength < 0 ? 0 : c->strength > 100 ? 100 : c->strength) / 100;
  const int sx = L + sw;
  place(c->part[CP_STRACK], L, c->y + 60, Rr, c->y + 62, 0xff555555u, show);
  place(c->part[CP_SBAR], L, c->y + 60, sx, c->y + 62, c->active ? kBtnSelectedBg : 0xff6a6a6au, show && sw > 0);
  if (c->part[CP_SKOUT]) place(c->part[CP_SKOUT], sx - 4, c->y + 53, sx + 4, c->y + 69, 0xff161616u, show);
  if (c->part[CP_SKNOB])
    place(c->part[CP_SKNOB], sx - 3, c->y + 54, sx + 3, c->y + 68, c->active ? 0xffb4b4b4u : 0xff6e6e6eu, show);
}

// The matching cards show with the playlist only — they act on it (User, 2026-11-24): not under a
// TK page (Settings, Library, Stats, Spotify). MATCHING keeps its state for when the list is back.
static bool cards_on(void) { return g_band_visible && g_cards_expanded && !g_active_page[0]; }

// The cards between the list's column headers and its rows (User, 2026-11-24): the list's header
// grows by the cards' height (tk_columns.mm hooks getHeaderHeight and draws each header cell at
// its normal height, so the gap stays unpainted) and the cards show in that gap, instead of
// pushing the whole list down under the band. false = the old layout (cards above the list).
static const bool kCardsInList = true;
static int g_cards_gap_h = 0;     // px added to the list's header height (0 = cards closed)
static int g_list_header_h = 24;  // the list's own header height (tk_columns.mm keeps it fresh)

static void cards_relayout(void) {
  if (g_quitting) return;
  if (!g_browserform || g_band_top < 0 || !g_list_orig_set) return;
  const double t0 = CFAbsoluteTimeGetCurrent();
  int n = g_nacards, W = g_band_right - g_band_left;
  // TK's own card flow (ui/np_format.py _flow_columns, User 2026-11-24 "just reuse the code"):
  // the fewest rows that keep a card at least kCardMinW wide, the cards spread evenly over them
  // (13 = 7 + 6, not 9 + 4), each stretched to fill its row.
  int fit = (W + kCardGap) / (kCardMinW + kCardGap);
  fit = fit < 1 ? 1 : (n > 0 && fit > n ? n : fit);
  const int nrows = n > 0 ? (n + fit - 1) / fit : 1;
  int per_row = n > 0 ? (n + nrows - 1) / nrows : 1;
  int card_w = (W - (per_row - 1) * kCardGap) / per_row;
  if (card_w < kCardMinW) card_w = kCardMinW;
  int rows = (cards_on() && n) ? (n + per_row - 1) / per_row : 0;
  int top = g_band_top + g_band_btn_h;
  if (kCardsInList) {
    // The list's top is the band's bottom; its rows begin under its header + the cards' gap.
    g_cards_gap_h = rows ? rows * (kCardH + kCardGap) + kCardGap : 0;
    top = g_band_top + g_band_btn_h + g_list_header_h + kCardGap;
  }
  for (int i = 0; i < MAX_AXIS_CARDS; i++) {
    TKAxisCard* c = &g_acards[i];
    bool show = cards_on() && i < n;
    if (i < n) {
      // A shorter last row (13 = 7 + 6) stretches its cards too: no empty slot at its end.
      const int row = i / per_row, in_row = n - row * per_row < per_row ? n - row * per_row : per_row;
      const int w = (W - (in_row - 1) * kCardGap) / in_row;
      c->x = g_band_left + (i % per_row) * (w + kCardGap);
      c->y = top + row * (kCardH + kCardGap);
      c->w = in_row == per_row ? card_w : w; c->h = kCardH;
    }
    if (show && !c->part[CP_BG]) {  // build lazily; order = paint order (bg first)
      c->part[CP_BG] = card_label("");
      c->part[CP_FILL] = card_label("");  // over the background, under everything else
      c->part[CP_NAME] = card_label(c->name);
      c->part[CP_VTRACK] = card_label("");
      c->part[CP_VFILL] = card_label("");
      c->part[CP_STRACK] = card_label("");
      c->part[CP_SBAR] = card_label("");
      c->part[CP_VMARK] = card_label("");  // the handle's outline, then its face
      c->part[CP_VKNOB] = card_label("");
      c->part[CP_SKOUT] = card_label("");
      c->part[CP_SKNOB] = card_label("");
      c->lbl = c->part[CP_NAME];
    }
    if (show) ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(c->part[CP_NAME], c->name);
    if (c->part[CP_BG]) card_layout(c, show);
  }
  void* list = *(void**)((char*)g_browserform + 0x518);
  if (list) {
    // Traktor moves the list's sides itself (a separator drag) without BrowserForm::resized:
    // start from where it is now, never from edges remembered before (stale ones cut the list's
    // first columns off under the left pane, 2026-11-24). Its top is ours when we put it there.
    // Edge by edge: an edge that is not where WE last put it was moved by Traktor (its own
    // value, without our band push or tree inset) and becomes the base.
    if (gum_memory_is_readable((char*)list + OFF_CTRL_RECT_L, 16)) {
      const int* cur = (const int*)((char*)list + OFF_CTRL_RECT_L);
      if (cur[1] != g_list_pushed_top) g_list_orig[1] = cur[1];
      if (cur[0] != g_list_pushed_left) g_list_orig[0] = cur[0];
      g_list_orig[2] = cur[2]; g_list_orig[3] = cur[3];
      g_band_left = g_list_orig[0] + 4; g_band_right = g_list_orig[2] - 4;
    }
    int push = g_band_visible ? g_band_btn_h + (kCardsInList ? 0 : rows * (kCardH + kCardGap)) : 0;
    // The native page's tree sits left of the list (mode 1): the list starts after it.
    const int inset = g_band_visible && g_nl_mode == 1 ? NL_TREE_W : 0;
    int nr[4] = { g_list_orig[0] + inset, g_list_orig[1] + push, g_list_orig[2], g_list_orig[3] };
    g_list_pushed_top = nr[1];
    g_list_pushed_left = nr[0];
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(list, nr);
    // A TK page covers the list: hide Traktor's tracks under it, so a separator drag or a resize
    // never shows them through while the page catches up (User, 2026-11-24: "feels hacked in").
    // The native page's mode 1 shows TK's tracks IN that list: it stays.
    ((void (*)(void*, bool))R(A_nglctl_setVisible))(list, !(g_band_visible && g_active_page[0] && g_nl_mode != 1));
  }
  band_place_buttons();  // the MATCHING bar shows whether the cards are open
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
  nl_layout();  // the native page follows the list's area
  layout_changed();  // the list's top moved: TK's page follows
  tklog("CARDS: %d card(s), %s, %d row(s) x %d, %d px, laid out in %.1f ms", n,
        g_cards_expanded ? "open" : "closed", rows, per_row, card_w, (CFAbsoluteTimeGetCurrent() - t0) * 1000.0);
}

// Place SOURCE:, the buttons and the two separators around MATCHING along the band.
// Buttons sit in groups with a line between two groups: source decks | matching cards + automation |
// list | library pages + Stats | the add-on's pages | Settings + About (User, 2026-11-24).
static int band_group(const char* n) {
  if (n[1] == 0 && n[0] >= 'A' && n[0] <= 'D') return 0;
  if (!strcmp(n, "MATCHING") || !strcmp(n, "AUTO")) return 1;
  if (!strcmp(n, "LIST")) return 2;
  if (is_spare_button(n)) return 4;  // an add-on's pages
  if (!strncmp(n, "S_", 2)) return 5;  // Settings, About
  if (!strncmp(n, "L_", 2) || !strcmp(n, "STATS")) return 3;  // library pages, Stats last
  return 6;
}

static void band_place_buttons(void) {
  if (g_quitting) return;
  int x = g_band_left, y = g_band_top + 8, h = 28;
  if (g_source_lbl) {
    int r[4] = { x, y, x + kSourceW, y + h };
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(g_source_lbl, r);
  }
  x += kSourceW + 6;
  // Separators from an earlier layout go first: a group now empty (an add-on's slots without
  // it) left its separator standing (User, 2026-11-25). Those still needed are placed again below.
  for (void* sep : g_sep)
    if (sep) ((void (*)(void*, bool))R(A_nglctl_setVisible))(sep, false);
  int nsep = 0, last_group = -1;
  for (int i = 0; i < g_nbtn; i++) {
    const bool hidden = g_btn_hidden[i];
    if (hidden) {
      ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_btn_ctrl[i], false);
      if (g_btn_tile[i]) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_btn_tile[i], false);
      continue;
    }
    ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_btn_ctrl[i], g_band_visible);
    const int group = band_group(g_btn_name[i]);
    if (last_group >= 0 && group != last_group && nsep < (int)(sizeof(g_sep) / sizeof(g_sep[0]))) {
      if (!g_sep[nsep]) g_sep[nsep] = card_label("");
      place(g_sep[nsep], x + kSepGap / 2 - 4, y + 4, x + kSepGap / 2 - 3, y + h - 4, 0xff505050u, g_band_visible);
      nsep++;
      x += kSepGap;
    }
    last_group = group;
    const bool matching = strcmp(g_btn_name[i], "MATCHING") == 0;
    if (g_btn_tile[i]) {
      unsigned fill = g_btn_bg;
      if (matching && g_cards_expanded) fill = kBtnSelectedBg;
      // LIST and the page buttons are one view switch: exactly one is lit (LIST = the playlist).
      if (g_active_page[0] && strcmp(g_btn_name[i], g_active_page) == 0) fill = kBtnSelectedBg;
      if (!g_active_page[0] && strcmp(g_btn_name[i], "LIST") == 0) fill = kBtnSelectedBg;
      if (strcmp(g_btn_name[i], "AUTO") == 0 && g_auto_bg) fill = g_auto_bg;
      place(g_btn_tile[i], x, y, x + g_btn_w[i], y + h, fill, g_band_visible);
      const bool lit = fill != g_btn_bg;
      const TKIcon* own = icon_for(g_btn_name[i]);
      void* mine = own ? own->pic[lit ? 1 : 0] : nullptr;
      if (mine) {  // TK's silhouette: grey off, black lit
        *(void**)((char*)g_btn_ctrl[i] + OFF_LBL_PICTURE) = mine;
        ((void (*)(void*, int))R(A_ngllabel_setDrawMode))(g_btn_ctrl[i], 2);
        ((void (*)(void*, unsigned))R(A_ngllabel_setPictureIndex))(g_btn_ctrl[i], 0);
      } else {
        const unsigned pic = strcmp(g_btn_name[i], "AUTO") == 0 ? g_auto_pic : band_icon(g_btn_name[i]);
        ((void (*)(void*, unsigned))R(A_ngllabel_setPictureIndex))(
            g_btn_ctrl[i], lit ? icon_lit_frame(pic) : kIconFrameOff);
      }
    }
    int rect[4] = { x, y, x + g_btn_w[i], y + h };
    // Traktor draws a label's picture from the label's top-left corner: for an icon button, size
    // the label to its picture and centre it in the button's slot.
    const char* pic = gum_memory_is_readable((char*)g_btn_ctrl[i] + 0xc8, 8)
                        ? *(const char* const*)((char*)g_btn_ctrl[i] + 0xc8) : nullptr;
    if (band_icon(g_btn_name[i]) && pic) {
      int pw = ((int (*)(const void*))R(A_pic_animWidth))(pic);
      int ph = ((int (*)(const void*))R(A_pic_animHeight))(pic);
      if (pw > 0 && ph > 0 && pw <= g_btn_w[i] + 8 && ph <= h + 8) {
        int cx = x + g_btn_w[i] / 2, cy = y + h / 2;
        rect[0] = cx - pw / 2; rect[1] = cy - ph / 2; rect[2] = rect[0] + pw; rect[3] = rect[1] + ph;
      }
    }
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(g_btn_ctrl[i], rect);
    x += g_btn_w[i] + 6;
    // The auto mode's name no longer sits right of AUTO (User, 2026-11-24): TK shows it in a
    // short-lived tooltip when the mode changes, and the band keeps no room for it.
    if (strcmp(g_btn_name[i], "AUTO") == 0 && g_auto_lbl)
      ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_auto_lbl, false);
  }
  for (int k = nsep; k < (int)(sizeof(g_sep) / sizeof(g_sep[0])); k++)  // lines no longer needed
    if (g_sep[k]) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_sep[k], false);
}

// BrowserForm::resized just laid its children out again (window resize, layout change): the
// track list is back at Traktor's own top, over our buttons (2026-11-23 bug). Take that as the
// new base, re-place the buttons, and push the list down again with the cards.
static void band_relayout(void) {
  if (!g_browserform || g_band_top < 0 || g_nbtn == 0) return;
  void* list = *(void**)((char*)g_browserform + 0x518);
  if (!list || !gum_memory_is_readable((char*)list + OFF_CTRL_RECT_L, 16)) return;
  int* lr = (int*)((char*)list + OFF_CTRL_RECT_L);
  if (lr[1] == g_list_pushed_top && lr[0] == g_list_pushed_left && lr[2] == g_list_orig[2]) return;  // still ours
  memcpy(g_list_orig, lr, sizeof(g_list_orig));
  g_band_top = lr[1]; g_band_left = lr[0] + 4; g_band_right = lr[2] - 4;
  band_place_buttons();
  place_auto_strip();
  tklog("BAND: Traktor re-laid out the browser -> band re-applied (list top %d)", g_band_top);
  cards_relayout();
}

static void* g_pic_cells[48][3];  // [i][0] picture label, [i][1] id text, [i][2] backdrop
static unsigned g_pic_bg = 0xff00c000u;  // bright, so dark glyphs show (picture hunting)
static int g_pic_mode = 1;        // LabelControl draw mode for the grid (picmode)
// "fontprobe <first> <count>" (2026-11-24, native-page stage 1): one label per font ID in a grid
// over the list area, so a window capture shows each ID's text colour and size (a label's text
// colour comes only from its font). The font is set BEFORE addControl, as for every label.
// "fontprobe 0 0" hides them. Read-only for Traktor's data.
static void* g_font_cells[200];
static void do_fontprobe(int first, int count) {
  if (!g_browserform || g_band_top < 0) { tklog("FONTPROBE: no band yet"); return; }
  const int cols = 8, cw = (g_band_right - g_band_left) / cols, ch = 22;
  const int y0 = g_band_top + g_band_btn_h + 4;
  for (int i = 0; i < 200; i++) {
    const bool on = i < count;
    if (on && !g_font_cells[i]) {
      void* l = ((void* (*)(void))R(A_ngllabel_createControl))();
      if (!l) break;
      ((void (*)(void*, unsigned))R(A_ngllabel_setFontID))(l, (unsigned)(first + i));
      char t[32]; snprintf(t, sizeof(t), "%d Artist Title 12d", first + i);
      ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(l, t);
      ((void (*)(void*, void*))R(A_nglform_addControl))(g_browserform, l);
      g_font_cells[i] = l;
    }
    if (!g_font_cells[i]) continue;
    const int x = g_band_left + (i % cols) * cw, y = y0 + (i / cols) * ch;
    place(g_font_cells[i], x, y, x + cw - 4, y + ch - 2, 0xff141414u, on);
  }
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
  tklog("FONTPROBE: fonts %d..%d", first, first + count - 1);
}

static void do_picgrid(const std::vector<unsigned>& ids) {
  if (!g_browserform || g_band_top < 0) return;
  bool show = !(ids.size() == 1 && ids[0] == 0);
  int cell = 64, x0 = g_band_left, y0 = g_band_top + g_band_btn_h + 4, per_row = (g_band_right - x0) / cell;
  if (per_row < 1) per_row = 1;
  for (int i = 0; i < 48; i++) {
    bool on = show && i < (int)ids.size();
    if (on && !g_pic_cells[i][0]) {
      g_pic_cells[i][2] = card_label("");  // first: drawn under the picture
      g_pic_cells[i][0] = card_label(""); g_pic_cells[i][1] = card_label("");
    }
    if (!g_pic_cells[i][0]) continue;
    int x = x0 + (i % per_row) * cell, y = y0 + (i / per_row) * cell;
    if (on) {
      char t[16]; snprintf(t, sizeof(t), "%u", ids[i]);
      ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(g_pic_cells[i][1], t);
      ((void (*)(void*, unsigned))R(A_ngllabel_setPictureID))(g_pic_cells[i][0], ids[i]);
      ((void (*)(void*, int))R(A_ngllabel_setDrawMode))(g_pic_cells[i][0], g_pic_mode);
      if (i < 3) {
        char* lc = (char*)g_pic_cells[i][0];
        tklog("PICGRID: id %u -> picture %p (mode %d)", ids[i], *(void**)(lc + 0xc8), *(int*)(lc + 0xd0));
      }
    }
    place(g_pic_cells[i][2], x, y, x + cell - 4, y + cell - 18, g_pic_bg, on);
    place(g_pic_cells[i][0], x, y, x + cell - 4, y + cell - 18, 0xff202020u, on);
    place(g_pic_cells[i][1], x, y + cell - 16, x + cell - 4, y + cell - 2, 0xff202020u, on);
  }
  // The track list paints over anything in its rect: push it below the grid while it shows.
  void* list = *(void**)((char*)g_browserform + 0x518);
  if (show && list) {
    int rows = ((int)ids.size() + per_row - 1) / per_row;
    int nr[4] = { g_list_orig[0], y0 + rows * cell + 4, g_list_orig[2], g_list_orig[3] };
    g_list_pushed_top = nr[1]; g_list_pushed_left = nr[0];
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(list, nr);
  } else {
    cards_relayout();
  }
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
  tklog("PICGRID: %zu id(s)%s", ids.size(), show ? "" : " (hidden)");
}

static void set_band_status(const char* text) {
  if (!g_browserform || g_band_top < 0 || g_nbtn == 0) return;
  if (!g_status_lbl) {
    g_status_lbl = card_label("");
    style_button(g_status_lbl, 0x00000000u, 0, g_btn_valign);  // transparent, left-aligned
  }
  const void* tail = g_btn_ctrl[g_nbtn - 1];  // after the last button (no mode text any more)
  const int* last = (const int*)((const char*)tail + OFF_CTRL_RECT_L);
  int r[4] = { last[2] + 16, last[1], g_band_right, last[3] };
  ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(g_status_lbl, text ? text : "");
  ((void (*)(void*, void*))R(A_nglctl_setPosition))(g_status_lbl, r);
  ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_status_lbl, g_band_visible && text && *text);
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

static void band_set_visible(bool on) {
  if (!g_browserform || g_band_top < 0) { g_band_visible = on; return; }
  bool changed = on != g_band_visible;
  g_band_visible = on;
  if (g_source_lbl) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_source_lbl, on);
  for (int i = 0; i < g_nbtn; i++) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_btn_ctrl[i], on);
  if (g_auto_strip) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_auto_strip, on);
  for (void* sep : g_sep) if (sep) ((void (*)(void*, bool))R(A_nglctl_setVisible))(sep, on);
  if (g_auto_lbl) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_auto_lbl, false);  // tooltip now
  if (g_sep[2]) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_sep[2], on);
  if (g_match_strip && !on) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_match_strip, false);
  if (g_status_lbl && !on) ((void (*)(void*, bool))R(A_nglctl_setVisible))(g_status_lbl, false);
  cards_relayout();  // shows/hides the cards and puts the list where it belongs
  tk_score_column(on);  // TK SCORE: first in TK's view, hidden elsewhere
  if (changed) tklog("BAND: %s", on ? "shown (TK view)" : "hidden");
}

// === Embedded Python (2026-11-23 trial): run TK's Python INSIDE Traktor, so Qt views can live in
// Traktor's own window. libpython is dlopen'ed (the dylib does not link it); PYTHONHOME points
// at the interpreter the venv was made from; the GIL is released after init and taken per call.
// Everything past "load Python" is a script (`pyexec <file>`), so iterating needs no rebuild. ===
typedef int (*fn_PyGILState_Ensure)(void);
typedef void (*fn_PyGILState_Release)(int);
static fn_PyGILState_Ensure p_gil_ensure = nullptr;
static fn_PyGILState_Release p_gil_release = nullptr;
static int (*p_run_string)(const char*) = nullptr;
static bool g_py_ready = false;

static std::string user_home(void);  // below, with the settings file

static bool py_init(void) {
  if (g_py_ready) return true;
  // Set by the kit inside the app (find_kit_in_app) or by tk-embed.conf (a checkout).
  const char* home = getenv("TK_PYTHONHOME");
  if (!home || !*home) { tklog("PY: no TK_PYTHONHOME (no kit in the app, none in tk-embed.conf)"); return false; }
  std::string lib = std::string(home) + "/lib/libpython3.13.dylib";
  void* h = dlopen(lib.c_str(), RTLD_NOW | RTLD_GLOBAL);
  if (!h) { tklog("PY: dlopen %s failed: %s", lib.c_str(), dlerror()); return false; }
  auto init = (void (*)(int))dlsym(h, "Py_InitializeEx");
  auto save = (void* (*)(void))dlsym(h, "PyEval_SaveThread");
  p_run_string = (int (*)(const char*))dlsym(h, "PyRun_SimpleString");
  p_gil_ensure = (fn_PyGILState_Ensure)dlsym(h, "PyGILState_Ensure");
  p_gil_release = (fn_PyGILState_Release)dlsym(h, "PyGILState_Release");
  if (!init || !save || !p_run_string || !p_gil_ensure || !p_gil_release) { tklog("PY: symbols missing"); return false; }
  setenv("PYTHONHOME", home, 1);
  // Compiled code goes to the user's cache folder, never beside the sources: inside "Traktor Pro 4
  // TK.app" the kit is sealed by the app's signature (a write there would break it), and without
  // any cache every start compiled TK, numpy, scipy... again. numba (librosa) keeps its own cache
  // beside the sources unless told otherwise. A variable already set wins.
  const std::string cache = user_home() + "/Library/Caches/Track Kommander";
  setenv("PYTHONPYCACHEPREFIX", (cache + "/pycache").c_str(), 0);
  setenv("NUMBA_CACHE_DIR", (cache + "/numba").c_str(), 0);
  // The kit keeps only Qt's AVFoundation media backend (trim_kit.py drops Qt's ffmpeg copy).
  setenv("QT_MEDIA_BACKEND", "darwin", 0);
  // UTF-8 mode: an embedded Python takes the process's locale for open()'s encoding, and inside
  // Traktor that was sometimes ASCII (a Finder launch has no LANG; Traktor sets its locale on its
  // own schedule). embed_tk.py's first "—" then failed with UnicodeDecodeError, and TK did not
  // start (2026-11-25, the "-> -1" launches; found by the traceback log below).
  setenv("PYTHONUTF8", "1", 1);
  init(0);  // 0: leave Traktor's signal handlers alone
  const char* repo = getenv("TK_REPO") ? getenv("TK_REPO") : "";  // set with TK_PYTHONHOME
  std::string boot = std::string("import sys\n")
                   + "sys.path[:0] = ['" + repo + "/.venv/lib/python3.13/site-packages', '" + repo + "/src']\n"
                   + "sys.argv = ['traktor-tk']\n";
  p_run_string(boot.c_str());
  save();  // release the GIL: calls below (and Qt's callbacks) take it as needed
  g_py_ready = true;
  tklog("PY: Python up (home=%s)", home);
  return true;
}

static int do_pyexec(const std::string& path) {
  if (!py_init()) return -1;
  // PyRun_SimpleString prints a traceback to Traktor's stderr, which a Finder launch sends
  // nowhere: TK failed to start with only "-> -1" logged (2026-11-25). The traceback goes here.
  const char* log_env = getenv("TK_DYLIB_LOG");
  const std::string log = log_env && *log_env ? log_env : "/tmp/tk_dylib.log";
  std::string code = "import traceback as _tk_tb\n"
                     "try:\n"
                     "    exec(compile(open(\"" + path + "\", encoding='utf-8').read(), \"" + path +
                     "\", 'exec'), {'__name__': '__tkembed__'})\n"
                     "except BaseException:\n"
                     "    with open(\"" + log + "\", 'a') as _tk_f:\n"
                     "        _tk_f.write('PY: traceback of " + path + "\\n' + _tk_tb.format_exc())\n"
                     "    raise\n";
  int g = p_gil_ensure();
  int rc = p_run_string(code.c_str());
  p_gil_release(g);
  tklog("PY: ran %s -> %d", path.c_str(), rc);
  return rc;
}

// For TK running INSIDE Traktor (embedded Python, ctypes): where the track list is, so TK can put
// its pages exactly there. out[0..3] = list rect {l,t,r,b} in BrowserForm coordinates (below the
// band), out[4..19] = the BrowserForm's first int fields from +0x18 (to find its origin in the
// window). Returns 0 when the browser is not known yet. Called on the main thread by Qt code.
extern "C" __attribute__((visibility("default"))) int tk_list_rect(int* out) {
  if (!g_browserform || !out) return 0;
  void* list = *(void**)((char*)g_browserform + 0x518);
  if (!list || !gum_memory_is_readable((char*)list + OFF_CTRL_RECT_L, 16)) return 0;
  memcpy(out, (char*)list + OFF_CTRL_RECT_L, 16);
  if (gum_memory_is_readable((char*)g_browserform + 0x18, 64)) memcpy(out + 4, (char*)g_browserform + 0x18, 64);
  return 1;
}

// Where band button <name> is ({l, t, r, b}, BrowserForm coordinates, its tile when it has one):
// TK places a short hint under it (the AUTO mode's name, User 2026-11-24). 0 = unknown / hidden.
extern "C" __attribute__((visibility("default"))) int tk_band_button_rect(const char* name, int* out) {
  if (!name || !out || !g_browserform || !g_band_visible) return 0;
  for (int i = 0; i < g_nbtn; i++) {
    if (strcmp(g_btn_name[i], name) != 0 || g_btn_hidden[i]) continue;
    void* c = g_btn_tile[i] ? g_btn_tile[i] : g_btn_ctrl[i];
    if (!c || !gum_memory_is_readable((char*)c + OFF_CTRL_RECT_L, 16)) return 0;
    memcpy(out, (char*)c + OFF_CTRL_RECT_L, 16);
    return 1;
  }
  return 0;
}

// TK (inside Traktor, ctypes) says which of its pages shows in the list area: "SETTINGS", "LIB",
// "STATS", or "" for none. That button's tile turns blue, like Traktor's "on" buttons. Main thread.
extern "C" __attribute__((visibility("default"))) void tk_band_active_page(const char* name) {
  strlcpy(g_active_page, name ? name : "", sizeof(g_active_page));
  // The cards show with the playlist only and the list hides under a TK page: lay out again.
  // (cards_relayout takes the list's edges from Traktor first, so a separator dragged meanwhile
  // is kept — the stale edges of 2026-11-24 cut the list's first columns off.)
  if (g_browserform && g_band_top >= 0) cards_relayout();
  band_place_buttons();
  if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

// The card slider being dragged (index, part), -1 when none: see card_event / form_mouse.
static int g_drag_card = -1, g_drag_part = -1;
static void do_cards_set(const std::vector<std::string>& lines) {
  int n = 0;
  for (const std::string& ln : lines) {
    if (n >= MAX_AXIS_CARDS) break;
    char key[24] = {0}, name[32] = {0}, desc[256] = {0};
    int on = 0, st = 0, val = -1, hue = 0, src = -1;
    const int got = sscanf(ln.c_str(), "%23[^|]|%31[^|]|%d|%d|%d|%d|%d|%255[^\n]", key, name, &on, &st,
                           &val, &hue, &src, desc);
    if (got < 6) continue;  // 6: a TK from before the playing-track fill; 8: with a description
    TKAxisCard* c = &g_acards[n];
    const bool same_card = n < g_nacards && !strcmp(c->key, key);
    n++;
    // While the user drags (or just toggled) this card here, TK's echo may be a step behind:
    // keep what the user sees until 0.8 s after the last touch.
    const bool settling = same_card && (n - 1 == g_drag_card ||
                                        CFAbsoluteTimeGetCurrent() - c->touched < 0.8);
    strlcpy(c->key, key, sizeof(c->key)); strlcpy(c->name, name, sizeof(c->name));
    if (!settling) { c->active = on != 0; c->strength = st; c->value = val; }
    if (!same_card) c->touched = 0;
    c->hue = hue; c->src = got >= 7 ? src : -1;
    strlcpy(c->desc, got == 8 ? desc : "", sizeof(c->desc));
  }
  g_nacards = n;
  cards_relayout();
}

// A press or drag on a card part: name toggles the axis; the value track/marker and the weight
// track/bar set that slider from the pointer's x (updated here at once, then applied by TK).
// One action per physical mouse press. The real button state (AppKit) decides: true for the first
// press event while the left button is down, false for the repeats and for the other controls
// Traktor offers the same press to. Main thread (all our NGL hooks run there).
static bool g_press_taken = false;
static bool mouse_down(void) { return ([NSEvent pressedMouseButtons] & 1u) != 0; }
static void press_track(void) { if (!mouse_down()) g_press_taken = false; }
static bool press_edge(void) {
  press_track();
  if (g_press_taken) return false;
  g_press_taken = true;
  return true;
}

// A slider of card i (part = value or strength track) moved to form x: redraw, tell TK on change.
static void card_slide(int i, int part, int x) {
  TKAxisCard* c = &g_acards[i];
  const bool is_value = part == CP_VTRACK || part == CP_VMARK || part == CP_VFILL || part == CP_VKNOB;
  if (is_value && c->value < 0) return;
  int L = track_l(c), Rr = track_r(c);
  int v = Rr > L ? (x - L) * 100 / (Rr - L) : 0;
  v = v < 0 ? 0 : v > 100 ? 100 : v;
  int* last = is_value ? &c->last_sent_value : &c->last_sent_strength;
  if (is_value) c->value = v; else c->strength = v;
  c->touched = CFAbsoluteTimeGetCurrent();
  card_layout(c, true);
  if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
  if (*last == v + 1) return;  // stored +1 so 0 means "nothing sent yet"
  *last = v + 1;
  char body[96];
  snprintf(body, sizeof(body), "{\"key\":\"%s\",\"field\":\"%s\",\"value\":%d}", c->key, is_value ? "value" : "strength", v);
  post_json("/uiCard", body);
}

// A slider press starts a drag that follows the mouse anywhere over the form (Form::onMouseEvent
// hook) until the button comes up: Traktor does not send moves to the pressed label itself.

static void card_event(int i, int part, unsigned evt, const char* d) {
  TKAxisCard* c = &g_acards[i];
  int button = *(const int*)(d + OFF_EVT_BUTTON), state = *(const int*)(d + OFF_EVT_STATE);
  static int seen = 0;
  if (seen < 60 && (evt == NGL_EVT_MOUSE || evt == NGL_EVT_MOUSE + 2) && part != CP_BG) {
    seen++;
    tklog("CARDEVT %s part=%d evt=%u button=%d state=%d x=%d", c->key, part, evt, button, state, *(const int*)d);
  }
  // The background (and the playing-track fill) sit under every other part, and Traktor offers a
  // click to each control under the mouse in turn, background first: acting on it toggled the axis
  // on every click meant for a slider ("click stealing", 2026-11-23). Decoration only.
  if (part == CP_BG || part == CP_FILL) return;
  const bool press = evt == NGL_EVT_MOUSE && button == 1 && state == 1;
  if (!press) return;
  if (part == CP_NAME) {
    if (!press_edge()) return;  // a toggle: once per click
    c->active = !c->active;  // show it now; TK's answer confirms it
    c->touched = CFAbsoluteTimeGetCurrent();
    card_layout(c, true);
    if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
    char body[64];
    snprintf(body, sizeof(body), "{\"action\":\"CARD:%s\"}", c->key);
    post_json("/uiAction", body);
    return;
  }
  g_drag_card = i;
  g_drag_part = (part == CP_VMARK || part == CP_VFILL || part == CP_VKNOB) ? CP_VTRACK
              : (part == CP_SBAR || part == CP_SKOUT || part == CP_SKNOB) ? CP_STRACK : part;
  card_slide(i, g_drag_part, *(const int*)d);
}

// Cards under the list's headers never get their own clicks: the cards sit behind the list (it
// only leaves the gap unpainted), which takes every mouse event in its area as a header click.
// The browser form sees each event first, in the cards' coordinates: a press on a card goes to
// card_event (as its own label would have), and every mouse event inside the cards' gap is
// emptied so the list ignores it (header sorting fires on the mouse UP, so ups are emptied too;
// our press logic reads the real mouse state). Drags then go on through form_mouse as before.
static void cards_form_event(unsigned evt, char* d) {
  if (!kCardsInList || g_cards_gap_h <= 0 || !cards_on()) return;
  if (evt != NGL_EVT_MOUSE || !d || !gum_memory_is_readable(d, OFF_EVT_STATE + 4)) return;
  const int x = ((const int*)d)[0], y = ((const int*)d)[1];
  const int gap_t = g_band_top + g_band_btn_h + g_list_header_h, gap_b = gap_t + g_cards_gap_h;
  if (y < gap_t || y >= gap_b || x < g_band_left || x > g_band_right) return;
  for (int i = 0; i < g_nacards; i++) {
    TKAxisCard* c = &g_acards[i];
    if (x < c->x || x >= c->x + c->w || y < c->y || y >= c->y + c->h) continue;
    const int dy = y - c->y;  // card_layout: name 4..24, value fader 30..46, amount fader 53..69
    card_event(i, dy < 27 ? CP_NAME : dy < 50 ? CP_VTRACK : CP_STRACK, evt, d);
    break;
  }
  *(int*)(d + OFF_EVT_BUTTON) = 0;
  *(int*)(d + OFF_EVT_STATE) = 0;
}

// Form::onMouseEvent(this x0, evt x1, EventData* x2) on the browser form, while a slider is held.
static void form_mouse(void* self, unsigned evt, const char* d) {
  if (self != g_browserform) return;
  cards_form_event(evt, (char*)d);  // cards under the headers: their clicks, taken from the list
  nl_form_event(evt, (char*)d);  // the native page: its clicks and wheel, taken from Traktor
  press_track();  // any mouse event with the button up ends "this press was taken" (cards too)
  if (g_drag_card < 0) return;
  static int logged = 0;
  if (logged < 12) { logged++; tklog("DRAG form evt=%u data=%p", evt, d); }
  // Released: the mouse event's own fields do not say (moves carry button 0 and no state), the
  // real button state does.
  if (!mouse_down()) { g_drag_card = g_drag_part = -1; g_press_taken = false; return; }
  if (!d || !gum_memory_is_readable(d, 8)) return;
  if (evt == NGL_EVT_MOUSE + 2 && g_drag_card < g_nacards) card_slide(g_drag_card, g_drag_part, *(const int*)d);
}

// === Backing playlist (2026-11-23): the in-memory list never became selectable, so the node now
// shows a REAL playlist "Results" inside the user's "Track Kommander" playlist folder, edited the
// way Traktor edits lists: getList (a copy) -> clear/add -> commitList (stores + notifies). Tree
// paths are "<PREFIX>\a<folder>\a<name>"; BrowserForm::getStructurePathFromTreePath maps them to
// the StructurePath (a std::string) the collection uses. MAIN THREAD ONLY. ===

// Tree path keys of BrowserStdTree's path->TreeNodeInfo map (+0x688, libc++ map; key @+0x20).
static std::vector<std::string> tree_path_keys(void) {
  std::vector<std::string> keys;
  if (!g_stdtree) return keys;
  char* end = (char*)g_stdtree + OFF_BST_PATHMAP + 8;
  char* it = *(char**)((char*)g_stdtree + OFF_BST_PATHMAP);
  for (int guard = 0; it && it != end && guard < 20000; guard++) {
    if (!gum_memory_is_readable(it, 0x38)) break;
    keys.push_back(decode_std_string(it + 0x20));
    char* right = *(char**)(it + 8);
    if (right) { it = right; while (*(char**)it) it = *(char**)it; continue; }
    char* parent = *(char**)(it + 0x10);
    while (parent != end && *(char**)(parent + 8) == it) { it = parent; parent = *(char**)(it + 0x10); }
    it = parent;
  }
  return keys;
}

static std::string printable_path(const std::string& p) {
  std::string out(p);
  for (char& c : out) if (c == '\a') c = '|';
  return out;
}

// The Results playlist's tree path: an existing one, else "<Track Kommander folder>\aResults".
static std::string results_tree_path(void) {
  if (!g_pl_treepath.empty()) return g_pl_treepath;
  migrate_pl_folder();  // once, as soon as the tree knows its paths
  std::string folder;
  std::vector<std::string> keys = tree_path_keys();
  tklog("PLAYLIST: %zu tree path(s) known", keys.size());
  for (const std::string& k : keys)
    if (k.find("Kommander") != std::string::npos) tklog("PLAYLIST:   key '%s'", printable_path(k).c_str());
  for (const std::string& k : keys) {
    size_t cut = k.rfind('\a');
    std::string last = cut == std::string::npos ? k : k.substr(cut + 1);
    if (last == "Results" && k.find(std::string("\a") + kPlFolder + "\a") != std::string::npos) {
      g_pl_treepath = k;
      tklog("PLAYLIST: found '%s'", printable_path(k).c_str());
      return k;
    }
    if (last == kPlFolder && k.find('\a') != std::string::npos) folder = k;
  }
  if (folder.empty()) { tklog("PLAYLIST: no '%s' playlist folder in the tree", kPlFolder); return ""; }
  return folder + "\aResults";
}

static bool structure_path(const std::string& treepath, void* out24) {
  if (!g_browserform) return false;
  memset(out24, 0, 24);
  call_sret2(R(A_bf_getStructPathFromTreePath), g_browserform, (void*)&treepath, out24);
  return !decode_std_string(out24).empty();
}

// The playlist's ContentList (a copy we own one ref of), creating the playlist the first time.
static void* results_list(void* mgr, void* sp24) {
  void* lst = nullptr;
  call_sret2(R(A_ccm_getList), mgr, sp24, &lst);
  if (lst) return lst;
  tklog("PLAYLIST: creating '%s'", decode_std_string(sp24).c_str());
  // createPlaylist returns SmartPtr<ContentList> via x8 (sret): calling it as a plain function
  // let Traktor store through a garbage x8 (SIGBUS, 2026-11-23).
  void* created = nullptr;
  call_sret3(R(A_ccm_createPlaylist), mgr, sp24, 0, &created);
  tklog("PLAYLIST: createPlaylist -> %p", created);
  if (created) ((void (*)(void*))R(A_refcount_release))(created);
  call_sret2(R(A_ccm_getList), mgr, sp24, &lst);
  return lst;
}

static void do_playlist_set(const std::vector<std::string>& paths) {
  if (g_quitting) return;
  const double t0 = CFAbsoluteTimeGetCurrent();
  std::string tp = results_tree_path();
  if (tp.empty()) return;
  alignas(16) char sp[32];
  if (!structure_path(tp, sp)) { tklog("PLAYLIST: no structure path for '%s'", printable_path(tp).c_str()); return; }
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  void* lst = results_list(mgr, sp);
  if (!lst) { tklog("PLAYLIST: could not get or create '%s'", decode_std_string(sp).c_str()); return; }
  g_pl_treepath = tp;
  ((void (*)(void*))R(A_cl_clear))(lst);
  int added = 0, missing = 0;
  g_quiet_paths = true;
  g_list_rows.clear();
  for (const std::string& p : paths) {
    void* track = get_track_by_path(mgr, p.c_str());
    if (!track) { missing++; continue; }
    void* tsp = track;
    ((int (*)(void*, void* const*, int, bool, void*, int))R(A_cl_add))(lst, &tsp, -1, false, nullptr, 0);
    g_list_rows.push_back(p);  // entry <added> of the Results playlist (TK's columns read it)
    added++;
  }
  g_quiet_paths = false;
  void* lsp = lst;
  ((int (*)(void*, void* const*))R(A_ccm_commitList))(mgr, &lsp);
  ((void (*)(void*))R(A_refcount_release))(lst);
  tklog("PLAYLIST: '%s' <- %d track(s), %d not in the collection (committed) in %.0f ms on the main thread",
        decode_std_string(sp).c_str(), added, missing, (CFAbsoluteTimeGetCurrent() - t0) * 1000.0);
}

static void do_list_set(const std::vector<std::string>& paths) {
  g_list_gen++;
  set_band_status("");
  if (paths == g_list_paths) { tklog("LIST: unchanged (%zu), kept", paths.size()); return; }
  g_list_paths = paths;
  do_playlist_set(paths);
}

// (Former in-memory list, kept for reference; no longer called.)
static void do_memlist_set(const std::vector<std::string>& paths) {
  void* list = tk_list();
  if (!list) return;
  // TK re-sends on every click of our node; rebuilding an identical list drops the selection and
  // breaks a drag that has just started — keep it as it is.
  if (paths == g_list_paths) { tklog("LIST: unchanged (%zu), kept", paths.size()); return; }
  g_list_paths = paths;
  // Never change a list Traktor is showing: filling the shown list in place left its rows
  // unselectable (every failing run showed it empty, then filled it; the run that worked showed it
  // already filled). Build a FRESH list and swap it in whole.
  void* old = g_tk_list;
  bool was_showing = tk_list_showing() || g_tk_selected;
  g_tk_list = nullptr;
  list = tk_list();
  if (!list) { g_tk_list = old; return; }
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  ((void (*)(void*))R(A_cl_clear))(list);
  int added = 0, missing = 0;
  g_quiet_paths = true;
  g_list_rows.clear();
  for (const std::string& p : paths) {
    void* track = get_track_by_path(mgr, p.c_str());
    if (!track) { missing++; continue; }
    void* sp = track;  // SmartPtr<Entity const> = raw pointer @0; add() takes its own reference
    ((int (*)(void*, void* const*, int, bool, void*, int))R(A_cl_add))(list, &sp, -1, false, nullptr, 0);
    g_list_rows.push_back(p);  // the list's entry <added> (TK's columns read their values by it)
    added++;
  }
  g_quiet_paths = false;
  tklog("LIST: set %d track(s), %d path(s) not in the collection", added, missing);
  if (was_showing) show_tk_list(g_browserform);
  if (old) ((void (*)(void*))R(A_refcount_release))(old);  // our creation ref; Traktor keeps its own
}

// BrowserForm::treeSelect replacement: a click on OUR node must not reach Traktor's 1300-line
// category switch with an unknown category. Run it as Track Collection (a known-safe state),
// then swap in TK's list and ask TK for fresh results. Everything else passes straight on.
static void (*g_orig_treeSelect)(void*, const void*, long) = nullptr;

// ── Tooltips for the band, through Traktor's own tooltip system ──────────────────────────────
// Traktor shows them only while its [i] button is on, with its own delay and look; a button of
// ours answers here, everything else goes to Traktor's lookup.
static long (*g_orig_getTooltip)(void*, void*, void*, void*) = nullptr;

// A card part's tooltip (title / value fader / amount fader / card), built per call.
static const char* card_tooltip(const void* control) {
  static std::string text;
  for (int i = 0; i < g_nacards; i++) {
    const TKAxisCard* c = &g_acards[i];
    int part = -1;
    for (int k = 0; k < CP_COUNT; k++) if (c->part[k] && c->part[k] == control) part = k;
    if (part < 0) continue;
    const std::string name(c->name);
    switch (part) {
      case CP_NAME:
        text = name + "\n" + (c->desc[0] ? std::string(c->desc) + " " : std::string()) +
               "Click to switch this axis " + (c->active ? "off" : "on") + ".";
        break;
      case CP_VTRACK: case CP_VMARK: case CP_VFILL: case CP_VKNOB:
        text = name + ": target value\nDrag the orange handle: tracks closest to this value rank "
               "first. The coloured fill behind the card shows where the playing track sits.";
        break;
      case CP_STRACK: case CP_SBAR: case CP_SKOUT: case CP_SKNOB:
        text = name + ": amount\nDrag the blue handle: how much this axis counts in the match. "
               "All the way left, it is ignored.";
        break;
      default:
        text = name + "\nThe coloured fill shows where the playing track sits on this axis." +
               (c->desc[0] ? std::string(" ") + c->desc : std::string());
    }
    return text.c_str();
  }
  return nullptr;
}

// Tooltips TK gives the band's buttons (its add-on's, on the spare slots): "Title\nText".
static std::map<std::string, std::string> g_btn_tips;

// TK: <name>'s tooltip. Main thread; 0 = kept.
extern "C" __attribute__((visibility("default"))) int tk_band_set_tooltip(const char* name, const char* text) {
  if (!name || !*name || !text || !pthread_main_np()) return -1;
  g_btn_tips[name] = text;
  return 0;
}

static const char* band_tooltip(const void* control) {
  if (!control) return nullptr;
  if (g_cards_expanded)
    if (const char* t = card_tooltip(control)) return t;
  int b = -1;
  for (int i = 0; i < g_nbtn && b < 0; i++)
    if (g_btn_ctrl[i] == control || (g_btn_tile[i] && g_btn_tile[i] == control)) b = i;
  if (b < 0) {
    if (control == g_source_lbl) return "Matching source\nThe deck whose track the results match.";
    if (control == g_auto_lbl) return "Automation\nClick to cycle: off, play + cue, AutoMix, Auto DJ.";
    return nullptr;
  }
  const char* n = g_btn_name[b];
  auto set = g_btn_tips.find(n);  // set by TK (tk_band_set_tooltip): an add-on's buttons
  if (set != g_btn_tips.end()) return set->second.c_str();
  if (!strcmp(n, "A")) return "Matching source: deck A\nMatch the results against the track on deck A.";
  if (!strcmp(n, "B")) return "Matching source: deck B\nMatch the results against the track on deck B.";
  if (!strcmp(n, "C")) return "Matching source: deck C\nMatch the results against the track on deck C.";
  if (!strcmp(n, "D")) return "Matching source: deck D\nMatch the results against the track on deck D.";
  if (!strcmp(n, "MATCHING")) return "Matching\nShow or hide the matching cards: what the results are matched on, and how much.";
  if (!strcmp(n, "LIST")) return "Results\nThe matching tracks, in the playlist.";
  if (!strcmp(n, "S_SETTINGS")) return "Settings\nKommander's settings: matching, AutoMix, tagging (Discogs, Spotify) and add-ons.";
  if (!strcmp(n, "S_ABOUT")) return "About\nVersion, updates and credits.";
  if (!strcmp(n, "L_SCAN")) return "Library: Scan\nAnalyse the collection's tracks for matching.";
  if (!strcmp(n, "L_HOTCUES")) return "Library: Hotcues\nFind and place hotcues automatically.";
  if (!strcmp(n, "L_TAGGING")) return "Library: Tagging\nLook up and fill in track metadata.";
  if (!strcmp(n, "L_PLAYLISTS")) return "Library: Playlists\nSmart playlists by date, in Traktor.";
  if (!strcmp(n, "L_EXPORT")) return "Library: Export\nExport playlists to USB / other players.";
  if (!strcmp(n, "STATS")) return "Stats\nThe library's metrics and statistics, in place of the playlist.";
  if (!strcmp(n, "AUTO")) return "Automation\nClick to cycle: off, play + cue, AutoMix, Auto DJ.";
  return nullptr;
}

static long tk_getTooltip(void* self, void* form, void* control, void* tooltip) {
  const bool on = gum_memory_is_readable(R(A_hint_tooltips_enabled), 1) &&
                  *(const unsigned char*)R(A_hint_tooltips_enabled) != 0;
  const char* text = (on && tooltip) ? grid_tooltip(control) : nullptr;  // decks: band or not
  if (!text && on && g_band_visible && tooltip) text = band_tooltip(control);
  if (!text) return g_orig_getTooltip(self, form, control, tooltip);
  const char* nl = strchr(text, '\n');
  *(std::string*)tooltip = nl ? std::string(text, (size_t)(nl - text)) : std::string();
  *(std::string*)((char*)tooltip + OFF_TOOLTIP_TEXT) = nl ? std::string(nl + 1) : std::string(text);
  return 1;
}

// Traktor re-runs treeSelect for the CURRENT state on its own (after every deck load, …), with the
// category BrowserForm stored — 1, since our selection runs as Track Collection — and not the path
// "COLLECTION" a real click on Track Collection carries. While our node is selected, such a call is
// a re-select of OUR view: without this, a deck load swapped the collection back in (2026-11-23).
static uint64_t g_tk_select_ns = 0;  // when our node was last clicked

static bool is_reselect_of_ours(const void* path, long cat) {
  (void)path;
  if (!g_tk_selected || (cat & 0xffffffff) != 1) return false;
  // Traktor's own follow-ups: re-selects of the current view (not from the tree), and the Track
  // Collection re-select its handler sends right after we ran it for our node (via the tree).
  bool echo = clock_gettime_nsec_np(CLOCK_MONOTONIC) - g_tk_select_ns < 1500ull * 1000000ull;
  return g_tree_depth == 0 || echo;
}

static void tk_treeSelect(void* self, const void* path, long cat) {
  bool clicked = (cat & 0xffffffff) == TK_TREE_CAT;
  if (clicked || is_reselect_of_ours(path, cat)) {
    tklog("TREE: %s -> TK list", clicked ? "Track Kommander node selected" : "re-select of our view");
    g_tk_selected = true;
    // A click runs Traktor's own treeSelect ONCE, as Track Collection: it sets up the browser
    // state selection and drag need (skipping it left rows unselectable, 2026-11-23). Its async
    // sort job can then put the collection's row mapping on our list — tk_list_heal repairs that.
    // Follow-ups only put our list back: running the handler again would start the race again.
    // A click: empty the playlist first, so the list is blank (not stale) until TK's fresh list
    // lands, and say so in the band; complain if TK never answers.
    if (clicked) {
      do_playlist_set(std::vector<std::string>());
      g_list_paths.clear();  // the next list from TK must not be skipped as "unchanged"
      band_set_visible(true);
      set_band_status("Loading Track Kommander results...");
      unsigned gen = g_list_gen;
      dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 6 * (int64_t)NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        if (g_list_gen == gen && g_band_visible)
          set_band_status(g_tk_problem.empty() ? "No answer from Track Kommander - is it running?"
                                               : g_tk_problem.c_str());
      });
    }
    // Show the Results playlist exactly as if the user had clicked it.
    std::string tp = results_tree_path();
    if (!tp.empty() && g_orig_treeSelect) {
      tklog("TREE: -> playlist '%s'", printable_path(tp).c_str());
      g_orig_treeSelect(self, &tp, 0x11);
    }
    g_tk_selected = false;  // Traktor now shows a normal playlist; its own re-selects carry 0x11
    band_set_visible(true);
    if (clicked) post_json("/uiAction", "{\"action\":\"KOMMANDER\"}");
    return;
  }
  g_tk_selected = false;
  if (g_orig_treeSelect) g_orig_treeSelect(self, path, cat);
  // Traktor re-selecting the Results playlist (after a deck load, …) is still TK's view.
  band_set_visible(!g_pl_treepath.empty() && decode_std_string(path) == g_pl_treepath);
}

// === Library WRITE spike (2026-11-22, fork A): set a track's Comment via Traktor's own
// MediaItem::setComment on the live collection track (base-0 MediaItem from get_track_by_key /
// get_selected_track). Uses a REAL libc++ std::string (same system ABI, as get_track_by_path
// does for URLs) — no hand-rolled SSO. setComment marks the entry modified -> persists on quit.
// MAIN THREAD ONLY. Verify by quitting Traktor and grepping collection.nml for the marker.
static void do_native_libwrite_track(void* mgr, void* track, uint32_t key, const char* text) {
  if (!track) { tklog("LIBWRITE: no track (key=%u)", key); return; }
  std::string cmt(text ? text : "");
  tklog("LIBWRITE: setComment(track=%p key=%u text='%s')", track, key, cmt.c_str());
  ((void (*)(void*, const void*))R(A_mi_setComment))(track, &cmt);
  // Read back the std::string member setComment assigns (this+0xb0) to confirm the setter took
  // on THIS object — distinguishes "setter failed" from "serialized a different/live entry".
  std::string back = decode_std_string((const char*)track + OFF_MI_COMMENT);
  tklog("LIBWRITE: readback this+0x%x = '%s'", OFF_MI_COMMENT, back.c_str());
  // Level-2: commit the (possibly copied) entry back into the collection so it serializes on quit.
  if (mgr) {
    void* sp = track;  // SmartPtr<Entity const> = raw ptr @ off0; commitEntry addRefs internally
    ((void (*)(void*, void*, int))R(A_ccm_commitEntry))(mgr, &sp, 0);
    tklog("LIBWRITE: commitEntry(mgr,&track,0) called — quit Traktor, grep collection.nml (key=%u)", key);
  }
}
static void do_native_libwrite(uint32_t key, const char* text) {
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  do_native_libwrite_track(mgr, get_track_by_key(mgr, key), key, text);
}
static void do_native_libwrite_sel(const char* text) {
  void* mgr = get_collection_mgr();
  if (!mgr) return;
  do_native_libwrite_track(mgr, get_selected_track(mgr), 0, text);
}

// === Playlist Favorites hijack (2026-11-22, fork B): reuse Traktor's own favorites tiles, which
// render real text natively. Set a slot's path to "KOMMANDER\a<label>" so the tile shows <label>;
// only fill EMPTY ("not assigned") slots so the user's real favorites are untouched. Uses a REAL
// libc++ std::string (same ABI) and the VERIFIED decode_std_string — no hand-rolled SSO. Requires
// "Show Playlist Favorites" ON (else the bar isn't built -> fav==null). MAIN THREAD ONLY.
static void* fav_form(void) {
  if (!g_browserform || !gum_memory_is_readable(g_browserform, OFF_BF_SHORTCUTS + 8)) return nullptr;
  void* f = *(void**)((char*)g_browserform + OFF_BF_SHORTCUTS);
  return (f && gum_memory_is_readable(f, OFF_FAV_SLOT0 + 12 * SZ_FAV_SLOT)) ? f : nullptr;
}
static void do_fav_assign(int slot, const char* label) {
  void* fav = fav_form();
  if (!fav) { tklog("FAV: no shortcuts form (is 'Show Playlist Favorites' on + browser built?)"); return; }
  if (slot < 0 || slot >= 12) { tklog("FAV: bad slot %d", slot); return; }
  std::string path = std::string("KOMMANDER\a") + (label ? label : "");
  ((void (*)(void*, int, const void*))R(A_fav_setSlot))(fav, slot, &path);
  ((void (*)(void*))R(A_bf_updateFavorites))(g_browserform);  // refresh tiles + favorites node
  tklog("FAV: slot %d = '%s'", slot, label ? label : "");
}
static void do_fav_strip(void) {
  void* fav = fav_form();
  if (!fav) { tklog("FAV: no shortcuts form (enable 'Show Playlist Favorites')"); return; }
  const char* labels[] = { "A", "B", "C", "D", "SET", "LIB", "AUTO" };
  int placed = 0;
  for (int slot = 0; slot < 12 && placed < 7; slot++) {
    std::string cur = decode_std_string((char*)fav + OFF_FAV_SLOT0 + slot * SZ_FAV_SLOT);
    if (cur.empty()) { do_fav_assign(slot, labels[placed]); placed++; }  // only empty slots
  }
  tklog("FAV: strip placed=%d (empty slots filled)", placed);
}
// Revert our test tiles: set any slot whose path starts with "KOMMANDER" back to empty.
static void do_fav_clear(void) {
  void* fav = fav_form();
  if (!fav) { tklog("FAV: no shortcuts form"); return; }
  std::string empty;
  int cleared = 0;
  for (int slot = 0; slot < 12; slot++) {
    std::string cur = decode_std_string((char*)fav + OFF_FAV_SLOT0 + slot * SZ_FAV_SLOT);
    if (cur.rfind("KOMMANDER", 0) == 0) {
      ((void (*)(void*, int, const void*))R(A_fav_setSlot))(fav, slot, &empty);
      cleared++;
    }
  }
  if (cleared) ((void (*)(void*))R(A_bf_updateFavorites))(g_browserform);
  tklog("FAV: cleared %d KOMMANDER slots", cleared);
}
// Hide the Playlist Favorites bar entirely and reclaim its space (user request). MAIN THREAD.
// Run favclear FIRST (needs the form) then favhide. Clears vismask bit1 + FeatureSet+0x78 so the
// layout stops reserving the strip, flips the pref off (permanent in .tsi), then relayouts.
static void do_fav_hide(void) {
  void* bf = g_browserform;
  if (!bf || !gum_memory_is_readable(bf, OFF_BF_VISMASK + 4)) { tklog("FAVHIDE: no browserform"); return; }
  void* fs = ((void* (*)(void))R(A_featureset_get))();
  if (fs && gum_memory_is_readable((char*)fs + OFF_FEATURESET_FAV, 1)) *((unsigned char*)fs + OFF_FEATURESET_FAV) = 0;
  *(unsigned*)((char*)bf + OFF_BF_VISMASK) &= ~2u;
  void* lm = ((void* (*)(void))R(A_layoutmgr_getInstance))();
  if (lm) ((void (*)(void*, int, int))R(A_layoutmgr_setProperty))(lm, LAYOUTPROP_FAVORITES, 0);
  int h = *(int*)((char*)bf + OFF_BF_HEIGHT);
  ((void (*)(void*, int))R(A_bf_resized))(bf, h);
  tklog("FAVHIDE: favorites hidden (vismask bit1=0, FeatureSet+0x78=0, pref0x37=0, resized h=%d)", h);
}

// === Custom top region (2026-11-22, fork B): carve our own band at the top of the browser by
// pushing the track-list child down, the same way "Show Playlist Favorites" reserves its strip.
// Step 1 (read-only): log candidate BrowserForm child rects to identify the track-list region.
// Step 2: push that child's top down by H (transient until the next relayout; a resized hook makes
// it permanent later). MAIN THREAD ONLY. No drawing yet — this just proves the reservation. ===
static void do_layout_probe(void) {
  void* bf = g_browserform;
  if (!bf || !gum_memory_is_readable(bf, 0x700)) { tklog("LAYOUT: no browserform"); return; }
  // Dense scan: any member pointer whose +0x38 looks like a sane control rect. Flag the big
  // right-side region (the track-list body) we want to push down.
  for (unsigned off = 0x4e0; off <= 0x6c0; off += 8) {
    void* child = *(void**)((char*)bf + off);
    if (!child || ((uintptr_t)child & 7) || !gum_memory_is_readable(child, OFF_CTRL_RECT_L + 16)) continue;
    int* r = (int*)((char*)child + OFF_CTRL_RECT_L);
    int l = r[0], t = r[1], rr = r[2], b = r[3], w = rr - l, h = b - t;
    if (l < -20 || t < -20 || rr > 6000 || b > 6000 || w < 40 || h < 20) continue;  // sane rects only
    const char* tag = (l > 100 && w > 400 && h > 200) ? "  <== LIST BODY?" : "";
    tklog("LAYOUT: off=0x%x child=%p rect={l=%d t=%d r=%d b=%d} (w=%d h=%d)%s", off, child, l, t, rr, b, w, h, tag);
  }
}
static void do_reserve_band(unsigned off, int H) {
  void* bf = g_browserform;
  if (!bf) { tklog("RSV: no browserform"); return; }
  void* child = *(void**)((char*)bf + off);
  if (!child || !gum_memory_is_readable(child, OFF_CTRL_RECT_L + 16)) { tklog("RSV: bad child at 0x%x", off); return; }
  int* r = (int*)((char*)child + OFF_CTRL_RECT_L);
  int nr[4] = { r[0], r[1] + H, r[2], r[3] };
  tklog("RSV: off=0x%x push list down H=%d old={l=%d t=%d r=%d b=%d} new_t=%d", off, H, r[0], r[1], r[2], r[3], nr[1]);
  ((void (*)(void*, void*))R(A_nglctl_setPosition))(child, nr);
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(bf, 0);
}

// Resource-driven form: build a Form the way Traktor does (MasterAreaSwitchL pattern) —
// set the resource context (vtable slot 0x78 with appBase->vtable[0x10]()), create(resId)
// (vtable slot 0x30, loads a .form resource with its controls fully wired incl. fonts),
// override the type to a unique id (avoid the form-map collision), then add it. Live-tunable
// via "nglform <resId>" so we can find a usable resource. MAIN THREAD.
static void do_ngl_form(int resId) {
  if (!g_mainview || !gum_memory_is_readable(g_mainview, OFF_MWVIEW_APPBASE + 8)) { tklog("NGL: no mainview"); return; }
  if (!g_panelmgr || !gum_memory_is_readable(g_panelmgr, 8)) { tklog("NGL: no panelmgr"); return; }
  void* appbase = *(void**)((char*)g_mainview + OFF_MWVIEW_APPBASE);
  if (!appbase || !gum_memory_is_readable(appbase, 8)) { tklog("NGL: no appbase"); return; }
  void* avt = *(void**)appbase;
  if (!gum_memory_is_readable(avt, 0x18)) { tklog("NGL: appbase vtable unreadable"); return; }
  void* ctx = ((void* (*)(void*))(*(void**)((char*)avt + 0x10)))(appbase);  // gui/resource context
  void* form = ((void* (*)(unsigned long))R(A_op_new))(0x400);
  if (!form) { tklog("NGL: form alloc failed"); return; }
  memset(form, 0, 0x400);
  ((void* (*)(void*))R(A_nglform_ctor))(form);
  void* fvt = *(void**)form;
  ((void (*)(void*, void*))(*(void**)((char*)fvt + 0x78)))(form, ctx);        // set resource context
  bool ok = ((bool (*)(void*, unsigned))(*(void**)((char*)fvt + 0x30)))(form, (unsigned)resId);  // create(resId)
  tklog("NGL: nglform resId=0x%x create=%d w=%d h=%d form=%p", resId, (int)ok,
        *(int*)((char*)form + OFF_NGLFORM_W), *(int*)((char*)form + OFF_NGLFORM_H), form);
  // The resource form's controls are fully font-wired. Move them into the VISIBLE
  // BrowserForm (stacked) — a resource-wired control should render its text there.
  if (!g_browserform || !gum_memory_is_readable(g_browserform, 8)) return;
  for (int idx = 0; idx < 16; idx++) {
    void* ctrl = ((void* (*)(void*, unsigned))R(A_nglform_getControl))(form, (unsigned)idx);
    if (!ctrl || !gum_memory_is_readable(ctrl, 8)) continue;
    int y = 2 + g_ngl_row * 22;
    int rect[4] = { 8, y, 320, y + 20 };
    ((void (*)(void*, void*))R(A_nglctl_setPosition))(ctrl, rect);
    ((void (*)(void*, bool))R(A_nglctl_setVisible))(ctrl, true);
    ((void (*)(void*, void*))R(A_nglform_addControl))(g_browserform, ctrl);
    // Try to override with OUR text (works on label-like controls; harmless log otherwise).
    char t[48]; snprintf(t, sizeof(t), "KOMMANDER %d  A B C D", idx);
    ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(ctrl, t);
    tklog("NGL:   moved resCtrl idx=%d ctrl=%p -> browser row=%d (setText)", idx, ctrl, g_ngl_row);
    g_ngl_row++;
  }
  ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
}

// === QML scene-graph probe ====================================================
// Traktor Pro 4 is a QGuiApplication / Qt Quick app (VERIFIED: 0 QApplication
// ctors, QQuickWindow/QQuickItem everywhere), so there is no QWidget tree — the
// cards bar must be injected as QQuickItems into the QQuickWindow's contentItem.
// This dumps the live scene graph (class, geometry) so we can find the browser
// list item and a container to parent our bar onto. Read-only.
static int g_qml_logged = 0;

static double qq_getd(uintptr_t addr, const void* item) {
  return ((double (*)(const void*))R(addr))(item);
}

static void walk_qquick(void* item, int depth) {
  if (!item || depth > 16 || g_qml_logged > 1500 || !looks_like_object(item)) return;
  const char* cn = qobject_classname(item);
  double x = qq_getd(A_qquickitem_x, item), y = qq_getd(A_qquickitem_y, item);
  double w = qq_getd(A_qquickitem_width, item), h = qq_getd(A_qquickitem_height, item);
  g_qml_logged++;
  tklog("  QML%*s%s %p  [%.0f,%.0f %.0fx%.0f]", depth * 2 + 1, "", cn, item, x, y, w, h);
  // childItems() -> QList<QQuickItem*> via x8 (member: `this` in x0).
  unsigned char ql[24]; memset(ql, 0, sizeof(ql));
  call_sret2(R(A_qquickitem_childItems), item, nullptr, ql);
  void** cptr = *(void** const*)(ql + 8);
  long csize = *(const long*)(ql + 0x10);
  if (!cptr || csize < 0 || csize > 8192 || !gum_memory_is_readable(cptr, csize * 8)) return;
  for (long i = 0; i < csize; i++) walk_qquick(cptr[i], depth + 1);
}

static void dump_from_window(void* win) {
  const char* wc = qobject_classname(win);
  tklog("QML: window %s %p", wc, win);
  void* root = ((void* (*)(void*))R(A_qquickwindow_contentItem))(win);
  if (!looks_like_object(root)) { tklog("QML: no contentItem"); return; }
  tklog("QML: contentItem %s %p", qobject_classname(root), root);
  walk_qquick(root, 1);
}

static void do_qml_scene_dump(void) {
  g_qml_logged = 0;
  // Resolve the MAIN UI window from the captured QBrowser item (now in the tree).
  if (!g_qq_window && looks_like_object(g_qbrowser)) {
    void* w = ((void* (*)(const void*))R(A_qquickitem_window))(g_qbrowser);
    if (looks_like_object(w)) { g_qq_window = w; tklog("QML: window via QBrowser=%p -> %s %p",
                                                       g_qbrowser, qobject_classname(w), w); }
  }
  tklog("QML: dumping scene graph pid=%d windows=%d qbrowser=%p", getpid(), g_qq_window_count, g_qbrowser);
  // Preferred: the offscreen QQuickWindows captured from QOffscreenView::onWindowChanged.
  if (g_qq_window_count > 0) {
    for (int i = 0; i < g_qq_window_count; i++) {
      if (!looks_like_object(g_qq_windows[i])) continue;
      tklog("QML === offscreen window[%d] ===", i);
      dump_from_window(g_qq_windows[i]);
    }
    tklog("QML: done (%d items across %d window(s))", g_qml_logged, g_qq_window_count);
    return;
  }
  if (looks_like_object(g_qq_window)) {
    dump_from_window(g_qq_window);
    tklog("QML: done (%d items)", g_qml_logged);
    return;
  }
  // QGuiApplication::focusWindow() returns a single QWindow* (x0, NOT sret) — a
  // cross-check that bypasses the sret QList path.
  void* fw = ((void* (*)(void))R(A_qgui_focusWindow))();
  tklog("QML: focusWindow()=%s %p", looks_like_object(fw) ? qobject_classname(fw) : "(null)", fw);
  if (looks_like_object(fw)) { dump_from_window(fw); tklog("QML: done (%d items)", g_qml_logged); return; }
  // Fallback: enumerate QGuiApplication window lists. allWindows() is more
  // inclusive than topLevelWindows() (render-control windows are not top-level).
  const uintptr_t listfns[2] = { A_qgui_allWindows, A_qgui_topLevelWindows };
  const char* listnames[2] = { "allWindows", "topLevelWindows" };
  for (int f = 0; f < 2; f++) {
    unsigned char wl[24]; memset(wl, 0, sizeof(wl));
    call_sret2(R(listfns[f]), nullptr, nullptr, wl);
    const unsigned long* raw = (const unsigned long*)wl;
    void** wptr = *(void** const*)(wl + 8);
    long wsize = *(const long*)(wl + 0x10);
    tklog("QML: %s raw=%016lx %016lx %016lx  count=%ld", listnames[f], raw[0], raw[1], raw[2], wsize);
    if (!wptr || wsize < 0 || wsize > 64 || !gum_memory_is_readable(wptr, wsize * 8)) continue;
    for (long i = 0; i < wsize; i++) {
      if (!looks_like_object(wptr[i])) { tklog("QML %s[%ld]: unreadable", listnames[f], i); continue; }
      tklog("QML %s[%ld]: %s %p", listnames[f], i, qobject_classname(wptr[i]), wptr[i]);
    }
    if (wsize > 0) {  // dump the scene of the first window found
      for (long i = 0; i < wsize; i++) if (looks_like_object(wptr[i])) { dump_from_window(wptr[i]); break; }
      tklog("QML: done (%d items)", g_qml_logged);
      return;
    }
  }
  tklog("QML: no live window found (lists empty; render hooks also silent)");
}

// === TK -> dylib command channel (mirror of the /show opener) ================
// A loopback TCP listener on 127.0.0.1:g_cmd_port. One newline-terminated text
// command per connection (see the header comment for the grammar). The reply
// ("OK\n"/"ERR ...\n") only reports whether the command parsed; the Traktor C++
// work runs on the MAIN THREAD and its result goes to the log.
static int g_cmd_port = 8767;

static void cmd_reply(int fd, const char* msg) {
  if (fd >= 0 && msg) (void)write(fd, msg, strlen(msg));
}

// App-property registry: native reads/writes/change callbacks of "app.traktor..." paths.
#include "tk_props.mm"
// The whole collection from memory (dump for TK / for checks).
#include "tk_collection.mm"
// The deck's GRID panel: the ALIGN button (align phase with the master deck).
#include "tk_deckgrid.mm"
// Hotcues written into the collection, live (stage 1: by hand).
#include "tk_cuewrite.mm"
// A TK page drawn with Traktor's own labels (native page, stage 1: Spotify).
#include "tk_nlist.mm"
// TK's own columns in Traktor's list, only in our playlist.
#include "tk_columns.mm"
#include "tk_kplaylists.mm"

// Parse and act on one trimmed command line. Dispatches the Traktor work to the
// main queue; never touches Traktor C++ on this (listener) thread.
static void handle_cmd_line(int fd, const char* line) {
  char verb[16] = {0};
  if (sscanf(line, "%15s", verb) != 1) { cmd_reply(fd, "ERR empty\n"); return; }
  if (strcmp(verb, "load") == 0) {
    int deck = -1; unsigned key = 0;
    if (sscanf(line, "%*s %d %u", &deck, &key) != 2 || deck < 0 || deck > 15) {
      cmd_reply(fd, "ERR usage: load <deck> <key>\n");
      return;
    }
    tklog("CMD load deck=%d key=%u", deck, key);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_load(deck, key); });
  } else if (strcmp(verb, "read") == 0) {
    unsigned key = 0;
    if (sscanf(line, "%*s %u", &key) != 1) {
      cmd_reply(fd, "ERR usage: read <key>\n");
      return;
    }
    tklog("CMD read key=%u", key);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_read(key); });
  } else if (strcmp(verb, "loadsel") == 0) {
    int deck = -1;
    if (sscanf(line, "%*s %d", &deck) != 1 || deck < 0 || deck > 15) {
      cmd_reply(fd, "ERR usage: loadsel <deck>\n");
      return;
    }
    tklog("CMD loadsel deck=%d", deck);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_load_sel(deck); });
  } else if (strcmp(verb, "readsel") == 0) {
    tklog("CMD readsel");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_read_sel(); });
  } else if (strcmp(verb, "prop") == 0) {
    handle_prop_cmd(fd, line);
  } else if (strcmp(verb, "audio") == 0) {
    handle_audio_cmd(fd, line);  // "audio list" / "audio set <Traktor device name>"
  } else if (strcmp(verb, "bandhide") == 0) {
    // "bandhide <BUTTON> <0|1>" — leave a band button out (TK: the add-on's page without it).
    char name[24] = {0}; int hide = 1;
    if (sscanf(line, "%*s %23s %d", name, &hide) < 1) { cmd_reply(fd, "ERR usage: bandhide <BUTTON> <0|1>\n"); return; }
    cmd_reply(fd, "OK\n");
    std::string nm(name);
    dispatch_async(dispatch_get_main_queue(), ^{
      for (int i = 0; i < g_nbtn; i++) if (nm == g_btn_name[i]) g_btn_hidden[i] = hide != 0;
      band_place_buttons();
      if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
    });
  } else if (strcmp(verb, "treeicon") == 0) {
    int icon = -1;
    if (sscanf(line, "%*s %d", &icon) != 1 || icon < 0 || icon > 4096) { cmd_reply(fd, "ERR usage: treeicon <id>\n"); return; }
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ g_tree_icon = icon; set_tree_icon(icon); });
  } else if (strcmp(verb, "bandfont") == 0) {
    int on = 0, off = 0;
    if (sscanf(line, "%*s %d %d", &on, &off) != 2) { cmd_reply(fd, "ERR usage: bandfont <on> <off>\n"); return; }
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ g_font_on = on; g_font_off = off; apply_deck_sel(); tklog("BTN: fonts on=%d off=%d", on, off); });
  } else if (strcmp(verb, "colldump") == 0) {
    handle_colldump_cmd(fd, line);
  } else if (strcmp(verb, "pldump") == 0) {
    handle_pldump_cmd(fd, line);
  } else if (strcmp(verb, "cuedump") == 0) {
    handle_cuedump_cmd(fd, line);
  } else if (strcmp(verb, "load_path") == 0) {
    // "load_path <deck> <path>": <path> is the rest of the line (may hold spaces).
    int deck = -1, consumed = 0;
    if (sscanf(line, "%*s %d %n", &deck, &consumed) < 1 || deck < 0 || deck > 15 || consumed <= 0) {
      cmd_reply(fd, "ERR usage: load_path <deck> <path>\n");
      return;
    }
    const char* path = line + consumed;
    while (*path == ' ') path++;
    if (!*path) { cmd_reply(fd, "ERR usage: load_path <deck> <path>\n"); return; }
    char* pcopy = strdup(path);  // the main-queue block outlives this stack frame
    if (!pcopy) { cmd_reply(fd, "ERR out of memory\n"); return; }
    tklog("CMD load_path deck=%d path=%s", deck, pcopy);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_load_path(deck, pcopy); free(pcopy); });
  } else if (strcmp(verb, "read_path") == 0) {
    // "read_path <path>": <path> is the rest of the line after the verb.
    const char* path = line;
    while (*path && *path != ' ') path++;  // skip verb
    while (*path == ' ') path++;           // skip spaces
    if (!*path) { cmd_reply(fd, "ERR usage: read_path <path>\n"); return; }
    char* pcopy = strdup(path);
    if (!pcopy) { cmd_reply(fd, "ERR out of memory\n"); return; }
    tklog("CMD read_path path=%s", pcopy);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_read_path(pcopy); free(pcopy); });
  } else if (strcmp(verb, "nativeui") == 0) {
    // Walk Traktor's widget tree + (guarded) inject the native cards bar. DRAFT.
    tklog("CMD nativeui");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_ui_inject(); });
  } else if (strcmp(verb, "nglbar") == 0) {
    // "nglbar [fontid]" — add a native NI::NGL label row (Traktor's toolkit). Optional
    // font id lets us try many fonts live; each call stacks a new row.
    const char* a = line; while (*a && *a != ' ') a++; while (*a == ' ') a++;
    int fid = (*a) ? atoi(a) : (g_fontid > 0 ? g_fontid : 36);
    tklog("CMD nglbar fontid=%d", fid);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_ngl_label(fid); });
  } else if (strcmp(verb, "nglform") == 0) {
    // "nglform <resId>" — build a resource-driven NI::NGL::Form (real controls w/ text).
    const char* a = line; while (*a && *a != ' ') a++; while (*a == ' ') a++;
    int resId = (*a) ? (int)strtol(a, nullptr, 0) : 0x11;
    tklog("CMD nglform resId=0x%x", resId);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_ngl_form(resId); });
  } else if (strcmp(verb, "buttonstrip") == 0) {
    // "buttonstrip [fontid]" — render A/B/C/D + action buttons across the top of the list.
    const char* a = line; while (*a && *a != ' ') a++; while (*a == ' ') a++;
    int fid = (*a) ? atoi(a) : 0;
    tklog("CMD buttonstrip fontid=%d", fid);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_button_strip(fid); });
  } else if (strcmp(verb, "card") == 0) {
    // "card <x> <y> <w> <h>" — draw a custom card (bg+border+bar+title) in the band.
    int x=0,y=0,w=0,h=0;
    if (sscanf(line, "%*s %d %d %d %d", &x,&y,&w,&h) != 4 || w<=0 || h<=0) {
      cmd_reply(fd, "ERR usage: card <x> <y> <w> <h>\n"); return;
    }
    tklog("CMD card %d %d %d %d", x,y,w,h);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_card(x,y,w,h); });
  } else if (strcmp(verb, "libwrite") == 0) {
    // "libwrite <key> <text>": set Comment on collection track <key> (text = rest of line).
    unsigned key = 0; int consumed = 0;
    if (sscanf(line, "%*s %u %n", &key, &consumed) < 1 || key == 0 || consumed <= 0) {
      cmd_reply(fd, "ERR usage: libwrite <key> <text>\n"); return;
    }
    const char* text = line + consumed;
    while (*text == ' ') text++;
    if (!*text) { cmd_reply(fd, "ERR usage: libwrite <key> <text>\n"); return; }
    char* tcopy = strdup(text);
    if (!tcopy) { cmd_reply(fd, "ERR out of memory\n"); return; }
    tklog("CMD libwrite key=%u text=%s", key, tcopy);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_libwrite(key, tcopy); free(tcopy); });
  } else if (strcmp(verb, "libwritesel") == 0) {
    // "libwritesel <text>": set Comment on the SELECTED track (text = rest of line).
    const char* text = line; while (*text && *text != ' ') text++; while (*text == ' ') text++;
    if (!*text) { cmd_reply(fd, "ERR usage: libwritesel <text>\n"); return; }
    char* tcopy = strdup(text);
    if (!tcopy) { cmd_reply(fd, "ERR out of memory\n"); return; }
    tklog("CMD libwritesel text=%s", tcopy);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_native_libwrite_sel(tcopy); free(tcopy); });
  } else if (strcmp(verb, "favstrip") == 0) {
    // "favstrip" — fill the first empty Playlist-Favorites slots with A/B/C/D/SET/LIB/AUTO.
    tklog("CMD favstrip");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_fav_strip(); });
  } else if (strcmp(verb, "favclear") == 0) {
    // "favclear" — revert our KOMMANDER test tiles to empty slots.
    tklog("CMD favclear");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_fav_clear(); });
  } else if (strcmp(verb, "favhide") == 0) {
    // "favhide" — hide the Playlist Favorites bar entirely + reclaim its space.
    tklog("CMD favhide");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_fav_hide(); });
  } else if (strcmp(verb, "favassign") == 0) {
    // "favassign <slot> <label>": set one favorites tile (label = rest of line).
    int slot = -1, consumed = 0;
    if (sscanf(line, "%*s %d %n", &slot, &consumed) < 1 || consumed <= 0) {
      cmd_reply(fd, "ERR usage: favassign <slot> <label>\n"); return;
    }
    const char* label = line + consumed;
    while (*label == ' ') label++;
    if (!*label) { cmd_reply(fd, "ERR usage: favassign <slot> <label>\n"); return; }
    char* lcopy = strdup(label);
    if (!lcopy) { cmd_reply(fd, "ERR out of memory\n"); return; }
    tklog("CMD favassign slot=%d label=%s", slot, lcopy);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_fav_assign(slot, lcopy); free(lcopy); });
  } else if (strcmp(verb, "deck") == 0) {
    // "deck <A-D|->" — highlight TK's source deck in the band.
    char d = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %c", &d) != 1 || !((d >= 'A' && d <= 'D') || d == '-')) {
      cmd_reply(fd, "ERR usage: deck <A-D|->\n"); return;
    }
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ g_deck_sel = (d == '-') ? 0 : d; apply_deck_sel(); });
  } else if (strcmp(verb, "auto") == 0) {
    // "auto <off|play_cue|automix|auto> <0|1>" — TK's automation mode on the AUTO button.
    char mode[16] = {0}; int busy = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %15s %d", mode, &busy) < 1) {
      cmd_reply(fd, "ERR usage: auto <mode> <0|1>\n"); return;
    }
    const char* text = !strcmp(mode, "play_cue") ? "Play + Cue" : !strcmp(mode, "automix") ? "AutoMix"
                     : !strcmp(mode, "auto") ? "Auto DJ" : !strcmp(mode, "off") ? "Off" : nullptr;
    if (!text) { cmd_reply(fd, "ERR unknown mode\n"); return; }
    // Like every band button (User, 2026-11-24): off = the grey tile, on = Traktor blue; a running
    // Auto DJ transition = orange.
    unsigned bg = !strcmp(mode, "off") ? 0u : busy ? kBtnBusyBg : kBtnSelectedBg;
    cmd_reply(fd, "OK\n");
    // Off shows the headphones too: the text says "Off" (the OFF picture would read "OFF Off").
    unsigned pic = !strcmp(mode, "automix") ? 133u : !strcmp(mode, "auto") ? 680u : 435u;
    const std::string mode_s(mode);
    dispatch_async(dispatch_get_main_queue(), ^{
      strlcpy(g_auto_text, text, sizeof(g_auto_text)); g_auto_bg = bg; g_auto_pic = pic;
      strlcpy(g_auto_mode, mode_s.c_str(), sizeof(g_auto_mode));
      apply_auto_state();
      tklog("BTN: AUTO -> %s%s", text, bg == kBtnBusyBg ? " (transition)" : "");
    });
  } else if (strcmp(verb, "fontprobe") == 0) {
    int first = 0, count = 0;
    sscanf(strstr(line, verb) + strlen(verb), " %d %d", &first, &count);
    if (count < 0) count = 0;
    if (count > 200) count = 200;
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_fontprobe(first, count); });
  } else if (strcmp(verb, "picgrid") == 0) {
    // "picgrid <id> [<id> ...]" (max 48) — show those picture ids in a grid over the band area,
    // each labelled with its id; "picgrid 0" hides it. Icon hunting for the band buttons.
    std::vector<unsigned> ids;
    const char* p = strstr(line, verb) + strlen(verb);
    unsigned v; int used = 0;
    while (ids.size() < 48 && sscanf(p, " %u%n", &v, &used) == 1) { ids.push_back(v); p += used; }
    cmd_reply(fd, "OK\n");
    auto* idsp = new std::vector<unsigned>(ids);
    dispatch_async(dispatch_get_main_queue(), ^{ do_picgrid(*idsp); delete idsp; });
  } else if (strcmp(verb, "picframe") == 0) {
    // "picframe <n>" — every picture-grid cell shows frame n of its picture.
    unsigned fr = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %u", &fr) != 1) { cmd_reply(fd, "ERR usage: picframe <n>\n"); return; }
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{
      for (int i = 0; i < 48; i++) if (g_pic_cells[i][0]) ((void (*)(void*, unsigned))R(A_ngllabel_setPictureIndex))(g_pic_cells[i][0], fr);
      if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
    });
  } else if (strcmp(verb, "picmode") == 0) {
    // "picmode <n>" — LabelControl draw mode for the picture grid (find the one that shows pictures).
    int m = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %d", &m) != 1) { cmd_reply(fd, "ERR usage: picmode <n>\n"); return; }
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{
      g_pic_mode = m;
      for (int i = 0; i < 48; i++) if (g_pic_cells[i][0]) ((void (*)(void*, int))R(A_ngllabel_setDrawMode))(g_pic_cells[i][0], m);
      if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
      tklog("PICGRID: draw mode %d", m);
    });
  } else if (strcmp(verb, "btnframe") == 0) {
    // "btnframe <BUTTON> <n>" — which frame (state) of a multi-frame picture a band button shows.
    char name[16] = {0}; unsigned fr = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %15s %u", name, &fr) != 2) { cmd_reply(fd, "ERR usage: btnframe <BUTTON> <n>\n"); return; }
    cmd_reply(fd, "OK\n");
    std::string nm(name);
    dispatch_async(dispatch_get_main_queue(), ^{
      for (int i = 0; i < g_nbtn; i++) if (nm == g_btn_name[i])
        ((void (*)(void*, unsigned))R(A_ngllabel_setPictureIndex))(g_btn_ctrl[i], fr);
      if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
    });
  } else if (strcmp(verb, "btnpic") == 0) {
    // "btnpic <BUTTON> <id>" — put picture <id> on a band button (0 = text again).
    char name[16] = {0}; unsigned pid = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %15s %u", name, &pid) != 2) { cmd_reply(fd, "ERR usage: btnpic <BUTTON> <id>\n"); return; }
    cmd_reply(fd, "OK\n");
    std::string nm(name);
    dispatch_async(dispatch_get_main_queue(), ^{
      for (int i = 0; i < g_nbtn; i++) if (nm == g_btn_name[i]) {
        ((void (*)(void*, const char*))R(A_ngllabel_setText_cstr))(g_btn_ctrl[i], pid ? "" : g_btn_name[i]);
        ((void (*)(void*, unsigned))R(A_ngllabel_setPictureID))(g_btn_ctrl[i], pid);
      }
      if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
      tklog("BTN: %s picture %u", nm.c_str(), pid);
    });
  } else if (strcmp(verb, "pyexec") == 0) {
    // "pyexec <file.py>" — run a Python file inside Traktor (embedded interpreter, main thread).
    const char* p = strstr(line, verb) + strlen(verb);
    while (*p == ' ') p++;
    if (!*p) { cmd_reply(fd, "ERR usage: pyexec <file.py>\n"); return; }
    std::string path(p);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_pyexec(path); });
  } else if (strcmp(verb, "btnstyle") == 0) {
    // "btnstyle <bgHex> <hAlign> <vAlign>" — restyle the band buttons live.
    unsigned bg = 0; int h = 0, v = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %x %d %d", &bg, &h, &v) != 3) {
      cmd_reply(fd, "ERR usage: btnstyle <bgHex> <hAlign> <vAlign>\n"); return;
    }
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{
      g_btn_bg = bg; g_btn_halign = h; g_btn_valign = v;
      for (int i = 0; i < g_nbtn; i++) style_button(g_btn_ctrl[i], bg, h, v);
      apply_deck_sel();
      for (int i = 0; i < g_ncards; i++) g_cards[i].bg = bg;
      if (g_browserform) ((void (*)(void*, unsigned))R(A_nglform_invalidate))(g_browserform, 0);
      tklog("BTN: style bg=%08x h=%d v=%d", bg, h, v);
    });
  } else if (strcmp(verb, "treelist") == 0) {
    // "treelist" — log the left tree's root nodes (label, category, path).
    tklog("CMD treelist");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_tree_list(); });
  } else if (strcmp(verb, "treeadd") == 0) {
    // "treeadd [label]" — add the Track Kommander root (and keep it across tree rebuilds).
    const char* label = strstr(line, verb) + strlen(verb);
    while (*label == ' ') label++;
    if (*label) strlcpy(g_tk_node_label, label, sizeof(g_tk_node_label));
    tklog("CMD treeadd '%s'", g_tk_node_label);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ g_tree_auto = true; do_tree_add(); do_tree_list(); });
  } else if (strcmp(verb, "treedel") == 0) {
    // "treedel <catHex>" — remove every root of that category (e.g. Beatport), now and after rebuilds.
    unsigned cat = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %x", &cat) != 1 || cat == TK_TREE_CAT) {
      cmd_reply(fd, "ERR usage: treedel <catHex>\n"); return;
    }
    tklog("CMD treedel 0x%x", cat);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{
      bool known = false;
      for (int i = 0; i < g_ntree_delcats; i++) if (g_tree_delcats[i] == cat) known = true;
      if (!known && g_ntree_delcats < 8) g_tree_delcats[g_ntree_delcats++] = cat;
      g_tree_auto = true;
      tklog("TREE: deleted %d node(s) of cat 0x%x", do_tree_del(cat), cat);
      do_tree_list();
    });
  } else if (strcmp(verb, "treeprobe") == 0) {
    // "treeprobe" — log the root nodes of every DataSetModel seen so far.
    tklog("CMD treeprobe");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_tree_probe(); });
  } else if (strcmp(verb, "gridprobe") == 0) {
    // "gridprobe" — log each deck GRID panel's controls (read-only; results in the log).
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ grid_probe_all(); });
  } else if (strcmp(verb, "cuewrite") == 0) {
    handle_cuewrite_cmd(fd, line);  // "cuewrite <slot> <start_ms> <type> <name|-> <path>"
  } else if (strcmp(verb, "colprobe") == 0) {
    cmd_reply(fd, "OK\n");  // read-only: the list's visible columns (results in the log)
    dispatch_async(dispatch_get_main_queue(), ^{ col_probe(); });
  } else if (strcmp(verb, "gridstyle") == 0) {
    // "gridstyle <fontID> <width>" — the decks' ALIGN buttons, live.
    int font = 0, width = 0;
    if (sscanf(strstr(line, verb) + strlen(verb), " %d %d", &font, &width) != 2) {
      cmd_reply(fd, "ERR usage: gridstyle <fontID> <width>\n"); return;
    }
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ grid_style(font, width); });
  } else if (strcmp(verb, "layoutprobe") == 0) {
    // "layoutprobe" — log BrowserForm child rects to identify the track-list region.
    tklog("CMD layoutprobe");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_layout_probe(); });
  } else if (strcmp(verb, "reserveband") == 0) {
    // "reserveband <childOffHex> <H>": push that child down by H pixels to carve a top band.
    unsigned off = 0; int H = 0;
    if (sscanf(line, "%*s %x %d", &off, &H) != 2 || off == 0 || H <= 0) {
      cmd_reply(fd, "ERR usage: reserveband <childOffHex> <H>\n"); return;
    }
    tklog("CMD reserveband off=0x%x H=%d", off, H);
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_reserve_band(off, H); });
  } else if (strcmp(verb, "qmlui") == 0) {
    // Dump the QML scene graph (Traktor is a Qt Quick app). Read-only probe.
    tklog("CMD qmlui");
    cmd_reply(fd, "OK\n");
    dispatch_async(dispatch_get_main_queue(), ^{ do_qml_scene_dump(); });
  } else {
    cmd_reply(fd, "ERR unknown command\n");
  }
}

// Read one line (bounded) from fd, trim trailing CR/LF/space, hand to the parser.
// 1 KiB holds "load_path <deck> <path>" for deep file paths; longer is truncated.
// The `list` command: read the rest of the connection (the sender shuts down its write side),
// one path per line, then rebuild TK's ContentList on the main queue. Bounded: 4 MB, 5000 paths.
static void handle_list_conn(int fd, const char* first, size_t first_len, bool cards) {
  std::string all(first, first_len);
  char chunk[4096];
  while (all.size() < (4u << 20)) {
    ssize_t n = read(fd, chunk, sizeof(chunk));
    if (n <= 0) break;
    all.append(chunk, (size_t)n);
  }
  auto* paths = new std::vector<std::string>();
  size_t pos = all.find('\n');  // skip the "list" line
  while (pos != std::string::npos && paths->size() < 5000) {
    size_t next = all.find('\n', pos + 1);
    std::string p = all.substr(pos + 1, (next == std::string::npos ? all.size() : next) - pos - 1);
    while (!p.empty() && (p.back() == '\r' || p.back() == ' ')) p.pop_back();
    if (!p.empty()) paths->push_back(p);
    pos = next;
  }
  tklog("CMD list: %zu path(s)", paths->size());
  char reply[48];
  snprintf(reply, sizeof(reply), "OK %zu\n", paths->size());
  cmd_reply(fd, reply);
  if (cards) dispatch_async(dispatch_get_main_queue(), ^{ do_cards_set(*paths); delete paths; });
  else dispatch_async(dispatch_get_main_queue(), ^{ do_list_set(*paths); delete paths; });
}

static void handle_cmd_conn(int fd) {
  char buf[1024];
  size_t off = 0;
  while (off < sizeof(buf) - 1) {
    ssize_t n = read(fd, buf + off, sizeof(buf) - 1 - off);
    if (n <= 0) break;
    off += (size_t)n;
    if (memchr(buf, '\n', off)) break;
  }
  buf[off] = '\0';
  // "list\n<path>\n<path>\n…" up to EOF: TK's result list, too long for one 1024-byte line.
  if (strncmp(buf, "list", 4) == 0 && (buf[4] == '\n' || buf[4] == '\r')) {
    handle_list_conn(fd, buf, off, false);
    return;
  }
  if (strncmp(buf, "cards", 5) == 0 && (buf[5] == '\n' || buf[5] == '\r')) {
    handle_list_conn(fd, buf, off, true);
    return;
  }
  while (off && (buf[off - 1] == '\n' || buf[off - 1] == '\r' || buf[off - 1] == ' '))
    buf[--off] = '\0';
  if (off == 0) { cmd_reply(fd, "ERR empty\n"); return; }
  handle_cmd_line(fd, buf);
}

static void* cmd_listener(void*) {
  int srv = socket(AF_INET, SOCK_STREAM, 0);
  if (srv < 0) { tklog("CMD: socket() failed — command channel off"); return nullptr; }
  int one = 1;
  setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons((uint16_t)g_cmd_port);
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);  // 127.0.0.1 only — never the network
  if (bind(srv, (struct sockaddr*)&addr, sizeof(addr)) != 0) {
    tklog("CMD: bind 127.0.0.1:%d failed (port in use?) — command channel off", g_cmd_port);
    close(srv);
    return nullptr;
  }
  if (listen(srv, 16) != 0) {
    tklog("CMD: listen on 127.0.0.1:%d failed — command channel off", g_cmd_port);
    close(srv);
    return nullptr;
  }
  tklog("CMD: listening on 127.0.0.1:%d", g_cmd_port);
  for (;;) {
    int fd = accept(srv, nullptr, nullptr);
    if (fd < 0) {
      if (errno == EINTR) continue;
      break;
    }
    struct timeval tv = { 2, 0 };  // a client that stops mid-line must not wedge accept
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    handle_cmd_conn(fd);
    close(fd);
  }
  close(srv);
  tklog("CMD: listener stopped");
  return nullptr;
}

// "Traktor Pro 4 TK.app" (make_slim_app.sh) loads libtk by itself, also from the Finder or the
// Dock, where the launch scripts' environment (TK_EMBED, TK_REPO, TK_DYLIB_LOG …) is missing:
// those come from tk-embed.conf, KEY=VALUE per line. A variable already set (a launch script)
// wins. Its place: Contents/SharedSupport/, where the app's signature seals it; apps built before
// 2026-11-25 have it directly in Contents/ (written after signing, outside the seal).
static std::string user_home(void) {  // "" when unknown
  const char* home = getenv("HOME");
  if (!home || !*home) {
    const struct passwd* pw = getpwuid(getuid());
    home = pw ? pw->pw_dir : nullptr;
  }
  return home ? std::string(home) : std::string();
}

static void load_embed_conf(const char* exe) {
  std::string dir(exe);
  const size_t slash = dir.rfind('/');
  if (slash == std::string::npos) return;
  dir = dir.substr(0, slash + 1);  // Contents/MacOS/: code only
  FILE* f = nullptr;
  for (const char* rel : {"../SharedSupport/tk-embed.conf", "../tk-embed.conf"}) {
    if ((f = fopen((dir + rel).c_str(), "r"))) break;
  }
  if (!f) return;
  char line[1024];
  while (fgets(line, sizeof(line), f)) {
    char* eq = strchr(line, '=');
    if (!eq || line[0] == '#') continue;
    *eq = '\0';
    char* value = eq + 1;
    value[strcspn(value, "\r\n")] = '\0';
    if (strncmp(line, "TK_", 3) != 0) continue;  // only TK's own settings
    // "~/…" is the user's home: the web builder's default repo folder is ~/GitHub/TraktorKontrol
    // (a page never knows the user's home folder; User, 2026-11-25).
    std::string v(value);
    if (v.compare(0, 2, "~/") == 0) {
      const std::string home = user_home();
      if (!home.empty()) v = home + v.substr(1);
    }
    setenv(line, v.c_str(), 0);
  }
  fclose(f);
}

// The release's "Traktor Pro 4 TK.app" carries TK's kit (make_kit.sh: Python, TK's packages and
// source) in Contents/SharedSupport/tk, made on the user's Mac by the installer (re/dmg/). The Plus
// release is the same with its add-on beside the kit, in SharedSupport/tk-addon; nothing is looked
// for anywhere else (User, 2026-11-26). A checkout named in tk-embed.conf (TK_REPO) wins: development.
static void find_kit_in_app(const char* exe) {
  if (getenv("TK_REPO")) return;  // a checkout (tk-embed.conf or a launch script)
  std::string tk(exe);
  const size_t slash = tk.rfind('/');
  if (slash == std::string::npos) return;
  tk = tk.substr(0, slash) + "/../SharedSupport/tk";  // from Contents/MacOS/
  if (access((tk + "/KIT-VERSION").c_str(), R_OK) != 0) {
    // The band then says "Track Kommander not found at <this place>" (no such file to run).
    setenv("TK_EMBED", (tk + "/traktor-native/re/embed_tk.py").c_str(), 0);
    tklog("KIT: none in this app (%s)", tk.c_str());
    return;
  }
  setenv("TK_REPO", tk.c_str(), 0);
  setenv("TK_PYTHONHOME", (tk + "/python").c_str(), 0);
  setenv("TK_EMBED", (tk + "/traktor-native/re/embed_tk.py").c_str(), 0);
  // The Plus release is one installer: its add-on sits beside the kit (make_release_dmg.sh --addon).
  const std::string addon = tk.substr(0, tk.size() - 2) + "tk-addon";
  const bool with_addon = access((addon + "/manifest.json").c_str(), R_OK) == 0;
  if (with_addon) setenv("TK_ADDON_DIR", addon.c_str(), 0);
  tklog("KIT: %s%s", tk.c_str(), with_addon ? " (with an add-on)" : "");
}

__attribute__((constructor))
static void tk_init(void) {
  // Only inside Traktor: a process started from Traktor inherits DYLD_INSERT_LIBRARIES, and libtk
  // in it would hook Traktor's addresses in a binary that is not Traktor (2026-11-24: hotcue
  // workers came up as whole Traktors). Anything else: no hooks, no threads, no command port.
  char exe[PATH_MAX]; uint32_t exe_len = sizeof(exe);
  if (_NSGetExecutablePath(exe, &exe_len) != 0 || !strstr(exe, "/Contents/MacOS/Traktor Pro 4")) {
    tklog("=== libtk loaded into %s (pid %d): not Traktor, inactive ===", exe_len <= sizeof(exe) ? exe : "?", getpid());
    return;
  }
  load_embed_conf(exe);  // the slim app's own settings (a Finder launch has no environment)
  find_kit_in_app(exe);  // no checkout named: TK's kit sealed inside this app (the release)
  [[NSNotificationCenter defaultCenter] addObserverForName:NSApplicationWillTerminateNotification
                                                    object:nil queue:nil usingBlock:^(NSNotification*) {
    g_quitting = true;
    tklog("QUIT: Traktor is quitting: libtk stops touching its controls");
  }];
  g_slide = main_exe_slide();
  if (getenv("TK_TREE") && strcmp(getenv("TK_TREE"), "0") == 0) g_tree_auto = false;

  const char* p = getenv("TK_SHOW_PORT");
  if (p && *p) { int v = atoi(p); if (v > 0 && v < 65536) g_port = v; }
  const char* m = getenv("TK_SHOW_MATCH");
  if (m && *m) {
    std::string lc = to_lower(std::string(m));
    strlcpy(g_match, lc.c_str(), sizeof(g_match));
  }
  const char* cp = getenv("TK_CMD_PORT");
  if (cp && *cp) { int v = atoi(cp); if (v > 0 && v < 65536) g_cmd_port = v; }
  if (g_cmd_port == 8766) {  // reserved by another local app; refuse and fall back
    tklog("CMD: port 8766 is reserved — using 8767 instead");
    g_cmd_port = 8767;
  }

  tklog("=== libtk (opener) Traktor %s pid=%d slide=0x%lx port=%d match='%s' ===",
        TK_TRAKTOR_VERSION, getpid(), (long)g_slide, g_port, g_match);

  gum_init_embedded();
  GumInterceptor* it = gum_interceptor_obtain();
  GumInvocationListener* l = gum_make_call_listener(on_enter, on_leave, nullptr, nullptr);

  gum_interceptor_begin_transaction(it);
  GumAttachOptions o1; memset(&o1, 0, sizeof(o1)); o1.listener_function_data = (gpointer)HK_TREESEL;
  GumAttachReturn r1 = gum_interceptor_attach(it, R(A_bf_treeSelect), l, &o1);
  GumAttachOptions o2; memset(&o2, 0, sizeof(o2)); o2.listener_function_data = (gpointer)HK_VALUEFORTRACK;
  GumAttachReturn r2 = gum_interceptor_attach(it, R(A_dsm_valueForTrack), l, &o2);
  GumAttachOptions o3; memset(&o3, 0, sizeof(o3)); o3.listener_function_data = (gpointer)HK_ROWCOUNT;
  GumAttachReturn r3 = gum_interceptor_attach(it, R(A_dsm_rowCount), l, &o3);
  GumAttachOptions o4; memset(&o4, 0, sizeof(o4)); o4.listener_function_data = (gpointer)HK_DATAQT;
  GumAttachReturn r4 = gum_interceptor_attach(it, R(A_dsm_data), l, &o4);
  GumAttachOptions o5; memset(&o5, 0, sizeof(o5)); o5.listener_function_data = (gpointer)HK_LOADDECK;
  GumAttachReturn r5 = gum_interceptor_attach(it, R(A_loadDeck), l, &o5);
  GumAttachOptions o6; memset(&o6, 0, sizeof(o6)); o6.listener_function_data = (gpointer)HK_POLISH;
  GumAttachReturn r6 = gum_interceptor_attach(it, R(A_qqrc_sync), l, &o6);
  GumAttachOptions o7; memset(&o7, 0, sizeof(o7)); o7.listener_function_data = (gpointer)HK_POLISH;
  GumAttachReturn r7 = gum_interceptor_attach(it, R(A_qqrc_render), l, &o7);
  GumAttachOptions o8; memset(&o8, 0, sizeof(o8)); o8.listener_function_data = (gpointer)HK_SCENE;
  GumAttachReturn r8 = gum_interceptor_attach(it, R(A_qqwin_syncSceneGraph), l, &o8);
  GumAttachOptions o9; memset(&o9, 0, sizeof(o9)); o9.listener_function_data = (gpointer)HK_SCENE;
  GumAttachReturn r9 = gum_interceptor_attach(it, R(A_qqwin_renderSceneGraph), l, &o9);
  GumAttachOptions o10; memset(&o10, 0, sizeof(o10)); o10.listener_function_data = (gpointer)HK_ACTIVATE;
  GumAttachReturn r10 = gum_interceptor_attach(it, R(A_qmeta_activate4), l, &o10);
  GumAttachOptions o11; memset(&o11, 0, sizeof(o11)); o11.listener_function_data = (gpointer)HK_ACTIVATE;
  GumAttachReturn r11 = gum_interceptor_attach(it, R(A_qmeta_activate3), l, &o11);
  GumAttachOptions o12; memset(&o12, 0, sizeof(o12)); o12.listener_function_data = (gpointer)HK_ACTIVATE;
  GumAttachReturn r12 = gum_interceptor_attach(it, R(A_qmeta_activate3b), l, &o12);
  GumAttachOptions o13; memset(&o13, 0, sizeof(o13)); o13.listener_function_data = (gpointer)HK_ITEM;
  GumAttachReturn r13 = gum_interceptor_attach(it, R(A_qquickitem_update), l, &o13);
  GumAttachOptions o14; memset(&o14, 0, sizeof(o14)); o14.listener_function_data = (gpointer)HK_ITEM;
  GumAttachReturn r14 = gum_interceptor_attach(it, R(A_qquickitem_polish), l, &o14);
  GumAttachOptions o15; memset(&o15, 0, sizeof(o15)); o15.listener_function_data = (gpointer)HK_OSVIEW;
  GumAttachReturn r15 = gum_interceptor_attach(it, R(A_osview_onWindowChanged), l, &o15);
  GumAttachOptions o16; memset(&o16, 0, sizeof(o16)); o16.listener_function_data = (gpointer)HK_QBROWSER;
  GumAttachReturn r16 = gum_interceptor_attach(it, R(A_qbrowser_update), l, &o16);
  GumAttachOptions o17; memset(&o17, 0, sizeof(o17)); o17.listener_function_data = (gpointer)HK_QBROWSER;
  GumAttachReturn r17 = gum_interceptor_attach(it, R(A_qbrowser_ctor1), l, &o17);
  GumAttachOptions o18; memset(&o18, 0, sizeof(o18)); o18.listener_function_data = (gpointer)HK_QBROWSER;
  GumAttachReturn r18 = gum_interceptor_attach(it, R(A_qbrowser_ctor2), l, &o18);
  GumAttachOptions o19; memset(&o19, 0, sizeof(o19)); o19.listener_function_data = (gpointer)HK_QBROWSER;
  GumAttachReturn r19 = gum_interceptor_attach(it, R(A_qbrowser_ctor3), l, &o19);
  GumAttachOptions o20; memset(&o20, 0, sizeof(o20)); o20.listener_function_data = (gpointer)HK_MAINVIEW;
  GumAttachReturn r20 = gum_interceptor_attach(it, R(A_mwview_createBrowserForm), l, &o20);
  GumAttachOptions o21; memset(&o21, 0, sizeof(o21)); o21.listener_function_data = (gpointer)HK_FONTID;
  GumAttachReturn r21 = gum_interceptor_attach(it, R(A_ngllabel_setFontID), l, &o21);
  GumAttachOptions o22; memset(&o22, 0, sizeof(o22)); o22.listener_function_data = (gpointer)HK_CARDBG;
  GumAttachReturn r22 = gum_interceptor_attach(it, R(A_bf_onDrawBg), l, &o22);
  GumAttachOptions o41; memset(&o41, 0, sizeof(o41)); o41.listener_function_data = (gpointer)HK_SETENTRY;
  GumAttachReturn r41 = gum_interceptor_attach(it, R(A_settings_setEntry), l, &o41);
  GumAttachOptions o42; memset(&o42, 0, sizeof(o42)); o42.listener_function_data = (gpointer)HK_AUDIOSETUP;
  GumAttachReturn r42 = gum_interceptor_attach(it, R(A_audiosetup_onCtlEvt), l, &o42);
  GumAttachOptions o43; memset(&o43, 0, sizeof(o43)); o43.listener_function_data = (gpointer)HK_NSASELECT;
  GumAttachReturn r43 = gum_interceptor_attach(it, R(A_nsa_selectByID), l, &o43);
  GumAttachOptions o44; memset(&o44, 0, sizeof(o44)); o44.listener_function_data = (gpointer)HK_CCMANUAL;
  GumAttachReturn r44 = gum_interceptor_attach(it, R(A_cc_changedManually), l, &o44);
  GumAttachOptions o47; memset(&o47, 0, sizeof(o47)); o47.listener_function_data = (gpointer)HK_GRIDCTOR;
  GumAttachReturn r47 = gum_interceptor_attach(it, R(A_advgrid_ctor), l, &o47);
  GumAttachOptions o48; memset(&o48, 0, sizeof(o48)); o48.listener_function_data = (gpointer)HK_GRIDRESIZE;
  GumAttachReturn r48 = gum_interceptor_attach(it, R(A_advgrid_resize), l, &o48);
  tklog("attach AdvancedGridForm ctor -> %d, resize -> %d", (int)r47, (int)r48);
  GumAttachOptions o45; memset(&o45, 0, sizeof(o45)); o45.listener_function_data = (gpointer)HK_CCENABLE;
  GumAttachReturn r45 = gum_interceptor_attach(it, R(A_cc_enable), l, &o45);
  GumReplaceReturn r46 = gum_interceptor_replace(it, R(A_cc_activateDevice), (gpointer)tk_activateDevice,
                                                 (gpointer*)&g_orig_activateDevice, nullptr);
  tklog("attach Settings::setEntry -> %d, AudioSetup::onControlEvent -> %d, NSA selectByID -> %d, "
        "manual -> %d, enable -> %d; replace activateDevice -> %d",
        (int)r41, (int)r42, (int)r43, (int)r44, (int)r45, (int)r46);
  GumAttachOptions o40; memset(&o40, 0, sizeof(o40)); o40.listener_function_data = (gpointer)HK_FORMMOUSE;
  GumAttachReturn r40 = gum_interceptor_attach(it, R(A_nglform_onMouseEvent), l, &o40);
  tklog("attach NGL::Form::onMouseEvent -> %d", (int)r40);
  GumAttachOptions o23; memset(&o23, 0, sizeof(o23)); o23.listener_function_data = (gpointer)HK_CTLEVENT;
  GumAttachReturn r23 = gum_interceptor_attach(it, R(A_nglctl_onEvent), l, &o23);
  GumAttachOptions o24; memset(&o24, 0, sizeof(o24)); o24.listener_function_data = (gpointer)HK_STDTREE;
  GumAttachReturn r24 = gum_interceptor_attach(it, R(A_bst_selectTreeNode), l, &o24);
  GumAttachOptions o24b; memset(&o24b, 0, sizeof(o24b)); o24b.listener_function_data = (gpointer)HK_STDTREE;
  tklog("attach selectTreeNode(5-arg) -> %d", (int)gum_interceptor_attach(it, R(A_bst_selectTreeNode5), l, &o24b));
  GumAttachOptions o28; memset(&o28, 0, sizeof(o28)); o28.listener_function_data = (gpointer)HK_PICID;
  gum_interceptor_attach(it, R(A_ngllabel_setPictureID), l, &o28);
  GumAttachOptions o29; memset(&o29, 0, sizeof(o29)); o29.listener_function_data = (gpointer)HK_SWINIT;
  gum_interceptor_attach(it, R(A_nglswitch_onInit), l, &o29);
  GumAttachOptions o27; memset(&o27, 0, sizeof(o27)); o27.listener_function_data = (gpointer)HK_BFRESIZED;
  tklog("attach BrowserForm::resized -> %d", (int)gum_interceptor_attach(it, R(A_bf_resized), l, &o27));
  GumAttachOptions o49; memset(&o49, 0, sizeof(o49)); o49.listener_function_data = (gpointer)HK_BFDTOR;
  tklog("attach BrowserForm::~BrowserForm -> %d", (int)gum_interceptor_attach(it, R(A_bf_dtor), l, &o49));
  GumAttachOptions o25; memset(&o25, 0, sizeof(o25)); o25.listener_function_data = (gpointer)HK_RELOAD;
  GumAttachReturn r25 = gum_interceptor_attach(it, R(A_bst_onReloadForm), l, &o25);
  {
    struct { uintptr_t a; int id; } diag[] = {
      { A_bst_getSelCategory, HK_DIAG_CAT }, { A_bst_getSelTreeNodeInfo, HK_DIAG_TNI },
      { A_bst_canDragAndDrop, HK_DIAG_DND }, { A_bf_showContextMenu, HK_DIAG_CTX },
      { A_bf_createContextMenu, HK_DIAG_CTX },
    };
    for (auto& d : diag) {
      GumAttachOptions o; memset(&o, 0, sizeof(o)); o.listener_function_data = (gpointer)(intptr_t)d.id;
      tklog("attach diag hook %d @%p -> %d", d.id, R(d.a), (int)gum_interceptor_attach(it, R(d.a), l, &o));
    }
  }
  // Replace (not just attach) treeSelect so a click on our node never reaches Traktor's switch.
  // Listeners attached above still fire around the replacement.
  GumReplaceReturn r26 = gum_interceptor_replace(it, R(A_bf_treeSelect), (gpointer)tk_treeSelect,
                                                 (gpointer*)&g_orig_treeSelect, nullptr);
  GumReplaceReturn r27 = gum_interceptor_replace(it, R(A_ttm_getTooltip), (gpointer)tk_getTooltip,
                                                 (gpointer*)&g_orig_getTooltip, nullptr);
  // TK's own columns in our playlist (tk_columns.mm): their cells, headers, and no sorting.
  tk_cols_hook(it);  // TK's columns as real columns of Traktor's list (tk_columns.mm)
  gum_interceptor_end_transaction(it);
  tklog("replace TooltipManager::getTooltip -> %d (orig=%p)", (int)r27, (void*)g_orig_getTooltip);
  tklog("attach BrowserStdTree::selectTreeNode -> %d, onReloadForm -> %d; replace treeSelect -> %d (orig=%p)",
        (int)r24, (int)r25, (int)r26, (void*)g_orig_treeSelect);
  tklog("attach NGL::Control::onEvent @%p -> %d", R(A_nglctl_onEvent), (int)r23);
  tklog("attach BrowserForm::onDrawBackground @%p -> %d", R(A_bf_onDrawBg), (int)r22);
  tklog("attach BrowserForm::treeSelect @%p -> %d", R(A_bf_treeSelect), (int)r1);
  tklog("attach DataSetModel::valueForTrack @%p -> %d", R(A_dsm_valueForTrack), (int)r2);
  tklog("attach DataSetModel::rowCount @%p -> %d", R(A_dsm_rowCount), (int)r3);
  tklog("attach DataSetModel::data(QModelIndex) @%p -> %d", R(A_dsm_data), (int)r4);
  tklog("attach MixerDeck::loadDeck @%p -> %d", R(A_loadDeck), (int)r5);
  tklog("attach QQuickRenderControl::sync @%p -> %d", R(A_qqrc_sync), (int)r6);
  tklog("attach QQuickRenderControl::render @%p -> %d", R(A_qqrc_render), (int)r7);
  tklog("attach QQuickWindowPrivate::syncSceneGraph @%p -> %d", R(A_qqwin_syncSceneGraph), (int)r8);
  tklog("attach QQuickWindowPrivate::renderSceneGraph @%p -> %d", R(A_qqwin_renderSceneGraph), (int)r9);
  tklog("attach QMetaObject::activate(4) @%p -> %d", R(A_qmeta_activate4), (int)r10);
  tklog("attach QMetaObject::activate(3) @%p -> %d", R(A_qmeta_activate3), (int)r11);
  tklog("attach QMetaObject::activate(3b) @%p -> %d", R(A_qmeta_activate3b), (int)r12);
  tklog("attach QQuickItem::update @%p -> %d", R(A_qquickitem_update), (int)r13);
  tklog("attach QQuickItem::polish @%p -> %d", R(A_qquickitem_polish), (int)r14);
  tklog("attach QOffscreenView::onWindowChanged @%p -> %d", R(A_osview_onWindowChanged), (int)r15);
  tklog("attach QBrowser::update @%p -> %d", R(A_qbrowser_update), (int)r16);
  tklog("attach QBrowser::QBrowser x3 -> %d %d %d", (int)r17, (int)r18, (int)r19);
  tklog("attach MainWindowNGLView::createBrowserForm @%p -> %d", R(A_mwview_createBrowserForm), (int)r20);
  tklog("attach LabelControl::setFontID @%p -> %d", R(A_ngllabel_setFontID), (int)r21);
  tklog("=== hooks installed ===");

  // Start the TK -> dylib command channel (loopback listener, own thread).
  pthread_t cmd_t;
  if (pthread_create(&cmd_t, nullptr, cmd_listener, nullptr) == 0) pthread_detach(cmd_t);
  else tklog("CMD: could not start listener thread");

  // TEST HARNESS ONLY (guarded by TK_FORCE_FRONT): a headless/occluded window
  // never paints, so the Qt table view never queries the model and the read
  // hooks never fire. Bring Traktor frontmost ~18 s after boot to force a paint
  // so valueForTrack runs. Not for production — the shipped opener never steals
  // focus; this just lets the read path be exercised without a human click.
  if (getenv("TK_FORCE_FRONT")) {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(18 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
      [[NSApplication sharedApplication] activateIgnoringOtherApps:YES];
      for (NSWindow* w in [[NSApplication sharedApplication] windows]) {
        [w makeKeyAndOrderFront:nil];
      }
      tklog("DIAG forced Traktor frontmost to provoke a paint");
    });
  }

  // Goal 3 one-shot load attempt (test harness): TK_TEST_LOAD=<deckIdx 0..3>.
  // Run ~22 s after boot on the main thread, once the collection is loaded.
  const char* tl = getenv("TK_TEST_LOAD");
  if (tl) {
    int deck = atoi(tl);  // "0" is valid (deck A); atoi("")==0 but tl!=NULL only if set
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(22 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
      tklog("=== TK_TEST_LOAD: attempting native load into deck %d ===", deck);
      do_test_load(deck);
    });
  }
}
