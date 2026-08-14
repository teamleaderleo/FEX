// SPDX-License-Identifier: MIT
/*
$info$
meta: glue|thunks ~ FEXCore side of thunks: Registration, Lookup
tags: glue|thunks
$end_info$
*/

#include "Thunks.h"
#include "LinuxSyscalls/Syscalls.h"
#include "LinuxSyscalls/ThreadManager.h"

#include <FEXCore/Config/Config.h>
#include <FEXCore/Core/CoreState.h>
#include <FEXCore/Core/X86Enums.h>
#include <FEXCore/Core/Thunks.h>
#include <FEXCore/Debug/InternalThreadState.h>
#include <FEXCore/Utils/LogManager.h>
#include <FEXCore/Utils/CompilerDefs.h>
#include <FEXCore/fextl/set.h>
#include <FEXCore/fextl/string.h>
#include <FEXCore/fextl/unordered_map.h>
#include <FEXCore/fextl/vector.h>

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <dlfcn.h>

#include <malloc.h>
#include <mutex>
#include <shared_mutex>
#include <stdint.h>
#include <utility>

#ifdef ENABLE_JEMALLOC_GLIBC
extern "C" {
// jemalloc defines nothrow on its internal C function signatures.
#define JEMALLOC_NOTHROW __attribute__((nothrow))
// Forward declare jemalloc functions because we can't include the headers from the glibc jemalloc project.
// This is because we can't simultaneously set up include paths for both of our internal jemalloc modules.
FEX_DEFAULT_VISIBILITY JEMALLOC_NOTHROW extern int glibc_je_is_known_allocation(void* ptr);
}
#endif

static __attribute__((aligned(16), naked, section("HostToGuestTrampolineTemplate"))) void HostToGuestTrampolineTemplate() {
#if defined(ARCHITECTURE_x86_64)
  asm("lea 0f(%rip), %r11 \n"
      "jmpq *0f(%rip) \n"
      ".align 8 \n"
      "0: \n"
      ".quad 0, 0, 0, 0 \n" // TrampolineInstanceInfo
  );
#elif defined(ARCHITECTURE_arm64)
  asm(
    // x11 is part of the custom ABI and needs to point to the TrampolineInstanceInfo.
    "ldr x16, 0f \n"
    "adr x11, 0f \n"
    "br x16 \n"
    // Manually align to the next 8-byte boundary
    // NOTE: GCC over-aligns to a full page when using .align directives on ARM (last tested on GCC 11.2)
    "nop \n"
    "0: \n"
    ".quad 0, 0, 0, 0 \n" // TrampolineInstanceInfo
  );
#else
#error Unsupported host architecture
#endif
}

extern char __start_HostToGuestTrampolineTemplate[];
extern char __stop_HostToGuestTrampolineTemplate[];

namespace FEX::HLE {

static thread_local FEX::HLE::ThreadStateObject* ThreadObject {};

struct TrampolineInstanceInfo {
  void* HostPacker;
  uintptr_t CallCallback;
  uintptr_t GuestUnpacker;
  uintptr_t GuestTarget;
};

struct GuestCallbackDescriptor {
  enum class State : uint32_t { Live, Draining, Revoked };

  explicit GuestCallbackDescriptor(uintptr_t Unpacker, uintptr_t Target)
    : GuestUnpacker {Unpacker}, GuestTarget {Target} {}

  bool TryAcquire() {
    std::unique_lock lk(Mutex);
    CV.wait(lk, [this]() { return Status != State::Draining; });
    if (Status != State::Live) {
      return false;
    }
    ++Active;
    return true;
  }

  void Release() {
    std::lock_guard lk(Mutex);
    LOGMAN_THROW_A_FMT(Active != 0, "Callback descriptor active-count underflow");
    --Active;
    if (Active == 0) {
      CV.notify_all();
    }
  }

  void BeginDrain() {
    std::lock_guard lk(Mutex);
    if (Status == State::Revoked) {
      return;
    }
    ++DrainRequests;
    Status = State::Draining;
  }

  void WaitForDrain() {
    std::unique_lock lk(Mutex);
    CV.wait(lk, [this]() { return Active == 0; });
  }

  void CommitRevoke() {
    std::lock_guard lk(Mutex);
    Status = State::Revoked;
    DrainRequests = 0;
    CV.notify_all();
  }

  void RollbackDrain() {
    std::lock_guard lk(Mutex);
    // Another overlapping successful retirement may already have permanently
    // revoked this descriptor. In that case this transaction has nothing left
    // to roll back and must not underflow the request count.
    if (Status == State::Revoked) {
      return;
    }
    LOGMAN_THROW_A_FMT(DrainRequests != 0, "Callback descriptor drain-request underflow");
    --DrainRequests;
    if (DrainRequests == 0 && Status == State::Draining) {
      Status = State::Live;
      CV.notify_all();
    }
  }

  State GetState() {
    std::lock_guard lk(Mutex);
    return Status;
  }

  size_t GetActive() {
    std::lock_guard lk(Mutex);
    return Active;
  }

  std::mutex Mutex;
  std::condition_variable CV;
  State Status {State::Live};
  size_t Active {};
  size_t DrainRequests {};
  const uintptr_t GuestUnpacker;
  const uintptr_t GuestTarget;
};

// Opaque type pointing to an instance of HostToGuestTrampolineTemplate and its
// embedded TrampolineInstanceInfo
struct HostToGuestTrampolinePtr;

static TrampolineInstanceInfo& GetInstanceInfo(HostToGuestTrampolinePtr* Trampoline) {
  const auto Length = __stop_HostToGuestTrampolineTemplate - __start_HostToGuestTrampolineTemplate;
  const auto InstanceInfoOffset = Length - sizeof(TrampolineInstanceInfo);
  return *reinterpret_cast<TrampolineInstanceInfo*>(reinterpret_cast<char*>(Trampoline) + InstanceInfoOffset);
}

struct GuestcallInfo {
  uintptr_t GuestUnpacker;
  uintptr_t GuestTarget;

  bool operator==(const GuestcallInfo&) const noexcept = default;
};

struct GuestcallInfoHash {
  size_t operator()(const GuestcallInfo& x) const noexcept {
    // Hash only the target address, which is generally unique.
    // For the unlikely case of a hash collision, fextl::unordered_map still picks the correct bucket entry.
    return std::hash<uintptr_t> {}(x.GuestTarget);
  }
};

namespace ThunkFunctions {
  void LoadLib(void* ArgsV);
  void IsLibLoaded(void* ArgsRV);
  void IsHostHeapAllocation(void* ArgsRV);
  void LinkAddressToGuestFunction(void* argsv);
  void AllocateHostTrampolineForGuestFunction(void* ArgsRV);
} // namespace ThunkFunctions

struct ThunkHandler_impl final : public FEX::HLE::ThunkHandler {
  std::shared_mutex ThunksMutex;

  // Can't be a string_view. We need to keep a copy of the library name in-case string_view pointer goes away.
  // Ideally we track when a library has been unloaded and remove it from this set before the memory backing goes away.
  fextl::set<fextl::string> Libs;

  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;

  struct DrainingGuestRange {
    uintptr_t Base;
    uintptr_t Length;

    bool Contains(uintptr_t Address) const {
      return Address >= Base && (Address - Base) < Length;
    }
  };
  fextl::vector<DrainingGuestRange> DrainingGuestRanges;
  fextl::unordered_map<uintptr_t, fextl::vector<uintptr_t>> LinkedHostClaims;
  fextl::unordered_map<uintptr_t, uintptr_t> ActiveHostToGuest;

  uint8_t* HostTrampolineInstanceDataPtr;
  size_t HostTrampolineInstanceDataAvailable = 0;

  /*
      Set arg0/1 to arg regs, use CTX::HandleCallback to handle the callback
  */
  static void CallCallback(void* callback, void* arg0, void* arg1) {
    if (!ThreadObject) {
      ERROR_AND_DIE_FMT("Thunked library attempted to invoke guest callback asynchronously");
    }

    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());

    if (ThunkHandler->Is64BitMode()) {
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDI] = (uintptr_t)arg0;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSI] = (uintptr_t)arg1;
    } else {
      if ((reinterpret_cast<uintptr_t>(arg1) >> 32) != 0) {
        ERROR_AND_DIE_FMT("Tried to call guest function with arguments packed to a 64-bit address");
      }
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RCX] = (uintptr_t)arg0;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDX] = (uintptr_t)arg1;
    }

    CTX->HandleCallback(ThreadObject->Thread, (uintptr_t)callback);
  }

  static void CallCallbackDescriptor(void* DescriptorV, void* UnusedTarget, void* ArgsRV) {
    (void)UnusedTarget;
    auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(DescriptorV);
    LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Host callback trampoline has no descriptor");

    if (!Descriptor->TryAcquire()) {
      std::_Exit(113);
    }

    struct CallbackExecutionLease final {
      GuestCallbackDescriptor* Descriptor;
      ~CallbackExecutionLease() { Descriptor->Release(); }
    } Lease {Descriptor};


    if (!ThreadObject) {
      ERROR_AND_DIE_FMT("Thunked library attempted to invoke guest callback asynchronously");
    }

    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    const uintptr_t GuestUnpacker = Descriptor->GuestUnpacker;
    const uintptr_t GuestTarget = Descriptor->GuestTarget;

    if (ThunkHandler->Is64BitMode()) {
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDI] = GuestTarget;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSI] = reinterpret_cast<uintptr_t>(ArgsRV);
    } else {
      if ((reinterpret_cast<uintptr_t>(ArgsRV) >> 32) != 0 || (GuestTarget >> 32) != 0) {
        ERROR_AND_DIE_FMT("Tried to call guest function with arguments packed to a 64-bit address");
      }
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RCX] = GuestTarget;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDX] = reinterpret_cast<uintptr_t>(ArgsRV);
    }

    CTX->HandleCallback(ThreadObject->Thread, GuestUnpacker);
  }

  FEXCore::ThunkedFunction* LookupThunk(const FEXCore::IR::SHA256Sum& sha256) override {

    std::shared_lock lk(ThunksMutex);

    auto it = Thunks.find(sha256);

    if (it != Thunks.end()) {
      return it->second;
    } else {
      return nullptr;
    }
  }

  void RegisterTLSState(FEX::HLE::ThreadStateObject* _ThreadObject) override {
    ThreadObject = _ThreadObject;
  }

  void BeginGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;
  void CommitGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;
  void RollbackGuestRangeRetirement(uintptr_t Base, uintptr_t Length) override;

  void AppendThunkDefinitions(std::span<const FEXCore::IR::ThunkDefinition> Definitions) override {
    for (auto& Definition : Definitions) {
      Thunks.emplace(Definition.Sum, Definition.ThunkFunction);
    }
  }

  void LoadLib(std::string_view Name);

private:
  // Bits in a SHA256 sum are already randomly distributed, so truncation yields a suitable hash function
  struct TruncatingSHA256Hash {
    size_t operator()(const FEXCore::IR::SHA256Sum& SHA256Sum) const noexcept {
      return (const size_t&)SHA256Sum;
    }
  };

  fextl::unordered_map<FEXCore::IR::SHA256Sum, FEXCore::ThunkedFunction*, TruncatingSHA256Hash> Thunks = {
    {// sha256(fex:loadlib)
     {0x27, 0x7e, 0xb7, 0x69, 0x5b, 0xe9, 0xab, 0x12, 0x6e, 0xf7, 0x85, 0x9d, 0x4b, 0xc9, 0xa2, 0x44,
      0x46, 0xcf, 0xbd, 0xb5, 0x87, 0x43, 0xef, 0x28, 0xa2, 0x65, 0xba, 0xfc, 0x89, 0x0f, 0x77, 0x80},
     &ThunkFunctions::LoadLib},
    {// sha256(fex:is_lib_loaded)
     {0xee, 0x57, 0xba, 0x0c, 0x5f, 0x6e, 0xef, 0x2a, 0x8c, 0xb5, 0x19, 0x81, 0xc9, 0x23, 0xe6, 0x51,
      0xae, 0x65, 0x02, 0x8f, 0x2b, 0x5d, 0x59, 0x90, 0x6a, 0x7e, 0xe2, 0xe7, 0x1c, 0x33, 0x8a, 0xff},
     &ThunkFunctions::IsLibLoaded},
    {// sha256(fex:is_host_heap_allocation)
     {0xf5, 0x77, 0x68, 0x43, 0xbb, 0x6b, 0x28, 0x18, 0x40, 0xb0, 0xdb, 0x8a, 0x66, 0xfb, 0x0e, 0x2d,
      0x98, 0xc2, 0xad, 0xe2, 0x5a, 0x18, 0x5a, 0x37, 0x2e, 0x13, 0xc9, 0xe7, 0xb9, 0x8c, 0xa9, 0x3e},
     &ThunkFunctions::IsHostHeapAllocation},
    {// sha256(fex:link_address_to_function)
     {0xe6, 0xa8, 0xec, 0x1c, 0x7b, 0x74, 0x35, 0x27, 0xe9, 0x4f, 0x5b, 0x6e, 0x2d, 0xc9, 0xa0, 0x27,
      0xd6, 0x1f, 0x2b, 0x87, 0x8f, 0x2d, 0x35, 0x50, 0xea, 0x16, 0xb8, 0xc4, 0x5e, 0x42, 0xfd, 0x77},
     &ThunkFunctions::LinkAddressToGuestFunction},
    {// sha256(fex:allocate_host_trampoline_for_guest_function)
     {0x9b, 0xb2, 0xf4, 0xb4, 0x83, 0x7d, 0x28, 0x93, 0x40, 0xcb, 0xf4, 0x7a, 0x0b, 0x47, 0x85, 0x87,
      0xf9, 0xbc, 0xb5, 0x27, 0xca, 0xa6, 0x93, 0xa5, 0xc0, 0x73, 0x27, 0x24, 0xae, 0xc8, 0xb8, 0x5a},
     &ThunkFunctions::AllocateHostTrampolineForGuestFunction},
  };

  FEX_CONFIG_OPT(Is64BitMode, IS64BIT_MODE);
  FEX_CONFIG_OPT(ThunkHostLibsPath, THUNKHOSTLIBS);
};

void ThunkHandler_impl::LoadLib(std::string_view Name) {
  auto SOName = ThunkHostLibsPath();
  while (SOName.ends_with('/')) {
    SOName.pop_back();
  }
  SOName = fmt::format("{}{}/{}-host.so", SOName, (Is64BitMode() ? "" : "_32"), Name);

  LogMan::Msg::DFmt("LoadLib: {} -> {}", Name, SOName);

  auto Handle = dlopen(SOName.c_str(), RTLD_LOCAL | RTLD_NOW);
  if (!Handle) {
    ERROR_AND_DIE_FMT("LoadLib: Failed to dlopen thunk library {}: {}", SOName, dlerror());
  }

  // Library names often include dashes, which may not be used in C++ identifiers.
  // They are replaced with underscores hence.
  auto InitSym = "fexthunks_exports_" + fextl::string {Name};
  std::replace(InitSym.begin(), InitSym.end(), '-', '_');

  struct ExportEntry {
    uint8_t* sha256;
    FEXCore::ThunkedFunction* Fn;
  };

  ExportEntry* (*InitFN)();
  (void*&)InitFN = dlsym(Handle, InitSym.c_str());
  if (!InitFN) {
    ERROR_AND_DIE_FMT("LoadLib: Failed to find export {}", InitSym);
  }

  auto Exports = InitFN();
  if (!Exports) {
    ERROR_AND_DIE_FMT("LoadLib: Failed to initialize thunk library {}. "
                      "Check if the corresponding host library is installed "
                      "or disable thunking of this library.",
                      Name);
  }

  {
    std::lock_guard lk(ThunksMutex);

    Libs.insert(fextl::string {Name});

    int i;
    for (i = 0; Exports[i].sha256; i++) {
      Thunks[*reinterpret_cast<FEXCore::IR::SHA256Sum*>(Exports[i].sha256)] = Exports[i].Fn;
    }

    LogMan::Msg::DFmt("Loaded {} syms", i);
  }

  _SyscallHandler->TriggerGuestLibWrapperCodeCacheLoad(*ThreadObject->Thread, ThreadObject->Thread->CurrentFrame->State.rip);
}

/**
 * Generates a host-callable trampoline to call guest functions via the host ABI.
 *
 * This trampoline uses the same calling convention as the given HostPacker. Trampolines
 * are cached, so it's safe to call this function repeatedly on the same arguments without
 * leaking memory.
 *
 * Invoking the returned trampoline has the effect of:
 * - packing the arguments (using the HostPacker identified by its SHA256)
 * - performing a host->guest transition
 * - unpacking the arguments via GuestUnpacker
 * - calling the function at GuestTarget
 *
 * The primary use case of this is ensuring that guest function pointers ("callbacks")
 * passed to thunked APIs can safely be called by the native host library.
 *
 * Returns a pointer to the generated host trampoline and its TrampolineInstanceInfo.
 *
 * If HostPacker is zero, the trampoline will be partially initialized and needs to be
 * finalized with a call to FinalizeHostTrampolineForGuestFunction. A typical use case
 * is to allocate the trampoline for a given GuestTarget/GuestUnpacker on the guest-side,
 * and provide the HostPacker host-side.
 */
void ThunkHandler_impl::BeginGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;

  fextl::vector<GuestCallbackDescriptor*> CallbackDescriptorsToDrain;
  {
    std::lock_guard lk(ThunksMutex);
    DrainingGuestRanges.emplace_back(DrainingGuestRange {Base, Length});

    for (auto& [Guestcall, Trampoline] : GuestcallToHostTrampoline) {
      const bool UnpackerInRange = Guestcall.GuestUnpacker >= Base && (Guestcall.GuestUnpacker - Base) < Length;
      const bool TargetInRange = Guestcall.GuestTarget >= Base && (Guestcall.GuestTarget - Base) < Length;
      if (!UnpackerInRange && !TargetInRange) continue;

      auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(GetInstanceInfo(Trampoline).GuestUnpacker);
      LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Beginning callback retirement without descriptor");
      Descriptor->BeginDrain();
      CallbackDescriptorsToDrain.emplace_back(Descriptor);
    }
  }

  // Never wait while holding ThunksMutex. An already-active guest callback may
  // itself invoke another thunk before returning.
  for (auto* Descriptor : CallbackDescriptorsToDrain) {
    Descriptor->WaitForDrain();
  }
}

void ThunkHandler_impl::CommitGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;

  struct Transition {
    uintptr_t Host;
    uintptr_t OldTarget;
    uintptr_t NewTarget;
  };
  fextl::vector<Transition> Transitions;

  {
    std::lock_guard lk(ThunksMutex);

    // Commit every descriptor currently depending on the unmapped range,
    // including descriptors created after Begin while the range was draining.
    for (auto It = GuestcallToHostTrampoline.begin(); It != GuestcallToHostTrampoline.end();) {
      const auto Unpacker = It->first.GuestUnpacker;
      const auto Target = It->first.GuestTarget;
      const bool UnpackerInRange = Unpacker >= Base && (Unpacker - Base) < Length;
      const bool TargetInRange = Target >= Base && (Target - Base) < Length;
      if (!UnpackerInRange && !TargetInRange) {
        ++It;
        continue;
      }

      auto* Trampoline = It->second;
      auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(GetInstanceInfo(Trampoline).GuestUnpacker);
      LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Committing callback retirement without descriptor");
      Descriptor->CommitRevoke();
      It = GuestcallToHostTrampoline.erase(It);
    }

    // Dynamic H->T state is retired only after host munmap succeeds. This is
    // transaction-safe but, by itself, does not close an already-selected H->T
    // execution window. The staged design pairs this callback transaction with
    // resident generated PFN bridge targets.
    for (auto it = LinkedHostClaims.begin(); it != LinkedHostClaims.end();) {
      const uintptr_t Host = it->first;
      auto& Claims = it->second;
      const auto ActiveIt = ActiveHostToGuest.find(Host);
      const uintptr_t OldActive = ActiveIt == ActiveHostToGuest.end() ? 0 : ActiveIt->second;
      const bool ActiveRetires = OldActive >= Base && OldActive - Base < Length;

      Claims.erase(std::remove_if(Claims.begin(), Claims.end(), [&](uintptr_t Target) {
        return Target >= Base && Target - Base < Length;
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

    auto Range = std::find_if(DrainingGuestRanges.begin(), DrainingGuestRanges.end(), [&](const DrainingGuestRange& R) {
      return R.Base == Base && R.Length == Length;
    });
    LOGMAN_THROW_A_FMT(Range != DrainingGuestRanges.end(), "Committing unknown guest-range retirement");
    DrainingGuestRanges.erase(Range);
  }

  auto CTX = static_cast<FEXCore::Context::Context*>(Thread->CTX);
  for (auto& Transition : Transitions) {
    CTX->RetireThunkTrampolineIRHandler(Thread, Transition.Host);
    if (Transition.NewTarget) {
      CTX->ActivateThunkTrampolineIRHandler(Thread, Transition.Host, Transition.NewTarget);
    }
  }
}

void ThunkHandler_impl::RollbackGuestRangeRetirement(uintptr_t Base, uintptr_t Length) {
  if (!Length) return;

  std::lock_guard lk(ThunksMutex);
  for (auto& [Guestcall, Trampoline] : GuestcallToHostTrampoline) {
    const bool UnpackerInRange = Guestcall.GuestUnpacker >= Base && (Guestcall.GuestUnpacker - Base) < Length;
    const bool TargetInRange = Guestcall.GuestTarget >= Base && (Guestcall.GuestTarget - Base) < Length;
    if (!UnpackerInRange && !TargetInRange) continue;

    auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(GetInstanceInfo(Trampoline).GuestUnpacker);
    LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Rolling back callback retirement without descriptor");
    Descriptor->RollbackDrain();
  }

  auto Range = std::find_if(DrainingGuestRanges.begin(), DrainingGuestRanges.end(), [&](const DrainingGuestRange& R) {
    return R.Base == Base && R.Length == Length;
  });
  LOGMAN_THROW_A_FMT(Range != DrainingGuestRanges.end(), "Rolling back unknown guest-range retirement");
  DrainingGuestRanges.erase(Range);
}

FEX_DEFAULT_VISIBILITY HostToGuestTrampolinePtr*
MakeHostTrampolineForGuestFunction(void* HostPacker, uintptr_t GuestTarget, uintptr_t GuestUnpacker) {
  LOGMAN_THROW_A_FMT(GuestTarget, "Tried to create host-trampoline to null pointer guest function");

  const auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());

  const GuestcallInfo gci = {GuestUnpacker, GuestTarget};

  // Try first with shared_lock
  {
    std::shared_lock lk(ThunkHandler->ThunksMutex);

    auto found = ThunkHandler->GuestcallToHostTrampoline.find(gci);
    if (found != ThunkHandler->GuestcallToHostTrampoline.end()) {
      return found->second;
    }
  }

  std::lock_guard lk(ThunkHandler->ThunksMutex);

  // Retry lookup with full lock before making a new trampoline to avoid double trampolines
  {
    auto found = ThunkHandler->GuestcallToHostTrampoline.find(gci);
    if (found != ThunkHandler->GuestcallToHostTrampoline.end()) {
      return found->second;
    }
  }

  LogMan::Msg::DFmt("Thunks: Adding host trampoline for guest function {:#x} via unpacker {:#x}", GuestTarget, GuestUnpacker);

  const auto HostToGuestTrampolineSize = __stop_HostToGuestTrampolineTemplate - __start_HostToGuestTrampolineTemplate;

  if (ThunkHandler->HostTrampolineInstanceDataAvailable < HostToGuestTrampolineSize) {
    const auto allocation_step = 16 * 1024;
    ThunkHandler->HostTrampolineInstanceDataAvailable = allocation_step;
    ThunkHandler->HostTrampolineInstanceDataPtr = (uint8_t*)mmap(0, ThunkHandler->HostTrampolineInstanceDataAvailable,
                                                                 PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

    LOGMAN_THROW_A_FMT(ThunkHandler->HostTrampolineInstanceDataPtr != MAP_FAILED, "Failed to mmap HostTrampolineInstanceDataPtr");
  }

  auto HostTrampoline = reinterpret_cast<HostToGuestTrampolinePtr*>(ThunkHandler->HostTrampolineInstanceDataPtr);
  ThunkHandler->HostTrampolineInstanceDataAvailable -= HostToGuestTrampolineSize;
  ThunkHandler->HostTrampolineInstanceDataPtr += HostToGuestTrampolineSize;
  memcpy(HostTrampoline, (void*)&HostToGuestTrampolineTemplate, HostToGuestTrampolineSize);
  auto* Descriptor = new GuestCallbackDescriptor {GuestUnpacker, GuestTarget};
  for (const auto& Range : ThunkHandler->DrainingGuestRanges) {
    if (Range.Contains(GuestUnpacker) || Range.Contains(GuestTarget)) {
      Descriptor->BeginDrain();
    }
  }
  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {
    .HostPacker = HostPacker,
    .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallbackDescriptor,
    .GuestUnpacker = reinterpret_cast<uintptr_t>(Descriptor),
    .GuestTarget = 0};

  ThunkHandler->GuestcallToHostTrampoline[gci] = HostTrampoline;
  return HostTrampoline;
}

FEX_DEFAULT_VISIBILITY void FinalizeHostTrampolineForGuestFunction(HostToGuestTrampolinePtr* TrampolineAddress, void* HostPacker) {
  if (TrampolineAddress == nullptr) {
    return;
  }

  auto& Trampoline = GetInstanceInfo(TrampolineAddress);

  LOGMAN_THROW_A_FMT(Trampoline.CallCallback == (uintptr_t)&ThunkHandler_impl::CallCallbackDescriptor,
                     "Invalid trampoline at {} passed to {}", fmt::ptr(TrampolineAddress), __FUNCTION__);

  if (!Trampoline.HostPacker) {
    LogMan::Msg::DFmt("Thunks: Finalizing trampoline at {} with host packer {}", fmt::ptr(TrampolineAddress), fmt::ptr(HostPacker));
    Trampoline.HostPacker = HostPacker;
  }
}

namespace ThunkFunctions {
  void LoadLib(void* ArgsV) {
    struct LoadlibArgs {
      const char* Name;
    };

    auto Args = reinterpret_cast<LoadlibArgs*>(ArgsV);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());

    ThunkHandler->LoadLib(Args->Name);
  }

  void IsLibLoaded(void* ArgsRV) {
    struct ArgsRV_t {
      const char* Name;
      bool rv;
    };

    auto& [Name, rv] = *reinterpret_cast<ArgsRV_t*>(ArgsRV);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());

    {
      std::shared_lock lk(ThunkHandler->ThunksMutex);
      rv = ThunkHandler->Libs.contains(Name);
    }
  }

  /**
   * Checks if the given pointer is allocated on the host heap.
   *
   * This is useful for thunking APIs that need to work with both guest
   * and host heap pointers.
   */
  void IsHostHeapAllocation(void* ArgsRV) {
#ifdef ENABLE_JEMALLOC_GLIBC
    struct ArgsRV_t {
      void* ptr;
      bool rv;
    }* args = reinterpret_cast<ArgsRV_t*>(ArgsRV);

    args->rv = glibc_je_is_known_allocation(args->ptr);
#else
    // Thunks usage without jemalloc isn't supported
    ERROR_AND_DIE_FMT("Unsupported: Thunks querying for host heap allocation information");
#endif
  }

  /**
   * Instructs the Core to redirect calls to functions at the given
   * address to another function. The original callee address is passed
   * to the target function through an implicit argument stored in r11.
   *
   * For 32-bit the implicit argument is stored in the lower 32-bits of mm0.
   *
   * The primary use case of this is ensuring that host function pointers
   * returned from thunked APIs can safely be called by the guest.
   */
  void LinkAddressToGuestFunction(void* argsv) {
    struct args_t {
      uintptr_t original_callee;
      uintptr_t target_addr; // Guest function to call when branching to original_callee
    };

    auto args = reinterpret_cast<args_t*>(argsv);
    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
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
      CTX->ActivateThunkTrampolineIRHandler(ThreadObject->Thread, args->original_callee, args->target_addr);
    } else {
    }
  }

  /**
   * Guest-side helper to initiate creation of a host trampoline for
   * calling guest functions. This must be followed by a host-side call
   * to FinalizeHostTrampolineForGuestFunction to make the trampoline
   * usable.
   *
   * This two-step initialization is equivalent to a host-side call to
   * MakeHostTrampolineForGuestFunction. The split is needed if the
   * host doesn't have all information needed to create the trampoline
   * on its own.
   */
  void AllocateHostTrampolineForGuestFunction(void* ArgsRV) {
    struct ArgsRV_t {
      uintptr_t GuestUnpacker;
      uintptr_t GuestTarget;
      uintptr_t rv; // Pointer to host trampoline + TrampolineInstanceInfo
    }* args = reinterpret_cast<ArgsRV_t*>(ArgsRV);

    args->rv = (uintptr_t)MakeHostTrampolineForGuestFunction(nullptr, args->GuestTarget, args->GuestUnpacker);
  }
} // namespace ThunkFunctions

FEX_DEFAULT_VISIBILITY void* GetGuestStack() {
  if (!ThreadObject) {
    ERROR_AND_DIE_FMT("Thunked library attempted to query guest stack pointer asynchronously");
  }

  return (void*)(uintptr_t)((ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSP]));
}

FEX_DEFAULT_VISIBILITY void MoveGuestStack(uintptr_t NewAddress) {
  if (!ThreadObject) {
    ERROR_AND_DIE_FMT("Thunked library attempted to query guest stack pointer asynchronously");
  }

  if (NewAddress >> 32) {
    ERROR_AND_DIE_FMT("Tried to set stack pointer for 32-bit guest to a 64-bit address");
  }

  ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSP] = NewAddress;
}

fextl::unique_ptr<ThunkHandler> CreateThunkHandler() {
  return fextl::make_unique<ThunkHandler_impl>();
}
} // namespace FEX::HLE
