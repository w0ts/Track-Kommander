// tkw_band_tree.cpp — TK's node in Traktor's left tree (port of the Mac tk_inject.mm tree code).
//
// The left tree is BrowserStdTree's NGL TreeControl (BrowserStdTree+0x5e8; Mac +0x598). We add a
// "Kommander" root at the top with Traktor's own createTreeNode (it registers the path, makes the
// NGL node and links info<->node), remove the roots the User does not want (Beatport, Beatport
// Preview, Apple Music), and re-apply both after every onReloadForm (Traktor rebuilds the tree from
// scratch). BrowserForm::treeSelect is replaced: a click on OUR node never reaches Traktor's
// category switch with an unknown category; it shows TK's "Results" playlist instead (the
// playlists module finds it) and shows the band. Windows layouts are MSVC's (std::map, std::string
// 32 bytes): see the offsets below and the map.
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>

#include "tkw_band_int.h"
#include "tkw_collection.h"  // the Results playlist (tkw_playlists.cpp)

namespace {

// Windows offsets (map "offsets"; Mac value in the comment).
const int kOffBstTreeCtl = 0x5e8;    // BrowserStdTree -> TreeControl* (0x598)
const int kOffTreeRoot = 0x5d0;      // root Node embedded in TreeControl (0x5a0)
const int kOffNodeInfo = 0x40;       // Node -> TreeNodeInfo* (0x30)
const int kOffNodeKids = 0x58;       // Node children vector<Node*> begin/end (0x48/0x50)
const int kOffTniCat = 0x20, kOffTniPath = 0x28, kOffTniNode = 0x68, kOffTniIcons = 0x70;  // (0x18/0x20/0x50/0x58)
const int kSzTni = 0xb0;             // (0x90)
const int kOffBfStdTree = 0x650;     // BrowserForm -> BrowserStdTree* (0x5f8)
// BrowserStdTree's std::map<int, ExternalCollectionInfo> (Mac +0x5f0, libc++) and the Node* its
// entries keep (Mac +0x60). Found in createFirstLevelNode 0xa5ff50 (default case 0xa6058e):
// `mov rcx,[rsi+0x640]` = the MSVC head, key int at node+0x20, `mov [rdi+0x78],rax` = the Node*.
const int kOffBstExtColl = 0x640, kOffExtCollNode = 0x78;
const unsigned kTkTreeCat = 0x7a;    // an unused EBrowserCategory for TK's root
const unsigned kCatPlaylist = 0x11;

void* g_stdtree = nullptr;
void* g_tk_node = nullptr;
unsigned g_delcats[8] = {0x1c, 0x22, 0x1a};  // Beatport, Beatport (Preview), Apple Music
int g_ndelcats = 3;
bool g_tree_auto = true;   // re-apply after every onReloadForm (TK_TREE=0 turns it off)
int g_tree_depth = 0;      // > 0 while BrowserStdTree::selectTreeNode runs (a real tree selection)
char g_label[64] = "Kommander";
// Traktor's tree pictures come in pairs (grey at an even id, white when selected at the next):
// the node-graph glyph is the pair 42/43.
int g_tree_icon = 42;
std::string g_show_match;  // TK_SHOW_MATCH: a selected path containing it opens TK (/show)

typedef void (*TreeSelectFn)(void*, const void*, unsigned);
TreeSelectFn g_orig_treeSelect = nullptr;

void* stdtree_ctl() {
  if (!g_stdtree || !tkw_readable((char*)g_stdtree + kOffBstTreeCtl, 8)) return nullptr;
  void* t = *(void**)((char*)g_stdtree + kOffBstTreeCtl);
  return t && tkw_readable((char*)t + kOffTreeRoot, kOffNodeKids + 16) ? t : nullptr;
}

// The root's children as a bounded array view; n = -1 when unreadable.
void** root_kids(void* tree, long* n) {
  char* root = (char*)tree + kOffTreeRoot;
  void** b = *(void***)(root + kOffNodeKids);
  void** e = *(void***)(root + kOffNodeKids + 8);
  *n = (b && e >= b) ? (long)(e - b) : -1;
  if (*n > 64 || (*n > 0 && !tkw_readable(b, (size_t)*n * 8))) *n = -1;
  return b;
}

const char* node_info(const void* node) {
  return tkw_readable((const char*)node + kOffNodeInfo, 8) ? *(const char* const*)((const char*)node + kOffNodeInfo)
                                                           : nullptr;
}

int node_cat(const void* node) {
  const char* info = node_info(node);
  return info && tkw_readable(info + kOffTniCat, 4) ? *(const int*)(info + kOffTniCat) : -1;
}

std::string node_label(const void* node) {
  std::string s;
  return tkw_read_string(node, &s) ? s : std::string("?");
}

void tree_redraw(void* tree) {
  tkw_call<void>(WF(WF_A_ngltree_invalidate), tree, 0u);
  tkb::invalidate();
}

// g_tk_node is ours only while it is still one of the root's children (Traktor may have rebuilt
// or edited the tree behind our back): otherwise forget it.
void* tk_node() {
  if (!g_tk_node) return nullptr;
  void* tree = stdtree_ctl();
  long n = -1;
  void** kids = tree ? root_kids(tree, &n) : nullptr;
  for (long i = 0; i < n; i++)
    if (kids[i] == g_tk_node) return g_tk_node;
  tklog("TREE: our node %p is no longer in the tree — forgotten", g_tk_node);
  g_tk_node = nullptr;
  return nullptr;
}

void tree_list() {
  void* tree = stdtree_ctl();
  if (!tree) {
    tklog("TREE: no BrowserStdTree yet (click any node in the left tree)");
    return;
  }
  long n;
  void** kids = root_kids(tree, &n);
  tklog("TREE: tree=%p roots=%ld (ours=%p)", tree, n, g_tk_node);
  for (long i = 0; i < n; i++) {
    const char* info = node_info(kids[i]);
    std::string path = "?";
    if (info && tkw_readable(info, kSzTni)) tkw_read_string(info + kOffTniPath, &path);
    tklog("TREE:   [%ld] node=%p cat=0x%x label='%s' path='%s'", i, kids[i], node_cat(kids[i]),
          node_label(kids[i]).c_str(), path.c_str());
  }
}

// Our root at the top of the tree via Traktor's own createTreeNode. False if Traktor refused.
bool tree_add() {
  void* tree = stdtree_ctl();
  if (!tree) {
    tklog("TREE: add: no BrowserStdTree yet");
    return false;
  }
  if (tk_node()) return true;
  alignas(16) char info[kSzTni + 0x40];
  memset(info, 0, sizeof(info));
  const std::string name(g_label);
  // (this, cat, name, icon, icon2 -1 = same, bool). createTreeNode deep-copies the info, so this
  // stack copy's strings are simply left behind (a few bytes, once per add).
  tkw_call<void*>(WF(WF_A_tni_ctor), (void*)info, kTkTreeCat, &name, g_tree_icon, -1, false);
  void* root = (char*)tree + kOffTreeRoot;
  void* node = tkw_call<void*>(WF(WF_A_bst_createTreeNode), g_stdtree, root, (void*)nullptr, false, (void*)info);
  // First in the tree: createTreeNode's position argument inserts AFTER a node, so move our entry
  // to the front of the root's children vector instead; the tree lays rows out when it draws.
  long nkids;
  void** kids = root_kids(tree, &nkids);
  for (long i = 1; node && i < nkids; i++) {
    if (kids[i] != node) continue;
    memmove(kids + 1, kids, (size_t)i * sizeof(void*));
    kids[0] = node;
    break;
  }
  g_tk_node = node;
  tree_redraw(tree);
  tklog("TREE: add '%s' cat=0x%x -> node=%p", g_label, kTkTreeCat, node);
  return node != nullptr;
}

bool map_nil(const char* n) { return !n || !tkw_readable(n, 0x20) || n[0x19] != 0; }

// Forget a node in Traktor's own bookkeeping before deleting it: its TreeNodeInfo keeps a Node*
// (selectTreeNode returns early on null) and an external collection's map entry keeps one
// (createFirstLevelNode reuses it). Walk that MSVC std::map in order (head->left = first).
void forget_node(const void* node) {
  char* info = (char*)node_info(node);
  if (info && tkw_readable(info + kOffTniNode, 8)) *(void**)(info + kOffTniNode) = nullptr;
  if (!tkw_readable((char*)g_stdtree + kOffBstExtColl, 8)) return;
  char* head = *(char**)((char*)g_stdtree + kOffBstExtColl);
  if (!head || !tkw_readable(head, 0x20)) return;
  char* it = *(char**)head;
  for (int guard = 0; guard < 64 && it != head && !map_nil(it) && tkw_readable(it, kOffExtCollNode + 8); guard++) {
    if (*(const void**)(it + kOffExtCollNode) == node) {
      *(void**)(it + kOffExtCollNode) = nullptr;
      tklog("TREE:   cleared external-collection entry key=0x%x", *(const int*)(it + 0x20));
    }
    char* right = *(char**)(it + 0x10);
    if (!map_nil(right)) {
      it = right;
      while (!map_nil(*(char**)it)) it = *(char**)it;
      continue;
    }
    char* parent = *(char**)(it + 8);
    while (!map_nil(parent) && *(char**)(parent + 0x10) == it) {
      it = parent;
      parent = *(char**)(it + 8);
    }
    it = parent;
  }
}

// Delete every root node of one category (never ours). Returns how many went.
int tree_del(unsigned cat) {
  void* tree = stdtree_ctl();
  if (!tree || cat == kTkTreeCat) return 0;
  int gone = 0;
  for (int pass = 0; pass < 16; pass++) {  // deleteNode shifts the vector: rescan after each
    long n;
    void** kids = root_kids(tree, &n);
    void* hit = nullptr;
    for (long i = 0; i < n && !hit; i++)
      if (node_cat(kids[i]) == (int)cat) hit = kids[i];
    if (!hit) break;
    tklog("TREE: delete cat=0x%x label='%s' node=%p", cat, node_label(hit).c_str(), hit);
    forget_node(hit);
    tkw_call<void>(WF(WF_A_ngltree_deleteNode), tree, hit);
    gone++;
  }
  if (gone) tree_redraw(tree);
  return gone;
}

// Our node's icon, changed in place in its TreeNodeInfo (icon, second icon, their next frames).
// Deleting and re-adding the node instead does NOT work (the second createTreeNode returned null).
void set_tree_icon(int icon) {
  void* tree = stdtree_ctl();
  char* info = tk_node() ? (char*)node_info(g_tk_node) : nullptr;
  if (!tree || !info || !tkw_readable(info, kOffTniIcons + 16)) {
    tklog("TREE: no node for an icon");
    return;
  }
  int* f = (int*)(info + kOffTniIcons);
  f[0] = icon; f[1] = icon; f[2] = icon + 1; f[3] = icon + 1;
  tree_redraw(tree);
  tklog("TREE: icon %d", icon);
}

void tree_apply() {
  if (g_tkw_quitting) return;
  for (int i = 0; i < g_ndelcats; i++) tree_del(g_delcats[i]);
  tree_add();
}

void adopt_stdtree(void* t, const char* how) {
  if (t == g_stdtree || !t || tkw_rtti_name(t) != ".?AVBrowserStdTree@@") return;
  g_stdtree = t;
  g_tk_node = nullptr;  // a node we added belongs to the old tree
  tklog("TREE: BrowserStdTree %p (%s)", t, how);
}

// ---- BrowserForm::treeSelect replacement ---------------------------------------------------------
// Traktor re-runs treeSelect for the CURRENT state on its own (after every deck load, …) with the
// category BrowserForm stored. While our node's click is being handled, such a call is a
// re-select of OUR view. (The Mac also had an "echo" timer here; it was never armed.)
bool is_reselect_of_ours(unsigned cat) { return tkb::g_tk_selected && cat == 1 && g_tree_depth == 0; }

void select_ours(void* self, bool clicked) {
  tklog("TREE: %s -> TK list", clicked ? "Track Kommander node selected" : "re-select of our view");
  tkb::g_tk_selected = true;
  // A click empties the playlist first, so the list is blank (not stale) until TK's fresh list
  // lands, and says so in the band; complain if TK never answers.
  if (clicked) {
    tkw_playlists_results_clear();
    tkb::band_set_visible(true);
    tkb::set_status("Loading Track Kommander results...");
    const unsigned gen = tkw_playlists_list_gen(), said = tkb::g_status_from_tk;
    tkw_after_ms(6000, [gen, said] {
      // No list and no status from TK since the click (a status alone is an answer too: the
      // stage-1 proof never sends a list).
      if (!g_tkw_quitting && tkw_playlists_list_gen() == gen && tkb::g_status_from_tk == said &&
          tkb::g_band_visible)
        tkb::set_status("No answer from Track Kommander - is it running?");
    });
  }
  // Show the Results playlist exactly as if the user had clicked it.
  const std::string tp = tkw_playlists_results_tree_path();
  if (!tp.empty() && g_orig_treeSelect) {
    tklog("TREE: -> playlist (%zu chars)", tp.size());
    g_orig_treeSelect(self, &tp, kCatPlaylist);
  } else {
    tklog("TREE: no Results playlist to show");
  }
  tkb::g_tk_selected = false;  // Traktor now shows a normal playlist; its re-selects carry 0x11
  tkb::band_set_visible(true);
  if (clicked) tkw_post_json("/uiAction", "{\"action\":\"KOMMANDER\"}");
}

void check_show_match(const std::string& path) {
  if (g_show_match.empty() || path.empty()) return;
  std::string lc(path);
  for (char& c : lc) c = (char)tolower((unsigned char)c);
  if (lc.find(g_show_match) == std::string::npos) return;
  tklog("  match '%s' -> opening Track Kommander", g_show_match.c_str());
  tkw_post_json("/show", "{}");
}

void tk_treeSelect(void* self, const void* path, unsigned cat) {
  std::string p;
  const bool readable = tkw_read_string(path, &p);
  bool ours = false;
  try {
    tklog("treeSelect path=%s cat=0x%x", readable ? p.c_str() : "?", cat);
    check_show_match(p);
    const bool clicked = cat == kTkTreeCat;
    ours = clicked || is_reselect_of_ours(cat);
    if (ours) select_ours(self, clicked);
  } catch (...) {
    tklog("TREE: treeSelect handling threw");
  }
  if (ours) return;
  tkb::g_tk_selected = false;
  const ULONGLONG t0 = GetTickCount64();
  if (g_orig_treeSelect) g_orig_treeSelect(self, path, cat);
  // Traktor's own list rebuild: timed, a lag suspect (three in a row after a deck load, 2026-11-25).
  if (GetTickCount64() - t0 >= 20) tklog("treeSelect: Traktor took %llu ms", GetTickCount64() - t0);
  // Traktor re-selecting the Results playlist (after a deck load, …) is still TK's view.
  try {
    const std::string known = tkw_playlists_results_known_path();
    tkb::band_set_visible(!known.empty() && readable && p == known);
  } catch (...) {
    tklog("TREE: band visibility after treeSelect threw");
  }
}

// ---- hooks ---------------------------------------------------------------------------------------
void on_select_enter(GumInvocationContext* ic) {  // BrowserStdTree::selectTreeNode (both overloads)
  g_tree_depth++;
  adopt_stdtree(tkw_arg(ic, 0), "selectTreeNode");
}
void on_select_leave(GumInvocationContext*) {
  if (g_tree_depth > 0) g_tree_depth--;
}
void on_reload_enter(GumInvocationContext* ic) {  // runs before any selectTreeNode at startup
  adopt_stdtree(tkw_arg(ic, 0), "onReloadForm");
}
void on_reload_leave(GumInvocationContext*) {
  // onReloadForm just rebuilt the tree: our node is gone and Beatport is back. Never edit the tree
  // inside onReloadForm itself (at startup it runs before the window is laid out: the Mac crashed
  // drawing past its picture); edit it once the main loop runs.
  g_tk_node = nullptr;
  tklog("TREE: onReloadForm rebuilt the tree (auto=%d)", (int)g_tree_auto);
  if (g_tree_auto)
    tkw_after_ms(500, [] {
      if (!tk_node()) tree_apply();
    });
}

std::string treeadd_command(const std::string& a) {  // "treeadd [label]"
  if (!a.empty()) tkb::copy_str(g_label, sizeof(g_label), a.c_str());
  g_tree_auto = true;
  tree_add();
  tree_list();
  return "OK";
}

std::string treeicon_command(const std::string& a) {  // "treeicon <id>"
  char* end = nullptr;
  const long icon = strtol(a.c_str(), &end, 10);
  if (a.empty() || end == a.c_str() || icon < 0 || icon > 4096) return "ERR usage: treeicon <id>";
  g_tree_icon = (int)icon;
  set_tree_icon(g_tree_icon);
  return "OK";
}

std::string treedel_command(const std::string& a) {  // "treedel <catHex>": now and after rebuilds
  unsigned cat = 0;
  if (sscanf_s(a.c_str(), "%x", &cat) != 1 || cat == kTkTreeCat) return "ERR usage: treedel <catHex>";
  bool known = false;
  for (int i = 0; i < g_ndelcats; i++) known = known || g_delcats[i] == cat;
  if (!known && g_ndelcats < 8) g_delcats[g_ndelcats++] = cat;
  g_tree_auto = true;
  tklog("TREE: deleted %d node(s) of cat 0x%x", tree_del(cat), cat);
  tree_list();
  return "OK";
}

void cmd(const char* verb, std::string (*fn)(const std::string&)) {
  tkw_command(verb, [fn](const std::string& a, const std::vector<std::string>&) { return fn(a); });
}

}  // namespace

namespace tkb {

void tree_init() {
  if (tkw_env("TK_TREE") == "0") g_tree_auto = false;
  g_show_match = tkw_env("TK_SHOW_MATCH");
  for (char& c : g_show_match) c = (char)tolower((unsigned char)c);
  tkw_hook(WF(WF_A_bst_selectTreeNode), "BrowserStdTree::selectTreeNode", on_select_enter, on_select_leave);
  tkw_hook(WF(WF_A_bst_selectTreeNode5), "BrowserStdTree::selectTreeNode(5)", on_select_enter, on_select_leave);
  tkw_hook(WF(WF_A_bst_onReloadForm), "BrowserStdTree::onReloadForm", on_reload_enter, on_reload_leave);
  // Replace (not just attach) treeSelect so a click on our node never reaches Traktor's switch.
  tkw_replace(WF(WF_A_bf_treeSelect), "BrowserForm::treeSelect", (void*)tk_treeSelect, (void**)&g_orig_treeSelect);
  cmd("treeadd", treeadd_command);
  cmd("treeicon", treeicon_command);
  cmd("treedel", treedel_command);
  tkw_command("treelist", [](const std::string&, const std::vector<std::string>&) {
    tree_list();
    return std::string("OK");
  });
}

// The band was built: if onReloadForm ran before our hooks, take the tree from the form.
void tree_adopt_from_form(void* bf) {
  if (!g_stdtree && bf && tkw_readable((char*)bf + kOffBfStdTree, 8))
    adopt_stdtree(*(void**)((char*)bf + kOffBfStdTree), "BrowserForm+0x650");
  if (g_stdtree && !tk_node() && g_tree_auto)
    tkw_after_ms(500, [] {
      if (!tk_node()) tree_apply();
    });
}

// Select our node as a click would (TK_EMBED: open on TK's view at start-up). The node may not be
// there yet (tree_apply runs 500 ms after onReloadForm): try again every 250 ms for 5 s.
void select_our_node_try(int left) {
  if (g_tkw_quitting) return;
  const char* info = tk_node() ? node_info(g_tk_node) : nullptr;
  if (!g_stdtree || !info) {
    if (left > 0) {
      tkw_after_ms(250, [left] { select_our_node_try(left - 1); });
      return;
    }
    tklog("EMBED: no tree node to select after 5 s");
    return;
  }
  std::string path;  // our node's tree path, as createTreeNode registered it
  if (!tkw_read_string(info + kOffTniPath, &path)) path.clear();
  tkw_call<void>(WF(WF_A_bst_selectTreeNode), g_stdtree, (const void*)&path, true, false, false);
  tklog("EMBED: Track Kommander selected at startup");
}

void select_our_node() { select_our_node_try(20); }

void forget_tree() {  // the BrowserStdTree lives in the BrowserForm that is gone
  g_stdtree = nullptr;
  g_tk_node = nullptr;
  g_tree_depth = 0;
}

}  // namespace tkb

void* tkw_band_stdtree() { return g_stdtree; }
