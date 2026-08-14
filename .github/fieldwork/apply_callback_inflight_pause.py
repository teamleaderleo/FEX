from pathlib import Path

p = Path('Source/Tools/LinuxEmulation/Thunks.cpp')
s = p.read_text()

inc = '#include <dlfcn.h>\n'
rep = '#include <dlfcn.h>\n#include <fcntl.h>\n#include <unistd.h>\n'
if s.count(inc) != 1:
    raise SystemExit(f'include anchor count={s.count(inc)}')
s = s.replace(inc, rep, 1)

anchor = '''    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);\n    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());\n\n'''
block = '''    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);\n    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());\n\n    if (::access("/tmp/fex-callback-race-arm", F_OK) == 0) {\n      int fd = ::open("/tmp/fex-callback-race-entered", O_WRONLY | O_CREAT | O_TRUNC, 0600);\n      if (fd >= 0) {\n        char marker = '1';\n        (void)::write(fd, &marker, 1);\n        ::close(fd);\n      }\n      fprintf(stderr, "DIAG_CALLBACK_INFLIGHT_SELECTED unpacker=%p target=%p\\n", callback, arg0);\n      fflush(stderr);\n      while (::access("/tmp/fex-callback-race-release", F_OK) != 0) {\n        ::usleep(1000);\n      }\n      fprintf(stderr, "DIAG_CALLBACK_INFLIGHT_RESUME unpacker=%p target=%p\\n", callback, arg0);\n      fflush(stderr);\n    }\n\n'''
if s.count(anchor) != 1:
    raise SystemExit(f'CallCallback anchor count={s.count(anchor)}')
p.write_text(s.replace(anchor, block, 1))
