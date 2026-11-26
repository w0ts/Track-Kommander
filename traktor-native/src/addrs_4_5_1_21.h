// Traktor Pro 4 build 4.5.1.21 (arm64) — preferred (Ghidra) addresses.
// Real runtime address = value + _dyld_get_image_vmaddr_slide(mainImage).
// Regenerate for a new build with re/remap.sh.
#pragma once
#include <stdint.h>

#define TK_TRAKTOR_VERSION "4.5.1.21"

// --- browser tree (BrowserStdTree) ---
static const uintptr_t A_onReloadForm             = 0x1007f40a8ULL; // BrowserStdTree::onReloadForm
static const uintptr_t A_createTreeNode           = 0x1007f3d00ULL; // BrowserStdTree::createTreeNode(Node*,Node*,bool,TreeNodeInfo const&)
static const uintptr_t A_createFirstLevelNode     = 0x1007f3674ULL; // BrowserStdTree::createFirstLevelNode(EBrowserCategory)
static const uintptr_t A_onTreeSelect             = 0x1007f9070ULL; // BrowserStdTree::onTreeSelect
static const uintptr_t A_getSelectedBrowserCategory = 0x1007e9e34ULL; // BrowserStdTree::getSelectedBrowserCategory

// --- TreeNodeInfo ctor: (EBrowserCategory, std::string const&, int icon, int, bool) ---
static const uintptr_t A_treeNodeInfoCtor         = 0x10081c770ULL;

// --- load-to-deck (phase 4; kept here for the whole map) ---
static const uintptr_t A_getMixer                 = 0x10004733cULL; // TraktorApp::getMixer
static const uintptr_t A_getMixerDeckChannel      = 0x100045ab0ULL; // TraktorApp::getMixerDeckChannel(uint)
static const uintptr_t A_loadDeck                 = 0x1000d4240ULL; // MixerDeck::loadDeck
static const uintptr_t A_loadMixerDeck            = 0x1000d4500ULL; // MixerDeck::loadMixerDeck(int deck, SmartPtr&, bool) — looks up channel + null Controllable
// TraktorApp multiple-inheritance: IAppBase::getInstance() returns the IAppBase
// subobject; the embedded MixerDeck is at that subobject +0x1f0 (from the
// getMixer non-virtual thunk `return this+0x1f0`; the primary TraktorApp this is
// +0x160 further on, where getMixer returns this+0x350). MixerDeck channel
// vector: begin@+0x178, end@+0x180, elems 0x10 each (ptr@0).
static const uintptr_t A_ccmGetTrack              = 0x10047f7c8ULL; // ContentCollectionMgr::getTrack

// === MAIN-WINDOW browser (Qt Widgets) — the real target (2026-11-21) ===
// BrowserStdTree above is the CONTROLLER-SCREEN (S4/S8/D2) browser; its hooks never
// fire for the main window. The main window = BrowserForm + DataSetModel (Qt model/view).
// Addresses from the full symbol dump (_ALL.txt); confirm wiring from the decompile.
//
// DataSetModel = the main list/tree Qt model. Qt calls the VIRTUAL rowCount(QModelIndex*),
// NOT getRowCount(), which is why the earlier getRowCount hook never fired.
static const uintptr_t A_dsm_rowCount        = 0x10099ad54ULL; // rowCount(QModelIndex*)  [virtual]
static const uintptr_t A_dsm_columnCount     = 0x10099cf44ULL; // columnCount(QModelIndex*)
static const uintptr_t A_dsm_data            = 0x10099d298ULL; // data(QModelIndex*,int)   [virtual]
static const uintptr_t A_dsm_data3           = 0x10099d3b8ULL; // data(int,int,TableDataType)
static const uintptr_t A_dsm_headerData      = 0x10099cf4cULL; // headerData(int,Orientation,int)
static const uintptr_t A_dsm_updateFirstLvl  = 0x1009992acULL; // updateFirstLevelNodes()
static const uintptr_t A_dsm_treeNodeInfo    = 0x10099da00ULL; // treeNodeInfo(string*)
static const uintptr_t A_dsm_subnodeCount    = 0x10099db28ULL; // subnodeCount()
static const uintptr_t A_dsm_getEntry        = 0x10099e2c8ULL; // getEntry(int)
static const uintptr_t A_dsm_valueForTrack   = 0x10099dcdcULL; // valueForTrack(SmartPtr,TableRole)
static const uintptr_t A_dsm_writeRootToTable= 0x10099e3f0ULL; // writeRootNodeDataToTable()
static const uintptr_t A_flnEmplace          = 0x1009a2da0ULL; // QGenericArrayOps<FirstLevelNode>::emplace(dp,pos,node)
static const uintptr_t A_qstrFromUtf8        = 0x1010f2c58ULL; // QString::fromUtf8(char*,longlong)
// FirstLevelNode = 0x38 bytes: {+0x00 icon/ptr; +0x08 QString name(d,ptr,size); +0x20 id inline-string, len at +0x37}
// Member QVector<FirstLevelNode> in DataSetModel: d@this+0x18, begin@this+0x20, size@this+0x28.
#define OFF_DSM_VEC_D    0x18
#define OFF_DSM_VEC_PTR  0x20
#define OFF_DSM_VEC_SIZE 0x28
#define FLN_SIZE         0x38
static const uintptr_t A_dsm_getContentType  = 0x10099abecULL; // getContentType()
// DataSetModel vtable (Itanium ABI: this pointer = &first-slot). From the ctor:
// `*this = &PTR_metaObject_10547a270`. Virtual slots (data/rowCount/columnCount/
// headerData/parent) are swizzle targets — the vtable is in __DATA_CONST (writable,
// no XOM/COW problem), unlike the XO __TEXT the functions live in.
static const uintptr_t A_dsm_vtable = 0x10547a270ULL; // in __TEXT,__const (RO, maxprot r-x)
// Vtable slot indices (from Ghidra DumpVtable; QAbstractTableModel layout):
//   3,4 = ~DataSetModel   13 = parent   15 = rowCount   16 = columnCount
//   18 = data             20 = headerData   31 = insertRows   33 = removeRows   44 = roleNames
// NOTE: the vtable is in __TEXT (maxprot r-x) so the SHARED class vtable cannot be patched.
// Swizzling requires PER-INSTANCE vptr replacement (point the object at a private patched copy).
#define VSLOT_PARENT      13
#define VSLOT_ROWCOUNT    15
#define VSLOT_COLUMNCOUNT 16
#define VSLOT_DATA        18
#define VSLOT_HEADERDATA  20
#define VSLOT_INSERTROWS  31
#define VSLOT_REMOVEROWS  33

// BrowserForm = main-window browser controller.
static const uintptr_t A_bf_create           = 0x1007bcd20ULL; // create(uint)
static const uintptr_t A_bf_treeSelect       = 0x1007c9300ULL; // treeSelect(string* treePath, EBrowserCategory)
static const uintptr_t A_bf_addPage          = 0x1007c0cd4ULL; // addPage(BrowserComponentBase*)
static const uintptr_t A_bf_getCurrContentList = 0x1007c8de0ULL; // getCurrContentList()
static const uintptr_t A_bf_selectContentListInTree = 0x1007c8e28ULL; // selectContentListInTree(SmartPtr*)
static const uintptr_t A_bf_getStructPathFromTree = 0x1007c2060ULL; // getStructurePathFromTreePath(string*)
static const uintptr_t A_bf_getSelectedEntry = 0x1007c8cbcULL; // getCurrentlySelectedContentEntry()

// === CALL surface (hybrid: XOM blocks hooking, NOT calling) ===
// App + collection singletons / accessors:
static const uintptr_t A_appGetInstance   = 0x100a924a0ULL; // IAppBase::getInstance()
static const uintptr_t A_ccmGetList       = 0x10047d4acULL; // ContentCollectionMgr::getList(StructurePath*)
static const uintptr_t A_ccmGetEntryCopy  = 0x10047ed10ULL; // getEntryCopy(TypedEntryKey*)
static const uintptr_t A_ccmGetTrackCopy  = 0x10047f6f4ULL; // getTrackCopy(TypedEntryKey*)
static const uintptr_t A_ccmGetMainCollection = 0x100478d2cULL; // CollectionsManager::getMainCollection() [method, needs singleton this]
static const uintptr_t A_helpersGetMainCollection = 0x100825dfcULL; // helpers::getMainCollection() [free fn] -> ContentCollectionMgr*
static const uintptr_t A_collectionsSingleton = 0x105653f48ULL; // CollectionsManager singleton OBJECT (ni::singleton::get<CollectionsManager>::instance -> &this)
// getTrack(mgr, TypedEntryKey<uint> const&) returns SmartPtr<ContentEntryTrack> via x8 (sret).
// A track's collection key is a uint at entry+0xc. loadDeck null-checks only track+channel
// (Controllable* may be null); it addRefs the SmartPtr itself, so &raw_ptr works as the arg.
static const uintptr_t A_mi_getBPM2       = 0x100b6bd60ULL; // MediaItem::getBPM (dup of A_mi_getBPM, kept explicit)
// Track metadata getters (traktor::browser::MediaItem). These return a POINTER
// to a field inside the object: getTitle=this+0x38, getArtist=this+0x50 (each an
// NI string {char* data; size_t len}); getBPM reads a float at this+0x144.
// ContentEntryTrack IS-A MediaItem (base@0), so a track pointer works directly.
static const uintptr_t A_mi_getTitle      = 0x100b6ba7cULL;
static const uintptr_t A_mi_getArtist     = 0x100b6bbc8ULL;
static const uintptr_t A_mi_getBPM        = 0x100b6bd60ULL;
#define OFF_MI_TITLE   0x38   // NI string {char* data, size_t len}
#define OFF_MI_ARTIST  0x50   // NI string {char* data, size_t len}
#define OFF_MI_BPM     0x144  // float
// Full field map, 2026-11-22, confirmed by Ghidra decompile of each getter (every
// string getter is `return this+OFF`; every value getter is `return *(u32*)(this+OFF)`).
// Strings are NI strings {char* data; size_t len}. Dates are packed u32.
#define OFF_MI_ALBUM        0x68   // NI string  (getAlbum @0x100b6bbf8)
#define OFF_MI_GENRE        0x80   // NI string  (getGenre @0x100b6bc38)
#define OFF_MI_LABEL        0x98   // NI string  (getLabel @0x100b6bc78)
#define OFF_MI_COMMENT      0xb0   // NI string  (getComment @0x100b6bcb8)   — goal 5
#define OFF_MI_COMMENT2     0xc8   // NI string  (getComment2 @0x100b6bcf8)  — goal 5 (Discogs INFO/RATING)

// === Library WRITE (2026-11-22, fork A): MediaItem setters — authoritative, persist on quit ===
// setComment assigns the std::string member at this+0xb0 (attr 7), calls ModifiedFlags::
// markAsModified and sets dirty byte this[0x5b0]=1. ContentEntryTrack IS-A MediaItem at base 0,
// so the raw track from get_track_by_key/get_selected_track is the `this`. Pass a real libc++
// std::string& (same system ABI, as get_track_by_path already does for URLs). The UI's own
// per-field apply (updateTrackTitle) calls the setter directly with no explicit commit, so
// Level-1 (setter -> modified flag) persists on quit; commitEntry is the Level-2 fallback.
static const uintptr_t A_mi_setComment   = 0x100b6bcc0ULL; // MediaItem::setComment(std::string const&)  [x1=&str]
static const uintptr_t A_ccm_commitEntry = 0x10047cdb8ULL; // ContentCollectionMgr::commitEntry(SmartPtr<Entity const>&, int) — commit modified entry so it serializes (Level-2)

// === Playlist Favorites bar (BrowserShortCutsForm) — hijack, 2026-11-22 (fork B) ===
// Reuse Traktor's own favorites tiles (they render real text natively). fav = *(g_browserform+0x548);
// 12 slots of 0x20 each from fav+0x2f0; slot record+0 = libc++ std::string path (empty => "not assigned").
// setSlot(slot, "KOMMANDER\a<label>") => tile label = last \a-component; click routes via selectSlot (hook later).
static const uintptr_t A_fav_setSlot        = 0x1007c0244ULL; // BrowserShortCutsForm::setSlot(int, std::string const&)
static const uintptr_t A_fav_updateSlots    = 0x1007c04c8ULL; // updateSlots()
static const uintptr_t A_fav_selectSlot     = 0x1007eec1cULL; // selectSlot(int,bool) — HOOK for clicks (later)
static const uintptr_t A_bf_updateFavorites = 0x1007c048cULL; // BrowserForm::updateFavorites() (refresh tiles + favorites node)
static const uintptr_t A_featureset_get     = 0x100793af4ULL; // FeatureSet::getInstance(); +0x78 = favorites-enabled
#define OFF_BF_SHORTCUTS   0x548   // BrowserForm -> BrowserShortCutsForm*
#define OFF_FAV_SLOT0      0x2f0   // first slot record
#define SZ_FAV_SLOT        0x20    // slot stride (12 slots 0..11); record+0 = libc++ std::string path

// === BrowserForm layout / custom region — 2026-11-22 (fork B) ===
// resized (vtable slot 0x210) positions each child by setting its Rect {l@0x38,t,r,b} via the
// control's setPosition. Reserve a top band = push the track-list child's top down by H and place
// our own rect in the gap; hook resized on_leave to make it survive relayouts (later). Custom draw
// = host an NGL Form + override onDrawBackground (slot 0x1c0) + Picture fillArea/drawRectangle (later).
static const uintptr_t A_bf_resized    = 0x1007cf4a0ULL; // BrowserForm::resized(int)  [vtable slot 0x210]
static const uintptr_t A_bf_dtor       = 0x1007b93a8ULL; // BrowserForm::~BrowserForm(), the body: the deleting dtor (0x1007b9bb4), the complete one (0x1007b9b80) and the thunks all reach it
static const uintptr_t A_form_onDraw   = 0x10217d51cULL; // Form::onDraw(Picture*,Region*)
static const uintptr_t A_form_onDrawBg = 0x10217d7b8ULL; // Form::onDrawBackground — vtable slot 0x1c0 (override)
static const uintptr_t A_pic_fillArea  = 0x10229c1a4ULL; // NI::UIA::Picture::fillArea(...)
static const uintptr_t A_pic_drawRect  = 0x10229be7cULL; // NI::UIA::Picture::drawRectangle(x,y,w,h,argb)
static const uintptr_t A_bf_onDrawBg   = 0x1007e56d4ULL; // BrowserForm::onDrawBackground(Picture*,Region*) — HOOK on_leave to draw cards
#define OFF_CTRL_RECT_L    0x38    // control rect: l@0x38, t@0x3c, r@0x40, b@0x44
#define OFF_CTRL_VISFLAG   0x7d    // control visible flag (checked at paint)
#define OFF_BF_VISMASK     0x3e0   // BrowserForm region visibility bitmask (bit1 = favorites strip)
#define OFF_BF_HEIGHT      0x2c    // resized() height source: engine calls resized(bf, *(int*)(bf+0x2c))
#define OFF_FEATURESET_FAV 0x78    // FeatureSet+0x78: favorites feature flag (0 => vismask bit1 forced off)
// Permanent "Show Playlist Favorites" preference (writes the .tsi): LayoutManager pref 0x37.
static const uintptr_t A_layoutmgr_getInstance = 0x100981964ULL; // LayoutManager::getInstance()
static const uintptr_t A_layoutmgr_setProperty = 0x10097dd9cULL; // LayoutManager::setProperty(eLayoutPropertyId, int)
#define LAYOUTPROP_FAVORITES 0x37
#define OFF_MI_PLAYCOUNT    0x148  // u32        (getPlayCount @0x100b6bf58)
#define OFF_MI_RATING       0x14c  // u32        (getRating @0x100b6bfc4)
#define OFF_MI_IMPORTDATE   0x150  // u32 packed (getImportDatePacked @0x100b6c0b0)
#define OFF_MI_RELEASEDATE  0x158  // u32 packed (getReleaseDatePacked @0x100b6c260)
// NOTE: no getMusicalKey / getBitrate getter exists on MediaItem — those fields
// live elsewhere; keep reading them from the NML until mapped.
// === In-memory collection enumeration (full-parity live library) ===
// ContentCollectionMgr::getEntryCount() -> MediaItemRepository::count(*(mgr+0x140)).
// So mgr+0x140 is the MediaItemRepository* that holds every MediaItem (each base@0,
// offsets above valid). mgr+0x90 is a main-thread guard (getEntryCount returns 0 off
// the owning thread) — call enumeration on the main queue, as we already do.
static const uintptr_t A_ccm_getEntryCount = 0x10048404cULL;
static const uintptr_t A_ccm_getList       = 0x10047d4acULL;
#define OFF_CCM_REPO        0x140  // ContentCollectionMgr -> MediaItemRepository*
#define OFF_CCM_THREADGUARD 0x90   // owning pthread_t (0 or self required)
// MediaItemRepository (the repo at mgr+0x140). 2026-11-22 Ghidra.
static const uintptr_t A_mir_count         = 0x100b642a0ULL; // count() = *(u32*)(repo+0x40)
static const uintptr_t A_mir_forEachEntity = 0x100b645d4ULL; // forEachEntity(std::function<void(SmartPtr<MediaItem>)> const&)
static const uintptr_t A_mir_getByKey      = 0x100b65310ULL; // getByKey(TypedEntryKey<uint> const&) -> SmartPtr<MediaItem> via x8 (casts Entity->MediaItem)
#define OFF_MIR_COUNT       0x40   // u32 entry count

// === #2/#3 CORRECTIONS (2026-11-22 live-test + Ghidra) ======================
// #3: the agent cast the selected Entity to ContentEntryTrack and read MediaItem
// offsets off it -> empty title/artist (wrong subobject). getByKey casts
// Entity->MediaItem. SIMPLEST FIX: the selected entry carries its collection key
// as a uint at entry+0xc (see note above getTitle block); read it and route
// through the PROVEN get_track_by_key(mgr, key), which returns a known-good
// MediaItem. No MediaItem::typeinfo needed.
#define OFF_ENTRY_KEY       0xc    // selected browser entry -> collection key (uint)
// #2: EntryKeyManager instance = *(void**)0x105653f58 (a CollectionsManager member,
// set lazily by ContentEntryTrack::getURL's singleton init). The agent's instance
// slot was correct; its FAILURE was gating on a bogus guard byte at 0x105653f60.
// FIX: gate on (*(void**)A_ekm_instance != 0) directly; drop A_ekm_instance_guard.
// Path route for enumeration / selection-by-path:
static const uintptr_t A_cet_getURL           = 0x100485fd8ULL; // ContentEntryTrack::getURL() -> URL via x8 (uses EntryKeyManager::getURLFromKey)
static const uintptr_t A_url_getFileSystemPath= 0x100b6e09cULL; // URL::getFileSystemPath() -> std::string via x8 (variant visit; kind at URL+0x18)
static const uintptr_t A_ekm_getURLFromKey    = 0x100b786c8ULL; // EntryKeyManager::getURLFromKey(key) -> URL via x8

// === Embedded UI: native column injection into DataSetModel (Qt 6.5.3, 2026-11-22) ===
// See traktor-native/notes/embedded-ui-plan.md. The DataSetModel addrs (columnCount
// @A_dsm_columnCount, headerData, data, getEntry, valueForTrack) are mapped above
// (lines ~38-47). Verified this session: columnCount=*(u32*)(this+0x48); headers are
// a QString[] at this+0x40 (0x18/entry); data cache grid at this+0x88, rows this+0x90;
// getEntry(row) maps a VISIBLE row -> SmartPtr<entry> (getIndexFromRow + ContentList).
// Hook plan: columnCount(+N), headerData(section>=orig -> our QVariant), data
// (col>=orig -> our QVariant via getEntry(row) -> track identity -> TK value).
static const uintptr_t A_dsm_getRowCount  = 0x10099da64ULL; // getRowCount()
#define OFF_DSM_COLCOUNT  0x48   // u32 column count
#define OFF_DSM_HEADERS   0x40   // QString[] header array, 0x18/entry
// QModelIndex layout (from data()): row = *(int*)(idx+0), col = *(int*)(idx+4).
// Qt building blocks (namespaced NI_6_5_3_TP2) for constructing our cell values:
static const uintptr_t A_qstring_fromUtf8      = 0x1010f2c58ULL; // fromUtf8(char* x0, len x1) -> QString via x8 (fits call_sret2)
static const uintptr_t A_qvariant_from_qstring = 0x1010953a0ULL; // QVariant::QVariant(this x0, QString* x1) in-place
static const uintptr_t A_qvariant_dtor         = 0x101094f30ULL; // QVariant::~QVariant()

// === Embedded UI: NATIVE cards bar inside Traktor (Qt widget injection, 2026-11-22) ===
// See traktor-native/notes/embedded-ui-plan.md. Build a QWidget+QHBoxLayout+QLabel bar in
// Traktor's own Qt and insert it into the layout above the list view. All namespaced NI_6_5_3_TP2.
// App / widget tree:
static const uintptr_t A_qapp_topLevelWidgets = 0x101b9e2c8ULL; // QApplication::topLevelWidgets() -> QList<QWidget*> via x8 {d@0,ptr@8,size@0x10}
static const uintptr_t A_qapp_activeWindow    = 0x101b9e878ULL; // QApplication::activeWindow() -> QWidget*
static const uintptr_t A_qobj_objectName      = 0x10107fabcULL; // QObject::objectName() -> QString via x8
static const uintptr_t A_qobj_metaObject      = 0x1010886acULL; // QObject::metaObject() -> const QMetaObject*
static const uintptr_t A_qmeta_className      = 0x101052ec4ULL; // QMetaObject::className() -> const char*
static const uintptr_t A_qav_model            = 0x101d86da0ULL; // QAbstractItemView::model() -> QAbstractItemModel* (match to our DataSetModel)
// Widget / layout construction:
static const uintptr_t A_op_new               = 0x102ec56e4ULL; // operator new(ulong) -> void* (over-allocate; op delete frees by alloc bookkeeping)
static const uintptr_t A_qwidget_ctor         = 0x101bd1d50ULL; // QWidget::QWidget(QWidget* parent x1, QFlags x2)  [this x0]
static const uintptr_t A_qwidget_setParent    = 0x101be69acULL; // QWidget::setParent(QWidget*)
static const uintptr_t A_qwidget_layout       = 0x101be353cULL; // QWidget::layout() = *(void**)(*(void**)(this+8)+0x98)
static const uintptr_t A_qwidget_show         = 0x101be374cULL; // QWidget::show()
static const uintptr_t A_qhboxlayout_ctor     = 0x101ba7f60ULL; // QHBoxLayout::QHBoxLayout(QWidget* parent)  [this x0, parent x1]
static const uintptr_t A_qboxlayout_insertW   = 0x101ba7d68ULL; // QBoxLayout::insertWidget(int idx, QWidget*, int stretch, QFlags align)
static const uintptr_t A_qboxlayout_addW      = 0x101ba7e68ULL; // QBoxLayout::addWidget(QWidget*, int stretch, QFlags align)
static const uintptr_t A_qlabel_ctor_text     = 0x101cafe88ULL; // QLabel::QLabel(QString* x1, QWidget* parent x2, QFlags x3)  [this x0]
static const uintptr_t A_qlabel_setText       = 0x101cafc34ULL; // QLabel::setText(QString*)
#define OFF_QWIDGET_DPTR   0x8    // QWidget -> d_ptr (QWidgetPrivate*)
#define OFF_QWPRIV_LAYOUT  0x98   // QWidgetPrivate -> QLayout* (what layout() returns)
#define OFF_QOBJDATA_CHILDREN 0x18 // QObjectData.children (QList<QObject*>) — QObjectData is polymorphic (vptr@0, q_ptr@8, parent@0x10, children@0x18); VERIFY live
// QList<T*> memory: ptr = *(T***)(ql+8), size = *(long*)(ql+0x10).

// === Embedded UI REVISED 2026-11-22: Traktor Pro 4 is a QML / Qt Quick app ===
// VERIFIED from the symbol dump: QApplication::QApplication ctor is used 0 times,
// QGuiApplication 1 time; QQuickWindow (95) / QQuickView (36) / QQuickItem everywhere.
// => There is NO QWidget tree for the main UI (topLevelWidgets() is legitimately
// empty). The QWidget block ABOVE is a dead end for the main UI. Inject QQuickItems
// into the QQuickWindow's contentItem scene graph instead. All namespaced NI_6_5_3_TP2.
static const uintptr_t A_qgui_topLevelWindows     = 0x101884b0cULL; // QGuiApplication::topLevelWindows() -> QList<QWindow*> via x8
static const uintptr_t A_qgui_allWindows          = 0x101884ae4ULL; // QGuiApplication::allWindows() -> QList<QWindow*> via x8
static const uintptr_t A_qquickwindow_contentItem = 0x1012d5e34ULL; // QQuickWindow::contentItem() -> QQuickItem*  [this x0]
static const uintptr_t A_qqwin_polishItems        = 0x1012d411cULL; // QQuickWindowPrivate::polishItems(this) — NOT used by Traktor's render path
// Traktor drives QML via QQuickRenderControl (offscreen render composited into its own window),
// so the window is NOT in topLevelWindows() and the standard polishItems hook never fires. These
// three run each frame on the GUI thread with `this`=QQuickRenderControl; the live QQuickWindow is
// window = *(*(this + OFF_QQRC_DPTR) + OFF_QQRCP_WINDOW). (Decompiled 2026-11-22.)
static const uintptr_t A_qqrc_sync                 = 0x101282b00ULL; // QQuickRenderControl::sync()  [this x0]
static const uintptr_t A_qqrc_render               = 0x101282b9cULL; // QQuickRenderControl::render() [this x0]
static const uintptr_t A_qqrc_polishItems          = 0x101282a98ULL; // QQuickRenderControl::polishItems() [this x0]
// sync()/render() above are inlined into Traktor's frame driver (never fire). The big scene-graph
// workers they call are not inlined: hook these, `this`=QQuickWindowPrivate, window = *(this+8) (q_ptr).
static const uintptr_t A_qqwin_syncSceneGraph      = 0x1012d4bacULL; // QQuickWindowPrivate::syncSceneGraph()   [this x0]
static const uintptr_t A_qqwin_renderSceneGraph    = 0x1012d547cULL; // QQuickWindowPrivate::renderSceneGraph() [this x0]
static const uintptr_t A_qgui_focusWindow          = 0x101884abcULL; // QGuiApplication::focusWindow() -> QWindow*
static const uintptr_t A_qmeta_activate3           = 0x10108654cULL; // QMetaObject::activate(QObject* x0, int, void**)
static const uintptr_t A_qmeta_activate4           = 0x101085474ULL; // QMetaObject::activate(QObject* x0, QMetaObject*, int, void**) — moc signals use THIS
static const uintptr_t A_qmeta_activate3b          = 0x10108652cULL; // QMetaObject::activate(QObject* x0, int, int, void**)
static const uintptr_t A_qquickitem_window         = 0x10123e5a8ULL; // QQuickItem::window() -> QQuickWindow* [this x0]
static const uintptr_t A_qquickitem_update          = 0x10124864cULL; // QQuickItem::update() — per-frame for animated items; this=QQuickItem
static const uintptr_t A_qquickitem_polish          = 0x101248714ULL; // QQuickItem::polish() — this=QQuickItem
// Traktor's real QML architecture (NI classes): QtManager -> OffscreenRenderControl -> QOffscreenView,
// each wrapping a QQuickWindow rendered offscreen and composited. These NI funcs are NOT inlined.
static const uintptr_t A_osview_onWindowChanged     = 0x10099efb4ULL; // QOffscreenView::onWindowChanged(QQuickWindow* x1) [this x0] — window handed in directly
static const uintptr_t A_osrc_offscreenView         = 0x100995c2cULL; // OffscreenRenderControl::offscreenView() -> QOffscreenView*
static const uintptr_t A_qtmgr_offscreenRenderControl = 0x10000d458ULL; // QtManager::offscreenRenderControl(bool) -> OffscreenRenderControl* (controller SCREENS, not main UI)
static const uintptr_t A_qtmgr_instance            = 0x105759a00ULL; // QtManager singleton (static storage address; instance() returns &this). engine = *(inst+8)
// QBrowser = the browser list QML item (registered Traktor.Gui.Browser, a QQuickItem). Hooking
// update() captures the live item -> QQuickItem::window() = the MAIN UI window, and the item is the
// cards-bar injection point.
static const uintptr_t A_qbrowser_update           = 0x100998904ULL; // QBrowser::update() [this x0] — only on interaction
static const uintptr_t A_qbrowser_ctor1            = 0x100996e24ULL; // QBrowser::QBrowser(QObject*) [this x0]
static const uintptr_t A_qbrowser_ctor2            = 0x100996e34ULL; // QBrowser::QBrowser(int, QObject*, int) [this x0] — QML type ctor
static const uintptr_t A_qbrowser_ctor3            = 0x1009971e8ULL; // QBrowser::QBrowser(QObject*) [this x0]

// === Traktor's REAL main-UI toolkit: NI::NGL::Form + Controls (native GL) ===
// The main window is MainWindowNGLView; it builds the UI from NI::NGL::Form objects placed via
// PanelFormManager (createHeaderForms/createDeckForms/createBrowserForm/...). QML (QBrowser,
// waveforms) is embedded INSIDE these forms. Native-inside = add an NI::NGL::Form + Controls.
static const uintptr_t A_mwview_ctor               = 0x10097025cULL; // MainWindowNGLView(FormManager*, IAppBase*) [this x0]
static const uintptr_t A_mwview_createBrowserForm  = 0x100977ce4ULL; // MainWindowNGLView::createBrowserForm(PanelFormManager* x1) [this x0] -> BrowserForm*
static const uintptr_t A_mwview_createHeaderForms  = 0x10097204cULL; // MainWindowNGLView::createHeaderForms(PanelFormManager*, FxPanelsViewModel*)
static const uintptr_t A_panelmgr_addFormGUIManager= 0x10079b198ULL;  // PanelFormManager::addFormGUIManager(Form* x1, uint zone x2) [this x0]
// NI::NGL::Form (base) vtable-ish API (addresses are the out-of-line impls):
static const uintptr_t A_nglform_ctor              = 0x10217ae58ULL; // NI::NGL::Form::Form()
static const uintptr_t A_nglform_setID             = 0x10217b34cULL; // NI::NGL::Form::setID(uint)
static const uintptr_t A_nglform_create            = 0x10217b354ULL; // NI::NGL::Form::create(uint)
static const uintptr_t A_nglform_resize            = 0x10217b408ULL; // NI::NGL::Form::resize(Point*)
static const uintptr_t A_nglform_setVisible        = 0x10217b6b0ULL; // NI::NGL::Form::setVisible(bool)
static const uintptr_t A_nglform_setVisibleSize    = 0x10217b86cULL; // NI::NGL::Form::setVisibleSize(Point*)
// NI::NGL::LabelControl (a native text control):
static const uintptr_t A_ngllabel_ctor             = 0x10218d9d0ULL; // NI::NGL::LabelControl::LabelControl()
static const uintptr_t A_ngllabel_createControl    = 0x10218e734ULL; // NI::NGL::LabelControl::createControl()
static const uintptr_t A_ngllabel_setText_cstr     = 0x10218ecf0ULL; // NI::NGL::LabelControl::setText(char*)
static const uintptr_t A_ngllabel_setColor         = 0x10218db70ULL; // NI::NGL::LabelControl::setColor(uint argb)
static const uintptr_t A_nglform_addControl        = 0x10217c424ULL; // NI::NGL::Form::addControl(Control* x1) [this x0]
static const uintptr_t A_nglctl_setPosition        = 0x1021a018cULL; // NI::NGL::Control::setPosition(Rect* x1) — Rect = 4 ints {l,t,r,b}
static const uintptr_t A_nglctl_setVisible         = 0x1021a3688ULL; // NI::NGL::Control::setVisible(bool)
// Click routing (out_click2, 2026-11-23). Form::onMouseEvent@0x10217e084 walks its control list
// (form+0x80) and, for each VISIBLE (ctrl+0x7c) control whose rect contains the point (data x@0,y@4),
// calls ctrl->vtable[0x10] = onEvent(evt, EventData*) until one returns 1. LabelControl does NOT
// override onEvent, so our labels land in the base Control::onEvent — hook it, filter by our ptrs.
static const uintptr_t A_nglctl_onEvent            = 0x10219ff4cULL; // NI::NGL::Control::onEvent(uint evt, EventData*)
// Main-window LEFT TREE = BrowserStdTree (NGL TreeControl), proven 2026-11-23 by a treeSelect
// backtrace: selectTreeNode -> BrowserForm::treeSelect. (DataSetModel is the controller/D2 model.)
static const uintptr_t A_bst_selectTreeNode  = 0x1007be950ULL; // selectTreeNode(string const&, bool,bool,bool) — capture this
static const uintptr_t A_bst_createTreeNode  = 0x1007f3d00ULL; // createTreeNode(Node* parent, Node* before, bool, TreeNodeInfo const&) -> Node*
static const uintptr_t A_bst_onReloadForm    = 0x1007f40a8ULL; // onReloadForm() — rebuilds the whole tree
static const uintptr_t A_ngllabel_setPictureID = 0x10218e83cULL; // LabelControl::setPictureID(uint) — loads via the form's PictureManager
static const uintptr_t A_ngllabel_setPictureIndex = 0x10218dd50ULL; // LabelControl::setPictureIndex(uint) — frame of a multi-frame picture (+0x154)
static const uintptr_t A_ngllabel_setDrawMode = 0x10218db24ULL; // LabelControl::setDrawMode(eDrawMode) (+0xd0)
static const uintptr_t A_pic_animWidth  = 0x102299cd8ULL; // NI::UIA::Picture::getAnimationWidth() — one frame
static const uintptr_t A_pic_animHeight = 0x102299d00ULL; // NI::UIA::Picture::getAnimationHeight()
static const uintptr_t A_nglswitch_onInit = 0x1021a5294ULL; // SwitchControl::onInit(Form*) — picture id at +0xc0
static const uintptr_t A_bst_selectTreeNode5 = 0x1007f9478ULL; // selectTreeNode(string const&, EBrowserCategory*, bool,bool,bool)
static const uintptr_t A_ngllabel_setHorzAlignment = 0x10218ec6cULL; // LabelControl::setHorzAlignment(eAlignment) (+0xf8)
static const uintptr_t A_ngltextitem_setVertAlignment = 0x10218ecd4ULL; // TextItem::setVertAlignment(eAlignment); label's TextItem @+0xd8
static const uintptr_t A_tni_ctor            = 0x10081c770ULL; // TreeNodeInfo(cat, string const& name, int icon, int icon2, bool)
static const uintptr_t A_ngltree_deleteNode  = 0x1021af934ULL; // TreeControl::deleteNode(Node*) (no redraw)
static const uintptr_t A_ngltree_invalidate  = 0x1021b0f24ULL; // TreeControl::invalidate(uint)
#define OFF_BST_TREECTL     0x598  // BrowserStdTree: TreeControl*
#define OFF_TREECTL_ROOT    0x5a0  // TreeControl: embedded root Node
#define OFF_BST_EXTCOLL_BEGIN 0x5f0 // BrowserStdTree: std::map<int,ExternalCollectionInfo> begin node; end sentinel @0x5f8
#define OFF_EXTCOLL_NODE    0x60   // map tree-node: Node* of that external collection's root
#define OFF_NODE_INFO       0x30   // Node: TreeNodeInfo*
#define OFF_NODE_KIDS_BEGIN 0x48   // Node: children vector<Node*> begin/end @0x48/0x50
#define OFF_NODE_PARENT     0x60
#define OFF_TNI_CAT         0x18   // TreeNodeInfo: category; path std::string @0x20; Node* @0x50
#define OFF_TNI_PATH        0x20
#define OFF_TNI_NODE        0x50
#define SZ_TNI              0x90
#define TK_TREE_CAT         0x7a   // unused EBrowserCategory for the Track Kommander root
// The browser LIST = BrowserForm+0x708, a refcounted ContentList that treeSelect picks per
// category; updateContentListMapping re-sorts it and marks the view dirty (+0x700 = 1).
// ContentList::create(type 0) == operator new(0x160) + ContentList(this, SmartPtr<mgr> const&, bool)
// + RefCount::addRef(this, true). add() commits to the collection ONLY for an entry the
// collection lacks or when its bool is true — false + collection tracks = no library write.
static const uintptr_t A_cl_ctor             = 0x10049432cULL; // ContentList(SmartPtr<ContentCollectionMgr> const&, bool)
static const uintptr_t A_cl_add              = 0x100495a74ULL; // add(SmartPtr<Entity const> const&, int idx(-1=end), bool, tCommitHandle*, int)
static const uintptr_t A_cl_clear            = 0x100496dc8ULL; // clear()
static const uintptr_t A_refcount_addRef     = 0x100b62d30ULL; // RefCount::addRef(bool)
static const uintptr_t A_bf_updateCLMapping  = 0x1007ce3a4ULL; // BrowserForm::updateContentListMapping()
// What gates actions on the list (diagnostic hooks, 2026-11-23).
static const uintptr_t A_bst_getSelCategory      = 0x1007e9e34ULL; // BrowserStdTree::getSelectedBrowserCategory()
static const uintptr_t A_bst_getSelTreeNodeInfo  = 0x1007c4740ULL; // BrowserStdTree::getSelectedTreeNodeInfo()
static const uintptr_t A_bst_canDragAndDrop      = 0x1007f9878ULL; // BrowserStdTree::canDragAndDrop(...)
static const uintptr_t A_bf_showContextMenu      = 0x1007c8b38ULL; // BrowserForm::showContextMenu(...)
static const uintptr_t A_bf_createContextMenu    = 0x1007c30bcULL; // BrowserForm::createContextMenu(...)
static const uintptr_t A_bf_getIndexFromRow     = 0x1007d8a24ULL; // BrowserForm::getIndexFromRow(int) — via +0x710 mapping
#define OFF_BF_TREEPATH     0x3b0  // BrowserForm: current tree path (libc++ std::string)
#define OFF_BF_CATEGORY     0x3cc  // BrowserForm: current EBrowserCategory
#define OFF_BF_LISTPAGE     0x6d0  // BrowserForm: the list page (sub-form); its own list ref @+0x350
#define OFF_PAGE_LIST       0x350
static const uintptr_t A_bf_getStructPathFromTreePath = 0x1007c2060ULL; // (this x0 unused, string const& x1) -> StructurePath via x8
static const uintptr_t A_ccm_createPlaylist  = 0x10047adf4ULL; // createPlaylist(StructurePath const&, bool) -> SmartPtr<ContentList> via x8 (sret!)
static const uintptr_t A_ccm_createFolder    = 0x100486e08ULL; // createFolder(StructurePath const&) -> bool (tk_kplaylists.mm)
static const uintptr_t A_ccm_deleteList      = 0x10047b084ULL; // deleteList(StructurePath const&) -> bool: a playlist or a smartlist
static const uintptr_t A_ccm_deleteFolder    = 0x100486f24ULL; // deleteFolder(StructurePath const&) -> bool
static const uintptr_t A_ccm_commitList      = 0x10047eee0ULL; // commitList(SmartPtr<ContentList const> const&) — store + notify
#define OFF_BST_PATHMAP     0x688  // BrowserStdTree: std::map<string tree path, TreeNodeInfo*>
#define OFF_BF_CONTENTLIST  0x708
#define SZ_CONTENTLIST      0x160
#define NGL_EVT_MOUSE       1000   // mouse button event (0x3ea/0x3eb = other mouse evts, 0x3ec/0x3ed enter/leave)
#define OFF_EVT_BUTTON      0x0c   // EventData: int button (1 = left)
#define OFF_EVT_STATE       0x10   // EventData: int state (1 = press, 2 = release, 3 = double-click)
static const uintptr_t A_nglform_invalidate        = 0x10217bc4cULL; // NI::NGL::Form::invalidate(uint)
static const uintptr_t A_ngllabel_setFontID        = 0x10218ea24ULL; // NI::NGL::LabelControl::setFontID(uint) — needed for text to draw
static const uintptr_t A_nglform_getControl        = 0x10217c3e0ULL; // NI::NGL::Form::getControl(uint idx) -> Control*
// Form layout offsets (from TraktorMainForm::addForm): type id @ +0xc (must be unique in the form map),
// width @ +0x28, height @ +0x2c (the SubFormControl is sized/placed from these). Control rect @ +0x38.
#define OFF_NGLFORM_TYPE   0x0c
#define OFF_NGLFORM_W      0x28
#define OFF_NGLFORM_H      0x2c
#define OFF_MWVIEW_APPBASE 0xa60  // MainWindowNGLView -> IAppBase* (used as *(view+0xa60))
#define OFF_QQRC_DPTR     0x8    // QQuickRenderControl -> d_ptr (QQuickRenderControlPrivate*)
#define OFF_QQRCP_WINDOW  0x88   // QQuickRenderControlPrivate -> QQuickWindow* window
#define OFF_QQWIN_DPTR    0x8    // QQuickWindow -> d_ptr (QQuickWindowPrivate*) (q_ptr/d_ptr both at +8 here)
static const uintptr_t A_qquickitem_childItems    = 0x10123aa70ULL; // QQuickItem::childItems() -> QList<QQuickItem*> via x8  [this x0]
static const uintptr_t A_qquickitem_x             = 0x10123ba38ULL; // QQuickItem::x() -> double (d0)  [this x0]
static const uintptr_t A_qquickitem_y             = 0x10123b6b4ULL; // QQuickItem::y() -> double (d0)
static const uintptr_t A_qquickitem_width         = 0x10123ba8cULL; // QQuickItem::width() -> double (d0)
static const uintptr_t A_qquickitem_height        = 0x10123b708ULL; // QQuickItem::height() -> double (d0)
static const uintptr_t A_qquickitem_setParentItem = 0x10123fcdcULL; // QQuickItem::setParentItem(QQuickItem*)
static const uintptr_t A_qquickitem_setX          = 0x10124d528ULL; // QQuickItem::setX(double d0)
static const uintptr_t A_qquickitem_setY          = 0x10124d7c4ULL; // QQuickItem::setY(double d0)
static const uintptr_t A_qquickitem_setWidth      = 0x10124ddccULL; // QQuickItem::setWidth(double d0)
static const uintptr_t A_qquickitem_setHeight     = 0x10124e4ccULL; // QQuickItem::setHeight(double d0)
static const uintptr_t A_qquickitem_setZ          = 0x10124b5b0ULL; // QQuickItem::setZ(double d0)
static const uintptr_t A_qquickitem_setVisible    = 0x10124c06cULL; // QQuickItem::setVisible(bool)

// Stem (goal 6): ContentEntryTrack stem metadata.
static const uintptr_t A_cet_getStemMetadata          = 0x10048b62cULL;
static const uintptr_t A_cet_getAssociatedStemFileMeta= 0x10048f00cULL;

// === Load/read Traktor's CURRENT SELECTION (#3) + load/read BY FILE PATH (#2) ===
// 2026-11-22. Derived from the shipped binary's own disassembly (otool), not a
// guess: see traktor-native/notes/phase1-status.md for the full trace.
//
// #3 — current selection. BrowserForm::getCurrentlySelectedContentEntry()
// (A_bf_getSelectedEntry, already above) returns SmartPtr<Entity const> via x8
// (reads its active ContentList at this+0x708, getTopSelection()+getEntry(int)).
// Narrow the Entity* to ContentEntryTrack* with the SAME __dynamic_cast that
// ContentCollectionMgr::getTrack uses internally: src/dst are these two
// type_info objects, 4th arg (src2dst hint) = 0.
static const uintptr_t A_ti_entity            = 0x1053c0a40ULL; // typeinfo traktor::browser::Entity  (dynamic_cast src)
static const uintptr_t A_ti_contententrytrack = 0x10540cf90ULL; // typeinfo ContentEntryTrack          (dynamic_cast dst)
static const uintptr_t A_refcount_release     = 0x100b62d88ULL; // traktor::browser::RefCount::release() const

// #2 — file path -> collection key -> track. URL::URL(boost::filesystem::path
// const&) copies its argument as a std::string@0 and tags the URL as a FILE
// (writes 1 at URL+0x18, so isFile() is true), so a plain libc++ std::string*
// can be passed as the path-like argument (same system libc++ ABI, via a stub).
// EntryKeyManager::getKeyFromURL(URL const&) then returns the existing
// collection key (uint via x8), 0 if the path is not in the collection; the
// proven getTrack(mgr,key) path takes it from there. The EntryKeyManager
// instance is the singleton slot that URL::getURL()/getURLFromKey use.
static const uintptr_t A_url_ctor_from_path = 0x100b6df3cULL; // URL::URL(boost::filesystem::path const&)  [sets file source +0x18=1]
static const uintptr_t A_url_dtor           = 0x1000423f0ULL; // URL::~URL()
static const uintptr_t A_ekm_getKeyFromURL  = 0x100b7835cULL; // EntryKeyManager::getKeyFromURL(URL const&) -> TypedEntryKey<uint> via x8
static const uintptr_t A_ekm_instance       = 0x105653f58ULL; // EntryKeyManager singleton pointer slot (used by URL::getURL)
static const uintptr_t A_ekm_instance_guard = 0x105653f60ULL; // its __cxa_guard byte (bit0 == initialised)
#define URL_OBJ_SIZE  0x40   // URL = variant{path std::string@0} + u32 kind@0x18 + u32 src@0x20 (0x24 used); pad generously

// Field offsets observed in the decompile.
#define OFF_TREECONTROL   0x598  // BrowserStdTree -> TreeControl*
#define OFF_ROOT_IN_TC    0x5a0  // root node container = *(this+0x598) + 0x5a0

// Our node.
#define TK_CATEGORY  0x2000      // a value outside the stock EBrowserCategory switch
#define TK_ICON      8           // reuse the Playlists icon id (known valid)
#define TK_NODE_NAME "Track Kommander"

// ── App-property registry (the C++ behind QML's AppProperty; 2026-11-24) ─────────────────────
// csi::app() returns the application; at +0x38 it holds a libc++
// std::map<std::string, std::shared_ptr<csi::application::Entity>> keyed by the very paths D2's
// QML uses ("app.traktor.decks.1.play" ...). Entity+8 (int) == 1 marks a Property.
// getTypedAppProperty<T>(path const&) -> shared_ptr<TypedProperty<T>> via x8 ({0,0} on a type
// mismatch). TypedProperty<T>::get() const / set(T const&) are the access-checked accessors
// QtPropertyWrapper uses. Property vtable: +0x28 type_id() -> type_info const&, +0x48 connectValue(
// std::function<void()> const&) -> nod::connection (3 words) via x8 — how QML gets onValueChanged.
static const uintptr_t A_csi_app            = 0x100be00f0ULL; // csi::app()
static const uintptr_t A_get_tprop_float    = 0x100383310ULL; // getTypedAppProperty<float>
static const uintptr_t A_get_tprop_bool     = 0x100365df4ULL; // getTypedAppProperty<bool>
static const uintptr_t A_get_tprop_int      = 0x1003b6b40ULL; // getTypedAppProperty<int>
static const uintptr_t A_get_tprop_string   = 0x1003eaec0ULL; // getTypedAppProperty<std::string>
static const uintptr_t A_tprop_float_get    = 0x10023a658ULL; // TypedProperty<float>::get() -> s0
static const uintptr_t A_tprop_float_set    = 0x10023a6bcULL; // TypedProperty<float>::set(float const&)
static const uintptr_t A_tprop_bool_get     = 0x10023cc8cULL; // TypedProperty<bool>::get() -> w0
static const uintptr_t A_tprop_bool_set     = 0x10023ccf0ULL; // TypedProperty<bool>::set(bool const&)
static const uintptr_t A_tprop_int_get      = 0x10023af0cULL; // TypedProperty<int>::get() -> w0
static const uintptr_t A_tprop_int_set      = 0x10023af70ULL; // TypedProperty<int>::set(int const&)
static const uintptr_t A_tprop_string_get   = 0x10023b9dcULL; // TypedProperty<std::string>::get() -> std::string via x8
static const uintptr_t A_tprop_string_set   = 0x10023ba4cULL; // TypedProperty<std::string>::set(std::string const&)
#define OFF_APP_ENTITY_MAP     0x38   // csi::app() + 0x38: std::map<string, shared_ptr<Entity>>
#define OFF_ENTITY_KIND        0x08   // int; 1 = Property
#define VT_PROP_TYPE_ID        0x28   // Property vtable: type_id() -> type_info const& (name ptr at +8, top bit masked)
#define VT_PROP_CONNECT_VALUE  0x48   // Property vtable: connectValue(function const&) -> connection (x8)

// ContentEntryTrack fields beyond MediaItem (2026-11-24, from TrackVisitor::getTrackValue and
// getMusicalKeyAsStringForExport): bitrate int (bps) at +0x240 ("%d kbps" of /1000), musical key
// index int at +0x274 (itl::harmonic key index; -1 = none).
#define OFF_CET_BITRATE     0x240
#define OFF_CET_PLAYTIME    0x258  // i32 seconds (TrackVisitor::getTrackValue, the Time column, 2026-11-24)
#define OFF_CET_KEYINDEX    0x274

// Playlist tree (2026-11-24, from NmlSax::ListStructureWrapper / SubNodesWrapper, the NML writer).
// ContentCollectionMgr+0x148 is the StructureTree; its root StructureNode is the tree itself
// (getRoot returns this): the invisible "$ROOT" folder. StructureNode: +0 int type (1 folder,
// 2 playlist, 3 smartlist, 4 search results), +8 std::string name, +0x28 traktor::browser::List*
// (types 2-4), folder children = std::list<StructureNode*> {sentinel @+0x48, first @+0x50; element
// {prev, next, node @+0x10}}. The writer skips unnamed nodes and playlists named "$...".
// ContentList entries: vector of 8-byte items at +0x30..+0x38, the collection key (u32) first.
// A smartlist's List is a ContentListSmart; its SearchExpression is at +0x160 and
// SearchExpressionPrinter::print(expr const&, std::string&) gives the NML's QUERY text.
#define OFF_CCM_STRUCTURE   0x148
#define OFF_SN_NAME         0x08
#define OFF_SN_LIST         0x28
#define OFF_SN_KIDS         0x48
#define OFF_CL_ENTRIES      0x30
#define OFF_CLS_FILTER      0x160
static const uintptr_t A_query_print        = 0x100b7bf4cULL; // SearchExpressionPrinter::print(SearchExpression const&, std::string&) -> int
static const uintptr_t A_ti_list            = 0x10540cfc0ULL; // typeinfo traktor::browser::List
static const uintptr_t A_ti_contentlist     = 0x10540cfd8ULL; // typeinfo ContentList
static const uintptr_t A_ti_contentlistsmart= 0x10540d008ULL; // typeinfo ContentListSmart

// Cue points (2026-11-24). The track's CuePointCollection is at ContentEntryTrack+0x460: a vector
// of 0x60-byte elements (getCuePoint(i) = begin + i*0x60). Layout MEASURED from raw bytes against
// the NML (the NML writer's offsets are for a different, copied CuePoint and do NOT apply here):
// +0 std::string name, +0x18 int hotcue slot (on a grid marker this word holds the grid's BPM as
// a double, and the slot is -1), +0x30 int type, +0x38 double start ms, +0x40 int display order,
// +0x44 int repeats. Length not located (0 everywhere sampled). BPM quality: float at
// MediaItem+0x254 (the NML writes it x100, and only when > 0).
static const uintptr_t A_cpc_getCueCount  = 0x100b66828ULL; // CuePointCollection::getCueCount() const -> int
static const uintptr_t A_cpc_hasCue       = 0x100b667f8ULL; // CuePointCollection::hasCue(int) const -> bool
static const uintptr_t A_cpc_getCuePoint  = 0x100b66b58ULL; // CuePointCollection::getCuePoint(int) -> CuePoint*
#define OFF_CET_CUES        0x460
#define OFF_MI_BPMQUALITY   0x254
#define OFF_CUE_HOTCUE      0x18
#define OFF_CUE_TYPE        0x30
#define OFF_CUE_START       0x38
#define OFF_CUE_ORDER       0x40
#define OFF_CUE_REPEATS     0x44
#define CUE_TYPE_GRID       4

// The UI's rename of a playlist-tree node (folders too): renamePlaylist(StructurePath const&,
// std::string const& new name) -> bool; builds the new path and calls movePlaylist, as the tree's
// own rename does. Used once to rename the old "Track Kommander" folder to "Kommander".
static const uintptr_t A_bf_renamePlaylist = 0x1007cc4d8ULL;

// Tooltips (2026-11-24). Traktor asks TooltipManager::getTooltip(Form*, Control*, ToolTip&) for
// the control under the mouse; ToolTip = {std::string title @0, std::string text @0x18} (a
// collection string "Title\nText" is split into the two). The top-right [i] button only sets the
// byte HintFormManager::s_floatingTooltipsEnabled (MainWindow::onToolTippToggle).
static const uintptr_t A_ttm_getTooltip        = 0x1007b1c30ULL; // bool getTooltip(Form*, Control*, ToolTip&) const
static const uintptr_t A_hint_tooltips_enabled = 0x10565ae40ULL; // HintFormManager::s_floatingTooltipsEnabled (bool)
#define OFF_TOOLTIP_TEXT 0x18
static const uintptr_t A_nglform_onMouseEvent = 0x10217e084ULL; // NI::NGL::Form::onMouseEvent(evt, EventData*) — every mouse event of a form

// Our own pictures (2026-11-24). NI::UIA::Picture is concrete: PictureManager does
// operator new(0x80) + Picture::Picture(); an empty one is made as TreeDragDropPicture does:
// setSize(w, h, true), vtable+0x10(1), clear(0). Pixels: uint32 ARGB at +0x30, width +0x38,
// height +0x3c, rows contiguous. A LabelControl draws the Picture* at +0xc8 (draw mode 2).
static const uintptr_t A_pic_ctor    = 0x102299b10ULL; // Picture::Picture()
static const uintptr_t A_pic_setSize = 0x102299bdcULL; // Picture::setSize(int w, int h, bool)
static const uintptr_t A_pic_clear   = 0x10229b310ULL; // Picture::clear(uint argb)
#define SZ_PICTURE        0x80
#define OFF_PIC_PIXELS    0x30
#define OFF_PIC_W         0x38
#define OFF_PIC_H         0x3c
#define OFF_LBL_PICTURE   0xc8

// Audio device selection (2026-11-24). Traktor's choice is the setting "Audio.DeviceName.Mac"
// (+ "Audio.DriverName.Mac" = CoreAudio), written through Settings::setEntry(key, Value, notify);
// the preferences' Audio Setup page writes it from its onControlEvent. Value: int type @0
// (3 = string), std::string @8.
static const uintptr_t A_settings_setEntry   = 0x100a20398ULL; // Settings::setEntry(string const&, Value const&, bool)
static const uintptr_t A_settings_getEntry   = 0x100a24cc8ULL; // Settings::getEntry(string const&, Value&)
static const uintptr_t A_audiosetup_onCtlEvt = 0x10087d068ULL; // PreferenceDialogPage::AudioSetup::onControlEvent
#define VALUE_TYPE_STRING 3
#define OFF_VALUE_STRING  0x08
static const uintptr_t A_value_ctor_string = 0x10002f4b4ULL; // Value::Value(std::string const&) — type 3, string @8
// The audio engine's own device choice: InterfaceNSA::audioDeviceGetIDList(map<string, void*>&,
// string&) lists the devices (key -> device id handle); audioDeviceSelectByID(void* id, bool) opens
// one. Writing "Audio.DeviceName.Mac" alone does NOT switch the device (measured 2026-11-24).
static const uintptr_t A_nsa_getIDList   = 0x1022759b0ULL;
static const uintptr_t A_nsa_selectByID  = 0x102275c9cULL;
// Every device switch ends in ControlCenter::activateDevice(AudioDeviceDescriptor const&); a
// manual pick (Preferences menu → AudioSettings::setDevice → SettingsBridge) arrives through
// onAudioDeviceChangedManually; Traktor's start through enable. AudioDeviceDescriptor:
// {std::string OS device name @0 (= CoreAudio name), std::string generic name @0x18}.
static const uintptr_t A_cc_activateDevice  = 0x100181db8ULL;
static const uintptr_t A_cc_changedManually = 0x100182b98ULL;
static const uintptr_t A_cc_enable          = 0x100183834ULL;
#define OFF_ADD_GENERIC 0x18

// The deck's GRID panel (2026-11-24, from the symbol table, not decompiled): one AdvancedGridForm
// per deck, built with (IAppBase*, deck index); resize lays out ↺GRID / ↺BPM / TAP / lock / ...
// The grid action itself is the property app.traktor.decks.N.track.grid.copy_phase_from_master.
static const uintptr_t A_advgrid_ctor   = 0x1009089b8ULL; // AdvancedGridForm::AdvancedGridForm(IAppBase*, unsigned)
static const uintptr_t A_advgrid_resize = 0x100909df8ULL; // AdvancedGridForm::resize(NI::GP::Point const&)

// Writing a hotcue into the collection (2026-11-24, from TrackDeck::storeHotCue and its lambda,
// read with otool): executeOnTrack(mgr, key, std::function<bool(ContentEntryTrack&)>) runs the
// lambda on the stored track: idx = addCue(cue, false) (int, < 0 = refused); assignHotCue(idx,
// slot); setUpdateAuthor(3, true); return idx >= 0. The CuePoint is built with its default
// constructor and setters (no out-of-line destructor: names kept <= 22 chars, inline in the string).
static const uintptr_t A_ccm_executeOnTrack  = 0x10048139cULL;
static const uintptr_t A_cet_addCue          = 0x100490764ULL; // int addCue(CuePoint const&, bool)
static const uintptr_t A_cet_assignHotCue    = 0x100490a88ULL; // void assignHotCue(int cueIdx, int slot)
static const uintptr_t A_cet_setUpdateAuthor = 0x10048f1b0ULL; // setUpdateAuthor(UpdateAuthor, bool)
static const uintptr_t A_cue_ctor            = 0x100b65908ULL; // CuePoint::CuePoint()
static const uintptr_t A_cue_setName         = 0x100b65b1cULL; // setName(std::string const&)
static const uintptr_t A_cue_setType         = 0x100b65b3cULL; // setType(Type) — NML TYPE (0 cue, 4 grid, 5 loop)
static const uintptr_t A_cue_setStart        = 0x100b65d98ULL; // setStartPosInMs(double)
static const uintptr_t A_cue_setLength       = 0x100b65dc0ULL; // setLengthInMs(double)
#define TK_UPDATE_AUTHOR_DECK 3  // what storeHotCue passes

// TK's own columns in Traktor's track list (tk_columns.mm, 2026-11-24, symbols + otool):
static const uintptr_t A_tlc_getCellValue        = 0x10082d2a0ULL; // (this, row, colType, AlphaNumericalValue&)
static const uintptr_t A_tlc_getHeaderText       = 0x10082d854ULL; // (this, colType) -> std::string const&
static const uintptr_t A_lcb_isColumnSortable    = 0x1021bc084ULL; // ListControlBase (const this, col) -> bool
static const uintptr_t A_lcb_onNumColumnsChanged = 0x1021904e0ULL; // ListControlBase: re-lay the columns out
static const uintptr_t A_anv_setString           = 0x10217ac9cULL; // AlphaNumericalValue::setString(std::string const&)
static const uintptr_t A_lcb_setColumnWidth      = 0x102190978ULL; // ListControlBase::setColumnWidth(int VIEW col, int w)
static const uintptr_t A_lcc_ctor                = 0x1021bc1f0ULL; // ListControlCustomColumns(int nTypes); TraktorListControl passes 38
static const uintptr_t A_tlc_getPopupMenuEntry   = 0x10082d5d0ULL; // (const this, type) -> std::string via x8; "" = not in the header menu
static const uintptr_t A_tlc_getTrackByIndex     = 0x100829b74ULL; // (const this, entry) -> SmartPtr<ContentEntryTrack> via x8
static const uintptr_t A_tlc_getSettingsNormal   = 0x100828ba4ULL; // (const this, std::string&) — the layout Traktor saves
static const uintptr_t A_tlc_setSettingsNormal   = 0x100828bc8ULL; // (this, std::string const&) — the layout Traktor loads
static const uintptr_t A_tlc_getSettingsHistory  = 0x100828c94ULL;
static const uintptr_t A_tlc_setSettingsHistory  = 0x100828cb8ULL;
static const uintptr_t A_lcc_createColumnMenu    = 0x1021c08c8ULL; // ListControlCustomControls::createColumnContextMenu(PopupMenu&) const
static const uintptr_t A_lcc_enableColumn        = 0x1021bcbccULL; // (this, type, bool) — show/hide a column type
static const uintptr_t A_lcc_moveColumn          = 0x1021bd49cULL; // (this, fromView, toView)
static const uintptr_t A_popup_addEntry          = 0x10218716cULL; // PopupMenu::addEntry(char const* text, char const* shortcut, int id)
static const uintptr_t A_popup_setState          = 0x102189e00ULL; // PopupMenu::setState(int POSITION, int state) — 1 dot, 2 none
static const uintptr_t A_bf_sortCurrentList      = 0x1007cdf34ULL; // BrowserForm::sortCurrentList(std::list<SortCriteria>) — by value (caller's temp)
static const uintptr_t A_bf_getCurrentListSorting = 0x1007ce28cULL; // (const this) -> std::list<SortCriteria> via x8; per playlist path
static const uintptr_t A_tlc_sortRequest         = 0x10082d878ULL; // TraktorListControl::sortRequest(unsigned col, bool) — a header click
#define VT_LCC_GETVIEWCOLUMN 0x2a0  // virtual int getViewColumn(type) const: -1 = hidden
#define VT_LCC_GETDATACOLUMN 0x2a8  // virtual int getDataColumn(view) const: the type
#define VT_LCB_GETCOLUMNWIDTH 0x148 // virtual int getColumnWidth(view) const (as getSettings uses it)
static const uintptr_t A_lcb_sortColumn          = 0x1021baf34ULL; // ListControlBase::sortColumn(VIEW col, eSortDirection) — a header click
static const uintptr_t A_lcd_getHeaderHeight     = 0x1021c0e68ULL; // ListControlDefImpl::getHeaderHeight() const (virtual): font + paddings
static const uintptr_t A_tlc_drawHeaderCell      = 0x10082bb40ULL; // TraktorListControl::drawHeaderCell(Picture&, VIEW col, Rect const& {l,t,r,b})
static const uintptr_t A_tlc_drawListCell        = 0x10082be68ULL; // (this, Picture&, row, VIEW col, Rect const& {l,t,r,b})
// The list's cell text colour (drawListCell 0x10082bfe0..c158, its Key column's keyColor): owner =
// this+0x1670 (vtable slot 0x20 returns an object), style = *(that + 0x10), two ARGB words at
// style+0x6c. keyColor's table (0x1031e4bf0) is 0xFFRRGGBB.
#define OFF_TLC_TEXT_OWNER   0x1670
#define VT_TEXT_OWNER_STYLE  0x20
#define OFF_TEXT_STYLE       0x10
#define OFF_STYLE_TEXT_ARGB  0x6c
#define TK_SORT_ATTR_INDEX   0      // EntryAttribute of the "#" column (attributeForColumn(1))
static const uintptr_t A_cet_removeCue       = 0x100490df8ULL; // ContentEntryTrack::removeCue(int idx)
