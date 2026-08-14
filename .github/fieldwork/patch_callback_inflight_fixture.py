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
      std::atomic<bool> worker_started {false};
      std::atomic<int> worker_rv {-999999};
      std::thread worker([&] {
        worker_started.store(true);
        worker_rv.store(call_first_callback(6));
      });

      while (!worker_started.load()) {
        usleep(1000);
      }
      // The FEX-side discriminator pauses callback entry 2 for up to 1.5s.
      // Give the worker ample time to reach that point before the main thread
      // either keeps the owner pinned or begins dlclose.
      usleep(250000);

      if (callback_race_unmap) {
        std::fprintf(stderr, "CALLBACK_RACE_DLCLOSE_BEGIN target=0x%016" PRIxPTR " unpacker=0x%016" PRIxPTR "\n", d.target, d.unpacker);
        std::fflush(stderr);
        if (dlclose(d.h) != 0) { std::fprintf(stderr, "dlclose race: %s\n", dlerror()); return 82; }
        d.h = nullptr;
        std::fprintf(stderr, "CALLBACK_RACE_DLCLOSE_RETURN target_exec=%d unpacker_exec=%d\n",
                     executable(d.target) ? 1 : 0, executable(d.unpacker) ? 1 : 0);
        std::fflush(stderr);
      } else {
        std::fprintf(stderr, "CALLBACK_RACE_PINNED\n");
        std::fflush(stderr);
      }

      worker.join();
      const int rv = worker_rv.load();
      std::fprintf(stderr, "CALLBACK_RACE_WORKER_RETURN rv=%d\n", rv);
      std::fflush(stderr);
      if (callback_race_pin) {
        return rv == (gen * 10000 + 63) ? 0 : 85;
      }
      // A surviving unmap arm is evidence against the expected in-flight fault.
      return 86;
    }

'''
if s.count(anchor) != 1:
    raise SystemExit(f'callback insertion anchor count={s.count(anchor)}')
s = s.replace(anchor, block, 1)
p.write_text(s)
