// Stands in for Traktor's crashpad_handler in the slim "Traktor Pro 4 TK" app (make_slim_app.sh).
//
// At start Traktor forks a child that execs Contents/MacOS/crashpad_handler, NI's crash reporter,
// which sends crash dumps to Native Instruments. The slim app leaves NI's handler out on purpose:
// a patched Traktor's dumps (libtk, TK's paths) must not go to NI (User, 2026-11-24). Without any
// file there, the exec failed and the child died on crashpad's fatal log, so macOS wrote a crash
// report on every launch (User, 2026-11-25: "can we avoid the crash ?"). This one is executed
// instead and exits at once: Traktor sees no handler, exactly as before, minus the crash. It reads
// nothing, writes nothing and sends nothing.
int main(void) { return 0; }
