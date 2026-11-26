// tk_supported.h — the Traktor builds Track Kommander's Windows integration supports, keyed by the
// SHA-256 of "Traktor Pro 4.exe" (the Windows twin of builder/lib/builds.js on the Mac, which keys
// the Mac executable the same way). Only hashes and names: no Native Instruments bytes.
//
// tk_launch.exe hashes the executable before it starts it and refuses any other file; it then
// hands the verified hash to libtk (TK_TRAKTOR_SHA256), which refuses to hook a Traktor the
// launcher did not verify. A new Traktor build needs its own address map
// (addrs_win_*.h) before its hash goes here.
#pragma once
#include <stdint.h>
#include <string.h>

struct TkSupportedBuild {
  const char* sha256;   // lowercase hex of the whole Traktor Pro 4.exe
  const char* name;     // for messages and logs
  uint32_t version_ms;  // VS_FIXEDFILEINFO dwFileVersionMS / LS, checked too
  uint32_t version_ls;
};

static const TkSupportedBuild kTkSupportedBuilds[] = {
    // Measured 2026-11-25 on the Windows test PC (certutil) and on the copy the address map was made from.
    {"1b4db54ab51d63e1f662971c74b3f37e75df7c19291844f5ac7ec8ccc04373ba", "Traktor Pro 4.5.1 (build 21)",
     (4u << 16) | 5u, (1u << 16) | 21u},
};

inline const TkSupportedBuild* tk_supported_build(const char* sha256_hex) {
  for (const TkSupportedBuild& b : kTkSupportedBuilds)
    if (sha256_hex && strcmp(b.sha256, sha256_hex) == 0) return &b;
  return nullptr;
}
