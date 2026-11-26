// tkw_audio.cpp — Traktor's audio device choice on Windows (port of ../../src/tk_audio.mm).
//
// Traktor keeps its audio device in the setting "Audio.DeviceName.Win" (Mac ".Mac"; both strings
// are in the Windows exe), written through Settings::setEntry. Every "Audio.*" write is logged
// (key, value, whether it came from the preferences' Audio Setup page, the thread), and the
// Settings object is kept for reading Traktor's settings from memory (tk_setting_get).
//
// ControlCenter::activateDevice is replaced: each switch is classified (manual / kommander /
// startup / auto) and TK's decision callback may veto it — the original is then not called and
// the current device stays. TK restores a device through onAudioDeviceChangedManually, the same
// path as the User's own pick.
//
// Windows differences (win/map/traktor_4_5_1_21_win.json):
// - onAudioDeviceChangedManually's `this` is ControlCenter+8 (its secondary vtable): libtk calls
//   it with the ControlCenter it saw in activateDevice + 8.
// - AudioDeviceDescriptor is 0x50 bytes: an 8-byte (zeroed) header then the OS name std::string
//   at +0x08, again for the generic name at +0x30 (Mac {string @0, string @0x18}).
// - Settings Value: int type @0, MSVC std::string @+8 (heap only when its capacity > 15), >= 0x30.
// - The connected devices come from the Windows audio endpoints (MMDevice API), one line per
//   active endpoint, in place of CoreAudio.
#include <objbase.h>
#include <mmdeviceapi.h>
#include <string.h>

#include <atomic>
#include <map>
#include <mutex>
#include <new>

#include "tkw.h"

#pragma comment(lib, "ole32.lib")

TKW_EXPORT int tk_audio_activate(const char* os_name);

namespace {

const char kDeviceKey[] = "Audio.DeviceName.Win";
const int kValueTypeString = 3;       // Value(std::string) writes 3 (0x509cb0)
const int kValueTypeStringArray = 7;  // Mac: one joined std::string at the same place (unchecked on Windows)
const size_t kValueSize = 0x60;       // >= 0x30 measured; room to spare
const size_t kDescSize = 0x50;
const size_t kDescOs = 0x08, kDescGeneric = 0x30;

std::atomic<int> g_in_audio_setup{0};  // > 0 while AudioSetup::onControlEvent runs
std::atomic<int> g_in_manual{0}, g_in_enable{0};
std::atomic<void*> g_settings{nullptr};  // Traktor's Settings (seen on its first write)
std::atomic<void*> g_nsa{nullptr};       // NI::AB::InterfaceNSA (seen on its first device select)
// activateDevice can run on Traktor's other threads: what it shares with the UI thread is
// guarded by g_audio_mu (never held while calling Traktor or TK).
std::mutex g_audio_mu;
void* g_control_center = nullptr;  // primary ControlCenter (seen in activateDevice)
std::string g_last_allowed;        // the device last let through (to restore after a late veto)
std::atomic<bool> g_kommander_switch{false};  // set on the UI thread around our own switch
void (*g_orig_activateDevice)(void*, const void*) = nullptr;
// TK: int decide(const char* device, const char* reason) -> 1 allow, 0 veto. Main thread.
int (*g_audio_decide)(const char*, const char*) = nullptr;
std::string g_generic_of[16][2];  // OS name -> generic name, as Traktor last used them (g_audio_mu)
int g_ngeneric = 0;

int copy_out(const std::string& s, char* out, int cap) {
  if (out && cap > 0) strncpy_s(out, (size_t)cap, s.c_str(), _TRUNCATE);
  return (int)s.size();
}

const char* thread_name() { return tkw_is_main_thread() ? "main" : "other"; }
const char* setup_origin() { return g_in_audio_setup.load() > 0 ? "picked in Audio Setup" : "NOT from Audio Setup"; }

std::string read_str(const void* s) {
  std::string out;
  return tkw_read_string(s, &out) ? out : std::string("?");
}

// Image-relative return addresses of the current call stack (Traktor's frames only): which code
// asked for a device — a manual pick, Traktor's startup restore, a controller plugged in.
std::string traktor_stack(int skip, int keep) {
  void* frames[32];
  const USHORT n = RtlCaptureStackBackTrace((ULONG)skip + 1, 32, frames, nullptr);
  std::string out;
  int kept = 0;
  for (USHORT i = 0; i < n && kept < keep; i++) {
    const uintptr_t a = (uintptr_t)frames[i];
    if (a < tkw_base() || a >= tkw_base() + TKW_IMAGE_SIZE) continue;
    char buf[24];
    snprintf(buf, sizeof(buf), "%s0x%llx", kept ? " " : "", (unsigned long long)(a - tkw_base()));
    out += buf;
    kept++;
  }
  return out;
}

// device id -> the engine's name for it (the id is the map KEY, as on the Mac).
std::map<void*, std::string> nsa_ids() {
  std::map<void*, std::string> ids;
  void* nsa = g_nsa.load();
  if (!nsa) return ids;
  const std::string filter;
  tkw_call<void>(WF(WF_A_nsa_getIDList), nsa, &ids, &filter);
  return ids;
}

// --- Settings Value buffers -------------------------------------------------------------------
// A Value whose string member is a valid empty MSVC string, whatever getEntry's assignment does.
struct ValueBuf {
  alignas(16) unsigned char b[kValueSize];
  ValueBuf() {
    memset(b, 0, sizeof(b));
    new (b + WOFF_VALUE_STRING) std::string();
  }
  int type() const { return *(const int*)b; }
  bool is_text() const { return type() == kValueTypeString || type() == kValueTypeStringArray; }
  std::string text() const { return read_str(b + WOFF_VALUE_STRING); }
  // Only a text value is known to own its string; our empty string owns no memory otherwise.
  void release() {
    if (is_text()) reinterpret_cast<std::string*>(b + WOFF_VALUE_STRING)->~basic_string();
  }
};

bool settings_get(const char* name, ValueBuf* v) {
  void* s = g_settings.load();
  if (!s) return false;
  const std::string key(name);
  return tkw_call<bool>(WF(WF_A_settings_getEntry), s, &key, (void*)v->b);
}

std::string value_string(const void* value) {
  if (!tkw_readable(value, 0x28)) return std::string();
  const int type = *(const int*)value;
  if (type != kValueTypeString) return "<type " + std::to_string(type) + ">";
  return read_str((const char*)value + WOFF_VALUE_STRING);
}

// --- hooks ------------------------------------------------------------------------------------
void on_setentry_enter(GumInvocationContext* ic) {
  void* settings = tkw_arg(ic, 0);
  void* expected = nullptr;
  if (settings) g_settings.compare_exchange_strong(expected, settings);
  std::string k;
  if (!tkw_read_string(tkw_arg(ic, 1), &k) || k.compare(0, 6, "Audio.") != 0) return;
  tklog("AUDIO: set %s = '%s' (%s, %s thread)", k.c_str(), value_string(tkw_arg(ic, 2)).c_str(), setup_origin(),
        thread_name());
}

void on_audiosetup_enter(GumInvocationContext*) { g_in_audio_setup++; }
void on_audiosetup_leave(GumInvocationContext*) { g_in_audio_setup--; }
void on_manual_enter(GumInvocationContext*) { g_in_manual++; }
void on_manual_leave(GumInvocationContext*) { g_in_manual--; }
void on_enable_enter(GumInvocationContext*) { g_in_enable++; }
void on_enable_leave(GumInvocationContext*) { g_in_enable--; }

void on_nsaselect_enter(GumInvocationContext* ic) {
  void* nsa = tkw_arg(ic, 0);
  if (nsa) g_nsa.store(nsa);
  void* id = tkw_arg(ic, 1);
  std::string key = "?";
  const auto ids = nsa_ids();  // before selectByID takes its own lock
  const auto found = ids.find(id);
  if (found != ids.end()) key = found->second;
  tklog("AUDIO: engine selects device id=%p key='%s' flag=%d (%s, %s thread)", id, key.c_str(),
        (int)((uintptr_t)tkw_arg(ic, 2) & 0xff), setup_origin(), thread_name());
  tklog("AUDIO:   called from %s", traktor_stack(1, 10).c_str());
}

void remember_generic(const std::string& os, const std::string& generic) {
  std::lock_guard<std::mutex> lock(g_audio_mu);
  for (int i = 0; i < g_ngeneric; i++)
    if (g_generic_of[i][0] == os) { g_generic_of[i][1] = generic; return; }
  if (g_ngeneric < 16) {
    g_generic_of[g_ngeneric][0] = os;
    g_generic_of[g_ngeneric][1] = generic;
    g_ngeneric++;
  }
}

int decide(const std::string& os, const char* reason) {
  if (!g_audio_decide) return 1;
  try {
    return g_audio_decide(os.c_str(), reason);
  } catch (...) {
    return 1;
  }
}

// A switch Traktor made on another thread was let through; TK's decider (ctypes, main thread
// only) is asked now, on the UI thread. A veto then restores the device let through before, the
// way TK's own restore does (onAudioDeviceChangedManually, classified "kommander").
void redecide_on_main(const std::string& os, const std::string& previous, const char* reason) {
  if (decide(os, reason)) {
    std::lock_guard<std::mutex> lock(g_audio_mu);
    g_last_allowed = os;
    return;
  }
  if (previous.empty() || previous == os) {
    tklog("AUDIO: '%s' vetoed late (%s), nothing to restore", os.c_str(), reason);
    return;
  }
  tklog("AUDIO: '%s' vetoed late (%s): restoring '%s'", os.c_str(), reason, previous.c_str());
  tk_audio_activate(previous.c_str());
}

const char* classify() {
  if (g_kommander_switch.load() && tkw_is_main_thread()) return "kommander";
  if (g_in_manual.load() > 0) return "manual";
  return g_in_enable.load() > 0 ? "startup" : "auto";
}

// Replaces ControlCenter::activateDevice(this rcx, AudioDeviceDescriptor const& rdx). On the UI
// thread TK decides first (as on the Mac); on any other thread the switch is allowed and TK
// decides afterwards on the UI thread.
void tk_activateDevice(void* cc, const void* desc) {
  int allow = 1;
  try {
    const std::string os = read_str((const char*)desc + kDescOs);
    const std::string generic = read_str((const char*)desc + kDescGeneric);
    remember_generic(os, generic);
    std::string previous;
    {
      std::lock_guard<std::mutex> lock(g_audio_mu);
      g_control_center = cc;
      previous = g_last_allowed;
    }
    const char* reason = classify();
    const bool main = tkw_is_main_thread();
    if (main) allow = decide(os, reason);
    tklog("AUDIO: activate '%s' (generic '%s', %s, %s thread) -> %s; from %s", os.c_str(), generic.c_str(), reason,
          thread_name(), !main ? "allowed, TK decides on the UI thread" : allow ? "allowed" : "VETOED",
          traktor_stack(1, 6).c_str());
    if (main && allow) {
      std::lock_guard<std::mutex> lock(g_audio_mu);
      g_last_allowed = os;
    }
    if (!main) tkw_on_main([os, previous, reason] { redecide_on_main(os, previous, reason); });
  } catch (...) {
    allow = 1;  // never lose Traktor's own switch to a libtk failure
  }
  if (allow) g_orig_activateDevice(cc, desc);
}

// --- connected devices (Windows audio endpoints) ----------------------------------------------
// PKEY_Device_FriendlyName and PKEY_AudioEngine_DeviceFormat (values from the Windows SDK).
const PROPERTYKEY kKeyFriendlyName = {{0xa45c254e, 0xdf1c, 0x4efd, {0x80, 0x20, 0x67, 0xd1, 0x46, 0xa8, 0x50, 0xe0}}, 14};
const PROPERTYKEY kKeyDeviceFormat = {{0xf19f064d, 0x082c, 0x4e27, {0xbc, 0x73, 0x68, 0x82, 0xa1, 0xbb, 0x8e, 0x4c}}, 0};

std::string utf8(const wchar_t* w) {
  if (!w || !*w) return std::string();
  const int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
  if (n <= 1) return std::string();
  std::string s((size_t)n - 1, '\0');
  WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], n, nullptr, nullptr);
  return s;
}

// "name\tID\tins\touts\n" for one endpoint; "" when its properties cannot be read.
std::string endpoint_line(IMMDevice* dev) {
  IMMEndpoint* ep = nullptr;
  EDataFlow flow = eRender;
  if (SUCCEEDED(dev->QueryInterface(__uuidof(IMMEndpoint), (void**)&ep)) && ep) {
    ep->GetDataFlow(&flow);
    ep->Release();
  }
  LPWSTR wid = nullptr;
  std::string id;
  if (SUCCEEDED(dev->GetId(&wid)) && wid) {
    id = utf8(wid);
    CoTaskMemFree(wid);
  }
  IPropertyStore* ps = nullptr;
  if (FAILED(dev->OpenPropertyStore(STGM_READ, &ps)) || !ps) return std::string();
  std::string name;
  int channels = 0;
  PROPVARIANT v;
  PropVariantInit(&v);
  if (SUCCEEDED(ps->GetValue(kKeyFriendlyName, &v)) && v.vt == VT_LPWSTR) name = utf8(v.pwszVal);
  PropVariantClear(&v);
  if (SUCCEEDED(ps->GetValue(kKeyDeviceFormat, &v)) && v.vt == VT_BLOB && v.blob.cbSize >= 4)
    channels = *(const WORD*)(v.blob.pBlobData + 2);  // WAVEFORMATEX::nChannels
  PropVariantClear(&v);
  ps->Release();
  if (name.empty()) return std::string();
  const int ins = flow == eCapture ? channels : 0, outs = flow == eRender ? channels : 0;
  return name + "\t" + id + "\t" + std::to_string(ins) + "\t" + std::to_string(outs) + "\n";
}

std::string audio_device_lines() {
  const HRESULT init = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);  // Traktor's thread: usually already on
  std::string out;
  IMMDeviceEnumerator* en = nullptr;
  IMMDeviceCollection* col = nullptr;
  if (SUCCEEDED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                 (void**)&en)) &&
      en && SUCCEEDED(en->EnumAudioEndpoints(eAll, DEVICE_STATE_ACTIVE, &col)) && col) {
    UINT n = 0;
    col->GetCount(&n);
    for (UINT i = 0; i < n; i++) {
      IMMDevice* dev = nullptr;
      if (FAILED(col->Item(i, &dev)) || !dev) continue;
      out += endpoint_line(dev);
      dev->Release();
    }
  } else {
    tklog("AUDIO: audio endpoints not readable (MMDevice API)");
  }
  if (col) col->Release();
  if (en) en->Release();
  if (SUCCEEDED(init)) CoUninitialize();
  return out;
}

}  // namespace

// ── Exported C API (ctypes, the Mac signatures) ─────────────────────────────────────────────────
// The current audio device (Traktor's setting), or -1 before Traktor's Settings was seen.
TKW_EXPORT int tk_audio_device_get(char* out, int cap) {
  if (!out || cap <= 0 || !g_settings.load() || !tkw_is_main_thread()) return -1;
  ValueBuf v;
  settings_get(kDeviceKey, &v);
  const std::string s = v.type() == kValueTypeString ? v.text() : std::string();
  v.release();
  return copy_out(s, out, cap);
}

// Any of Traktor's settings read from its memory (TK never reads Traktor Settings.tsi inside
// Traktor). Strings (type 3) and string arrays (type 7: one joined std::string at the same place
// on the Mac) come back as stored. Other types: -2. Main thread.
TKW_EXPORT int tk_setting_get(const char* name, char* out, int cap) {
  if (!name || !out || cap <= 0 || !g_settings.load() || !tkw_is_main_thread()) return -1;
  ValueBuf v;
  settings_get(name, &v);
  if (!v.is_text()) {
    tklog("SETTING: %s has type %d (not read)", name, v.type());
    return -2;
  }
  const std::string s = v.text();
  v.release();
  return copy_out(s, out, cap);
}

// One line per active audio endpoint: "name\tID\tinput channels\toutput channels". A
// "situation" (TK) is the set of what is connected.
TKW_EXPORT int tk_audio_devices(char* out, int cap) { return copy_out(audio_device_lines(), out, cap); }

// Select Traktor's audio device by writing its setting, as the Audio Setup page does. Main thread.
TKW_EXPORT int tk_audio_device_set(const char* name) {
  void* settings = g_settings.load();
  if (!name || !*name || !settings || !tkw_is_main_thread()) return -1;
  alignas(16) unsigned char value[kValueSize];
  memset(value, 0, sizeof(value));
  const std::string v(name), key(kDeviceKey);
  tkw_call<void*>(WF(WF_A_value_ctor_string), (void*)value, &v);
  tkw_call<bool>(WF(WF_A_settings_setEntry), settings, &key, (const void*)value, true);
  reinterpret_cast<std::string*>(value + WOFF_VALUE_STRING)->~basic_string();  // the ctor's copy
  tklog("AUDIO: device set to '%s' by Kommander", name);
  return 0;
}

TKW_EXPORT void tk_audio_set_decider(int (*decide_fn)(const char*, const char*)) { g_audio_decide = decide_fn; }

// Switch Traktor to <os_name> the way a pick in Preferences does. Main thread; 0 = asked.
TKW_EXPORT int tk_audio_activate(const char* os_name) {
  if (!os_name || !*os_name || !tkw_is_main_thread()) return -1;
  std::string generic;
  void* cc = nullptr;
  {
    std::lock_guard<std::mutex> lock(g_audio_mu);
    cc = g_control_center;
    for (int i = 0; i < g_ngeneric; i++)
      if (g_generic_of[i][0] == os_name) generic = g_generic_of[i][1];
  }
  if (!cc) return -1;
  alignas(16) unsigned char desc[kDescSize];
  memset(desc, 0, sizeof(desc));  // the two 8-byte headers stay zero
  std::string* os = new (desc + kDescOs) std::string(os_name);
  std::string* gen = new (desc + kDescGeneric) std::string(generic.empty() ? std::string(os_name) : generic);
  g_kommander_switch.store(true);
  // `this` is the secondary base, ControlCenter+8 (the function has no thunk on Windows).
  tkw_call<void>(WF(WF_A_cc_changedManually), (void*)((char*)cc + 8), (const void*)desc);
  g_kommander_switch.store(false);
  os->~basic_string();
  gen->~basic_string();
  return 0;
}

// ── "audio" line command: list | set <name> | ids | select <engine name> ─────────────────────────
static std::string audio_command(const std::string& args) {
  const size_t sp = args.find(' ');
  const std::string sub = args.substr(0, sp), arg = sp == std::string::npos ? "" : args.substr(sp + 1);
  if (sub == "list") {
    char cur[256] = {0};
    const int n = tk_audio_device_get(cur, sizeof(cur));
    tklog("AUDIO: devices:\n%s", audio_device_lines().c_str());
    tklog("AUDIO: Traktor's device = '%s'", cur);
    return n >= 0 ? std::string("OK Traktor's device '") + cur + "' (devices in the log)"
                  : std::string("OK Traktor's Settings not seen yet (devices in the log)");
  }
  if (sub == "set" && !arg.empty()) return tk_audio_device_set(arg.c_str()) == 0 ? "OK" : "ERR Settings not seen yet";
  if (sub == "ids") {
    if (!g_nsa.load()) return "ERR no engine interface seen yet";
    const auto ids = nsa_ids();
    for (const auto& kv : ids) tklog("AUDIO: id %p -> '%s'", kv.first, kv.second.c_str());
    return "OK " + std::to_string(ids.size()) + " (in the log)";
  }
  if (sub == "select" && !arg.empty()) {
    void* nsa = g_nsa.load();
    if (!nsa) return "ERR no engine interface seen yet";
    void* id = nullptr;
    for (const auto& kv : nsa_ids())
      if (kv.second == arg) id = kv.first;
    if (!id) return "ERR no device named '" + arg + "'";
    tkw_call<void>(WF(WF_A_nsa_selectByID), nsa, id, true);
    tklog("AUDIO: engine asked to select '%s'", arg.c_str());
    return "OK";
  }
  return "ERR usage: audio list | set <Traktor device name> | ids | select <engine device name>";
}

void tkw_mod_audio_init() {
  tkw_hook(WF(WF_A_settings_setEntry), "Settings::setEntry", on_setentry_enter, nullptr);
  tkw_hook(WF(WF_A_audiosetup_onCtlEvt), "AudioSetup::onControlEvent", on_audiosetup_enter, on_audiosetup_leave);
  tkw_hook(WF(WF_A_nsa_selectByID), "InterfaceNSA::audioDeviceSelectByID", on_nsaselect_enter, nullptr);
  tkw_hook(WF(WF_A_cc_changedManually), "ControlCenter::onAudioDeviceChangedManually", on_manual_enter,
           on_manual_leave);
  tkw_hook(WF(WF_A_cc_enable), "ControlCenter::enable", on_enable_enter, on_enable_leave);
  tkw_replace(WF(WF_A_cc_activateDevice), "ControlCenter::activateDevice", (void*)tk_activateDevice,
              (void**)&g_orig_activateDevice);
  tkw_command("audio", [](const std::string& a, const std::vector<std::string>&) { return audio_command(a); });
}
