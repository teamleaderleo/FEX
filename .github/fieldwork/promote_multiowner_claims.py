#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    s = path.read_text()
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    path.write_text(s.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    p = root / "Source/Tools/LinuxEmulation/Thunks.cpp"

    replace_once(
        p,
        '  fextl::unordered_map<uintptr_t, uintptr_t> LinkedHostToGuest;',
        '''  fextl::unordered_map<uintptr_t, fextl::vector<uintptr_t>> LinkedHostClaims;
  fextl::unordered_map<uintptr_t, uintptr_t> ActiveHostToGuest;''',
        'claim maps',
    )

    old_retire = r'''void ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;
  fextl::vector<uintptr_t> ToRetire;
  {
    std::lock_guard lk(ThunksMutex);
    for (auto& [Host, Target] : LinkedHostToGuest) {
      if (Target >= Base && Target - Base < Length) {
        ToRetire.emplace_back(Host);
        fprintf(stderr, "DIAG_MT_MATCH H=%#lx T=%#lx range=%#lx+%#lx\n", Host, Target, Base, Length);
      }
    }
    for (auto Host : ToRetire) LinkedHostToGuest.erase(Host);
  }
  auto CTX = static_cast<FEXCore::Context::Context*>(Thread->CTX);
  for (auto Host : ToRetire) CTX->RetireThunkTrampolineIRHandler(Thread, Host);
}
'''
    new_retire = r'''void ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;

  struct Transition {
    uintptr_t Host;
    uintptr_t OldTarget;
    uintptr_t NewTarget;
  };
  fextl::vector<Transition> Transitions;

  {
    std::lock_guard lk(ThunksMutex);
    for (auto it = LinkedHostClaims.begin(); it != LinkedHostClaims.end();) {
      const uintptr_t Host = it->first;
      auto& Claims = it->second;
      const auto ActiveIt = ActiveHostToGuest.find(Host);
      const uintptr_t OldActive = ActiveIt == ActiveHostToGuest.end() ? 0 : ActiveIt->second;
      bool ActiveRetires = OldActive >= Base && OldActive - Base < Length;

      Claims.erase(std::remove_if(Claims.begin(), Claims.end(), [&](uintptr_t Target) {
        const bool Retiring = Target >= Base && Target - Base < Length;
        if (Retiring) {
          fprintf(stderr, "DIAG_MULTI_DROP H=%#lx T=%#lx range=%#lx+%#lx\n", Host, Target, Base, Length);
        }
        return Retiring;
      }), Claims.end());

      if (ActiveRetires) {
        const uintptr_t NewActive = Claims.empty() ? 0 : Claims.front();
        Transitions.push_back({Host, OldActive, NewActive});
        if (NewActive) ActiveHostToGuest[Host] = NewActive;
        else ActiveHostToGuest.erase(Host);
      }

      if (Claims.empty()) it = LinkedHostClaims.erase(it);
      else ++it;
    }
  }

  auto CTX = static_cast<FEXCore::Context::Context*>(Thread->CTX);
  for (auto& Transition : Transitions) {
    fprintf(stderr, "DIAG_MULTI_RETIRE H=%#lx OLD=%#lx NEW=%#lx\n",
            Transition.Host, Transition.OldTarget, Transition.NewTarget);
    CTX->RetireThunkTrampolineIRHandler(Thread, Transition.Host);
    if (Transition.NewTarget) {
      CTX->AddThunkTrampolineIRHandler(Transition.Host, Transition.NewTarget);
      fprintf(stderr, "DIAG_MULTI_PROMOTE H=%#lx T=%#lx\n", Transition.Host, Transition.NewTarget);
    }
  }
}
'''
    replace_once(p, old_retire, new_retire, 'multi-owner retirement')

    old_link = r'''    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    CTX->AddThunkTrampolineIRHandler(args->original_callee, args->target_addr);
    {
      std::lock_guard lk(ThunkHandler->ThunksMutex);
      ThunkHandler->LinkedHostToGuest[args->original_callee] = args->target_addr;
    }
    fprintf(stderr, "DIAG_MT_OWNER H=%#lx T=%#lx\n", args->original_callee, args->target_addr);
'''
    new_link = r'''    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    bool FirstClaim = false;
    {
      std::lock_guard lk(ThunkHandler->ThunksMutex);
      auto& Claims = ThunkHandler->LinkedHostClaims[args->original_callee];
      if (std::find(Claims.begin(), Claims.end(), args->target_addr) == Claims.end()) {
        Claims.emplace_back(args->target_addr);
      }
      auto Active = ThunkHandler->ActiveHostToGuest.find(args->original_callee);
      if (Active == ThunkHandler->ActiveHostToGuest.end()) {
        ThunkHandler->ActiveHostToGuest[args->original_callee] = args->target_addr;
        FirstClaim = true;
      }
    }
    if (FirstClaim) {
      CTX->AddThunkTrampolineIRHandler(args->original_callee, args->target_addr);
      fprintf(stderr, "DIAG_MULTI_ACTIVE H=%#lx T=%#lx\n", args->original_callee, args->target_addr);
    } else {
      fprintf(stderr, "DIAG_MULTI_STANDBY H=%#lx T=%#lx\n", args->original_callee, args->target_addr);
    }
'''
    replace_once(p, old_link, new_link, 'multi-owner registration')

    print('Converted exact owner-retirement diagnostic to retained multi-owner claims')


if __name__ == '__main__':
    main()
