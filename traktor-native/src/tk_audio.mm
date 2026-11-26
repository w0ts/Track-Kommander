// tk_audio.mm — Traktor's audio device choice, observed (stage 1; included by tk_inject.mm).
//
// Traktor keeps its audio device in the setting "Audio.DeviceName.Mac", written through
// Settings::setEntry. Stage 1 only LOGS every "Audio.*" write — key, value, whether it came from
// the preferences' Audio Setup page (= the User picked it by hand) and the thread — so the path
// Traktor's own auto-switch takes (plugging in an S4 MK2) is measured before anything is blocked.
// It also keeps the Settings object, for reading/writing the device later (tk_audio_device_get).

static std::atomic<int> g_in_audio_setup{0};  // > 0 while AudioSetup::onControlEvent runs
static void* g_settings = nullptr;            // Traktor's Settings (seen on its first write)

#include <map>

static void* g_nsa = nullptr;  // NI::AB::InterfaceNSA (seen on its first device select)

// device id -> the engine's name for it. The ID is the KEY (AudioSetup::getAudioDevices passes
// node+0x20, the key, as the id). An earlier map<string, void*> guess read garbage (2026-11-24).
static std::map<void*, std::string> nsa_ids(void) {
  std::map<void*, std::string> ids;
  if (!g_nsa) return ids;
  std::string err;
  ((void (*)(void*, std::map<void*, std::string>*, std::string*))R(A_nsa_getIDList))(g_nsa, &ids, &err);
  return ids;
}

#include <execinfo.h>

// Image-relative return addresses of the current call stack (Traktor's frames only): which code
// asked for a device — a manual pick, Traktor's startup restore, a controller plugged in.
static std::string traktor_stack(int skip, int keep) {
  void* frames[32];
  const int n = backtrace(frames, 32);
  std::string out;
  int kept = 0;
  for (int i = skip; i < n && kept < keep; i++) {
    Dl_info info;
    if (!dladdr(frames[i], &info) || !info.dli_fname || !strstr(info.dli_fname, "Traktor Pro 4")) continue;
    char buf[24];
    snprintf(buf, sizeof(buf), "%s0x%llx", kept ? " " : "", (unsigned long long)((uintptr_t)frames[i] - (uintptr_t)g_slide));
    out += buf;
    kept++;
  }
  return out;
}

static void audio_select_seen(void* nsa, void* id, int flag) {
  if (nsa) g_nsa = nsa;
  std::string key = "?";
  const auto ids = nsa_ids();
  const auto found = ids.find(id);
  if (found != ids.end()) key = found->second;
  tklog("AUDIO: engine selects device id=%p key='%s' flag=%d (%s, %s thread)", id, key.c_str(), flag & 0xff,
        g_in_audio_setup.load() > 0 ? "picked in Audio Setup" : "NOT from Audio Setup",
        pthread_main_np() ? "main" : "other");
  tklog("AUDIO:   called from %s", traktor_stack(1, 10).c_str());
}

static std::string value_string(const void* value) {
  if (!value || !gum_memory_is_readable(value, 0x20)) return std::string();
  if (*(const int*)value != VALUE_TYPE_STRING) return std::string("<type ") + std::to_string(*(const int*)value) + ">";
  return decode_std_string((const char*)value + OFF_VALUE_STRING);
}

static void audio_setentry_seen(void* settings, const void* key, const void* value) {
  if (!g_settings && settings) g_settings = settings;
  const std::string k = decode_std_string(key);
  if (k.compare(0, 6, "Audio.") != 0) return;
  tklog("AUDIO: set %s = '%s' (%s, %s thread)", k.c_str(), value_string(value).c_str(),
        g_in_audio_setup.load() > 0 ? "picked in Audio Setup" : "NOT from Audio Setup",
        pthread_main_np() ? "main" : "other");
}

// The current audio device (Traktor's setting), or "" before Traktor's Settings was seen.
TK_EXPORT int tk_audio_device_get(char* out, int cap) {
  if (!out || cap <= 0 || !g_settings || !pthread_main_np()) return -1;
  alignas(16) unsigned char value[0x40];
  memset(value, 0, sizeof(value));
  const std::string key("Audio.DeviceName.Mac");
  ((long (*)(void*, const std::string*, void*))R(A_settings_getEntry))(g_settings, &key, value);
  const std::string v = value_string(value);
  if (*(const int*)value == VALUE_TYPE_STRING && (value[0x1f] & 0x80u))
    operator delete(*(void**)(value + OFF_VALUE_STRING));  // the copy getEntry made
  strlcpy(out, v.c_str(), (size_t)cap);
  return (int)v.size();
}

// Any of Traktor's settings read from its memory (2026-11-24: TK never reads Traktor Settings.tsi
// inside Traktor). Strings (type 3) and string arrays (type 7: one joined std::string at the same
// place, split by Value::getStringArray) come back as stored, e.g. "Browser.Dir.Music" =
// "Macintosh HD:Users:me:Music:Tracks:;…". Other types: -2. Main thread.
#define VALUE_TYPE_STRING_ARRAY 7
TK_EXPORT int tk_setting_get(const char* name, char* out, int cap) {
  if (!name || !out || cap <= 0 || !g_settings || !pthread_main_np()) return -1;
  alignas(16) unsigned char value[0x40];
  memset(value, 0, sizeof(value));
  const std::string key(name);
  ((long (*)(void*, const std::string*, void*))R(A_settings_getEntry))(g_settings, &key, value);
  const int type = *(const int*)value;
  if (type != VALUE_TYPE_STRING && type != VALUE_TYPE_STRING_ARRAY) {
    tklog("SETTING: %s has type %d (not read)", name, type);
    return -2;
  }
  const std::string v = decode_std_string(value + OFF_VALUE_STRING);
  if (value[0x1f] & 0x80u) operator delete(*(void**)(value + OFF_VALUE_STRING));  // getEntry's copy
  strlcpy(out, v.c_str(), (size_t)cap);
  return (int)v.size();
}

// ── Connected audio devices (CoreAudio) ─────────────────────────────────────────────────────────
// One line per device: "name\tUID\tinput channels\toutput channels". A "situation" (TK) is the set
// of what is connected.
static std::string cf_string(CFStringRef s) {
  if (!s) return std::string();
  char buf[512];
  const bool ok = CFStringGetCString(s, buf, sizeof(buf), kCFStringEncodingUTF8);
  CFRelease(s);
  return ok ? std::string(buf) : std::string();
}

static int device_channels(AudioDeviceID dev, AudioObjectPropertyScope scope) {
  AudioObjectPropertyAddress a = {kAudioDevicePropertyStreamConfiguration, scope, kAudioObjectPropertyElementMain};
  UInt32 size = 0;
  if (AudioObjectGetPropertyDataSize(dev, &a, 0, nullptr, &size) != noErr || size == 0) return 0;
  std::vector<unsigned char> buf(size);
  AudioBufferList* list = (AudioBufferList*)buf.data();
  if (AudioObjectGetPropertyData(dev, &a, 0, nullptr, &size, list) != noErr) return 0;
  int n = 0;
  for (UInt32 i = 0; i < list->mNumberBuffers; i++) n += (int)list->mBuffers[i].mNumberChannels;
  return n;
}

static std::string audio_device_lines(void) {
  AudioObjectPropertyAddress a = {kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
  UInt32 size = 0;
  if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &a, 0, nullptr, &size) != noErr) return std::string();
  std::vector<AudioDeviceID> ids(size / sizeof(AudioDeviceID));
  if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, nullptr, &size, ids.data()) != noErr) return std::string();
  std::string out;
  for (AudioDeviceID dev : ids) {
    CFStringRef name = nullptr, uid = nullptr;
    UInt32 sz = sizeof(CFStringRef);
    AudioObjectPropertyAddress an = {kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    AudioObjectGetPropertyData(dev, &an, 0, nullptr, &sz, &name);
    sz = sizeof(CFStringRef);
    AudioObjectPropertyAddress au = {kAudioDevicePropertyDeviceUID, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    AudioObjectGetPropertyData(dev, &au, 0, nullptr, &sz, &uid);
    out += cf_string(name) + "\t" + cf_string(uid) + "\t" +
           std::to_string(device_channels(dev, kAudioObjectPropertyScopeInput)) + "\t" +
           std::to_string(device_channels(dev, kAudioObjectPropertyScopeOutput)) + "\n";
  }
  return out;
}

TK_EXPORT int tk_audio_devices(char* out, int cap) {
  const std::string all = audio_device_lines();
  if (out && cap > 0) strlcpy(out, all.c_str(), (size_t)cap);
  return (int)all.size();
}

// Select Traktor's audio device by writing its setting, as the Audio Setup page does. Main thread.
TK_EXPORT int tk_audio_device_set(const char* name) {
  if (!name || !*name || !g_settings || !pthread_main_np()) return -1;
  alignas(16) unsigned char value[0x40];
  memset(value, 0, sizeof(value));
  const std::string v(name), key("Audio.DeviceName.Mac");
  ((void (*)(void*, const std::string*))R(A_value_ctor_string))(value, &v);
  ((long (*)(void*, const std::string*, const void*, bool))R(A_settings_setEntry))(g_settings, &key, value, true);
  if (value[0x1f] & 0x80u) operator delete(*(void**)(value + OFF_VALUE_STRING));
  tklog("AUDIO: device set to '%s' by Kommander", name);
  return 0;
}

static void cmd_reply(int fd, const char* msg);
static void handle_audio_cmd(int fd, const char* line) {
  char sub[16] = {0}, arg[256] = {0};
  const int n = sscanf(line, "%*s %15s %255[^\n]", sub, arg);
  std::string s(sub), a(arg);
  cmd_reply(fd, "OK\n");
  dispatch_async(dispatch_get_main_queue(), ^{
    if (s == "list") {
      tklog("AUDIO: devices:\n%s", audio_device_lines().c_str());
      char cur[256];
      tk_audio_device_get(cur, sizeof(cur));
      tklog("AUDIO: Traktor's device = '%s'", cur);
    } else if (s == "set" && n == 2) {
      tk_audio_device_set(a.c_str());
    } else if (s == "ids") {
      if (!g_nsa) tklog("AUDIO: no engine interface seen yet");
      for (const auto& kv : nsa_ids()) tklog("AUDIO: id %p -> '%s'", kv.first, kv.second.c_str());
    } else if (s == "select" && n == 2) {
      void* id = nullptr;
      for (const auto& kv : nsa_ids()) if (kv.second == a) id = kv.first;
      if (!id) { tklog("AUDIO: no device named '%s'", a.c_str()); return; }
      ((void (*)(void*, void*, bool))R(A_nsa_selectByID))(g_nsa, id, true);
      tklog("AUDIO: engine asked to select '%s'", a.c_str());
    }
  });
}

// ── Stage 2: who switches the device, and a veto ────────────────────────────────────────────────
// activateDevice is replaced: each switch is classified (manual / Kommander / startup / auto) and
// TK's decision callback (if registered) may veto it — the original is then not called and the
// current device stays. TK restores a device through onAudioDeviceChangedManually, the same path
// as the User's own pick.
static std::atomic<int> g_in_manual{0}, g_in_enable{0};
static bool g_kommander_switch = false;
static void* g_control_center = nullptr;
static void (*g_orig_activateDevice)(void*, const void*) = nullptr;
// TK: int decide(const char* device, const char* reason) -> 1 allow, 0 veto. Main thread.
static int (*g_audio_decide)(const char*, const char*) = nullptr;
static std::string g_generic_of[16][2];  // OS name -> generic name, as Traktor last used them
static int g_ngeneric = 0;

static void remember_generic(const std::string& os, const std::string& generic) {
  for (int i = 0; i < g_ngeneric; i++) if (g_generic_of[i][0] == os) { g_generic_of[i][1] = generic; return; }
  if (g_ngeneric < 16) { g_generic_of[g_ngeneric][0] = os; g_generic_of[g_ngeneric][1] = generic; g_ngeneric++; }
}

static void tk_activateDevice(void* cc, const void* desc) {
  g_control_center = cc;
  const std::string os = decode_std_string(desc);
  const std::string generic = decode_std_string((const char*)desc + OFF_ADD_GENERIC);
  remember_generic(os, generic);
  const char* reason = g_kommander_switch ? "kommander" : g_in_manual.load() > 0 ? "manual"
                     : g_in_enable.load() > 0 ? "startup" : "auto";
  const int allow = g_audio_decide ? g_audio_decide(os.c_str(), reason) : 1;
  tklog("AUDIO: activate '%s' (generic '%s', %s) -> %s; from %s", os.c_str(), generic.c_str(), reason,
        allow ? "allowed" : "VETOED", traktor_stack(1, 6).c_str());
  if (allow) g_orig_activateDevice(cc, desc);
}

TK_EXPORT void tk_audio_set_decider(int (*decide)(const char*, const char*)) { g_audio_decide = decide; }

// Switch Traktor to <os_name> the way a pick in Preferences does. Main thread; 0 = asked.
TK_EXPORT int tk_audio_activate(const char* os_name) {
  if (!os_name || !*os_name || !g_control_center || !pthread_main_np()) return -1;
  std::string generic;
  for (int i = 0; i < g_ngeneric; i++) if (g_generic_of[i][0] == os_name) generic = g_generic_of[i][1];
  struct { std::string os, generic; char pad[0x40]; } desc{os_name, generic.empty() ? std::string(os_name) : generic, {}};
  g_kommander_switch = true;
  ((void (*)(void*, const void*))R(A_cc_changedManually))(g_control_center, &desc);
  g_kommander_switch = false;
  return 0;
}
