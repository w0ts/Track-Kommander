// tk_launch.exe — starts Traktor Pro 4 with Track Kommander inside (Windows; private).
//
// The installed Traktor is not changed: this starts "Traktor Pro 4.exe" suspended, loads
// libtk.dll (next to this exe) into it with CreateRemoteThread(LoadLibraryW), waits until libtk
// says its hooks are in place (event "Local\TKReady-<pid>": above all, the block on Traktor's
// crash reporter), then lets Traktor run. On any failure the suspended Traktor is ended before it
// ran a single instruction of its own, and a message says why.
//
// `tk_launch.exe --verify` only checks the Traktor executable (exit 0 = supported) and starts nothing.
// Settings: tk-embed.conf next to this exe, KEY=VALUE lines (TK_* only), e.g. TK_REPO,
// TK_PYTHONHOME, TK_EMBED, TK_DYLIB_LOG, TK_TRAKTOR_EXE. Variables already set win.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>

#include <bcrypt.h>
#include <stdio.h>

#include <string>
#include <vector>

#include "tk_supported.h"

#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "version.lib")

static std::wstring g_log;

static void lg(const wchar_t* fmt, ...) {
  wchar_t msg[1024];
  va_list ap;
  va_start(ap, fmt);
  _vsnwprintf_s(msg, _countof(msg), _TRUNCATE, fmt, ap);
  va_end(ap);
  FILE* f = nullptr;
  if (_wfopen_s(&f, g_log.c_str(), L"a, ccs=UTF-8") == 0 && f) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    fwprintf(f, L"%02d:%02d:%02d launch: %s\n", t.wHour, t.wMinute, t.wSecond, msg);
    fclose(f);
  }
}

static bool quiet() {  // TK_LAUNCH_QUIET=1: no dialog (scripted runs, e.g. over SSH)
  wchar_t v[8];
  return GetEnvironmentVariableW(L"TK_LAUNCH_QUIET", v, 8) > 0 && v[0] == L'1';
}

static int fail(const wchar_t* why) {
  lg(L"FAILED: %s", why);
  if (!quiet()) MessageBoxW(nullptr, why, L"Traktor + Track Kommander", MB_ICONERROR | MB_OK);
  return 1;
}

static std::wstring env(const wchar_t* name, const std::wstring& fallback = L"") {
  wchar_t buf[2048];
  DWORD n = GetEnvironmentVariableW(name, buf, _countof(buf));
  return (n > 0 && n < _countof(buf)) ? std::wstring(buf, n) : fallback;
}

static std::wstring here() {
  wchar_t p[MAX_PATH];
  GetModuleFileNameW(nullptr, p, MAX_PATH);
  std::wstring s(p);
  return s.substr(0, s.rfind(L'\\'));
}

static void load_conf(const std::wstring& path) {
  FILE* f = nullptr;
  if (_wfopen_s(&f, path.c_str(), L"r, ccs=UTF-8") != 0 || !f) return;
  wchar_t line[2048];
  while (fgetws(line, _countof(line), f)) {
    std::wstring s(line);
    while (!s.empty() && (s.back() == L'\n' || s.back() == L'\r')) s.pop_back();
    const size_t eq = s.find(L'=');
    if (s.empty() || s[0] == L'#' || eq == std::wstring::npos || s.rfind(L"TK_", 0) != 0) continue;
    const std::wstring k = s.substr(0, eq);
    std::wstring v = s.substr(eq + 1);
    // A value may start with ~\ or ~/ : the user's home (as the Mac libtk reads tk-embed.conf).
    if (v.size() >= 2 && v[0] == L'~' && (v[1] == L'\\' || v[1] == L'/')) v = env(L"USERPROFILE") + v.substr(1);
    if (env(k.c_str()).empty()) SetEnvironmentVariableW(k.c_str(), v.c_str());
  }
  fclose(f);
}

static bool traktor_running() {
  HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snap == INVALID_HANDLE_VALUE) return false;
  PROCESSENTRY32W pe = {sizeof(pe)};
  bool found = false;
  for (BOOL ok = Process32FirstW(snap, &pe); ok; ok = Process32NextW(snap, &pe))
    if (_wcsicmp(pe.szExeFile, L"Traktor Pro 4.exe") == 0) found = true;
  CloseHandle(snap);
  return found;
}

// SHA-256 of the whole file (lowercase hex), "" on any error. Windows' own CNG, 1 MB at a time.
static std::string sha256_file(const std::wstring& path) {
  HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                         FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
  if (f == INVALID_HANDLE_VALUE) return "";
  BCRYPT_ALG_HANDLE alg = nullptr;
  BCRYPT_HASH_HANDLE h = nullptr;
  std::string hex;
  if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0) == 0 &&
      BCryptCreateHash(alg, &h, nullptr, 0, nullptr, 0, 0) == 0) {
    std::vector<unsigned char> buf(1 << 20);
    DWORD got = 0;
    bool ok = true;
    while (ok && ReadFile(f, buf.data(), (DWORD)buf.size(), &got, nullptr) && got > 0)
      ok = BCryptHashData(h, buf.data(), got, 0) == 0;
    unsigned char digest[32];
    if (ok && BCryptFinishHash(h, digest, sizeof(digest), 0) == 0) {
      static const char* d = "0123456789abcdef";
      for (unsigned char c : digest) {
        hex += d[c >> 4];
        hex += d[c & 15];
      }
    }
  }
  if (h) BCryptDestroyHash(h);
  if (alg) BCryptCloseAlgorithmProvider(alg, 0);
  CloseHandle(f);
  return hex;
}

static bool file_version(const std::wstring& path, uint32_t* ms, uint32_t* ls) {
  DWORD dummy = 0, sz = GetFileVersionInfoSizeW(path.c_str(), &dummy);
  if (!sz) return false;
  std::vector<char> buf(sz);
  VS_FIXEDFILEINFO* fi = nullptr;
  UINT len = 0;
  if (!GetFileVersionInfoW(path.c_str(), 0, sz, buf.data()) || !VerQueryValueW(buf.data(), L"\\", (void**)&fi, &len) || !fi)
    return false;
  *ms = fi->dwFileVersionMS;
  *ls = fi->dwFileVersionLS;
  return true;
}

// The Mac builder's rule (builder/lib/builds.js): only a Traktor executable whose SHA-256 is
// known, and whose version is the one recorded with it. Returns the verified hash, or "".
static std::string verify_traktor(const std::wstring& exe) {
  const std::string sha = sha256_file(exe);
  uint32_t ms = 0, ls = 0;
  const bool have_version = file_version(exe, &ms, &ls);
  lg(L"Traktor sha256 %S, version %u.%u.%u.%u", sha.empty() ? "?" : sha.c_str(), HIWORD(ms), LOWORD(ms),
     HIWORD(ls), LOWORD(ls));
  const TkSupportedBuild* b = tk_supported_build(sha.c_str());
  if (!b || !have_version || b->version_ms != ms || b->version_ls != ls) return "";
  lg(L"Traktor verified: %S", b->name);
  return sha;
}

static bool wait_ready(DWORD pid, DWORD ms) {
  wchar_t name[64];
  swprintf_s(name, L"Local\\TKReady-%lu", pid);
  const ULONGLONG end = GetTickCount64() + ms;
  while (GetTickCount64() < end) {
    HANDLE ev = OpenEventW(SYNCHRONIZE, FALSE, name);
    if (ev) {
      const DWORD r = WaitForSingleObject(ev, (DWORD)(end - GetTickCount64()));
      CloseHandle(ev);
      return r == WAIT_OBJECT_0;
    }
    Sleep(20);
  }
  return false;
}

static bool inject(HANDLE proc, const std::wstring& dll) {
  const SIZE_T bytes = (dll.size() + 1) * sizeof(wchar_t);
  void* mem = VirtualAllocEx(proc, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
  if (!mem || !WriteProcessMemory(proc, mem, dll.c_str(), bytes, nullptr)) return false;
  auto load = (LPTHREAD_START_ROUTINE)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
  HANDLE t = CreateRemoteThread(proc, nullptr, 0, load, mem, 0, nullptr);
  if (!t) {
    VirtualFreeEx(proc, mem, 0, MEM_RELEASE);
    return false;
  }
  const DWORD w = WaitForSingleObject(t, 20000);
  DWORD code = 0;
  GetExitCodeThread(t, &code);
  CloseHandle(t);
  VirtualFreeEx(proc, mem, 0, MEM_RELEASE);
  // The exit code is the module handle cut to 32 bits: it can be 0 for a loaded module, so the
  // ready event (wait_ready) is the real proof; here only a thread that did not finish fails.
  lg(L"LoadLibraryW in Traktor: wait=%lu module(low 32 bits)=0x%lx", w, code);
  return w == WAIT_OBJECT_0;
}

// The release (the MSI): TK's kit beside this exe (<install>\tk: Python, TK's packages and source,
// as make_kit builds it), and in the Plus edition its add-on beside the kit (<install>\tk-addon):
// one install, nothing looked for anywhere else (User, 2026-11-26). A checkout named in
// tk-embed.conf (TK_REPO) wins: development.
static void find_kit_beside(const std::wstring& dir) {
  if (!env(L"TK_REPO").empty()) return;
  const std::wstring tk = dir + L"\\tk";
  if (GetFileAttributesW((tk + L"\\KIT-VERSION").c_str()) == INVALID_FILE_ATTRIBUTES) {
    lg(L"kit: none beside the launcher (%s)", tk.c_str());
    return;
  }
  SetEnvironmentVariableW(L"TK_REPO", tk.c_str());
  SetEnvironmentVariableW(L"TK_PYTHONHOME", (tk + L"\\python").c_str());
  SetEnvironmentVariableW(L"TK_EMBED", (tk + L"\\traktor-native\\win\\embed_tk_win.py").c_str());
  const std::wstring addon = dir + L"\\tk-addon";
  const bool with_addon = GetFileAttributesW((addon + L"\\manifest.json").c_str()) != INVALID_FILE_ATTRIBUTES;
  if (with_addon) SetEnvironmentVariableW(L"TK_ADDON_DIR", addon.c_str());
  lg(L"kit: %s%s", tk.c_str(), with_addon ? L" (with an add-on)" : L"");
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, LPWSTR args, int) {
  const std::wstring dir = here();
  load_conf(dir + L"\\tk-embed.conf");
  const std::wstring logdir = env(L"LOCALAPPDATA", L"C:") + L"\\Track Kommander\\logs";
  CreateDirectoryW((env(L"LOCALAPPDATA", L"C:") + L"\\Track Kommander").c_str(), nullptr);
  CreateDirectoryW(logdir.c_str(), nullptr);
  g_log = logdir + L"\\tk_launch.log";
  if (env(L"TK_DYLIB_LOG").empty()) SetEnvironmentVariableW(L"TK_DYLIB_LOG", (logdir + L"\\libtk.log").c_str());
  find_kit_beside(dir);

  const std::wstring exe = env(L"TK_TRAKTOR_EXE", L"C:\\Program Files\\Native Instruments\\Traktor Pro 4\\Traktor Pro 4.exe");
  const std::wstring dll = dir + L"\\libtk.dll";
  lg(L"start: %s + %s", exe.c_str(), dll.c_str());
  if (GetFileAttributesW(dll.c_str()) == INVALID_FILE_ATTRIBUTES) return fail(L"libtk.dll is missing next to tk_launch.exe.");
  if (traktor_running()) return fail(L"Traktor is already running. Quit it first, then start Traktor + Track Kommander again.");
  const std::string verified = verify_traktor(exe);
  if (args && wcsstr(args, L"--verify")) {  // check only (installers, tests): exit 0 = supported
    lg(L"--verify: %s", verified.empty() ? L"NOT supported" : L"supported");
    return verified.empty() ? 1 : 0;
  }
  if (verified.empty())
    return fail(L"This Traktor version is not supported yet: Track Kommander was not started inside it.\n\n"
                L"Supported: Traktor Pro 4.5.1 (build 21). Start Traktor from its own shortcut meanwhile.");
  SetEnvironmentVariableA("TK_TRAKTOR_SHA256", verified.c_str());  // libtk checks the launcher did
  // Python inside Traktor reads these through the C runtime's environment, which is copied when
  // Traktor starts: they must be in the environment Traktor is started with, not set later.
  // PYTHONUTF8: the Mac's intermittent "-> -1" starts came from a non-UTF-8 locale (main 2a4535e).
  SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
  // Compiled code in the user's cache folder, never beside the sources (Program Files is not the
  // user's to write; before, every start compiled TK, numpy, scipy... again). numba (librosa)
  // keeps its own cache beside the sources unless told otherwise. Set ones win.
  const std::wstring cache = env(L"LOCALAPPDATA", L"C:") + L"\\Track Kommander\\Cache";
  if (env(L"PYTHONPYCACHEPREFIX").empty()) SetEnvironmentVariableW(L"PYTHONPYCACHEPREFIX", (cache + L"\\pycache").c_str());
  if (env(L"NUMBA_CACHE_DIR").empty()) SetEnvironmentVariableW(L"NUMBA_CACHE_DIR", (cache + L"\\numba").c_str());
  // The kit keeps only Qt's Windows media backend (trim_kit.py drops Qt's ffmpeg copy).
  if (env(L"QT_MEDIA_BACKEND").empty()) SetEnvironmentVariableW(L"QT_MEDIA_BACKEND", L"windows");
  if (!env(L"TK_PYTHONHOME").empty()) SetEnvironmentVariableW(L"PYTHONHOME", env(L"TK_PYTHONHOME").c_str());

  const std::wstring cwd = exe.substr(0, exe.rfind(L'\\'));
  std::wstring cmd = L"\"" + exe + L"\"";
  STARTUPINFOW si = {sizeof(si)};
  PROCESS_INFORMATION pi = {};
  // Until Traktor is resumed it sits in a kill-on-close job: if this launcher dies half-way (killed,
  // logoff), Traktor ends with it instead of staying suspended with libtk half loaded.
  HANDLE job = CreateJobObjectW(nullptr, nullptr);
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION lim = {};
  lim.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
  if (job) SetInformationJobObject(job, JobObjectExtendedLimitInformation, &lim, sizeof(lim));
  if (!CreateProcessW(exe.c_str(), &cmd[0], nullptr, nullptr, FALSE, CREATE_SUSPENDED | CREATE_BREAKAWAY_FROM_JOB,
                      nullptr, cwd.c_str(), &si, &pi) &&
      !CreateProcessW(exe.c_str(), &cmd[0], nullptr, nullptr, FALSE, CREATE_SUSPENDED, nullptr, cwd.c_str(), &si, &pi))
    return fail(L"Could not start Traktor Pro 4.exe.");
  if (job && !AssignProcessToJobObject(job, pi.hProcess)) lg(L"job object: assign failed (%lu)", GetLastError());
  lg(L"Traktor pid %lu (suspended)", pi.dwProcessId);
  if (!inject(pi.hProcess, dll)) {
    TerminateProcess(pi.hProcess, 1);
    return fail(L"Could not load Track Kommander into Traktor. Traktor was stopped before it started.");
  }
  if (!wait_ready(pi.dwProcessId, 20000)) {
    TerminateProcess(pi.hProcess, 1);
    return fail(L"Track Kommander did not get ready inside Traktor in 20 s. Traktor was stopped before it started.");
  }
  // Traktor leaves the job before it runs: closing the job must never end the real session.
  if (job) {
    lim.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_BREAKAWAY_OK;
    SetInformationJobObject(job, JobObjectExtendedLimitInformation, &lim, sizeof(lim));
  }
  if (ResumeThread(pi.hThread) == (DWORD)-1) {
    TerminateProcess(pi.hProcess, 1);
    return fail(L"Could not resume Traktor after loading Track Kommander.");
  }
  lg(L"ready: Traktor resumed");
  if (job) CloseHandle(job);
  CloseHandle(pi.hThread);
  CloseHandle(pi.hProcess);
  return 0;
}
