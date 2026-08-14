from pathlib import Path

p = Path('/tmp/repro/fex-full-thunk-pair/guest/main.cpp')
s = p.read_text()

s = '#include <atomic>\n#include <thread>\n' + s

old = '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n'
new = old + '    else if (!std::strcmp(argv[i], "--callback-race-unmap")) callback_race_unmap = true;\n    else if (!std::strcmp(argv[i], "--callback-race-pin")) callback_race_pin = true;\n'
if s.count(old) != 1:
    raise SystemExit(f'arg anchor count={s.count(old)}')
s = s.replace(old, new, 1)

old = '  bool pin = false;\n'
new = old + '  bool callback_race_unmap = false;\n  bool callback_race_pin = false;\n'
if s.count(old) != 1:
    raise SystemExit(f'bool anchor count={s.count(old)}')
s = s.replace(old, new, 1)

anchor = '    if (cb_before != want_cb) return 11;\n\n'
block = r'''    if (cb_before != want_cb) return 11;

    if (callback_race_unmap || callback_race_pin) {
      const char* arm = "./fex-callback-race-arm";
      const char* entered = "./fex-callback-race-entered";
      const char* release = "./fex-callback-race-release";
      unlink(arm); unlink(entered); unlink(release);
      {
        FILE* f = fopen(arm, "w");
        if (!f) { perror("fopen arm"); return 80; }
        fputs("1", f); fclose(f);
      }

      std::atomic<int> worker_rv {-999999};
      std::thread worker([&] { worker_rv.store(call_first_callback(6)); });

      bool saw_entered = false;
      for (int i = 0; i < 10000; ++i) {
        if (access(entered, F_OK) == 0) { saw_entered = true; break; }
        usleep(1000);
      }
      if (!saw_entered) {
        std::fprintf(stderr, "FAIL: callback worker never reached FEX in-flight barrier\n");
        {
          FILE* f = fopen(release, "w"); if (f) { fputs("1", f); fclose(f); }
        }
        worker.join();
        return 81;
      }
      std::fprintf(stderr, "CALLBACK_RACE_ENTERED target=0x%016" PRIxPTR " unpacker=0x%016" PRIxPTR "\n", d.target, d.unpacker);
      std::fflush(stderr);

      if (callback_race_unmap) {
        if (dlclose(d.h) != 0) { std::fprintf(stderr, "dlclose race: %s\n", dlerror()); return 82; }
        d.h = nullptr;
        std::fprintf(stderr, "CALLBACK_RACE_UNMAPPED target_exec=%d unpacker_exec=%d\n",
                     executable(d.target) ? 1 : 0, executable(d.unpacker) ? 1 : 0);
        std::fflush(stderr);
        if (executable(d.target) || executable(d.unpacker)) return 83;
      } else {
        std::fprintf(stderr, "CALLBACK_RACE_PINNED\n");
        std::fflush(stderr);
      }

      {
        FILE* f = fopen(release, "w");
        if (!f) { perror("fopen release"); return 84; }
        fputs("1", f); fclose(f);
      }
      worker.join();
      unlink(arm); unlink(entered); unlink(release);

      const int rv = worker_rv.load();
      std::fprintf(stderr, "CALLBACK_RACE_WORKER_RETURN rv=%d\n", rv);
      std::fflush(stderr);
      if (callback_race_pin) {
        return rv == (gen * 10000 + 63) ? 0 : 85;
      }
      // The unmap arm should fault while resuming the already-entered callback.
      return 86;
    }

'''
if s.count(anchor) != 1:
    raise SystemExit(f'callback insertion anchor count={s.count(anchor)}')
s = s.replace(anchor, block, 1)
p.write_text(s)
